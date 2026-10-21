import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path

import pytest

from jdocbench.adapters import ADAPTERS
from jdocbench.adapters.mineru import MineruAdapter, _env
from jdocbench.normalizers import NORMALIZERS
from jdocbench.normalizers.mineru import normalize

_SAMPLES = Path(__file__).resolve().parents[1] / "samples"
_OPT_IN_ENV = "JDOCBENCH_MINERU"


def _raw_dir(tmp_path: Path, data: dict) -> Path:
    raw_dir = tmp_path / "raw" / "mineru"
    raw_dir.mkdir(parents=True)
    (raw_dir / "middle_json.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return raw_dir


def _text(kind: str, text: str, bbox: list[float] | None = None, **fields: object) -> dict:
    block = {"type": kind, "index": 0, "content": [{"type": "text", "content": text}], **fields}
    if bbox is not None:
        block["bbox"] = bbox
    return block


def test_registered() -> None:
    assert "mineru" in [adapter.name for adapter in ADAPTERS]
    assert NORMALIZERS["mineru"] is normalize


def test_available_is_false_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    # mineru が入っていても、環境変数が無ければ動かさない（ライセンスのためオプトイン）
    monkeypatch.delenv(_OPT_IN_ENV, raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert MineruAdapter().available() is False


def test_available_is_true_with_opt_in_and_mineru(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_OPT_IN_ENV, "1")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert MineruAdapter().available() is True


def test_available_is_false_without_mineru(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_OPT_IN_ENV, "1")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert MineruAdapter().available() is False


def test_version_is_unknown_without_mineru(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    assert MineruAdapter().version() == "unknown"


def test_env_disables_llm_aided_and_fixes_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 補助の後処理（外部 API）を環境変数で切り、小モデルは ONNX に固定する。"""
    monkeypatch.delenv("MINERU_MODEL_SOURCE", raising=False)
    env = _env()
    assert env["MINERU_LLM_AIDED_FEATURES_TITLE_LEVELING"] == "false"
    assert env["MINERU_LLM_AIDED_FEATURES_CROSS_PAGE_TABLE_CELL_MERGE"] == "false"
    assert env["MINERU_MODEL_SMALL_BACKEND"] == "onnx"
    assert env["MINERU_MODEL_SOURCE"] == "huggingface"
    # 利用者が取得元を設定済みならそれを使う（local なら通信しない）
    monkeypatch.setenv("MINERU_MODEL_SOURCE", "local")
    assert _env()["MINERU_MODEL_SOURCE"] == "local"


def test_normalize_blocks_tables_and_pages(tmp_path: Path) -> None:
    """段落・見出し・表（キャプション → 本体 → 脚注）を読み順に、画像と行間数式は落とす。"""
    data = {
        "metadata": {"document": {"page_count": 3}},
        "extensions": {
            "docvortex_layout": {"pages": [{"page_idx": 0, "width_pt": 600.0, "height_pt": 800.0}]}
        },
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    _text("header", "報道資料", [0.1, 0.1, 0.2, 0.15]),
                    _text("doc_title", "我が国のこどもの数", [0.25, 0.3, 0.75, 0.35], level=1),
                    {
                        "type": "text",
                        "index": 2,
                        "bbox": [0.1, 0.4, 0.9, 0.45],
                        # 行内要素は出現順に連結する（リンクは中の要素を辿る）
                        "content": [
                            {"type": "text", "content": "詳しくは "},
                            {
                                "type": "hyperlink",
                                "url": "https://example.jp/",
                                "content": [{"type": "text", "content": "https://example.jp"}],
                            },
                            {"type": "equation_inline", "content": "x^2"},
                        ],
                    },
                    {
                        "type": "table",
                        "index": 3,
                        "bbox": [0.1, 0.5, 0.9, 0.7],
                        "content": [
                            _text("table_caption", "表1 男女別こどもの数", [0.3, 0.48, 0.7, 0.5]),
                            {
                                "type": "table_body",
                                "index": 4,
                                "bbox": [0.1, 0.5, 0.9, 0.7],
                                "image_path": "images/page_1_table_4.jpg",
                                "content": (
                                    "<table><tr><td colspan='2'>区分</td><td>人数</td></tr>"
                                    "<tr><td rowspan='2'>こども</td><td>男</td><td>681</td></tr>"
                                    "<tr><td>女</td><td>648</td></tr></table>"
                                ),
                            },
                            _text("table_footnote", "注）四捨五入", [0.1, 0.7, 0.5, 0.72]),
                        ],
                    },
                    {
                        "type": "chart",
                        "index": 5,
                        "content": [
                            _text("chart_caption", "図1 推移"),
                            {"type": "chart_body", "index": 6, "content": ""},
                        ],
                    },
                    {"type": "equation", "index": 7, "content": "E = mc^2"},
                    # 空の段落は捨てる
                    _text("text", "  "),
                    _text("page_number", "1", [0.5, 0.95, 0.52, 0.97]),
                ],
            },
            # ページの大きさが無いページは bbox を 0〜1 のまま。page_idx 1 は空ページとして残す
            {"page_idx": 2, "blocks": [_text("text", "おわり", [0.1, 0.2, 0.3, 0.4])]},
        ],
    }
    doc = normalize(_raw_dir(tmp_path, data))
    assert doc.adapter == "mineru"
    assert [page.number for page in doc.pages] == [1, 2, 3]
    page1 = doc.pages[0]
    assert [(block.type, block.extra["type"]) for block in page1.blocks] == [
        ("paragraph", "header"),
        ("paragraph", "doc_title"),
        ("paragraph", "text"),
        ("paragraph", "table_caption"),
        ("table", "table_body"),
        ("paragraph", "table_footnote"),
        ("paragraph", "chart_caption"),
        ("paragraph", "page_number"),
    ]
    assert page1.blocks[1].extra["level"] == 1
    assert page1.blocks[2].text == "詳しくは https://example.jpx^2"
    # 結合セルは範囲の先頭にだけ文字列を置く（IR の規約）
    assert page1.blocks[4].rows == [
        ["区分", "", "人数"],
        ["こども", "男", "681"],
        ["", "女", "648"],
    ]
    # 0〜1 の bbox をページの大きさ（PDF ポイント）に直す
    assert page1.blocks[0].bbox == pytest.approx((60.0, 80.0, 120.0, 120.0))
    assert page1.blocks[0].extra["bbox_unit"] == "pt"
    assert doc.pages[1].blocks == []
    assert doc.pages[2].blocks[0].bbox == pytest.approx((0.1, 0.2, 0.3, 0.4))
    assert doc.pages[2].blocks[0].extra["bbox_unit"] == "ratio"


def test_normalize_skips_broken_items(tmp_path: Path) -> None:
    """形の崩れた要素では止まらない（ページ番号の無いページ・空の表・数値でない bbox）。"""
    data = {
        "pages": [
            {"blocks": [_text("text", "ページ番号なし")]},
            {
                "page_idx": 0,
                "blocks": [
                    "not a block",
                    {"type": "table_body", "index": 0, "content": "<table></table>"},
                    _text("text", "本文", ["a", 0, 1, 1]),
                ],
            },
        ]
    }
    doc = normalize(_raw_dir(tmp_path, data))
    assert [page.number for page in doc.pages] == [1]
    assert [(block.type, block.text, block.bbox) for block in doc.pages[0].blocks] == [
        ("paragraph", "本文", None)
    ]


@pytest.mark.skipif(
    not os.environ.get(_OPT_IN_ENV),
    reason=f"{_OPT_IN_ENV} が無い環境ではスキップ（初回はモデル約 820MB を取得する）",
)
@pytest.mark.skipif(
    importlib.util.find_spec("mineru") is None, reason="mineru が無い環境ではスキップ"
)
def test_mineru_writes_raw_output(tmp_path: Path) -> None:
    # 表を 4 つ含みスパイク（#195）で実測済みの文書を使う
    pdf = _SAMPLES / "stat-go-jp-children-population-2026.pdf"
    if not pdf.is_file():
        pytest.skip("samples/ に対象 PDF が無い")

    out_dir = tmp_path / "raw" / "mineru"
    metrics = MineruAdapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0
    assert metrics["extra"]["tier"] == "basic"
    assert metrics["extra"]["small_backend"] == "onnx"
    assert (out_dir / "markdown.md").is_file()

    doc = normalize(out_dir)
    assert [page.number for page in doc.pages] == [1, 2, 3, 4, 5, 6]
    # スパイクの実測と同じく p2 に 2 つ、p4 に 2 つの表を検出する
    assert [len([b for b in page.blocks if b.type == "table"]) for page in doc.pages] == [
        0,
        2,
        0,
        2,
        0,
        0,
    ]
