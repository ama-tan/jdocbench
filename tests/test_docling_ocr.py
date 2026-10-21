import importlib.metadata
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

from jdocbench.adapters.docling_ocr import DoclingOcrAdapter, _pipeline_options
from jdocbench.normalizers import NORMALIZERS

_SAMPLES = Path(__file__).resolve().parents[1] / "samples"
_ALL_SAMPLE_PDFS = sorted(_SAMPLES.glob("*.pdf"))


def test_available_follows_find_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert DoclingOcrAdapter().available() is False


def test_version_is_unknown_without_docling(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    assert DoclingOcrAdapter().version() == "unknown"


@pytest.mark.skipif(importlib.util.find_spec("docling") is None, reason="docling が無い環境")
def test_pipeline_options_batch_one_page() -> None:
    # 省メモリ設定が docling に渡る。page_batch_size はスレッド版パイプラインで効かない（#157）
    options = _pipeline_options()
    assert options.do_ocr is True
    assert options.ocr_batch_size == 1
    assert options.layout_batch_size == 1
    assert options.table_batch_size == 1


@pytest.mark.skipif(
    importlib.util.find_spec("pypdfium2") is None,
    reason="pypdfium2（extra level1）が無い環境ではスキップ",
)
def test_text_layer_pdf_is_skipped(tmp_path: Path) -> None:
    """テキスト層のある PDF では OCR を走らせず skipped を返す（docling が無くても通る）。"""
    pdf = _SAMPLES / "stat-go-jp-children-population-2026.pdf"
    if not pdf.is_file():
        pytest.skip("samples/ に対象 PDF が無い")

    out_dir = tmp_path / "raw" / "docling_ocr"
    metrics = DoclingOcrAdapter().run(pdf, out_dir)
    assert "skipped" in metrics
    assert "text layer" in str(metrics["skipped"])
    # 子プロセスを起動していない（raw を作らない）
    assert not out_dir.exists()


@pytest.mark.skipif(
    importlib.util.find_spec("pypdfium2") is None,
    reason="pypdfium2（extra level1）が無い環境ではスキップ",
)
def test_scan_pdf_is_not_skipped_by_text_layer_check() -> None:
    """スキャン相当サンプルはテキスト層なしと判定される（OCR 自体は走らせない）。"""
    from jdocbench.adapters.docling_ocr import _TEXT_LAYER_MIN_CHARS, _text_layer_chars

    pdf = _SAMPLES / "stat-go-jp-children-population-2026-scan.pdf"
    if not pdf.is_file():
        pytest.skip("samples/ に対象 PDF が無い")

    assert _text_layer_chars(pdf) < _TEXT_LAYER_MIN_CHARS


@pytest.mark.skipif(
    importlib.util.find_spec("pypdfium2") is None,
    reason="pypdfium2（extra level1）が無い環境ではスキップ",
)
@pytest.mark.parametrize("pdf", _ALL_SAMPLE_PDFS, ids=lambda pdf: pdf.name)
def test_text_layer_skip_decision_unchanged_for_all_samples(pdf: Path) -> None:
    """#210: get_text_range → get_text_bounded に変えても、全サンプルでスキップ判定が変わらない。"""
    import pypdfium2 as pdfium

    from jdocbench.adapters.docling_ocr import _TEXT_LAYER_MIN_CHARS, _text_layer_chars

    document = pdfium.PdfDocument(pdf)
    try:
        range_chars = sum(len(page.get_textpage().get_text_range()) for page in document)
    finally:
        document.close()

    bounded_chars = _text_layer_chars(pdf)
    assert (range_chars >= _TEXT_LAYER_MIN_CHARS) == (bounded_chars >= _TEXT_LAYER_MIN_CHARS)


def test_text_layer_chars_counts_supplementary_plane_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_text_bounded は get_text_range が落とす補助面の文字（CJK 統合漢字拡張 B 等）も数える。

    サンプル PDF には該当する文字が無いため（実測では差は -4〜0 chars、#210）、
    pypdfium2 を差し替えてこの差分だけを再現する。
    """
    from jdocbench.adapters.docling_ocr import _text_layer_chars

    supplementary_char = "\U00020b9f"  # CJK 統合漢字拡張 B の文字。UCS-2 までの API では落ちる

    class _FakeTextPage:
        def get_text_range(self) -> str:
            return "通常文字"

        def get_text_bounded(self) -> str:
            return "通常文字" + supplementary_char

    class _FakePage:
        def get_textpage(self) -> _FakeTextPage:
            return _FakeTextPage()

    class _FakeDocument:
        def __init__(self, pdf: Path) -> None:
            del pdf

        def __iter__(self):
            return iter([_FakePage()])

        def close(self) -> None:
            pass

    fake_pypdfium2 = types.ModuleType("pypdfium2")
    fake_pypdfium2.PdfDocument = _FakeDocument  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pypdfium2", fake_pypdfium2)

    assert _text_layer_chars(Path("dummy.pdf")) == len("通常文字" + supplementary_char)


@pytest.mark.skipif(
    importlib.util.find_spec("docling") is None or not os.environ.get("JDOCBENCH_OCR_TEST"),
    reason="OCR は重いので JDOCBENCH_OCR_TEST を設定した環境でのみ実行する",
)
def test_docling_ocr_writes_raw_output(tmp_path: Path) -> None:
    pdf = _SAMPLES / "stat-go-jp-children-population-2026-scan.pdf"
    if not pdf.is_file():
        pytest.skip("samples/ に対象 PDF が無い")

    out_dir = tmp_path / "raw" / "docling_ocr"
    metrics = DoclingOcrAdapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0
    # 実験条件が戻り値に入る（cli が run.json の extra に写す）
    extra = metrics["extra"]
    assert extra["ocr"] is True
    assert extra["ocr_engine"] == "rapidocr"
    assert extra["ocr_backend"] in ("onnxruntime", "torch")
    assert extra["ocr_lang"] == "japan"
    assert extra["ocr_batch_size"] == 1
    assert extra["layout_batch_size"] == 1
    assert extra["table_batch_size"] == 1
    # スレッド版パイプラインで効かない設定は記録しない（#157）
    assert "page_batch_size" not in extra
    assert extra["models"]

    document = json.loads((out_dir / "document.json").read_text(encoding="utf-8"))
    assert len(document["tables"]) >= 1, "表が 1 つも検出されていない"

    # 正規化は docling のものを流用するが、アダプタ名は docling_ocr になる
    normalized = NORMALIZERS["docling_ocr"](out_dir)
    assert normalized.adapter == "docling_ocr"
    assert normalized.table_count() >= 1, "表が IR に載っていない"
    assert normalized.char_count() > 0, "本文が IR に載っていない"
