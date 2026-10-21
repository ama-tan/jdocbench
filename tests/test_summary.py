import json

from jdocbench.summary import find_failures, render, to_summary

# GT のある文書（DSQS あり、使えないアダプタが 1 件）
_RUN_A: dict = {
    "input": "/tmp/docs/a.pdf",
    "pages": 6,
    "ground_truth": "groundtruth/a.json",
    "ground_truth_sha256": "a" * 64,
    "adapters": {
        "pdftotext": {
            "status": "ok",
            "wall_time_sec": 1.0,
            "peak_rss_kb": 51200,
            # 生の ROA は 0.9 だが対応率が低く、Total には 0.2 で入った想定（平均は後者を使う）
            "dsqs": {
                "cf": 0.8,
                "sed": 0.6,
                "tsf": 0.4,
                "roa": 0.9,
                "roa_effective": 0.2,
                "total": 50.0,
                "jnf": 0.5,
            },
        },
        "pdfplumber": {
            "status": "ok",
            "wall_time_sec": 2.5,
            "peak_rss_kb": 102400,
            # 表の無い文書では TSF が None になる（総合点にも平均にも入れない）
            "dsqs": {
                "cf": 0.6,
                "sed": 0.4,
                "tsf": None,
                "roa": 0.4,
                "roa_effective": 0.4,
                "total": 46.7,
                # 表記対象文字が無い文書では None（平均から除く）
                "jnf": None,
            },
        },
        "pymupdf": {"status": "skipped", "reason": "not available"},
    },
}

# GT の無い文書（DSQS なし。エラーのアダプタと、A に出てこないアダプタを含む）
_RUN_B: dict = {
    "input": "/tmp/docs/b.pdf",
    "pages": 3,
    "ground_truth": None,
    "adapters": {
        "pdftotext": {"status": "ok", "wall_time_sec": 0.5, "peak_rss_kb": 25600},
        "pdfplumber": {"status": "error", "message": "変換に失敗しました"},
        "docling": {"status": "ok", "wall_time_sec": 10.0, "peak_rss_kb": 2097152},
    },
}
_RUNS = [_RUN_A, _RUN_B]


def _rows(text: str, heading: str) -> list[str]:
    """指定した節の表のデータ行（ヘッダと区切り行を除く）を返す。"""
    section = text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("|")]
    return lines[2:]


def _header(text: str, heading: str) -> str:
    section = text.split(f"## {heading}\n", 1)[1]
    return section.splitlines()[0]


def test_title_and_overview() -> None:
    text = render(_RUNS)

    assert text.startswith("# JDocBench Summary\n")
    assert "文書 2 件 × Parser 4 件（pdftotext、pdfplumber、pymupdf、docling）" in text
    assert text.endswith("詳細は各文書の report.md を参照。\n")


def test_columns_are_union_of_adapters_in_order() -> None:
    # 列は全 run のアダプタの和集合。出現順（A の 3 件のあと B だけの docling）
    assert _header(render(_RUNS), "DSQS Total") == (
        "| Document | pdftotext | pdfplumber | pymupdf | docling |"
    )


def test_dsqs_total_table() -> None:
    rows = _rows(render(_RUNS), "DSQS Total")

    assert len(rows) == 2
    # 行は文書。skipped / GT の無い文書 / 列に無いアダプタは `-`
    assert rows[0] == "| a.pdf | 50.0 | 46.7 | - | - |"
    assert rows[1] == "| b.pdf | - | - | - | - |"


def test_jnf_table_is_next_to_total() -> None:
    text = render(_RUNS)

    # DSQS Total の直後に、Total に入れない別の表として置く
    assert text.index("## DSQS Total") < text.index("## JNF（Total に入れない）")
    assert text.index("## JNF（Total に入れない）") < text.index("## Wall [s]")
    assert _rows(text, "JNF（Total に入れない）") == [
        "| a.pdf | 0.500 | - | - | - |",
        "| b.pdf | - | - | - | - |",
    ]


def test_wall_table() -> None:
    rows = _rows(render(_RUNS), "Wall [s]")

    assert rows == [
        "| a.pdf | 1.000 | 2.500 | - | - |",
        "| b.pdf | 0.500 | - | - | 10.000 |",
    ]


def test_peak_rss_table_is_mib() -> None:
    rows = _rows(render(_RUNS), "Peak RSS [MiB]")

    assert rows == [
        "| a.pdf | 50.0 | 100.0 | - | - |",
        "| b.pdf | 25.0 | - | - | 2048.0 |",
    ]


def test_parser_mean_table() -> None:
    text = render(_RUNS)
    heading = "Parser 平均（Ground Truth のある文書のみ）"

    assert _header(text, heading) == "| Parser | n | Total | JNF | CF | SED | TSF | ROA |"
    # DSQS を計算した文書が無いアダプタ（pymupdf / docling）は行を作らない
    assert _rows(text, heading) == [
        "| pdftotext | 1 | 50.000 | 0.500 | 0.800 | 0.600 | 0.400 | 0.200 |",
        # TSF / JNF が None の文書だけなので TSF / JNF は `-`
        "| pdfplumber | 1 | 46.700 | - | 0.600 | 0.400 | - | 0.400 |",
    ]


def test_parser_mean_excludes_none_subscores() -> None:
    other = {
        **_RUN_A,
        "input": "/tmp/docs/c.pdf",
        "adapters": {
            "pdfplumber": {
                "status": "ok",
                "dsqs": {
                    "cf": 0.8,
                    "sed": 0.6,
                    "tsf": 0.5,
                    "roa": 0.2,
                    "roa_effective": 0.2,
                    "total": 52.5,
                    "jnf": 0.8,
                },
            }
        },
    }
    rows = _rows(render([_RUN_A, other]), "Parser 平均（Ground Truth のある文書のみ）")

    # n=2。TSF / JNF は値のある 1 件だけの平均（None は除く）
    assert "| pdfplumber | 2 | 49.600 | 0.800 | 0.700 | 0.500 | 0.500 | 0.300 |" in rows


def test_parser_mean_section_has_cf_sed_tsf_roa_note() -> None:
    text = render(_RUNS)

    assert (
        "CF = 文書の全テキスト（表の行を含む）の再現率／"
        "SED = 表を検出できたか（表数の F1）／"
        "TSF = 検出できた表の中身の再現率（分割・結合には寛容）／"
        "ROA = 行の並び順（一致した行だけで計算）。Total に入れた値の平均: "
        "GT 行の対応率が 0.15 未満、または対応行が無い文書では 0 として数える。"
        "JNF = 日本語表記の忠実度（全角/半角・丸数字・旧字体・△ など。docs/jnf.md）。"
        "Total には入れない。表記対象文字の無い文書（None）は平均から除く。"
    ) in text


def test_parser_mean_section_is_omitted_without_ground_truth() -> None:
    text = render([_RUN_B])

    assert "Parser 平均" not in text
    assert "CF = 文書の全テキスト" not in text


def test_to_summary_documents() -> None:
    summary = to_summary(_RUNS)

    assert [document["stem"] for document in summary["documents"]] == ["a", "b"]
    assert summary["documents"][0]["pages"] == 6
    # GT の版（企画書 §49）。GT の無い文書では None
    assert summary["documents"][0]["ground_truth_sha256"] == "a" * 64
    assert summary["documents"][1]["ground_truth_sha256"] is None
    assert summary["documents"][0]["adapters"]["pdftotext"] == {
        "status": "ok",
        "total": 50.0,
        "cf": 0.8,
        "sed": 0.6,
        "tsf": 0.4,
        "roa": 0.9,
        "roa_effective": 0.2,
        "jnf": 0.5,
        "wall_time_sec": 1.0,
        "peak_rss_kb": 51200,
    }
    # 無い値は None（skipped / GT なし）
    assert summary["documents"][0]["adapters"]["pymupdf"] == {
        "status": "skipped",
        "total": None,
        "cf": None,
        "sed": None,
        "tsf": None,
        "roa": None,
        "roa_effective": None,
        "jnf": None,
        "wall_time_sec": None,
        "peak_rss_kb": None,
    }
    assert summary["documents"][1]["adapters"]["docling"]["total"] is None


def test_to_summary_is_json_serializable() -> None:
    assert json.loads(json.dumps(to_summary(_RUNS)))["documents"][1]["stem"] == "b"


def test_render_handles_empty_runs() -> None:
    text = render([])

    assert text.startswith("# JDocBench Summary\n")
    assert "文書 0 件 × Parser 0 件" in text


def test_missing_documents_are_listed() -> None:
    markdown = render([_RUN_A], ["c.pdf", "d|e.pdf"])
    assert "文書 1 件" in markdown
    assert r"**結果の無い文書 2 件**: c.pdf、d\|e.pdf" in markdown
    assert to_summary([_RUN_A], ["c.pdf"])["missing"] == ["c.pdf"]
    # 無ければ summary.md も summary.json も従来と同じ
    assert render([_RUN_A], []) == render([_RUN_A])
    assert "missing" not in to_summary([_RUN_A])


# 正規化・DSQS の失敗と、理由つきの skipped（失敗ではない）を含む文書（#207）
_RUN_C: dict = {
    "input": "/tmp/docs/c.pdf",
    "adapters": {
        "pdftotext": {"status": "ok", "normalize_error": "壊れた raw"},
        "pymupdf": {"status": "ok", "dsqs_error": "GT が読めない"},
        # 文字列が空の例外（`raise ValueError()`）でも失敗として拾う
        "pdfplumber": {"status": "ok", "normalize_error": ""},
        "tesseract": {"status": "skipped", "reason": "text layer present (3852 chars)"},
    },
}


def test_find_failures_lists_error_normalize_error_dsqs_error() -> None:
    assert find_failures([_RUN_A, _RUN_B, _RUN_C]) == [
        {"stem": "b", "adapter": "pdfplumber", "kind": "error", "message": "変換に失敗しました"},
        {"stem": "c", "adapter": "pdftotext", "kind": "normalize_error", "message": "壊れた raw"},
        {"stem": "c", "adapter": "pymupdf", "kind": "dsqs_error", "message": "GT が読めない"},
        {"stem": "c", "adapter": "pdfplumber", "kind": "normalize_error", "message": ""},
    ]


def test_find_failures_lists_not_available_only_when_asked() -> None:
    # 未導入はローカルでは普通なので既定では載せず、strict（CI）のときだけ載せる。
    # 理由つきの skipped（テキスト層ありの OCR 系）はどちらでも失敗ではない
    failures = find_failures([_RUN_A, _RUN_C], not_available=True)
    assert [(f["stem"], f["adapter"], f["kind"]) for f in failures] == [
        ("a", "pymupdf", "not available"),
        ("c", "pdftotext", "normalize_error"),
        ("c", "pymupdf", "dsqs_error"),
        ("c", "pdfplumber", "normalize_error"),
    ]
    assert failures[0]["message"] is None


def test_find_failures_lists_missing_adapters_when_names_given() -> None:
    # adapter_names を渡したときだけ、run の adapters に無い名前を missing として拾う（#226）
    failures = find_failures([_RUN_A], adapter_names=["pdftotext", "docling", "marker"])
    assert [(f["stem"], f["adapter"], f["kind"], f["message"]) for f in failures] == [
        ("a", "docling", "missing", None),
        ("a", "marker", "missing", None),
    ]


def test_find_failures_omits_missing_without_adapter_names() -> None:
    # adapter_names を渡さなければ従来どおり missing は出さない（strict でないとき）
    assert find_failures([_RUN_A]) == []


# キーはあるが値が dict でない run.json（例: 壊れた JSON 書き出し）（#246）
_RUN_D: dict = {
    "input": "/tmp/docs/d.pdf",
    "adapters": {"pdftotext": {"status": "ok"}, "pymupdf": None},
}


def test_find_failures_lists_invalid_values_when_names_given() -> None:
    # adapter_names にある名前がキーとして存在するが値が dict でなければ invalid として拾う。
    # #226 の欠落検出はキーの有無しか見ず、値が null のような壊れた結果を見逃していた
    failures = find_failures([_RUN_D], adapter_names=["pdftotext", "pymupdf"])
    assert [(f["stem"], f["adapter"], f["kind"], f["message"]) for f in failures] == [
        ("d", "pymupdf", "invalid", "NoneType"),
    ]


def test_find_failures_omits_invalid_without_adapter_names() -> None:
    # adapter_names を渡さなければ従来どおり invalid は出さない（strict でないとき）
    assert find_failures([_RUN_D]) == []


def test_find_failures_ignores_invalid_value_outside_adapter_names() -> None:
    # adapter_names に無い（未登録の）名前の値が dict でなくても invalid にはしない
    assert find_failures([_RUN_D], adapter_names=["pdftotext"]) == []


def test_render_lists_failures() -> None:
    long = "1 行目\n2|行目 " + "x" * 200
    failures = [
        {"stem": "b", "adapter": "pdfplumber", "kind": "error", "message": long},
        {"stem": "a", "adapter": "pymupdf", "kind": "not available", "message": None},
    ]
    text = render(_RUNS, failures=failures)

    heading = "失敗したアダプタ（2 件）"
    assert _header(text, heading) == "| Document | Parser | Kind | Message |"
    # 改行は空白に、`|` はエスケープし、先頭 80 文字に切る
    message = ("1 行目 2|行目 " + "x" * 200)[:80].replace("|", r"\|")
    assert _rows(text, heading) == [
        f"| b | pdfplumber | error | {message} |",
        "| a | pymupdf | not available | - |",
    ]
    # 概要の直後、DSQS Total の前に置く
    assert text.index("## 失敗したアダプタ") < text.index("## DSQS Total")
    # summary.json には切り詰めずに残す
    assert to_summary(_RUNS, failures=failures)["failures"] == failures


def test_no_failure_section_without_failures() -> None:
    assert "失敗したアダプタ" not in render(_RUNS)
    assert render(_RUNS, [], []) == render(_RUNS)
    assert "failures" not in to_summary(_RUNS, [], [])
