"""jdocbench site（公開サイトの静的 HTML）のテスト（jdoc-bench#270）。

fixture の値はすべてダミー。実データの文書名・原文は入れない。
"""

import copy
import json
import re
from pathlib import Path

import pytest

from jdocbench.cli import main
from jdocbench.site import build_site_data
from jdocbench.sitehtml import build_site
from jdocbench.summary import render, to_summary

_FORBIDDEN_TAG = re.compile(r"<\s*(script|link|img|iframe)\b", re.IGNORECASE)


def _adapter(total, cf, tsf, jnf, version="1.0") -> dict:
    """run.json のアダプタ 1 件（DSQS あり）。"""
    return {
        "status": "ok",
        "version": version,
        "wall_time_sec": 1.0,
        "peak_rss_kb": 51200,
        "dsqs": {
            "total": total,
            "cf": cf,
            "sed": 0.5,
            "tsf": tsf,
            "roa": 0.1,
            "roa_effective": 0.0,
            "jnf": jnf,
        },
    }


def _run(stem: str, adapters: dict, gt: bool = True) -> dict:
    return {
        "jdocbench_version": "9.9.9",
        "input": f"/dummy/{stem}.pdf",
        "pages": 2,
        "ground_truth_sha256": ("c" * 64) if gt else None,
        "started_at": "2000-01-01T00:00:00+00:00",
        "python": "3.12.0",
        "platform": "Linux-test",
        "cpu": f"Dummy CPU {stem}",
        "ram_total_bytes": 8 * 1024**3,
        "adapters": adapters,
    }


# GT のある文書 2 件と GT の無い文書 1 件。tsf / jnf の None、失敗したアダプタを混ぜる
_RUNS = [
    _run(
        "doc-a",
        {
            "alpha": _adapter(50.0, 0.8, None, 1.0),
            "beta": _adapter(70.0, 0.9, 0.4, None),
            "yomitoku": _adapter(99.0, 1.0, 1.0, 1.0),
        },
    ),
    _run(
        "doc-b",
        {
            "alpha": _adapter(40.0, 0.7, 0.2, 0.5),
            "beta": {"status": "error", "version": "2.0", "wall_time_sec": 0.1},
            "yomitoku": _adapter(98.0, 1.0, 1.0, 1.0),
        },
    ),
    _run(
        "doc-c",
        {
            "alpha": {"status": "ok", "version": "1.0", "wall_time_sec": 0.2, "peak_rss_kb": 1024},
            "beta": {"status": "ok", "version": "2.0", "wall_time_sec": 0.3},
        },
        gt=False,
    ),
]


def _data() -> dict:
    """run.json 相当 → summary.json → data.json（yomitoku は除外しないまま渡す）。"""
    runs = {run["input"].split("/")[-1][:-4]: run for run in _RUNS}
    return build_site_data(to_summary(_RUNS), runs)


def _meta() -> dict:
    return {
        "source": {
            "run_id": "123",
            "run_url": None,
            "commit": "abc1234",
            "tool_sha": "def5678",
            "measured_at": "2000-01-01",
            "repo_url": "https://example.com/repo",
            "article_url": None,
            "notes": ["ダミーの注記"],
        },
        "documents": [
            {
                "stem": stem,
                "title": f"ダミー文書 {stem}",
                "publisher": '架空の発行元 & "テスト"',
                "domain": "Dummy",
                "pages": 2,
                "source_url": f"https://example.com/{stem}",
                "license": {"name": "ダミーライセンス", "url": "https://example.com/license"},
                "attribution": "ダミーの出典表示",
                "features": ["simple-table", "scan"],
                "excerpt_note": "抜粋なし",
                "notes": ["文書のダミー注記"],
            }
            for stem in ("doc-a", "doc-b", "doc-c")
        ],
    }


def _write_inputs(tmp_path: Path, data: dict, meta: dict) -> tuple[Path, Path]:
    data_json = tmp_path / "data.json"
    meta_json = tmp_path / "documents.json"
    data_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    meta_json.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return data_json, meta_json


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _cells(row: str) -> list[str]:
    return [re.sub(r"<[^>]+>", "", cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row)]


def test_cli_writes_index_and_document_pages_deterministically(tmp_path, capsys):
    data_json, meta_json = _write_inputs(tmp_path, _data(), _meta())
    for out in ("out1", "out2"):
        assert (
            main(["site", str(data_json), "--meta", str(meta_json), "--out", str(tmp_path / out)])
            == 0
        )
    first, second = _files(tmp_path / "out1"), _files(tmp_path / "out2")
    assert sorted(first) == [
        "documents/doc-a.html",
        "documents/doc-b.html",
        "documents/doc-c.html",
        "index.html",
    ]
    assert first == second


def test_no_forbidden_tags_data_uri_or_hidden_adapter():
    meta = _meta()
    # 危険な値を入れても、エスケープされ・リンクにならないことを確かめる（空振り防止）
    meta["documents"][0]["title"] = "<script>alert(1)</script>"
    meta["documents"][0]["source_url"] = "javascript:alert(1)"
    meta["documents"][1]["license"]["url"] = "data:text/html,<img src=x>"
    pages = build_site(_data(), meta)
    joined = "".join(pages.values())
    assert "&lt;script&gt;" in joined
    assert '<a href="https://example.com/doc-c">' in joined
    assert "<style>" in joined and 'lang="ja"' in joined
    for text in pages.values():
        assert _FORBIDDEN_TAG.search(text) is None
        assert "data:" not in text
        assert "javascript:" not in text
        assert "yomitoku" not in text


def test_stem_mismatch_fails_without_writing(tmp_path, capsys):
    for change in ("drop", "add", "path"):
        meta = _meta()
        if change == "drop":
            meta["documents"].pop()
        elif change == "add":
            meta["documents"].append(dict(meta["documents"][0], stem="doc-x"))
        else:
            meta["documents"][0]["stem"] = "../doc-a"
        with pytest.raises(ValueError):
            build_site(_data(), meta)
        data_json, meta_json = _write_inputs(tmp_path, _data(), meta)
        out = tmp_path / f"out-{change}"
        assert main(["site", str(data_json), "--meta", str(meta_json), "--out", str(out)]) == 2
        assert not out.exists()
    assert "一致しません" in capsys.readouterr().err


def test_parser_means_match_summary():
    index = build_site(_data(), _meta())["index.html"]
    section = index.split("Parser 平均")[1].split("</table>")[0]
    site_rows = [_cells(row) for row in re.findall(r"<tr>(.*?)</tr>", section) if "<td" in row]

    markdown = render(_RUNS).split("## Parser 平均")[1].split("\n\n")[0]
    summary_rows = [
        [cell.strip() for cell in line.strip("|").split("|")] for line in markdown.splitlines()[3:]
    ]
    # yomitoku は site 側では落とす。それ以外は summary.md と同じ値
    summary_rows = [row for row in summary_rows if row[0] != "yomitoku"]
    assert len(site_rows) == 2
    assert ["alpha", "2"] == site_rows[0][:2]
    assert site_rows == summary_rows


def test_document_page_digits_and_fields():
    page = build_site(_data(), _meta())["documents/doc-a.html"]
    row = next(r for r in re.findall(r"<tr>(.*?)</tr>", page) if "<td>alpha</td>" in r)
    assert _cells(row) == [
        "alpha",
        "ok",
        "50.0",
        "1.000",
        "0.800",
        "0.500",
        "-",
        "0.000",
        "1.000",
        "50.0",
        "1.0",
    ]
    assert "架空の発行元 &amp; &quot;テスト&quot;" in page
    assert "c" * 12 in page and "c" * 13 not in page
    assert "Dummy CPU doc-a" in page and "8.0 GiB" in page
    assert "文書のダミー注記" in page
    # GT の無い文書は DSQS が `-`
    page_c = build_site(_data(), _meta())["documents/doc-c.html"]
    row_c = next(r for r in re.findall(r"<tr>(.*?)</tr>", page_c) if "<td>alpha</td>" in r)
    assert _cells(row_c)[2:8] == ["-"] * 6
    assert _cells(row_c)[9] == "1.0"


def test_index_conditions_and_best_total():
    meta = _meta()
    index = build_site(_data(), meta)["index.html"]
    assert "Dummy CPU doc-a（1 文書）" in index
    assert "abc1234" in index and "def5678" in index and "123" in index
    assert "70.0（beta）" in index  # doc-a の最良 Total（yomitoku の 99.0 は出さない）
    assert ">run<" not in index  # run_url が null なら出さない
    meta2 = copy.deepcopy(meta)
    meta2["source"]["run_url"] = "https://example.com/run"
    assert '<a href="https://example.com/run">' in build_site(_data(), meta2)["index.html"]
