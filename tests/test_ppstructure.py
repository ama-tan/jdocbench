import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path

import pytest
from _samples import SAMPLES

from jdocbench.adapters.ppstructure import PpStructureAdapter
from jdocbench.normalizers.ppstructure import normalize as normalize_ppstructure

_OPT_IN_ENV = "JDOCBENCH_PPSTRUCTURE"


def test_available_is_false_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    # paddleocr が入っていても、環境変数が無ければ動かさない（実行に約 4 分かかるため）
    monkeypatch.delenv(_OPT_IN_ENV, raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert PpStructureAdapter().available() is False


def test_available_is_true_with_opt_in_and_paddleocr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_OPT_IN_ENV, "1")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert PpStructureAdapter().available() is True


def test_available_is_false_without_paddleocr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_OPT_IN_ENV, "1")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert PpStructureAdapter().available() is False


def test_version_is_unknown_without_paddleocr(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    assert PpStructureAdapter().version() == "unknown"


@pytest.mark.skipif(
    not os.environ.get(_OPT_IN_ENV),
    reason=f"{_OPT_IN_ENV} が無い環境ではスキップ（6 ページで約 4 分かかる）",
)
@pytest.mark.skipif(
    importlib.util.find_spec("paddleocr") is None,
    reason="paddleocr が無い環境ではスキップ",
)
def test_ppstructure_writes_raw_output(tmp_path: Path) -> None:
    # 表を 4 つ含みスパイク（#92）で実測済みの文書を使う
    pdf = SAMPLES / "stat-go-jp-children-population-2026.pdf"
    if not pdf.is_file():
        pytest.skip("samples/ に対象 PDF が無い")

    out_dir = tmp_path / "raw" / "ppstructure"
    metrics = PpStructureAdapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0
    # 実験条件が戻り値に入る（cli が run.json の extra に写す）
    assert metrics["extra"]["dpi"] == 150
    assert metrics["extra"]["enable_mkldnn"] is False
    assert metrics["extra"]["text_detection_model"] == "PP-OCRv5_mobile_det"

    # ページごとに raw が揃う（ページ順の分かるファイル名）
    jsons = sorted((out_dir / "json").glob("*.json"))
    assert len(jsons) == 6, "6 ページ分の JSON が揃っていない"
    assert sorted((out_dir / "markdown").glob("*.md"))

    page1 = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert page1["layout_det_res"]["boxes"], "レイアウト検出の結果が空"
    # 表は HTML で返る（正規化は #97）
    tables = [
        table
        for path in jsons
        for table in json.loads(path.read_text(encoding="utf-8")).get("table_res_list") or []
    ]
    assert tables, "表が 1 つも検出されていない"
    assert "<table" in tables[0]["pred_html"]


# CI アーティファクト（`ci-results-pp`）の raw を渡すと正規化を実データで検証する。
# paddle を動かさずに済むので 8GB 機でも回せる。未設定ならスキップ
_RAW_ENV = "JDOCBENCH_PPSTRUCTURE_RAW"


@pytest.mark.skipif(
    not os.environ.get(_RAW_ENV),
    reason=f"{_RAW_ENV}（raw/ppstructure のパス）が無い環境ではスキップ",
)
def test_normalize_real_raw_output() -> None:
    raw_dir = Path(os.environ[_RAW_ENV])
    if not (raw_dir / "json").is_dir():
        pytest.skip(f"{_RAW_ENV} の先に json/ が無い")

    doc = normalize_ppstructure(raw_dir)

    # スパイク（#92）で実測した検出数と同じ（p2 に 2 つ、p4 に 2 つ）
    assert doc.table_count() == 4
    assert [page.number for page in doc.pages] == [1, 2, 3, 4, 5, 6]
    assert [len([b for b in page.blocks if b.type == "table"]) for page in doc.pages] == [
        0,
        2,
        0,
        2,
        0,
        0,
    ]
    assert doc.char_count() > 0
    # 表は行 × 列に展開されている（1 行 1 セルのまま潰れていない）
    tables = [block for page in doc.pages for block in page.blocks if block.type == "table"]
    assert all(len(table.rows) > 1 and len(table.rows[0]) > 1 for table in tables)
