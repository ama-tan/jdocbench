import importlib.metadata
import importlib.util
import json
from pathlib import Path

import pytest
from _samples import SAMPLES

from jdocbench.adapters.pymupdf import PymupdfAdapter
from jdocbench.normalizers._tables import drop_table_content_lines
from jdocbench.normalizers.pymupdf import normalize


def test_available_follows_find_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert PymupdfAdapter().available() is False


def test_version_is_unknown_without_pymupdf(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    assert PymupdfAdapter().version() == "unknown"


@pytest.mark.skipif(
    importlib.util.find_spec("pymupdf") is None,
    reason="pymupdf が無い環境ではスキップ（オプトイン extra）",
)
def test_pymupdf_writes_raw_output(tmp_path: Path) -> None:
    pdfs = sorted(SAMPLES.glob("*.pdf"))
    if not pdfs:
        pytest.skip("samples/ に PDF が無い")

    out_dir = tmp_path / "raw" / "pymupdf"
    metrics = PymupdfAdapter().run(pdfs[0], out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0

    text = (out_dir / "text.txt").read_text(encoding="utf-8")
    assert text.strip(), "テキストが抽出されていない"

    # 表は全ページの合計で数える（表を含まないページもあるため）
    tables = json.loads((out_dir / "tables.json").read_text(encoding="utf-8"))
    assert sum(len(page) for page in tables) >= 1, "表が 1 つも検出されていない"

    blocks = json.loads((out_dir / "blocks.json").read_text(encoding="utf-8"))
    assert len(blocks[0]) >= 1, "1 ページ目のブロックが取れていない"
    # 各行の座標（表の中身の行を位置で判定するため。#186）。blocks.json と同じページ数
    lines = json.loads((out_dir / "lines.json").read_text(encoding="utf-8"))
    assert len(lines) == len(blocks) and len(lines[0]) >= 1
    # 各行の 7 要素目は文字座標（行の一部だけが表の中にある行で、表の中の語だけを除くため。#199）
    assert all(len(line) == 7 and isinstance(line[6], list) for page in lines for line in page)

    # raw が IR に載ること（report の pymupdf 行に Chars / Tables が入る条件）
    doc = normalize(out_dir)
    assert doc.char_count() > 0, "テキストが IR に載っていない"
    # 段落を blocks.json から作るようにしても、表は従来どおり tables.json の数だけ載る
    assert doc.table_count() == sum(len(page) for page in tables), "表の数が tables.json と違う"

    # 実 PDF でも lines.json の行がブロックと一致し、全段落に行の bbox が付く
    assert all(block.bbox is not None for page in doc.pages for block in page.blocks if block.text)
    # 表の中身の行は段落に残っていない（もう一度かけても何も落ちない = 二重計上なし）
    for page in doc.pages:
        assert len(drop_table_content_lines(page.blocks)) == len(page.blocks), (
            f"p{page.number} の段落に表のセルだけで構成できる行が残っている"
        )


@pytest.mark.skipif(
    importlib.util.find_spec("pymupdf") is None,
    reason="pymupdf が無い環境ではスキップ（オプトイン extra）",
)
def test_pymupdf_strips_table_part_of_a_joined_line_in_a_real_pdf(tmp_path: Path) -> None:
    """mof-financial の p1 で左右の表の行が連結された段落から、検出した表の側だけを除く（#199）。

    pymupdf は右の表だけを表として検出し、左の表の行と右の表の行を 1 行に連結する。右の表の
    語だけを除き、左の表の語（table ブロックには無い）は段落に残す。
    """
    pdf = SAMPLES / "mof-financial-statements-fy2024-excerpt.pdf"
    if not pdf.is_file():
        pytest.skip("samples/ に mof-financial の PDF が無い")

    out_dir = tmp_path / "raw" / "pymupdf"
    PymupdfAdapter().run(pdf, out_dir)
    page = normalize(out_dir).pages[0]

    paragraphs = [block.text for block in page.blocks if block.type == "paragraph"]
    assert "人件費 5.2 5.5 0.3" in paragraphs
    assert not [text for text in paragraphs if text and "前年度末資産・負債差額 ▲" in text]
