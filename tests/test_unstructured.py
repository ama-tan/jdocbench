import importlib.metadata
import importlib.util
import json
from pathlib import Path

import pytest

from jdocbench.adapters import ADAPTERS
from jdocbench.adapters.unstructured import UnstructuredAdapter
from jdocbench.normalizers import NORMALIZERS
from jdocbench.normalizers.unstructured import normalize

_SAMPLES = Path(__file__).resolve().parents[1] / "samples"

_needs_unstructured = pytest.mark.skipif(
    importlib.util.find_spec("unstructured") is None,
    reason="unstructured が無い環境ではスキップ（extra unstructured）",
)


def _raw_dir(tmp_path: Path, elements: list[dict]) -> Path:
    raw_dir = tmp_path / "raw" / "unstructured"
    raw_dir.mkdir(parents=True)
    (raw_dir / "elements.json").write_text(json.dumps(elements, ensure_ascii=False), "utf-8")
    return raw_dir


def _points(x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"points": [[x0, y0], [x0, y1], [x1, y1], [x1, y0]], "system": "PixelSpace"}


def test_registered() -> None:
    assert "unstructured" in [adapter.name for adapter in ADAPTERS]
    assert NORMALIZERS["unstructured"] is normalize


def test_available_follows_find_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert UnstructuredAdapter().available() is False


def test_version_is_unknown_without_unstructured(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    assert UnstructuredAdapter().version() == "unknown"


def test_normalize_elements_with_table(tmp_path: Path) -> None:
    """Table は text_as_html を行 × 列に（結合セルは先頭だけ）、他は 1 要素 = 1 段落。"""
    elements = [
        {
            "type": "Title",
            "text": "1.3 申請に必要な書類",
            "metadata": {"page_number": 1, "coordinates": _points(56.7, 75.7, 201.1, 93.2)},
        },
        {
            "type": "Table",
            "text": "書類 法人 個人 財務諸表 ○",
            "metadata": {
                "page_number": 1,
                "text_as_html": (
                    "<table><tr><td>書類</td><td colspan='2'>区分</td></tr>"
                    "<tr><td>財務諸表</td><td>○</td><td></td></tr></table>"
                ),
            },
        },
        # HTML の無い Table は text を段落にする
        {"type": "Table", "text": "表 の 文字列", "metadata": {"page_number": 1}},
        # 文字の無い要素は捨てる
        {"type": "PageBreak", "text": "", "metadata": {"page_number": 1}},
        # ページ 2 は要素が無くても空ページとして残す
        {"type": "Footer", "text": "12", "metadata": {"page_number": 3}},
        # ページ番号の無い要素は直前のページに置く
        {"type": "NarrativeText", "text": "続き\n2 行目", "metadata": {}},
    ]
    doc = normalize(_raw_dir(tmp_path, elements))
    assert doc.adapter == "unstructured"
    assert [page.number for page in doc.pages] == [1, 2, 3]

    first = doc.pages[0].blocks
    assert [block.type for block in first] == ["paragraph", "table", "paragraph"]
    assert first[0].text == "1.3 申請に必要な書類"
    assert first[0].bbox == (56.7, 75.7, 201.1, 93.2)
    assert first[0].extra == {"type": "Title"}
    assert first[1].rows == [["書類", "区分", ""], ["財務諸表", "○", ""]]
    assert first[1].extra == {"type": "Table"}
    assert first[2].text == "表 の 文字列"

    assert doc.pages[1].blocks == []
    assert doc.pages[2].lines() == ["12", "続き", "2 行目"]
    assert doc.table_count() == 1


def test_normalize_keeps_trailing_pages_from_meta(tmp_path: Path) -> None:
    """末尾の白紙ページは要素が出ないので、meta.json の page_count でページ数を揃える。"""
    raw_dir = _raw_dir(
        tmp_path, [{"type": "Title", "text": "表紙", "metadata": {"page_number": 1}}]
    )
    (raw_dir / "meta.json").write_text(json.dumps({"page_count": 3}), encoding="utf-8")
    doc = normalize(raw_dir)
    assert [page.number for page in doc.pages] == [1, 2, 3]
    assert [page.lines() for page in doc.pages] == [["表紙"], [], []]


def test_normalize_no_elements_has_no_pages(tmp_path: Path) -> None:
    """テキスト層の無い PDF では fast は要素を返さない。"""
    assert normalize(_raw_dir(tmp_path, [])).pages == []


@_needs_unstructured
def test_unstructured_writes_raw_elements(tmp_path: Path) -> None:
    pdf = _SAMPLES / "geps-manual-internet-excerpt.pdf"
    if not pdf.is_file():
        pytest.skip("samples/geps-manual-internet-excerpt.pdf が無い")

    out_dir = tmp_path / "raw" / "unstructured"
    metrics = UnstructuredAdapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0
    assert metrics["extra"] == {"strategy": "fast", "languages": ["jpn"]}

    elements = json.loads((out_dir / "elements.json").read_text(encoding="utf-8"))
    assert elements, "要素が 1 つも無い"
    assert {"type", "text", "metadata"} <= set(elements[0])
    assert max(element["metadata"]["page_number"] for element in elements) == 7
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"page_count": 7, "strategy": "fast", "languages": ["jpn"]}

    doc = normalize(out_dir)
    assert len(doc.pages) == 7
    assert "登記事項証明書" in doc.text().replace(" ", "")
