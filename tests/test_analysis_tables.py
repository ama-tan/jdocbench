"""分析レポートの表の元を出すスクリプト（#175）。小さな偽の結果ディレクトリで確かめる。"""

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "analysis_tables", Path(__file__).resolve().parents[1] / "scripts" / "analysis_tables.py"
)
analysis_tables = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(analysis_tables)


def _a(total, wall=1.0, rss_kb=102400, status="ok"):
    return {
        "status": status,
        "total": total if status == "ok" else None,
        "cf": 0.5,
        "sed": 1.0,
        "tsf": 0.25,
        "roa_effective": 0.75,
        "wall_time_sec": wall,
        "peak_rss_kb": rss_kb,
    }


def _documents() -> list[dict]:
    text = {
        "pdftotext": _a(40.0, 0.01),
        "pdfplumber": _a(70.0, 0.5),
        "camelot_stream": _a(75.0, 0.4),
        "docling": _a(90.0, 50.0, 1536000),
        "docling_ocr": _a(None, status="skipped"),
    }
    scan = {"pdfplumber": _a(0.0, 0.1), "docling_ocr": _a(80.0, 90.0)}
    return [{"stem": "text", "adapters": text}, {"stem": "scan", "adapters": scan}]


def test_level_best_picks_max_per_level_and_skips_not_ok():
    best = analysis_tables.level_best(_documents()[0]["adapters"])
    assert best == {0: ("pdftotext", 40.0), 1: ("camelot_stream", 75.0), 2: ("docling", 90.0)}
    scan = analysis_tables.level_best(_documents()[1]["adapters"])
    assert scan == {0: None, 1: ("pdfplumber", 0.0), 2: ("docling_ocr", 80.0)}


def test_main_renders_subscores_costs_and_ratio(tmp_path, capsys):
    (tmp_path / "summary.json").write_text(json.dumps({"documents": _documents()}))
    metrics = tmp_path / "text" / "metrics"
    metrics.mkdir(parents=True)
    (metrics / "docling.json").write_text(json.dumps({"roa_match_rate": 0.9}))

    assert analysis_tables.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "| text | pdftotext 40.0 | camelot_stream 75.0 | docling 90.0 |" in out
    assert "| scan | - | pdfplumber 0.0 | docling_ocr 80.0 |" in out
    assert "| docling | 90.0 | 0.500 | 1.000 | 0.250 | 0.750 | 0.900 |" in out
    assert "| pdfplumber | 70.0 | 0.500 | 1.000 | 0.250 | 0.750 | - |" in out
    assert "| docling | 1 | 50.000〜50.000 | 1500.0〜1500.0 |" in out
    # pdfplumber が 0 点のスキャンは比を出さない
    assert "| text | 100 倍 | - |" in out
    assert "| scan |" not in out.split("## Wall の pdfplumber 比")[1]


def test_main_without_summary_returns_2(tmp_path):
    assert analysis_tables.main([str(tmp_path)]) == 2
