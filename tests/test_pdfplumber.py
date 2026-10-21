import importlib.metadata
import importlib.util
import json
from pathlib import Path

import pytest
from _samples import SAMPLES

from jdocbench.adapters.pdfplumber import PdfplumberAdapter
from jdocbench.normalizers._tables import drop_table_content_lines
from jdocbench.normalizers.pdfplumber import normalize


def test_available_follows_find_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert PdfplumberAdapter().available() is False


def test_version_is_unknown_without_pdfplumber(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    assert PdfplumberAdapter().version() == "unknown"


@pytest.mark.skipif(
    importlib.util.find_spec("pdfplumber") is None,
    reason="pdfplumber が無い環境ではスキップ",
)
def test_pdfplumber_writes_raw_output(tmp_path: Path) -> None:
    pdfs = sorted(SAMPLES.glob("*.pdf"))
    if not pdfs:
        pytest.skip("samples/ に PDF が無い")

    out_dir = tmp_path / "raw" / "pdfplumber"
    metrics = PdfplumberAdapter().run(pdfs[0], out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0

    text = (out_dir / "text.txt").read_text(encoding="utf-8")
    assert text.strip(), "テキストが抽出されていない"

    # 表は全ページの合計で数える（表を含まないページもあるため）
    tables = json.loads((out_dir / "tables.json").read_text(encoding="utf-8"))
    assert sum(len(page) for page in tables) >= 1, "表が 1 つも検出されていない"

    words = json.loads((out_dir / "words.json").read_text(encoding="utf-8"))
    assert len(words[0]) >= 1, "1 ページ目の単語が取れていない"

    # 段落と表の位置（表の中身の行を位置で判定するため。#186）。tables.json と同じ並び
    bboxes = json.loads((out_dir / "table_bboxes.json").read_text(encoding="utf-8"))
    assert [len(page) for page in bboxes] == [len(page) for page in tables]
    lines = json.loads((out_dir / "lines.json").read_text(encoding="utf-8"))
    assert len(lines) == len(tables) and len(lines[0]) >= 1
    # 各行の文字座標（行の一部だけが表の中にある行で、表の中の語だけを除くため。#199）
    assert all(isinstance(line["chars"], list) for page in lines for line in page)

    # raw をそのまま正規化できる（表は table ブロックとして IR に載る）
    doc = normalize(out_dir)
    assert doc.table_count() >= 1, "表が IR に載っていない"
    assert doc.char_count() > 0, "テキストが IR に載っていない"

    # 実 PDF でも text.txt の全行に座標が付き、表にも bbox が付く（位置による判定が効いている）
    assert all(block.bbox is not None for page in doc.pages for block in page.blocks)
    # 表の中身の行は段落に残っていない（もう一度かけても何も落ちない = 二重計上なし）
    for page in doc.pages:
        assert len(drop_table_content_lines(page.blocks)) == len(page.blocks), (
            f"p{page.number} の段落に表のセルだけで構成できる行が残っている"
        )


@pytest.mark.skipif(
    importlib.util.find_spec("pdfplumber") is None,
    reason="pdfplumber が無い環境ではスキップ",
)
def test_pdfplumber_strips_table_part_of_a_joined_line_in_a_real_pdf(tmp_path: Path) -> None:
    """stat の p2 で本文と表の行が 1 行に連結された段落から、表の値だけを除く（#199）。"""
    pdf = SAMPLES / "stat-go-jp-children-population-2026.pdf"
    if not pdf.is_file():
        pytest.skip("samples/ に stat の PDF が無い")

    out_dir = tmp_path / "raw" / "pdfplumber"
    PdfplumberAdapter().run(pdf, out_dir)
    page = normalize(out_dir).pages[1]

    paragraphs = [block.text for block in page.blocks if block.type == "paragraph"]
    assert "100人に対する男子の数（人口性" in paragraphs
    assert not [text for text in paragraphs if text and "12286" in text]
    assert sum("12286" in line for line in page.lines()) == 1
