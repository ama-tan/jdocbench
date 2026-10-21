import argparse
import importlib.util
import json
from pathlib import Path

import pytest
from _samples import SAMPLES

from jdocbench.cli import _run, main

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pymupdf") is None,
    reason="pymupdf が無い環境ではスキップ（オプトイン extra）",
)


_LINE = "x" * 40  # Helvetica 11pt で約 220pt（A4 幅 595pt の 25% を超える長い行）


def _make_pdf(path: Path, build) -> Path:
    """1 ページの PDF を作り、`build(page)` で中身を描く。"""
    import pymupdf

    doc = pymupdf.open()
    build(doc.new_page())  # 既定は A4（595 x 842pt）
    doc.save(path)
    doc.close()
    return path


def _two_columns(page) -> None:
    for i in range(10):
        y = 80 + i * 20
        page.insert_text((40, y), _LINE)
        page.insert_text((320, y), _LINE)


def _one_column(page) -> None:
    for i in range(10):
        page.insert_text((40, 80 + i * 20), _LINE * 2)


def _grid(page) -> None:
    for i in range(11):  # 横罫 11 本
        page.draw_line((50, 100 + i * 20), (500, 100 + i * 20))
    for x in (50, 200, 350, 500):  # 縦罫 4 本
        page.draw_line((x, 100), (x, 300))


def _frame_only(page) -> None:
    # 外枠と列の縦罫だけで行の罫線が無い表（mof-budget 型）
    page.draw_rect((50, 100, 500, 300))
    for x in (200, 350):
        page.draw_line((x, 100), (x, 300))


def _vertical(page) -> None:
    # rotate=270 で書くと行の dir は (0, 1)（本物の縦書きと同じ上→下の向き）になる
    for i in range(8):
        page.insert_text(
            (540 - i * 24, 80),
            "縦書きの本文の行です漢字かな交じり文",
            fontname="japan",
            fontsize=11,
            rotate=270,
        )
    page.insert_text((40, 820), "1")  # 横書きのページ番号


def _vertical_cell(page) -> None:
    # 横書きの本文に縦書きのセルが 1 つだけある（mof-financial p3 型）
    _one_column(page)
    page.insert_text(
        (30, 100), "資産・負債差額の構成要素", fontname="japan", fontsize=9, rotate=270
    )


def test_two_column_page(tmp_path: Path) -> None:
    from jdocbench.profile import profile_pdf

    profile = profile_pdf(_make_pdf(tmp_path / "two.pdf", _two_columns))
    assert profile["columns"] == 2
    assert profile["multi_column_pages"] == [1]
    assert profile["text_layer"] == 1.0


def test_one_column_page(tmp_path: Path) -> None:
    from jdocbench.profile import profile_pdf

    profile = profile_pdf(_make_pdf(tmp_path / "one.pdf", _one_column))
    assert profile["columns"] == 1
    assert profile["multi_column_pages"] == []
    assert profile["cjk_ratio"] == 0.0


def test_grid_lines_are_ruled_page(tmp_path: Path) -> None:
    from jdocbench.profile import profile_pdf

    profile = profile_pdf(_make_pdf(tmp_path / "grid.pdf", _grid))
    assert profile["ruled_pages"] == [1]
    assert profile["page_details"][0]["h_rules"] == 11
    assert profile["page_details"][0]["v_rules"] == 4


def test_frame_only_table_is_not_ruled_page(tmp_path: Path) -> None:
    from jdocbench.profile import profile_pdf

    profile = profile_pdf(_make_pdf(tmp_path / "frame.pdf", _frame_only))
    assert profile["ruled_pages"] == []
    assert profile["page_details"][0]["h_rules"] == 2


def test_empty_page_has_no_text_layer(tmp_path: Path) -> None:
    from jdocbench.profile import profile_pdf

    profile = profile_pdf(_make_pdf(tmp_path / "empty.pdf", lambda page: None))
    assert profile["pages"] == 1
    assert profile["text_layer"] == 0.0
    assert profile["chars_per_page"] == 0.0
    assert profile["columns"] == 0
    assert profile["embedded_fonts"] is None
    assert profile["vertical_text_pages"] == []


def test_vertical_text_page(tmp_path: Path) -> None:
    from jdocbench.profile import profile_pdf

    profile = profile_pdf(_make_pdf(tmp_path / "vertical.pdf", _vertical))
    assert profile["vertical_text_pages"] == [1]
    detail = profile["page_details"][0]
    assert detail["vertical_chars"] == detail["chars"] - 1  # 横書きのページ番号だけ除く


def test_single_vertical_cell_is_not_vertical_text_page(tmp_path: Path) -> None:
    from jdocbench.profile import profile_pdf

    profile = profile_pdf(_make_pdf(tmp_path / "cell.pdf", _vertical_cell))
    # 縦書きの文字は数えるが、ページの文字の過半ではないので縦書きのページにはしない
    assert profile["page_details"][0]["vertical_chars"] == 12  # 「資産・負債差額の構成要素」
    assert profile["vertical_text_pages"] == []


def test_base14_font_is_not_embedded(tmp_path: Path) -> None:
    from jdocbench.profile import profile_pdf

    # Helvetica（Base-14、Type1）は埋め込まれない。Type0 ではないので CJK 非埋め込みには数えない
    profile = profile_pdf(_make_pdf(tmp_path / "helv.pdf", _one_column))
    assert profile["embedded_fonts"] == 0.0
    assert profile["nonembedded_cjk_font_pages"] == []


def test_samples_match_known_characteristics() -> None:
    """手で確認した特性（samples/README.md）と一致すること。"""
    from jdocbench.profile import profile_pdf

    env = SAMPLES / "env-financial-statements-fy2024-excerpt.pdf"
    jacn = SAMPLES / "jacn-29-0039-katakana-transliteration-2025.pdf"
    scan = SAMPLES / "stat-go-jp-children-population-2026-scan.pdf"
    budget = SAMPLES / "mof-budget-summary-fy2026.pdf"
    jsda = SAMPLES / "jsda-9-3-e15-oais-information-package-2025-excerpt.pdf"
    if not all(p.is_file() for p in (env, jacn, scan, budget, jsda)):
        pytest.skip("samples/ に PDF が無い")

    env_profile = profile_pdf(env)
    assert env_profile["nonembedded_cjk_font_pages"] == [1, 2, 3]
    assert env_profile["ruled_pages"] == [4, 5]
    assert profile_pdf(jacn)["columns"] == 2
    assert profile_pdf(scan)["text_layer"] == 0.0
    assert profile_pdf(budget)["ruled_pages"] == []
    # ページ単位で 2 段と推定されるのは p2 だけ（p1 は全幅の表題・抄録の行が多く、p3・p4 は
    # 列の外に出た柱で中央線がずれるため 1 段と推定される）が、文書としては 2 段組み
    # （samples/README.md）
    assert profile_pdf(jsda)["columns"] == 2


def test_samples_have_no_vertical_text_pages() -> None:
    """既存 9 文書には縦書きが主なページが無い（#234）。

    samples/*.pdf を glob せずに列挙する（縦書きの文書を後から足しても壊れないように）。
    """
    from jdocbench.profile import profile_pdf

    names = (
        "env-financial-statements-fy2024-excerpt.pdf",
        "estat-census-1920-scan-excerpt.pdf",
        "geps-manual-internet-excerpt.pdf",
        "jacn-29-0039-katakana-transliteration-2025.pdf",
        "jsda-9-3-e15-oais-information-package-2025-excerpt.pdf",
        "mof-budget-summary-fy2026.pdf",
        "mof-financial-statements-fy2024-excerpt.pdf",
        "stat-go-jp-children-population-2026-scan.pdf",
        "stat-go-jp-children-population-2026.pdf",
    )
    pdfs = [SAMPLES / name for name in names]
    if not all(p.is_file() for p in pdfs):
        pytest.skip("samples/ に PDF が無い")

    for pdf in pdfs:
        assert profile_pdf(pdf)["vertical_text_pages"] == [], pdf.name
    # mof-financial p3 の縦書きセル（12 文字）は数えるが、閾値には届かない
    mof = profile_pdf(SAMPLES / "mof-financial-statements-fy2024-excerpt.pdf")
    assert mof["page_details"][2]["vertical_chars"] == 12


def test_profile_command_writes_profile_json(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "two.pdf", _two_columns)
    out = tmp_path / "results"

    assert main(["profile", str(pdf), "--out", str(out)]) == 0

    profile = json.loads((out / "two" / "profile.json").read_text(encoding="utf-8"))
    assert profile["columns"] == 2
    assert profile["profiler"].startswith("pymupdf ")


def test_profile_command_missing_file_exits_2(tmp_path: Path) -> None:
    assert main(["profile", str(tmp_path / "missing.pdf"), "--out", str(tmp_path)]) == 2


def test_run_writes_profile_json(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "grid.pdf", _grid)
    out = tmp_path / "results"
    args = argparse.Namespace(pdfs=[str(pdf)], out=str(out), gt=None)

    # アダプタを走らせずに run の出力の流れだけを確かめる
    assert _run(args, adapters=[], normalizers={}) == 0

    assert (out / "grid" / "run.json").is_file()
    profile = json.loads((out / "grid" / "profile.json").read_text(encoding="utf-8"))
    assert profile["ruled_pages"] == [1]


def test_profile_command_unreadable_pdf_exits_2(tmp_path: Path) -> None:
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    assert main(["profile", str(pdf), "--out", str(tmp_path / "results")]) == 2
    profile = json.loads((tmp_path / "results" / "broken" / "profile.json").read_text("utf-8"))
    assert "error" in profile


def test_run_keeps_previous_profile_of_failed_input(tmp_path: Path) -> None:
    out = tmp_path / "results"
    # 前回の run の出力が残っている文書を、今回は存在しないパスで指定する
    (out / "gone").mkdir(parents=True)
    (out / "gone" / "run.json").write_text("{}", encoding="utf-8")
    (out / "gone" / "profile.json").write_text('{"pages": 1}', encoding="utf-8")
    args = argparse.Namespace(pdfs=[str(tmp_path / "gone.pdf")], out=str(out), gt=None)

    assert _run(args, adapters=[], normalizers={}) == 2
    assert json.loads((out / "gone" / "profile.json").read_text("utf-8")) == {"pages": 1}


def test_textless_image_pages_excludes_blank_pages(tmp_path: Path) -> None:
    """画像だけ（またはページ番号程度の文字だけ）のページは入り、白紙ページは入らない（#169）。"""
    import pymupdf

    from jdocbench.profile import profile_pdf

    pdf = tmp_path / "mixed.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "テキストのあるページ", fontname="japan")
    doc.new_page()  # 白紙ページ（文字も画像も無い）
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pix.clear_with(200)
    doc.new_page().insert_image(pymupdf.Rect(72, 72, 272, 272), pixmap=pix)  # 画像だけのページ
    scanned = doc.new_page()  # 画像 + ページ番号だけのテキスト（スキャンにノンブルが載った形）
    scanned.insert_image(pymupdf.Rect(72, 72, 272, 272), pixmap=pix)
    scanned.insert_text((290, 800), "4")
    doc.save(pdf)
    doc.close()

    profile = profile_pdf(pdf)
    assert profile["textless_image_pages"] == [3, 4]
