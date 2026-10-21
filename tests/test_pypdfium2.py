import importlib.metadata
import importlib.util
import json
from pathlib import Path

import pytest

from jdocbench.adapters.pypdfium2 import Pypdfium2Adapter
from jdocbench.normalizers.pypdfium2 import normalize

_SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def test_available_follows_find_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert Pypdfium2Adapter().available() is False


def test_version_is_unknown_without_pypdfium2(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    assert Pypdfium2Adapter().version() == "unknown"


def test_normalize_splits_lines_and_keeps_empty_pages(tmp_path: Path) -> None:
    # PDFium の行区切りは \r\n。空白だけの行は捨て、空ページはページとして残す
    pages = {
        "pdfium": "153.0.7999.0",
        "pages": ["貸借対照表 （単位：百万円）\r\n  \r\n資産 1,257\r\n", ""],
    }
    (tmp_path / "pages.json").write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    doc = normalize(tmp_path)
    assert doc.adapter == "pypdfium2"
    assert [page.number for page in doc.pages] == [1, 2]
    assert [block.text for block in doc.pages[0].blocks] == [
        "貸借対照表 （単位：百万円）",
        "資産 1,257",
    ]
    assert all(block.type == "paragraph" and block.bbox is None for block in doc.pages[0].blocks)
    assert doc.pages[1].blocks == []


@pytest.mark.skipif(
    importlib.util.find_spec("pypdfium2") is None,
    reason="pypdfium2 が無い環境ではスキップ（extra level1）",
)
def test_pypdfium2_writes_raw_output(tmp_path: Path) -> None:
    pdf = _SAMPLES / "stat-go-jp-children-population-2026.pdf"
    if not pdf.exists():
        pytest.skip("samples/ に対象 PDF が無い")

    out_dir = tmp_path / "raw" / "pypdfium2"
    metrics = Pypdfium2Adapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0
    assert metrics["extra"]["pdfium"] != "unknown"

    raw = json.loads((out_dir / "pages.json").read_text(encoding="utf-8"))
    assert len(raw["pages"]) == 6
    text = (out_dir / "text.txt").read_text(encoding="utf-8")
    assert text.count("\f") == 5

    doc = normalize(out_dir)
    assert len(doc.pages) == 6
    assert doc.char_count() > 0
    assert doc.table_count() == 0
