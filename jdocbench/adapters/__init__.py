"""アダプタ一覧。"""

from jdocbench.adapters.base import Adapter
from jdocbench.adapters.camelot import CamelotAdapter, CamelotStreamAdapter
from jdocbench.adapters.docling import DoclingAdapter
from jdocbench.adapters.docling_ocr import DoclingOcrAdapter
from jdocbench.adapters.marker import MarkerAdapter
from jdocbench.adapters.markitdown import MarkitdownAdapter
from jdocbench.adapters.mineru import MineruAdapter
from jdocbench.adapters.mutool import MutoolAdapter
from jdocbench.adapters.pdf2json import Pdf2jsonAdapter
from jdocbench.adapters.pdfplumber import PdfplumberAdapter
from jdocbench.adapters.pdftotext import PdftotextAdapter
from jdocbench.adapters.ppstructure import PpStructureAdapter
from jdocbench.adapters.pymupdf import PymupdfAdapter
from jdocbench.adapters.pypdfium2 import Pypdfium2Adapter
from jdocbench.adapters.tabula import TabulaAdapter, TabulaStreamAdapter
from jdocbench.adapters.tesseract import TesseractAdapter, TesseractPsm6Adapter
from jdocbench.adapters.tika import TikaAdapter
from jdocbench.adapters.unstructured import UnstructuredAdapter
from jdocbench.adapters.yomitoku import YomitokuAdapter

ADAPTERS: list[Adapter] = [
    PdftotextAdapter(),
    Pdf2jsonAdapter(),
    Pypdfium2Adapter(),
    MutoolAdapter(),
    TikaAdapter(),
    PdfplumberAdapter(),
    PymupdfAdapter(),
    CamelotAdapter(),
    CamelotStreamAdapter(),
    TabulaAdapter(),
    TabulaStreamAdapter(),
    MarkitdownAdapter(),
    UnstructuredAdapter(),
    DoclingAdapter(),
    DoclingOcrAdapter(),
    PpStructureAdapter(),
    TesseractAdapter(),
    TesseractPsm6Adapter(),
    MarkerAdapter(),
    MineruAdapter(),
    YomitokuAdapter(),
]

__all__ = [
    "ADAPTERS",
    "Adapter",
    "CamelotAdapter",
    "CamelotStreamAdapter",
    "DoclingAdapter",
    "DoclingOcrAdapter",
    "MutoolAdapter",
    "MarkerAdapter",
    "MarkitdownAdapter",
    "MineruAdapter",
    "Pdf2jsonAdapter",
    "PdfplumberAdapter",
    "PdftotextAdapter",
    "PpStructureAdapter",
    "PymupdfAdapter",
    "Pypdfium2Adapter",
    "TabulaAdapter",
    "TabulaStreamAdapter",
    "TikaAdapter",
    "TesseractAdapter",
    "TesseractPsm6Adapter",
    "UnstructuredAdapter",
    "YomitokuAdapter",
]
