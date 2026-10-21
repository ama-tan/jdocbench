import importlib.metadata
import importlib.util
import json
from pathlib import Path

import pytest
from _samples import SAMPLES

from jdocbench.adapters.docling import DoclingAdapter, _pipeline_options
from jdocbench.normalizers.docling import normalize


def test_available_follows_find_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert DoclingAdapter().available() is False


def test_version_is_unknown_without_docling(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    assert DoclingAdapter().version() == "unknown"


@pytest.mark.skipif(
    importlib.util.find_spec("docling") is None,
    reason="docling が無い環境ではスキップ",
)
def test_pipeline_options_batch_one_page() -> None:
    # 省メモリ設定（#86）が docling に渡る。page_batch_size はスレッド版パイプラインで効かない
    options = _pipeline_options()
    assert options.do_ocr is False
    assert options.do_table_structure is True
    assert options.layout_batch_size == 1
    assert options.table_batch_size == 1


@pytest.mark.skipif(
    importlib.util.find_spec("docling") is None,
    reason="docling が無い環境ではスキップ",
)
def test_docling_writes_raw_output(tmp_path: Path) -> None:
    # 表を 4 つ含み事前調査で実測済みの文書を使う（他のサンプルは実行時間が読めない）
    pdf = SAMPLES / "stat-go-jp-children-population-2026.pdf"
    if not pdf.is_file():
        pytest.skip("samples/ に対象 PDF が無い")

    out_dir = tmp_path / "raw" / "docling"
    metrics = DoclingAdapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0
    # 実験条件が戻り値に入る（cli が run.json の extra に写す）
    assert metrics["extra"]["ocr"] is False
    assert metrics["extra"]["layout_batch_size"] == 1
    assert metrics["extra"]["table_batch_size"] == 1
    assert metrics["extra"]["models"]

    document = json.loads((out_dir / "document.json").read_text(encoding="utf-8"))
    assert len(document["tables"]) >= 1, "表が 1 つも検出されていない"

    markdown = (out_dir / "document.md").read_text(encoding="utf-8")
    assert markdown.strip(), "Markdown が空"

    # 実物の raw が正規化を通り、表と本文が IR に載る
    normalized = normalize(out_dir)
    assert normalized.adapter == "docling"
    assert normalized.table_count() >= 1, "表が IR に載っていない"
    assert normalized.char_count() > 0, "本文が IR に載っていない"
