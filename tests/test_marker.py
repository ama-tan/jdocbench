import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _samples import SAMPLES

from jdocbench.adapters import ADAPTERS
from jdocbench.adapters import marker as marker_adapter
from jdocbench.adapters.marker import MarkerAdapter, find_document_json
from jdocbench.normalizers import NORMALIZERS
from jdocbench.normalizers._html_table import table_rows
from jdocbench.normalizers.marker import normalize as normalize_marker
from jdocbench.perf import run_command

_OPT_IN_ENV = "JDOCBENCH_MARKER"


def test_available_is_false_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    # marker が入っていても、環境変数が無ければ動かさない（重みのライセンスとメモリのため）
    monkeypatch.delenv(_OPT_IN_ENV, raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert MarkerAdapter().available() is False


def test_available_is_false_with_empty_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_OPT_IN_ENV, "")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert MarkerAdapter().available() is False


def test_available_is_true_with_opt_in_and_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_OPT_IN_ENV, "1")
    asked: list[str] = []
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: asked.append(name) or object())
    assert MarkerAdapter().available() is True
    assert asked == ["marker"]


def test_available_is_false_without_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_OPT_IN_ENV, "1")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert MarkerAdapter().available() is False


def test_version_is_unknown_without_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    assert MarkerAdapter().version() == "unknown"


def test_registered_as_adapter_and_normalizer() -> None:
    assert "marker" in [adapter.name for adapter in ADAPTERS]
    assert NORMALIZERS["marker"] is normalize_marker


def test_marker_runs_without_vlm_and_external_api() -> None:
    # VLM を使わない構成の固定（スパイクの判定: VLM 経路は 3GB を超える。外部 API は企画書 §54-1）
    assert "--disable_ocr" in marker_adapter._MARKER_OPTIONS
    assert "--use_llm" not in marker_adapter._MARKER_OPTIONS
    # pdftext のワーカープロセスを使わせない（Peak RSS の和から漏れないように）
    assert "--disable_multiprocessing" in marker_adapter._MARKER_OPTIONS
    # VLM（llama-server / vllm）と ocr_error サーバーは自動起動させない
    assert marker_adapter._NO_AUTOSTART["SURYA_INFERENCE_AUTOSTART"] == "false"
    assert marker_adapter._NO_AUTOSTART["OCR_ERROR_SERVER_AUTOSTART"] == "false"


def _block(block_id: str, block_type: str, html: str, bbox: list[float], **extra) -> dict:
    x0, y0, x1, y1 = bbox
    return {
        "id": block_id,
        "block_type": block_type,
        "html": html,
        "polygon": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
        "bbox": bbox,
        "children": extra.pop("children", None),
        "section_hierarchy": {},
        "images": {},
    }


def _write_raw(raw_dir: Path, data: dict, stem: str = "doc") -> None:
    doc_dir = raw_dir / stem
    doc_dir.mkdir(parents=True)
    (doc_dir / f"{stem}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # 目次・統計の `_meta.json` は正規化の対象外
    (doc_dir / f"{stem}_meta.json").write_text('{"table_of_contents": []}', encoding="utf-8")


# 2 ページ。1 ページ目に見出し・本文（改行あり）・空の Text・PageHeader・TableGroup（キャプション +
# 結合セルのある表）・ListGroup、2 ページ目に本文 1 つ。ページ番号は id の 0 始まりの番号から取る
_TABLE_HTML = (
    "<table><thead><tr><th rowspan='2'>区分</th><th colspan='2'>人口</th></tr>"
    "<tr><th>男</th><th>女</th></tr></thead>"
    "<tbody><tr><td>こども</td><td>681</td><td>648</td></tr></tbody></table>"
)
_RAW = {
    "block_type": "Document",
    "children": [
        _block(
            "/page/0/Page/10",
            "Page",
            "<content-ref src='/page/0/PageHeader/0'></content-ref>…",
            [0, 0, 596, 842],
            children=[
                _block("/page/0/PageHeader/0", "PageHeader", "", [428, 104, 546, 139]),
                _block(
                    "/page/0/SectionHeader/1",
                    "SectionHeader",
                    "<h1>我が国の こどもの数</h1>",
                    [165, 266, 440, 320],
                ),
                _block(
                    "/page/0/Text/2",
                    "Text",
                    '<p block-type="Text">令和8年<br/>5月4日 &amp; <b>推計</b></p>',
                    [424, 184, 524, 199],
                ),
                _block("/page/0/Text/3", "Text", '<p block-type="Text"></p>', [427, 148, 545, 165]),
                _block(
                    "/page/0/TableGroup/4",
                    "TableGroup",
                    "<content-ref src='/page/0/Caption/5'></content-ref>",
                    [70, 400, 530, 600],
                    children=[
                        _block(
                            "/page/0/Caption/5",
                            "Caption",
                            "<p>表1 男女別こどもの数</p>",
                            [70, 400, 530, 420],
                        ),
                        _block("/page/0/Table/6", "Table", _TABLE_HTML, [70, 420, 530, 600]),
                    ],
                ),
                _block(
                    "/page/0/ListGroup/7",
                    "ListGroup",
                    "<ul><li>項目A</li><li>項目B</li></ul>",
                    [70, 610, 530, 650],
                    children=[
                        _block(
                            "/page/0/ListItem/8", "ListItem", "<li>項目A</li>", [70, 610, 530, 630]
                        ),
                        _block(
                            "/page/0/ListItem/9", "ListItem", "<li>項目B</li>", [70, 630, 530, 650]
                        ),
                    ],
                ),
            ],
        ),
        _block(
            "/page/1/Page/20",
            "Page",
            "",
            [0, 0, 596, 842],
            children=[
                _block("/page/1/Text/0", "Text", "<p>2 ページ目の本文</p>", [80, 90, 500, 120])
            ],
        ),
    ],
}


def test_normalize_paragraphs_and_html_table(tmp_path: Path) -> None:
    _write_raw(tmp_path, _RAW)
    doc = normalize_marker(tmp_path)

    assert doc.adapter == "marker"
    assert [page.number for page in doc.pages] == [1, 2]
    page1 = doc.pages[0]
    # 読み順は Page.children の順。グループは子を辿り、空の html（PageHeader・空の Text）は落ちる
    assert [(block.type, block.extra["block_type"]) for block in page1.blocks] == [
        ("paragraph", "SectionHeader"),
        ("paragraph", "Text"),
        ("paragraph", "Caption"),
        ("table", "Table"),
        ("paragraph", "ListItem"),
        ("paragraph", "ListItem"),
    ]
    # タグを除き、<br> は改行、文字参照は戻す。marker の空白はそのまま残す
    assert page1.blocks[0].text == "我が国の こどもの数"
    assert page1.blocks[1].text == "令和8年\n5月4日 & 推計"
    # 表は HTML の rowspan / colspan を解釈し、結合セルは範囲の先頭にだけ文字列を置く
    assert page1.blocks[3].rows == [
        ["区分", "人口", ""],
        ["", "男", "女"],
        ["こども", "681", "648"],
    ]
    # bbox は polygon の外接矩形（PDF ポイント・左上原点）
    assert page1.blocks[1].bbox == (424.0, 184.0, 524.0, 199.0)
    # グループ自身の html（<ul> 全体）は使わないので、リストの項目は 1 回ずつ
    assert page1.lines().count("項目A") == 1
    assert doc.pages[1].lines() == ["2 ページ目の本文"]


def test_normalize_page_number_falls_back_to_order(tmp_path: Path) -> None:
    raw = {
        "block_type": "Document",
        "children": [
            {
                "block_type": "Page",
                "html": "",
                "children": [_block("x", "Text", "<p>本文</p>", [0, 0, 1, 1])],
            }
        ],
    }
    _write_raw(tmp_path, raw)
    doc = normalize_marker(tmp_path)
    assert [page.number for page in doc.pages] == [1]
    assert doc.pages[0].lines() == ["本文"]


def test_normalize_scan_without_text_layer_is_empty(tmp_path: Path) -> None:
    # --disable_ocr ではテキスト層の無いスキャンの html が空になる（OCR しない）。0 として残る
    raw = {
        "block_type": "Document",
        "children": [
            _block(
                "/page/0/Page/1",
                "Page",
                "",
                [0, 0, 596, 842],
                children=[
                    _block("/page/0/Text/0", "Text", '<p block-type="Text"></p>', [0, 0, 1, 1]),
                    _block(
                        "/page/0/Table/1",
                        "Table",
                        "<table><tr><td></td></tr></table>",
                        [0, 0, 1, 1],
                    ),
                ],
            )
        ],
    }
    _write_raw(tmp_path, raw)
    doc = normalize_marker(tmp_path)
    assert [page.number for page in doc.pages] == [1]
    assert doc.char_count() == 0


def test_table_cell_br_becomes_newline() -> None:
    # Marker はセル内の複数行を <br> で区切る。区切りを捨てず `\n` にする
    # （pdfplumber のセル内改行と同じ）
    html = "<table><tr><td>男女計<br>こどもの<br/>数</td><td>1329</td></tr></table>"
    assert table_rows(html) == [["男女計\nこどもの\n数", "1329"]]


def test_find_document_json_skips_meta(tmp_path: Path) -> None:
    _write_raw(tmp_path, {"block_type": "Document", "children": []}, stem="a_meta")
    # stem 自体が `_meta` で終わっても、本体（`a_meta.json`）を選ぶ
    assert find_document_json(tmp_path) == tmp_path / "a_meta" / "a_meta.json"


def test_find_document_json_none_without_output(tmp_path: Path) -> None:
    assert find_document_json(tmp_path) is None
    with pytest.raises(FileNotFoundError):
        normalize_marker(tmp_path)


def test_run_raises_when_child_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 子プロセスが失敗したら（marker の失敗・サーバーの起動失敗）例外にし、計測値を返さない
    def fake_run_command(cmd: list[str], env: dict[str, str] | None = None):
        return run_command([sys.executable, "-c", "import sys; sys.exit(3)"])

    monkeypatch.setattr(marker_adapter, "run_command", fake_run_command)
    out_dir = tmp_path / "raw" / "marker"
    with pytest.raises(subprocess.CalledProcessError):
        MarkerAdapter().run(SAMPLES / "dummy.pdf", out_dir)
    assert not (out_dir / "peak_rss.json").exists()


@pytest.mark.skipif(
    not os.environ.get(_OPT_IN_ENV),
    reason=f"{_OPT_IN_ENV} が無い環境ではスキップ（torch を読むサーバーを起動するため）",
)
@pytest.mark.skipif(
    importlib.util.find_spec("marker") is None, reason="marker が無い環境ではスキップ"
)
def test_marker_writes_raw_output_and_stops_server(tmp_path: Path) -> None:
    pdf = SAMPLES / "stat-go-jp-children-population-2026.pdf"
    if not pdf.is_file():
        pytest.skip("samples/ に対象 PDF が無い")

    out_dir = tmp_path / "raw" / "marker"
    metrics = MarkerAdapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    # Peak RSS は子プロセス・marker・レイアウトサーバーの和（常駐サーバーを計測から漏らさない）
    by_process = metrics["extra"]["peak_rss_kb_by_process"]
    assert set(by_process) == {"wrapper", "marker", "fast_layout_server"}
    assert all(value > 0 for value in by_process.values())
    assert metrics["peak_rss_kb"] == sum(by_process.values())

    raw = find_document_json(out_dir)
    assert raw is not None
    doc = normalize_marker(out_dir)
    assert [page.number for page in doc.pages] == [1, 2, 3, 4, 5, 6]
    # デジタル PDF の表はテキスト層から再構成される（スパイクで 4 つ）
    assert doc.table_count() == 4
