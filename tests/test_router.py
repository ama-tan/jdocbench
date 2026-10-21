"""文書単位の Router 初版（#162）。各分岐を固定の Profile で確かめる。"""

import json
from pathlib import Path

import pytest

from jdocbench.cli import main
from jdocbench.router import MODES, ROUTER_VERSION, route, route_all


def _profile(**overrides) -> dict:
    """1 段組み・テキスト層あり・日本語フォント埋め込み・全ページ罫線ありの Profile。"""
    profile = {
        "source": "x.pdf",
        "sha256": "0" * 64,
        "pages": 4,
        "text_layer": 1.0,
        "chars_per_page": 500.0,
        "columns": 1,
        "ruled_pages": [1, 2, 3, 4],
        "nonembedded_cjk_font_pages": [],
        "textless_image_pages": [],
    }
    profile.update(overrides)
    return profile


_SCAN = {"text_layer": 0.0, "chars_per_page": 0.0, "columns": 0, "ruled_pages": []}
# 透かしだけが文字として入ったスキャン（全ページに文字はあるが 1 ページ 20 文字未満）。
# docling_ocr は文字があると skipped になりうるので推奨しない
_WATERMARK = {"text_layer": 1.0, "chars_per_page": 3.0}
_TWO_COLUMNS = {"columns": 2}
_NONEMBEDDED = {"nonembedded_cjk_font_pages": [1, 2]}

# (Profile の上書き, モード, 期待する rule, parser, fallbacks)
_CASES = [
    # 実物スキャン estat で ppstructure が 16.7 点負けたため docling_ocr 主（#188）
    (_SCAN, "quality", "Q1-scan", "docling_ocr", ["ppstructure"]),
    (_WATERMARK, "quality", "Q1-scan", "ppstructure", []),
    ({}, "quality", "Q2-text", "docling", ["ppstructure"]),
    (_TWO_COLUMNS, "quality", "Q2-text", "docling", ["ppstructure"]),
    (_NONEMBEDDED, "quality", "Q2-text", "docling", ["ppstructure"]),
    (_SCAN, "cost", "C1-scan", "docling_ocr", ["ppstructure"]),
    (_WATERMARK, "cost", "C1-scan", "ppstructure", []),
    (_TWO_COLUMNS, "cost", "C2-multicolumn", "docling", ["pymupdf"]),
    # 2 段組みは非埋め込みより先に見る（docling は pdfminer 系ではないので両方に効く）
    ({**_TWO_COLUMNS, **_NONEMBEDDED}, "cost", "C2-multicolumn", "docling", ["pymupdf"]),
    # 実行環境の日本語フォントを渡さない（調べられない）ときは docling（#173）
    (_NONEMBEDDED, "cost", "C3-nonembedded-cjk", "docling", ["pymupdf"]),
    ({}, "cost", "C4-classical", "pdfplumber", ["docling"]),
    # 罫線は分岐に使わない（罫線の無い表と表の無い文書を Profile で区別できないため）
    ({"ruled_pages": []}, "cost", "C4-classical", "pdfplumber", ["docling"]),
]


@pytest.mark.parametrize(("overrides", "mode", "rule", "parser", "fallbacks"), _CASES)
def test_route_branches(overrides, mode, rule, parser, fallbacks) -> None:
    result = route(_profile(**overrides), mode)
    assert result["mode"] == mode
    assert result["rule"] == rule
    assert result["parser"] == parser
    assert result["fallbacks"] == fallbacks
    assert result["reasons"]


@pytest.mark.parametrize(
    ("fonts_ja", "rule", "parser", "fallbacks"),
    [
        (["Noto Sans CJK JP"], "C3-nonembedded-cjk", "docling", ["pymupdf"]),
        (None, "C3-nonembedded-cjk", "docling", ["pymupdf"]),
        ([], "C3-nonembedded-cjk-nofonts", "pymupdf", ["docling"]),
    ],
)
def test_route_nonembedded_cjk_depends_on_environment_fonts(fonts_ja, rule, parser, fallbacks):
    """日本語フォント非埋め込みの文書は、環境にフォントが無いと確かめたときだけ Level 1（#173）。

    フォントがあれば Document AI はページ画像で日本語を描いて読める（env の docling 97.2、
    pymupdf 78.9）。無ければ docling も落ちる（77.5）。調べられないときは損失の浅い docling。
    """
    result = route(_profile(**_NONEMBEDDED), "cost", fonts_ja)
    assert (result["rule"], result["parser"], result["fallbacks"]) == (rule, parser, fallbacks)
    # quality と他の分岐はフォントに依存しない
    assert route(_profile(**_NONEMBEDDED), "quality", fonts_ja)["parser"] == "docling"
    assert route(_profile(), "cost", fonts_ja)["rule"] == "C4-classical"


@pytest.mark.parametrize("mode", ["quality", "cost"])
def test_route_error_profile_returns_safe_default(mode) -> None:
    result = route({"source": "x.pdf", "sha256": None, "error": "No module named 'pymupdf'"}, mode)
    assert result["rule"] == "default-error"
    assert result["parser"] == "docling"
    assert result["fallbacks"] == ["docling_ocr", "ppstructure"]
    assert "pymupdf" in result["reasons"][0]


@pytest.mark.parametrize("broken", [{"columns": None}, {"pages": 0}, {"text_layer": "x"}])
def test_route_incomplete_profile_returns_safe_default(broken) -> None:
    assert route(_profile(**broken), "cost")["rule"] == "default-error"
    profile = _profile()
    del profile["ruled_pages"]
    assert route(profile, "quality")["rule"] == "default-error"


@pytest.mark.parametrize(
    ("mode", "rule"), [("quality", "Q1p-partial-scan"), ("cost", "C1p-partial-scan")]
)
def test_route_partial_scan_goes_to_ocr_parser(mode, rule) -> None:
    """テキスト層が一部のページだけ無い文書は、全ページを OCR できる ppstructure に回す（#169）。

    OCR しないパーサー（docling / pdfplumber）ではテキスト層の無いページの内容が欠落し、
    docling_ocr はテキスト層があると skipped になる。段組みやフォントの分岐より先に判定する。
    """
    for overrides in (
        {"text_layer": 0.5, "textless_image_pages": [3, 4]},
        {"text_layer": 0.5, "textless_image_pages": [3, 4], "columns": 2},
    ):
        result = route(_profile(**overrides), mode)
        assert result["rule"] == rule
        assert result["parser"] == "ppstructure"
        assert "docling_ocr" not in result["fallbacks"]
        assert any("text_layer=0.5" in reason for reason in result["reasons"])


def test_route_unknown_mode() -> None:
    with pytest.raises(ValueError):
        route(_profile(), "fast")


def test_route_all_records_identity_and_features() -> None:
    data = route_all(_profile(ruled_pages=[1]), ("cost",))
    assert data["router_version"] == ROUTER_VERSION
    assert data["source"] == "x.pdf"
    assert data["features"]["ruled_ratio"] == 0.25
    assert data["fonts_ja"] is None
    assert list(data["routes"]) == ["cost"]


def test_route_all_records_and_uses_fonts_ja() -> None:
    """route.json に分岐が依存した実行環境の日本語フォントを残し、その値で分岐する（#173）。"""
    data = route_all(_profile(**_NONEMBEDDED), ("cost",), [])
    assert data["fonts_ja"] == []
    assert data["routes"]["cost"]["rule"] == "C3-nonembedded-cjk-nofonts"


def test_cli_route_writes_route_json_with_safe_default(tmp_path: Path) -> None:
    """Profile の取れないダミー PDF でも route.json を書き、安全側の既定を返す。"""
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    out = tmp_path / "results"

    assert main(["route", str(pdf), "--out", str(out)]) == 0

    data = json.loads((out / "sample" / "route.json").read_text(encoding="utf-8"))
    assert data["router_version"] == ROUTER_VERSION
    assert "fonts_ja" in data
    assert data["source"] == str(pdf)
    assert len(data["sha256"]) == 64
    assert "profile_error" in data
    assert data["features"] is None
    assert set(data["routes"]) == {"quality", "cost"}
    for result in data["routes"].values():
        assert result["rule"] == "default-error"


def test_cli_route_missing_file(tmp_path: Path) -> None:
    out = tmp_path / "results"
    assert main(["route", str(tmp_path / "none.pdf"), "--mode", "cost", "--out", str(out)]) == 2
    assert not out.exists()


@pytest.mark.parametrize(("mode", "rule"), [("quality", "Q2-text"), ("cost", "C4-classical")])
def test_route_blank_pages_are_not_partial_scan(mode, rule) -> None:
    """白紙ページ（文字も画像も無い）があるだけのテキスト PDF は部分スキャンにしない（#169）。"""
    result = route(_profile(text_layer=0.75, textless_image_pages=[]), mode)
    assert result["rule"] == rule


def test_route_old_profile_without_image_pages_falls_back_to_text_layer() -> None:
    """#169 より前の Profile（textless_image_pages 無し）は text_layer < 1 で部分スキャン扱い。"""
    profile = _profile(text_layer=0.5)
    del profile["textless_image_pages"]
    assert route(profile, "cost")["rule"] == "C1p-partial-scan"


@pytest.mark.parametrize("mode", MODES)
def test_route_ignores_vertical_text_pages(mode) -> None:
    """vertical_text_pages はまだ分岐に使わない（#234。使うときはこのテストを書き換える）。"""
    with_field = route({**_profile(), "vertical_text_pages": [1, 2]}, mode)
    without_field = route(_profile(), mode)
    assert with_field == without_field
