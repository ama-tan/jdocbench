"""アダプタ名 → 正規化関数の対応表。正規化関数が無いアダプタは正規化しない。"""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from jdocbench.ir import Document
from jdocbench.normalizers.camelot import normalize as normalize_camelot
from jdocbench.normalizers.docling import normalize as normalize_docling
from jdocbench.normalizers.marker import normalize as normalize_marker
from jdocbench.normalizers.markitdown import normalize as normalize_markitdown
from jdocbench.normalizers.mineru import normalize as normalize_mineru
from jdocbench.normalizers.mutool import normalize as normalize_mutool
from jdocbench.normalizers.pdf2json import normalize as normalize_pdf2json
from jdocbench.normalizers.pdfplumber import normalize as normalize_pdfplumber
from jdocbench.normalizers.pdftotext import normalize as normalize_pdftotext
from jdocbench.normalizers.ppstructure import normalize as normalize_ppstructure
from jdocbench.normalizers.pymupdf import normalize as normalize_pymupdf
from jdocbench.normalizers.pypdfium2 import normalize as normalize_pypdfium2
from jdocbench.normalizers.tabula import normalize as normalize_tabula
from jdocbench.normalizers.tesseract import normalize as normalize_tesseract
from jdocbench.normalizers.tika import normalize as normalize_tika
from jdocbench.normalizers.unstructured import normalize as normalize_unstructured
from jdocbench.normalizers.yomitoku import normalize as normalize_yomitoku

Normalizer = Callable[[Path], Document]
"""raw ディレクトリ（`results/<stem>/raw/<adapter>/`）を読んで Document を返す関数。"""


def _normalize_docling_ocr(raw_dir: Path) -> Document:
    """docling_ocr の raw は docling と同形式。正規化を流用し、アダプタ名だけ差し替える。"""
    return replace(normalize_docling(raw_dir), adapter="docling_ocr")


def _normalize_camelot_stream(raw_dir: Path) -> Document:
    """camelot_stream の raw は camelot と同形式。正規化を流用し、アダプタ名だけ差し替える。"""
    return replace(normalize_camelot(raw_dir), adapter="camelot_stream")


def _normalize_tabula_stream(raw_dir: Path) -> Document:
    """tabula_stream の raw は tabula と同形式。正規化を流用し、アダプタ名だけ差し替える。"""
    return replace(normalize_tabula(raw_dir), adapter="tabula_stream")


def _normalize_tesseract_psm6(raw_dir: Path) -> Document:
    """tesseract_psm6 の raw は tesseract と同形式。正規化を流用し、アダプタ名だけ差し替える。"""
    return replace(normalize_tesseract(raw_dir), adapter="tesseract_psm6")


NORMALIZERS: dict[str, Normalizer] = {
    "pdftotext": normalize_pdftotext,
    "pdf2json": normalize_pdf2json,
    "pypdfium2": normalize_pypdfium2,
    "mutool": normalize_mutool,
    "tika": normalize_tika,
    "pdfplumber": normalize_pdfplumber,
    "pymupdf": normalize_pymupdf,
    "camelot": normalize_camelot,
    "camelot_stream": _normalize_camelot_stream,
    "tabula": normalize_tabula,
    "tabula_stream": _normalize_tabula_stream,
    "markitdown": normalize_markitdown,
    "unstructured": normalize_unstructured,
    "docling": normalize_docling,
    "docling_ocr": _normalize_docling_ocr,
    "ppstructure": normalize_ppstructure,
    "tesseract": normalize_tesseract,
    "tesseract_psm6": _normalize_tesseract_psm6,
    "marker": normalize_marker,
    "mineru": normalize_mineru,
    "yomitoku": normalize_yomitoku,
}

__all__ = ["NORMALIZERS", "Normalizer"]
