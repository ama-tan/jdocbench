"""compare_summaries.py のテスト（#273）。値はダミー（実データの原文を入れない）。"""

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "compare_summaries", Path(__file__).resolve().parents[1] / "scripts" / "compare_summaries.py"
)
compare_summaries = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(compare_summaries)


def _entry(total=80.0, status="ok", wall=1.0, rss=1024, **extra) -> dict:
    entry = {
        "status": status,
        "total": total,
        "cf": 0.9,
        "sed": 0.8,
        "tsf": 0.7,
        "roa_effective": 0.6,
        "jnf": 0.5,
        "wall_time_sec": wall,
        "peak_rss_kb": rss,
    }
    entry.update(extra)
    return entry


def _summary(documents=None, missing=None, failures=None) -> dict:
    summary: dict = {"documents": documents or []}
    if missing:
        summary["missing"] = missing
    if failures:
        summary["failures"] = failures
    return summary


def _doc(stem, adapters) -> dict:
    return {"stem": stem, "pages": 1, "ground_truth_sha256": "x" * 64, "adapters": adapters}


def _write(tmp_path, name, summary):
    path = tmp_path / name
    path.write_text(json.dumps(summary), encoding="utf-8")
    return path


def test_identical_summaries_exit_zero(tmp_path):
    summary = _summary([_doc("doc1", {"pdfplumber": _entry()})])
    a = _write(tmp_path, "a.json", summary)
    b = _write(tmp_path, "b.json", summary)
    assert compare_summaries.main([str(a), str(b)]) == 0


def test_score_diff_fails_without_allow(tmp_path):
    a = _write(tmp_path, "a.json", _summary([_doc("doc1", {"markitdown": _entry(total=80.0)})]))
    b = _write(tmp_path, "b.json", _summary([_doc("doc1", {"markitdown": _entry(total=79.0)})]))
    diffs = compare_summaries.field_diffs(json.loads(a.read_text()), json.loads(b.read_text()))
    assert diffs == [
        {"stem": "doc1", "adapter": "markitdown", "field": "total", "a": 80.0, "b": 79.0}
    ]
    assert compare_summaries.main([str(a), str(b)]) == 1


def test_allow_suppresses_exit_code_but_still_shown(tmp_path, capsys):
    a = _write(tmp_path, "a.json", _summary([_doc("kanpo", {"markitdown": _entry(total=80.0)})]))
    b = _write(tmp_path, "b.json", _summary([_doc("kanpo", {"markitdown": _entry(total=79.0)})]))
    code = compare_summaries.main([str(a), str(b), "--allow", "kanpo:markitdown"])
    assert code == 0
    out = capsys.readouterr().out
    assert "kanpo" in out and "markitdown" in out and "許可" in out


def test_allow_does_not_mask_other_pairs(tmp_path):
    a = _write(
        tmp_path,
        "a.json",
        _summary(
            [
                _doc(
                    "doc1",
                    {
                        "markitdown": _entry(total=80.0),
                        "pdfplumber": _entry(total=50.0),
                    },
                )
            ]
        ),
    )
    b = _write(
        tmp_path,
        "b.json",
        _summary(
            [
                _doc(
                    "doc1",
                    {
                        "markitdown": _entry(total=79.0),
                        "pdfplumber": _entry(total=49.0),
                    },
                )
            ]
        ),
    )
    assert compare_summaries.main([str(a), str(b), "--allow", "doc1:markitdown"]) == 1


def test_pair_only_in_one_side_is_a_diff(tmp_path):
    a = _write(tmp_path, "a.json", _summary([_doc("doc1", {"pdfplumber": _entry()})]))
    b = _write(tmp_path, "b.json", _summary([_doc("doc1", {})]))
    diffs = compare_summaries.field_diffs(json.loads(a.read_text()), json.loads(b.read_text()))
    # 存在しない側は空 dict として扱うため、値を持つ全フィールドが差になる
    assert {d["field"] for d in diffs} == set(compare_summaries._FIELDS)
    assert compare_summaries.main([str(a), str(b)]) == 1


def test_perf_fields_ignored_by_default(tmp_path):
    a = _write(
        tmp_path, "a.json", _summary([_doc("doc1", {"pdfplumber": _entry(wall=1.0, rss=1024)})])
    )
    b = _write(
        tmp_path, "b.json", _summary([_doc("doc1", {"pdfplumber": _entry(wall=99.0, rss=999999)})])
    )
    assert compare_summaries.main([str(a), str(b)]) == 0
    assert compare_summaries.main([str(a), str(b), "--include-perf"]) == 1


def test_missing_diff_fails_even_with_allow(tmp_path):
    a = _write(tmp_path, "a.json", _summary(missing=["doc2.pdf"]))
    b = _write(tmp_path, "b.json", _summary(missing=[]))
    assert compare_summaries.main([str(a), str(b), "--allow", "doc2:anything"]) == 1


def test_failures_diff(tmp_path):
    failure = {"stem": "doc1", "adapter": "pdf2json", "kind": "error", "message": "boom"}
    a = _write(tmp_path, "a.json", _summary(failures=[failure]))
    b = _write(tmp_path, "b.json", _summary(failures=[]))
    diffs = compare_summaries.failure_diffs(json.loads(a.read_text()), json.loads(b.read_text()))
    assert diffs == [{**failure, "side": "A"}]
    assert compare_summaries.main([str(a), str(b)]) == 1
    assert compare_summaries.main([str(a), str(b), "--allow", "doc1:pdf2json"]) == 0


def test_no_diff_renders_message(tmp_path, capsys):
    summary = _summary([_doc("doc1", {"pdfplumber": _entry()})])
    a = _write(tmp_path, "a.json", summary)
    b = _write(tmp_path, "b.json", summary)
    compare_summaries.main([str(a), str(b)])
    assert "差はありません" in capsys.readouterr().out


def test_bad_allow_format_errors(tmp_path):
    a = _write(tmp_path, "a.json", _summary())
    b = _write(tmp_path, "b.json", _summary())
    assert compare_summaries.main([str(a), str(b), "--allow", "no-colon"]) == 2
