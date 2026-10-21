import importlib.metadata
import importlib.util
from pathlib import Path

import pytest
from _samples import SAMPLES

from jdocbench.adapters import ADAPTERS
from jdocbench.adapters.markitdown import MarkitdownAdapter
from jdocbench.normalizers import NORMALIZERS
from jdocbench.normalizers.markitdown import normalize

_needs_markitdown = pytest.mark.skipif(
    importlib.util.find_spec("markitdown") is None,
    reason="markitdown が無い環境ではスキップ（extra markitdown）",
)


def _raw_dir(tmp_path: Path, text: str) -> Path:
    raw_dir = tmp_path / "raw" / "markitdown"
    raw_dir.mkdir(parents=True)
    (raw_dir / "output.md").write_text(text, encoding="utf-8", newline="")
    return raw_dir


def test_registered() -> None:
    assert "markitdown" in [adapter.name for adapter in ADAPTERS]
    assert NORMALIZERS["markitdown"] is normalize


def test_available_follows_find_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert MarkitdownAdapter().available() is False


def test_version_is_unknown_without_markitdown(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    assert MarkitdownAdapter().version() == "unknown"


def test_normalize_markdown_table_and_paragraphs(tmp_path: Path) -> None:
    """表は区切り行を捨てて行 × 列に、見出しは記号を外して段落に、他は 1 行 = 1 段落。"""
    text = (
        "# 1.3 申請に必要な書類\n"
        "\n"
        "| 書類       | 法人 | 個人 |\n"
        "| ---------- | :--: | ---- |\n"
        "| 財務諸表   | ○    |      |\n"
        "| a \\| b    | -    | ○    |\n"
        "本文の 1 行目\n"
        "  本文の 2 行目  \n"
    )
    doc = normalize(_raw_dir(tmp_path, text))
    assert doc.adapter == "markitdown"
    assert len(doc.pages) == 1  # \f が無いので文書全体を 1 ページ
    blocks = doc.pages[0].blocks
    assert [block.type for block in blocks] == ["paragraph", "table", "paragraph", "paragraph"]
    assert blocks[0].text == "1.3 申請に必要な書類"
    assert blocks[1].rows == [
        ["書類", "法人", "個人"],
        ["財務諸表", "○", ""],
        ["a | b", "-", "○"],
    ]
    assert blocks[2].text == "本文の 1 行目"
    assert blocks[3].text == "本文の 2 行目"
    assert doc.table_count() == 1


def test_normalize_separator_with_empty_column_and_dash_data(tmp_path: Path) -> None:
    """全セルが空の列は区切り行のその位置も空になる。データ行の `-` は区切りと見なさない。"""
    text = "| 書類 |  | 個人 |\n| ---- |  | ---- |\n| -    |  | -    |\n| 財務 |  | ○    |\n"
    doc = normalize(_raw_dir(tmp_path, text))
    assert doc.pages[0].blocks[0].rows == [
        ["書類", "", "個人"],
        ["-", "", "-"],
        ["財務", "", "○"],
    ]


def test_normalize_splits_pages_on_form_feed(tmp_path: Path) -> None:
    """pdfminer 経由の出力は \f でページを分ける。末尾の \f は 1 つ落とし、空ページは残す。"""
    doc = normalize(_raw_dir(tmp_path, "1 ページ目\n\f\f3 ページ目\n\f"))
    assert [page.number for page in doc.pages] == [1, 2, 3]
    assert [page.lines() for page in doc.pages] == [["1 ページ目"], [], ["3 ページ目"]]


def test_normalize_empty_output_has_no_pages(tmp_path: Path) -> None:
    """テキスト層の無い PDF では MarkItDown の出力が空になる。"""
    assert normalize(_raw_dir(tmp_path, "")).pages == []


@_needs_markitdown
def test_markitdown_writes_raw_markdown(tmp_path: Path) -> None:
    """表を含む文書（予算の概要）では Markdown の表が出て、table ブロックになる。"""
    pdf = SAMPLES / "mof-budget-summary-fy2026.pdf"
    if not pdf.is_file():
        pytest.skip("samples/mof-budget-summary-fy2026.pdf が無い")

    out_dir = tmp_path / "raw" / "markitdown"
    metrics = MarkitdownAdapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0
    assert metrics["extra"]["enable_plugins"] is False
    assert metrics["extra"]["llm_client"] is None

    text = (out_dir / "output.md").read_text(encoding="utf-8")
    assert "| --- |" in text
    doc = normalize(out_dir)
    assert doc.table_count() >= 1
    assert "前年度予算額" in doc.text().replace(" ", "")


@_needs_markitdown
def test_markitdown_keeps_pages_for_plain_text_pdf(tmp_path: Path) -> None:
    """フォーム・表を検出しない文書は pdfminer の \f 区切りになり、ページが残る。"""
    pdf = SAMPLES / "geps-manual-internet-excerpt.pdf"
    if not pdf.is_file():
        pytest.skip("samples/geps-manual-internet-excerpt.pdf が無い")

    out_dir = tmp_path / "raw" / "markitdown"
    MarkitdownAdapter().run(pdf, out_dir)
    doc = normalize(out_dir)
    assert len(doc.pages) == 7
    assert "登記事項証明書" in doc.text()
