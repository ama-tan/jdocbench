import builtins
import importlib.util
from pathlib import Path

import pytest

from jdocbench.view import render, render_pages

_SAMPLES = Path(__file__).resolve().parents[1] / "samples"
_SAMPLE_PDF_NAME = "stat-go-jp-children-population-2026.pdf"
_SAMPLE_PAGES = 6

_RUN: dict = {
    "input": "/tmp/docs/sample.pdf",
    "pages": 2,
    "started_at": "2026-01-02T03:04:05+00:00",
    "adapters": {
        "ok_adapter": {"status": "ok", "version": "1.0.0"},
        "skipped_adapter": {"status": "skipped", "reason": "not available"},
    },
}

_DOCS: dict = {
    "ok_adapter": {
        "adapter": "ok_adapter",
        "pages": [
            {
                "number": 1,
                "blocks": [
                    {"type": "paragraph", "text": "<b>"},
                    {"type": "table", "rows": [["A", "B"], ["1", "2"]]},
                ],
            },
            {"number": 2, "blocks": []},
        ],
    }
}

_IMAGE_PATHS: list[str | None] = ["pages/p1.png", None]


@pytest.fixture
def html() -> str:
    return render(_RUN, _DOCS, _IMAGE_PATHS)


def test_render_makes_one_section_per_page(html: str) -> None:
    assert html.count("<section") == 2
    assert "ページ 1" in html
    assert "ページ 2" in html


def test_render_escapes_text(html: str) -> None:
    assert "&lt;b&gt;" in html
    assert "<p><b></p>" not in html


def test_render_makes_table(html: str) -> None:
    assert html.count("<table>") == 1
    assert html.count("<tr>") == 2
    assert "<td>A</td>" in html


def test_render_shows_image_and_missing_image(html: str) -> None:
    assert 'src="pages/p1.png"' in html
    assert "画像なし" in html


def test_render_shows_status_for_adapter_without_document(html: str) -> None:
    assert "skipped_adapter" in html
    assert "skipped" in html


def test_render_shows_nothing_marker_for_empty_page(html: str) -> None:
    assert "(なし)" in html


def test_render_makes_static_html(html: str) -> None:
    assert html.startswith("<!DOCTYPE html>")
    assert '<html lang="ja">' in html
    assert '<meta charset="utf-8">' in html
    assert "<h1>" in html
    assert "sample.pdf" in html
    # 外部 JS を読まない静的 HTML
    assert "<script" not in html


def test_render_uses_document_pages_when_images_are_missing() -> None:
    html = render(_RUN, _DOCS, [])

    assert html.count("<section") == 2
    assert html.count("画像なし") == 2


@pytest.mark.skipif(
    importlib.util.find_spec("pypdfium2") is None, reason="pypdfium2 が無い環境ではスキップ"
)
def test_render_pages_writes_png_per_page(tmp_path: Path) -> None:
    pdf = _SAMPLES / _SAMPLE_PDF_NAME
    if not pdf.exists():
        pytest.skip("samples/ に対象 PDF が無い")

    paths = render_pages(pdf, tmp_path, _SAMPLE_PAGES)

    assert paths == [f"pages/p{number}.png" for number in range(1, _SAMPLE_PAGES + 1)]
    assert len(list((tmp_path / "pages").glob("*.png"))) == _SAMPLE_PAGES
    assert all((tmp_path / path).stat().st_size > 0 for path in paths)


def test_render_pages_without_pypdfium2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pypdfium2 が import できない環境では画像なしで続行する。"""
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pypdfium2":
            raise ImportError("No module named 'pypdfium2'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert render_pages(tmp_path / "sample.pdf", tmp_path, 3) == [None, None, None]
    assert render_pages(tmp_path / "sample.pdf", tmp_path, None) == []
    assert not (tmp_path / "pages").exists()
