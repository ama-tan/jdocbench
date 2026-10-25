"""jdocbench site-data（公開サイト用の数値だけの data.json）のテスト（jdoc-bench#269）。

fixture の値はすべてダミー。原文らしき文字列は、出力に漏れないことを確かめるための目印。
"""

import json
from pathlib import Path

from jdocbench.cli import main
from jdocbench.site import build_site_data

# 出力に現れてはいけない目印（パス・抽出テキスト・GT の断片を模したダミー）
_SECRET_PATH = "/home/someone/private/samples/doc-a.pdf"
_SECRET_GT = "groundtruth/doc-a.json"
_SECRET_TEXT = "ダミー原文テキスト（漏れてはいけない）"
_SECRETS = (_SECRET_PATH, _SECRET_GT, _SECRET_TEXT, "private", "漏れて")

_SUMMARY: dict = {
    "documents": [
        {
            "stem": "doc-a",
            "pages": 2,
            "ground_truth_sha256": "a" * 64,
            "adapters": {
                "pdftotext": {
                    "status": "ok",
                    "roa": 0.9,
                    "total": 50.0,
                    "cf": 0.8,
                    "sed": 0.6,
                    "tsf": None,
                    "roa_effective": 0.9,
                    "jnf": 1.0,
                    "wall_time_sec": 0.5,
                    "peak_rss_kb": 1024,
                    "extra_key": _SECRET_TEXT,
                },
                "yomitoku": {"status": "ok", "total": 99.0, "wall_time_sec": 9.0},
            },
        },
        {
            "stem": "doc-b",
            "pages": 1,
            "ground_truth_sha256": None,
            "adapters": {
                "pdftotext": {"status": "error", "total": None, "wall_time_sec": 0.1},
                "yomitoku": {"status": "ok", "total": 1.0},
            },
        },
    ],
    "missing": ["doc-c.pdf"],
    "failures": [
        {"stem": "doc-b", "adapter": "pdftotext", "kind": "error", "message": _SECRET_TEXT},
        {"stem": "doc-b", "adapter": "yomitoku", "kind": "dsqs_error", "message": _SECRET_PATH},
    ],
}


def _run(cpu: str, version: str) -> dict:
    """run.json のダミー。allowlist 外のキーに目印を入れる。"""
    return {
        "jdocbench_version": "9.9.9",
        "input": _SECRET_PATH,
        "sha256": "b" * 64,
        "size_bytes": 123,
        "pages": 2,
        "pages_source": "pdfinfo",
        "ground_truth": _SECRET_GT,
        "ground_truth_sha256": "a" * 64,
        "started_at": "2000-01-01T00:00:00+00:00",
        "python": "3.12.0",
        "platform": "Linux-test",
        "cpu": cpu,
        "ram_total_bytes": 8,
        "fonts_ja": ["Dummy Font JP"],
        "extra": {"note": _SECRET_TEXT},
        "adapters": {
            "pdftotext": {
                "status": "ok",
                "version": version,
                "output_dir": _SECRET_PATH,
                "reason": _SECRET_TEXT,
                "message": _SECRET_TEXT,
                "extra": {"text": _SECRET_TEXT},
            },
            "yomitoku": {"status": "ok", "version": "0.0.1"},
        },
    }


_RUNS = {"doc-a": _run("CPU-A", "1.0"), "doc-b": _run("CPU-B", "1.0")}

_TOP_KEYS = {"documents", "missing", "failures", "run"}
_DOCUMENT_KEYS = {"stem", "pages", "ground_truth_sha256", "adapters", "run"}
_ADAPTER_KEYS = {
    "status",
    "roa",
    "total",
    "cf",
    "sed",
    "tsf",
    "roa_effective",
    "jnf",
    "wall_time_sec",
    "peak_rss_kb",
    "version",
}
_RUN_KEYS = {
    "jdocbench_version",
    "python",
    "platform",
    "cpu",
    "ram_total_bytes",
    "started_at",
    "fonts_ja",
}
_FAILURE_KEYS = {"stem", "adapter", "kind"}


def _assert_allowlist(data: dict) -> None:
    """出力のキーが allowlist の範囲に収まっていること。"""
    assert set(data) <= _TOP_KEYS
    assert set(data["run"]) == _RUN_KEYS | {"adapter_versions"}
    names = {name for document in data["documents"] for name in document["adapters"]}
    assert set(data["run"]["adapter_versions"]) == names
    for document in data["documents"]:
        assert set(document) == _DOCUMENT_KEYS
        assert set(document["run"]) == _RUN_KEYS
        for entry in document["adapters"].values():
            assert set(entry) == _ADAPTER_KEYS
    for failure in data.get("failures", []):
        assert set(failure) == _FAILURE_KEYS


def test_build_site_data_keeps_numbers_and_meta() -> None:
    data = build_site_data(_SUMMARY, _RUNS)
    _assert_allowlist(data)
    doc_a = data["documents"][0]
    assert doc_a["stem"] == "doc-a"
    assert doc_a["pages"] == 2
    assert doc_a["adapters"]["pdftotext"] == {
        "status": "ok",
        "roa": 0.9,
        "total": 50.0,
        "cf": 0.8,
        "sed": 0.6,
        "tsf": None,
        "roa_effective": 0.9,
        "jnf": 1.0,
        "wall_time_sec": 0.5,
        "peak_rss_kb": 1024,
        "version": "1.0",
    }
    # 文書ごとのメタは全キー。トップは全文書で同じ値だけ残し、違うもの（cpu）は None
    meta = {
        "jdocbench_version": "9.9.9",
        "python": "3.12.0",
        "platform": "Linux-test",
        "ram_total_bytes": 8,
        "started_at": "2000-01-01T00:00:00+00:00",
        "fonts_ja": ["Dummy Font JP"],
    }
    assert doc_a["run"] == {**meta, "cpu": "CPU-A"}
    assert data["documents"][1]["run"]["cpu"] == "CPU-B"
    assert data["run"] == {
        **meta,
        "cpu": None,
        "adapter_versions": {"pdftotext": "1.0", "yomitoku": "0.0.1"},
    }
    assert data["missing"] == ["doc-c.pdf"]
    # message は落とす
    assert data["failures"][0] == {"stem": "doc-b", "adapter": "pdftotext", "kind": "error"}


def test_build_site_data_drops_unlisted_values() -> None:
    """run.json の input・extra・reason や failures の message が出力に現れない。"""
    text = json.dumps(build_site_data(_SUMMARY, _RUNS), ensure_ascii=False)
    for secret in _SECRETS:
        assert secret not in text


def test_build_site_data_excludes_adapter_column() -> None:
    data = build_site_data(_SUMMARY, _RUNS, ["yomitoku"])
    _assert_allowlist(data)
    for document in data["documents"]:
        assert "yomitoku" not in document["adapters"]
    assert all(failure["adapter"] != "yomitoku" for failure in data["failures"])
    assert "yomitoku" not in json.dumps(data)


def test_build_site_data_rejects_unexpected_strings() -> None:
    """想定外の status・kind・パスを含む stem・長い文字列は落とす。"""
    summary = {
        "documents": [
            {"stem": "../x", "adapters": {}},
            {"stem": "doc-a", "adapters": {"p": {"status": _SECRET_TEXT, "total": "50"}}},
        ],
        "missing": [_SECRET_PATH],
        "failures": [{"stem": "doc-a", "adapter": "p", "kind": _SECRET_TEXT}],
    }
    run = {
        "cpu": "x" * 500,
        "started_at": _SECRET_TEXT,
        "fonts_ja": [_SECRET_TEXT * 10, "Ok Font"],
    }
    data = build_site_data(summary, {"doc-a": run})
    assert [d["stem"] for d in data["documents"]] == ["doc-a"]
    entry = data["documents"][0]["adapters"]["p"]
    assert entry["status"] is None
    assert entry["total"] is None
    assert "missing" not in data
    assert "failures" not in data
    assert data["run"]["cpu"] is None
    assert data["run"]["started_at"] is None
    assert data["run"]["fonts_ja"] == ["Ok Font"]


def test_build_site_data_without_run_json() -> None:
    """run.json の無い文書は、run.json 由来の値が None になる。"""
    data = build_site_data(_SUMMARY, {}, ["yomitoku"])
    _assert_allowlist(data)
    assert set(data["documents"][0]["run"].values()) == {None}
    assert data["documents"][0]["adapters"]["pdftotext"]["version"] is None
    assert data["documents"][0]["pages"] == 2


def _write_results(root: Path) -> None:
    root.mkdir()
    (root / "summary.json").write_text(json.dumps(_SUMMARY, ensure_ascii=False), encoding="utf-8")
    for stem, run in _RUNS.items():
        (root / stem).mkdir()
        (root / stem / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        # 開いてはいけないファイル。壊れた JSON・バイナリなので、開けば例外で落ちる
        (root / stem / "profile.json").write_text(_SECRET_TEXT + "{", encoding="utf-8")
        for sub in ("raw", "normalized", "metrics"):
            (root / stem / sub).mkdir()
            (root / stem / sub / "x.json").write_bytes(b"\xff\xfe" + _SECRET_TEXT.encode())
        (root / f"{stem}.pdf").write_bytes(b"%PDF-\xff")


def test_cli_site_data(tmp_path: Path) -> None:
    results = tmp_path / "results"
    _write_results(results)
    out = tmp_path / "site" / "data.json"
    code = main(["site-data", str(results), "--out", str(out), "--exclude-adapter", "yomitoku"])
    assert code == 0
    text = out.read_text(encoding="utf-8")
    data = json.loads(text)
    _assert_allowlist(data)
    assert [d["stem"] for d in data["documents"]] == ["doc-a", "doc-b"]
    assert data["documents"][0]["adapters"]["pdftotext"]["version"] == "1.0"
    assert "yomitoku" not in text
    for secret in _SECRETS:
        assert secret not in text


def test_cli_site_data_broken_run_json(tmp_path: Path, capsys) -> None:
    """読めない run.json は、その文書のメタを None にして書き出し、2 を返す。"""
    results = tmp_path / "results"
    _write_results(results)
    (results / "doc-b" / "run.json").write_text(_SECRET_TEXT + "{", encoding="utf-8")
    out = tmp_path / "data.json"
    assert main(["site-data", str(results), "--out", str(out)]) == 2
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data["documents"][1]["run"].values()) == {None}
    assert data["documents"][0]["run"]["cpu"] == "CPU-A"
    assert _SECRET_TEXT not in capsys.readouterr().err


def test_cli_site_data_without_summary(tmp_path: Path) -> None:
    code = main(["site-data", str(tmp_path), "--out", str(tmp_path / "data.json")])
    assert code == 2
    assert not (tmp_path / "data.json").exists()
