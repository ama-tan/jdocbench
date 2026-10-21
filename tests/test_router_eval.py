"""Router の評価スクリプト（#163）。小さな偽の結果ディレクトリで集計を確かめる。"""

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "router_eval", Path(__file__).resolve().parents[1] / "scripts" / "router_eval.py"
)
router_eval = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(router_eval)

_TEXT = {
    "pages": 2,
    "text_layer": 1.0,
    "chars_per_page": 500.0,
    "columns": 1,
    "ruled_pages": [1, 2],
    "nonembedded_cjk_font_pages": [],
}
_SCAN = {**_TEXT, "text_layer": 0.0, "chars_per_page": 0.0, "columns": 0, "ruled_pages": []}


def _a(total, wall, status="ok"):
    return {"status": status, "total": total if status == "ok" else None, "wall_time_sec": wall}


def _documents() -> list[dict]:
    # text: quality → docling、cost → pdfplumber（C4）。最良は ppstructure 80.0、
    # 1 点以内の最安は docling 79.5（pdfplumber 70.0 は外れる）
    text = {
        "pdfplumber": _a(70.0, 0.5),
        "pymupdf": _a(60.0, 0.4),
        "camelot_stream": _a(50.0, 0.3),
        "docling": _a(79.5, 20.0),
        "ppstructure": _a(80.0, 100.0),
        "docling_ocr": _a(None, None, "skipped"),
    }
    # scan: cost → docling_ocr だが skipped にして fallback（ppstructure）を確かめる。
    # 単一の camelot_stream は error で 0 として数える
    scan = {
        "pdfplumber": _a(0.0, 0.1),
        "pymupdf": _a(0.0, 0.1),
        "camelot_stream": _a(None, None, "error"),
        "docling": _a(0.0, 10.0),
        "ppstructure": _a(81.0, 50.0),
        "docling_ocr": _a(None, None, "skipped"),
    }
    return [{"stem": "text", "adapters": text}, {"stem": "scan", "adapters": scan}]


def _evaluate() -> dict:
    return router_eval.evaluate(_documents(), {"text": _TEXT, "scan": _SCAN})["strategies"]


def test_router_uses_route_and_falls_back_when_not_ok():
    s = _evaluate()
    assert [c["parser"] for c in s["router-quality"]["choices"]] == ["docling", "ppstructure"]
    cost = s["router-cost"]["choices"]
    assert [c["parser"] for c in cost] == ["pdfplumber", "ppstructure"]
    assert "note" not in cost[0]
    assert "fallback" in cost[1]["note"]


def test_single_counts_non_ok_as_zero_with_note():
    camelot = _evaluate()["camelot_stream"]
    assert [c["total"] for c in camelot["choices"]] == [50.0, 0.0]
    assert camelot["choices"][1]["note"] == "camelot_stream が error"
    assert camelot["mean"] == 25.0
    assert camelot["wall"] == 0.3


def test_oracle_best_and_cheapest_within_one_point():
    s = _evaluate()
    assert [c["parser"] for c in s["oracle"]["choices"]] == ["ppstructure", "ppstructure"]
    assert [c["parser"] for c in s["oracle-cost"]["choices"]] == ["docling", "ppstructure"]
    assert s["oracle"]["within"] == s["oracle-cost"]["within"] == 2


def test_aggregates_mean_wall_and_within_count():
    s = _evaluate()
    quality = s["router-quality"]
    assert quality["mean"] == round((79.5 + 81.0) / 2, 1)
    assert quality["wall"] == 70.0
    assert quality["within"] == 2
    assert s["pdfplumber"]["within"] == 0  # 70.0 は最良 80.0 から 1 点超、スキャンは 0
    assert s["docling"]["within"] == 1


def test_oracle_tie_prefers_shorter_wall_and_missing_single_is_zero():
    docs = [{"stem": "t", "adapters": {"a": _a(80.0, 9.0), "b": _a(80.0, 1.0)}}]
    s = router_eval.evaluate(docs, {"t": _TEXT})["strategies"]
    assert s["oracle"]["choices"][0]["parser"] == "b"
    assert s["docling"]["choices"][0]["note"] == "docling が なし"


def test_main_reads_results_dir(tmp_path, capsys):
    (tmp_path / "summary.json").write_text(
        json.dumps({"documents": _documents()}), encoding="utf-8"
    )
    for stem, profile in (("text", _TEXT), ("scan", _SCAN)):
        (tmp_path / stem).mkdir()
        (tmp_path / stem / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    assert router_eval.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "| text | 79.5 | 70.0 |" in out
    assert "| **平均 Total** |" in out
    assert "scan / router-cost: docling_ocr が ok でないため fallback" in out


def test_main_passes_fonts_ja_from_run_json(tmp_path, capsys):
    """C3（日本語フォント非埋め込み）は結果を出した環境の fonts_ja（run.json）で分岐する。"""
    nonembedded = {**_TEXT, "nonembedded_cjk_font_pages": [1]}
    adapters = {"pymupdf": _a(78.9, 0.1), "docling": _a(97.2, 90.0)}
    docs = [{"stem": "fonts", "adapters": adapters}, {"stem": "nofonts", "adapters": adapters}]
    (tmp_path / "summary.json").write_text(json.dumps({"documents": docs}), encoding="utf-8")
    for stem, fonts_ja in (("fonts", ["Noto Sans CJK JP"]), ("nofonts", [])):
        (tmp_path / stem).mkdir()
        (tmp_path / stem / "profile.json").write_text(json.dumps(nonembedded), encoding="utf-8")
        (tmp_path / stem / "run.json").write_text(
            json.dumps({"fonts_ja": fonts_ja}), encoding="utf-8"
        )
    assert router_eval.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "| fonts | docling | docling |" in out
    assert "| nofonts | docling | pymupdf |" in out


def test_main_without_summary_returns_2(tmp_path):
    assert router_eval.main([str(tmp_path)]) == 2


def test_failed_choice_is_never_within_even_if_best_is_low():
    docs = [{"stem": "t", "adapters": {"pdfplumber": _a(0.5, 1.0)}}]
    s = router_eval.evaluate(docs, {"t": _TEXT})["strategies"]
    assert s["pdfplumber"]["within"] == 1
    assert s["docling"]["choices"][0]["total"] == 0.0
    assert s["docling"]["within"] == 0
