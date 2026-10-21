import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest
from _samples import SAMPLES

from jdocbench.adapters import tesseract as tesseract_module
from jdocbench.adapters.tesseract import (
    TesseractAdapter,
    TesseractPsm6Adapter,
    parse_langs,
    parse_version,
)
from jdocbench.normalizers import NORMALIZERS
from jdocbench.normalizers.tesseract import normalize as normalize_tesseract


def _tesseract_jpn_available() -> bool:
    return TesseractAdapter().available()


def test_parse_version() -> None:
    stdout = "tesseract 5.3.4\n leptonica-1.82.0\n  libgif 5.2.1 : libjpeg 8d\n"
    assert parse_version(stdout) == "5.3.4"
    assert parse_version("") == "unknown"
    assert parse_version("something else\n") == "unknown"


def test_parse_langs() -> None:
    stdout = (
        'List of available languages in "/usr/share/tesseract-ocr/5/tessdata/" (3):\n'
        "eng\njpn\nosd\n"
    )
    assert parse_langs(stdout) == ["eng", "jpn", "osd"]
    assert parse_langs("") == []


def test_available_is_false_without_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert TesseractAdapter().available() is False


def test_available_requires_jpn(monkeypatch: pytest.MonkeyPatch) -> None:
    # 本体があっても jpn の学習データが無ければ動かさない（英語モデルで読むと別物になる）
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tesseract")
    monkeypatch.setattr(
        tesseract_module, "_run_quiet", lambda cmd: "List of available languages (2):\neng\nosd\n"
    )
    assert TesseractAdapter().available() is False

    monkeypatch.setattr(
        tesseract_module, "_run_quiet", lambda cmd: "List of available languages (2):\neng\njpn\n"
    )
    expected = importlib.util.find_spec("pypdfium2") is not None
    assert TesseractAdapter().available() is expected


def test_version_is_unknown_when_command_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_os_error(*args: object, **kwargs: object) -> None:
        raise OSError("not found")

    monkeypatch.setattr(subprocess, "run", raise_os_error)
    assert TesseractAdapter().version() == "unknown"


def test_psm_is_fixed_per_adapter() -> None:
    assert (TesseractAdapter.name, TesseractAdapter.psm) == ("tesseract", 3)
    assert (TesseractPsm6Adapter.name, TesseractPsm6Adapter.psm) == ("tesseract_psm6", 6)


def test_normalize_splits_paragraphs_on_blank_lines(tmp_path: Path) -> None:
    # Tesseract の txt は段落の終わりに空行を入れ、ページの終わりに \f を付ける
    (tmp_path / "p001.txt").write_text(
        "令和8年5月4日\n\n統計トピックスNo. 148\n我が国のこどもの数\n \n\f", encoding="utf-8"
    )
    (tmp_path / "p002.txt").write_text("\f", encoding="utf-8")  # 何も読めなかったページ
    (tmp_path / "p003.txt").write_text("全國 381,808 55,963\n北海道 88,454\n\f", encoding="utf-8")
    (tmp_path / "p001.tsv").write_text("level\tpage_num\n", encoding="utf-8")  # 読まない

    document = normalize_tesseract(tmp_path)
    assert document.adapter == "tesseract"
    assert [page.number for page in document.pages] == [1, 2, 3]
    assert [block.text for block in document.pages[0].blocks] == [
        "令和8年5月4日",
        "統計トピックスNo. 148\n我が国のこどもの数",
    ]
    # 空のページも番号を保ったまま残す
    assert document.pages[1].blocks == []
    # 表は作らない（行のテキストとして段落に入る）
    assert document.table_count() == 0
    assert document.pages[2].lines() == ["全國 381,808 55,963", "北海道 88,454"]


def test_psm6_normalizer_renames_adapter(tmp_path: Path) -> None:
    (tmp_path / "p001.txt").write_text("本文\n\f", encoding="utf-8")
    assert NORMALIZERS["tesseract_psm6"](tmp_path).adapter == "tesseract_psm6"


@pytest.mark.skipif(
    importlib.util.find_spec("pypdfium2") is None,
    reason="pypdfium2（extra level1）が無い環境ではスキップ",
)
def test_text_layer_pdf_is_skipped(tmp_path: Path) -> None:
    """テキスト層のある PDF では OCR を走らせず skipped を返す（tesseract が無くても通る）。"""
    pdf = SAMPLES / "stat-go-jp-children-population-2026.pdf"
    if not pdf.is_file():
        pytest.skip("samples/ に対象 PDF が無い")

    out_dir = tmp_path / "raw" / "tesseract"
    metrics = TesseractAdapter().run(pdf, out_dir)
    assert "text layer" in str(metrics["skipped"])
    assert not out_dir.exists()


@pytest.mark.skipif(not _tesseract_jpn_available(), reason="tesseract（jpn）が無い環境ではスキップ")
def test_real_scan_respects_crop_box(tmp_path: Path) -> None:
    """見開きの MediaBox を CropBox で片ページにした実物スキャンを、ページ数どおりに読む。"""
    pdf = SAMPLES / "estat-census-1920-scan-excerpt.pdf"
    if not pdf.is_file():
        pytest.skip("samples/ に対象 PDF が無い")

    out_dir = tmp_path / "raw" / "tesseract_psm6"
    metrics = TesseractPsm6Adapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0
    extra = metrics["extra"]
    assert (extra["lang"], extra["psm"], extra["oem"], extra["dpi"]) == ("jpn", 6, 1, 300)

    # 4 ページ分の txt / tsv が揃う。ページ画像は raw に残さない
    assert len(sorted(out_dir.glob("p*.txt"))) == 4
    assert len(sorted(out_dir.glob("p*.tsv"))) == 4
    assert not sorted(out_dir.glob("*.png"))
    # tsv のページ幅は CropBox（約 519pt × 300dpi ≈ 2163px）で、見開き（約 4288px）ではない
    page_row = (out_dir / "p001.tsv").read_text(encoding="utf-8").splitlines()[1].split("\t")
    assert page_row[0] == "1"  # level 1 = ページ
    assert int(page_row[8]) < 2500

    document = NORMALIZERS["tesseract_psm6"](out_dir)
    assert len(document.pages) == 4
    assert document.char_count() > 0
    # p3 の表 1 の見出し（「面 積 及 人 口」）の一部が読めている。旧字体・かすれで誤読が多いため
    # 現代の字形でも読める「面」だけを見る
    assert any("面" in line for line in document.pages[2].lines())


def test_run_removes_stale_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """前回の実行のページ出力が残っていても、今回のページだけが正規化される。"""
    from jdocbench.perf import CommandResult

    out_dir = tmp_path / "raw" / "tesseract"
    out_dir.mkdir(parents=True)
    for name in ("p001.txt", "p002.txt", "p002.tsv"):
        (out_dir / name).write_text("前回\n\f", encoding="utf-8")

    def fake_run_command(cmd: list[str], env: dict[str, str] | None = None) -> CommandResult:
        # 子プロセスの代わり: 1 ページ分だけ書く
        (out_dir / "p001.txt").write_text("今回\n\f", encoding="utf-8")
        (out_dir / "p001.tsv").write_text("level\n", encoding="utf-8")
        return CommandResult(0, "", "", 0.1, 1)

    monkeypatch.setattr(tesseract_module, "_text_layer_chars", lambda pdf: 0)
    monkeypatch.setattr(tesseract_module, "run_command", fake_run_command)
    TesseractAdapter().run(tmp_path / "dummy.pdf", out_dir)

    assert sorted(path.name for path in out_dir.iterdir()) == ["p001.tsv", "p001.txt"]
    assert normalize_tesseract(out_dir).lines() == ["今回"]
