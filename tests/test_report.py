from jdocbench.report import render

_RUN: dict = {
    "jdocbench_version": "0.1.0",
    "input": "/tmp/docs/sample.pdf",
    "sha256": "0123456789abcdef0123456789abcdef",
    "size_bytes": 2048,
    "pages": 6,
    "started_at": "2026-01-02T03:04:05+00:00",
    "python": "3.12.3",
    "platform": "Linux-6.1.0-x86_64",
    "cpu": "Test CPU @ 3.00GHz",
    "ram_total_bytes": 16 * 1024**3,
    "fonts_ja": ["Noto Sans CJK JP", "Noto Serif CJK JP"],
    "adapters": {
        "ok_adapter": {
            "status": "ok",
            "version": "1.0.0",
            "output_dir": "raw/ok_adapter",
            "wall_time_sec": 1.23456,
            "peak_rss_kb": 51200,
            "sec_per_page": 0.2057,
            "chars": 1234,
            "tables": 2,
        },
        "skipped_adapter": {"status": "skipped", "reason": "not available"},
        "error_adapter": {
            "status": "error",
            "version": "2.0.0",
            "message": "変換に失敗しました: a | b\n2 行目",
        },
        "normalize_failed": {
            "status": "ok",
            "version": "3.0.0",
            "output_dir": "raw/normalize_failed",
            "wall_time_sec": 0.5,
            "peak_rss_kb": 1024,
            "normalize_error": "正規化に失敗しました",
        },
    },
}


def _comparison_rows(text: str) -> list[str]:
    """Parser 比較表のデータ行（ヘッダと区切り行を除く）を返す。"""
    # 後ろに DSQS-JP の節が続くことがあるので、次の見出しの手前で切る
    section = text.split("## Parser 比較\n", 1)[1].split("\n## ", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("|")]
    return lines[2:]


def _row(text: str, name: str) -> str:
    return next(line for line in _comparison_rows(text) if line.startswith(f"| {name} |"))


def test_title_uses_input_filename() -> None:
    assert render(_RUN).startswith("# JDocBench Report: sample.pdf\n")


def test_document_profile_and_environment() -> None:
    text = render(_RUN)

    assert "| ファイル | sample.pdf |" in text
    assert "| SHA-256 | 0123456789ab… |" in text
    assert "| ページ数 | 6 |" in text
    assert "| サイズ | 2.0 KB |" in text
    assert "| jdocbench | 0.1.0 |" in text
    assert "| Python | 3.12.3 |" in text
    assert "| OS | Linux-6.1.0-x86_64 |" in text
    assert "| CPU | Test CPU @ 3.00GHz |" in text
    assert "| RAM | 16.0 GiB |" in text
    assert "| 日本語フォント | Noto Sans CJK JP, Noto Serif CJK JP |" in text
    assert "| 実行日時 | 2026-01-02T03:04:05+00:00 |" in text
    # GT が無い run では `-`
    assert "| Ground Truth | - |" in text
    assert "| GT SHA-256 | - |" in text


def test_fonts_ja_distinguishes_none_from_unrecorded() -> None:
    """日本語フォントが無い環境（#157 の CI）は「なし」、記録の無い古い run.json は `-`。"""
    assert "| 日本語フォント | なし |" in render({**_RUN, "fonts_ja": []})
    unrecorded = {key: value for key, value in _RUN.items() if key != "fonts_ja"}
    assert "| 日本語フォント | - |" in render(unrecorded)


def test_document_profile_shows_ground_truth_sha256_even_without_dsqs_rows() -> None:
    """GT は存在するが評価が1件も成功しない run でも GT の版は report.md に残る（Codex 指摘）。"""
    run = {
        **_RUN,
        "ground_truth": "groundtruth/sample.json",
        "ground_truth_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcd",
    }

    text = render(run)

    assert "| Ground Truth | groundtruth/sample.json |" in text
    assert "| GT SHA-256 | 0123456789ab… |" in text
    # この run の adapters には dsqs が無いので DSQS-JP 節自体は出ない
    assert "## DSQS-JP" not in text


def test_every_adapter_gets_one_row() -> None:
    assert len(_comparison_rows(render(_RUN))) == 4


def test_ok_row_rounds_numbers() -> None:
    row = _row(render(_RUN), "ok_adapter")

    assert row == "| ok_adapter | 1.0.0 | ok | 1.235 | 0.206 | 50.0 | 1234 | 2 | - | - |"


def test_skipped_row_shows_reason_and_dashes() -> None:
    row = _row(render(_RUN), "skipped_adapter")

    assert row == "| skipped_adapter | - | skipped | - | - | - | - | - | - | not available |"


def test_error_row_escapes_pipe_and_newline() -> None:
    row = _row(render(_RUN), "error_adapter")

    assert row == (
        "| error_adapter | 2.0.0 | error | - | - | - | - | - | - | "
        "変換に失敗しました: a \\| b 2 行目 |"
    )
    # 列数はヘッダ（10 列）と一致し、表が壊れていない
    assert row.count("|") - row.count("\\|") == 11


def test_error_message_is_truncated_to_80_chars() -> None:
    run = {**_RUN, "adapters": {"long": {"status": "error", "message": "あ" * 200}}}
    row = _row(render(run), "long")

    assert "あ" * 80 in row
    assert "あ" * 81 not in row


def test_normalize_error_is_shown_on_ok_row() -> None:
    row = _row(render(_RUN), "normalize_failed")

    assert row == (
        "| normalize_failed | 3.0.0 | ok | 0.500 | - | 1.0 | - | - | - | "
        "正規化失敗: 正規化に失敗しました |"
    )


def test_extra_is_shown_as_key_value_notes() -> None:
    run = {
        **_RUN,
        "adapters": {
            "docling": {
                "status": "ok",
                "version": "1.0.0",
                "wall_time_sec": 1.0,
                "extra": {"ocr": False, "page_batch_size": 1, "models": "4.0.3"},
            }
        },
    }
    row = _row(render(run), "docling")

    assert row.endswith("| ocr=false, page_batch_size=1, models=4.0.3 |")


def test_extra_is_truncated_to_80_chars() -> None:
    run = {
        **_RUN,
        "adapters": {"long": {"status": "ok", "extra": {"k": "あ" * 200}}},
    }
    row = _row(render(run), "long")

    assert ("k=" + "あ" * 78) in row
    assert ("k=" + "あ" * 79) not in row


def test_extra_is_ignored_when_normalize_error_present() -> None:
    run = {
        **_RUN,
        "adapters": {
            "docling": {
                "status": "ok",
                "normalize_error": "正規化に失敗しました",
                "extra": {"ocr": False},
            }
        },
    }
    row = _row(render(run), "docling")

    assert "正規化失敗: 正規化に失敗しました" in row
    assert "ocr=" not in row


def test_pages_none_is_unknown() -> None:
    assert "| ページ数 | 不明 |" in render({**_RUN, "pages": None})


def test_ends_with_reference_line_and_newline() -> None:
    text = render(_RUN)

    assert text.endswith(
        "raw 出力は raw/<adapter>/、正規化結果は normalized/<adapter>.json を参照。\n"
    )


_DSQS_RUN: dict = {
    **_RUN,
    "ground_truth": "groundtruth/sample.json",
    "adapters": {
        "scored": {
            **_RUN["adapters"]["ok_adapter"],
            # ROA 列には生の roa ではなく Total に入れた roa_effective を出す
            "dsqs": {
                "cf": 0.9123,
                "sed": 0.5,
                "tsf": 0.25,
                "roa": 0.9,
                "roa_effective": 0.75,
                "total": 55.4,
                "jnf": 0.75,
                "jnf_classes": {
                    "fullwidth-alnum": {"gt": 4, "exact": 3, "folded": 1},
                    "fullwidth-symbol": {"gt": 0, "exact": 0, "folded": 0},
                    # 出力側だけの表記差（GT は新字体）
                    "kyujitai": {"gt": 0, "exact": 0, "folded": 1},
                    "ditto": {"gt": 0, "exact": 0, "folded": 0},
                },
            },
        },
        "no_table_gt": {
            **_RUN["adapters"]["ok_adapter"],
            "dsqs": {
                "cf": 1.0,
                "sed": 1.0,
                "tsf": None,
                "roa": 1.0,
                "roa_effective": 1.0,
                "total": 100.0,
                "jnf": 1.0,
                "jnf_classes": {
                    "fullwidth-alnum": {"gt": 4, "exact": 4, "folded": 0},
                    "fullwidth-symbol": {"gt": 0, "exact": 0, "folded": 0},
                    "kyujitai": {"gt": 0, "exact": 0, "folded": 0},
                    "ditto": {"gt": 0, "exact": 0, "folded": 0},
                },
            },
        },
        "not_evaluated": _RUN["adapters"]["skipped_adapter"],
    },
}


def test_dsqs_column_shows_total_or_dash() -> None:
    text = render(_DSQS_RUN)

    assert _row(text, "scored").endswith("| 1234 | 2 | 55.4 | - |")
    assert _row(text, "not_evaluated").endswith("| - | - | - | not available |")


def test_dsqs_subscore_table_is_shown_with_ground_truth_path() -> None:
    text = render(_DSQS_RUN)

    assert "## DSQS-JP（Ground Truth: groundtruth/sample.json）" in text
    # JNF は Total の隣に別の列として出す（Total には入れない）
    assert "| Parser | CF | SED | TSF | ROA | Total | JNF |" in text
    assert "| scored | 0.912 | 0.500 | 0.250 | 0.750 | 55.4 | 0.750 |" in text
    # GT に表が無くて TSF が None のときは `-`
    assert "| no_table_gt | 1.000 | 1.000 | - | 1.000 | 100.0 | 1.000 |" in text
    # 評価していないアダプタはサブスコア表に出ない
    assert "| not_evaluated |" not in text.split("## DSQS-JP", 1)[1]


def test_dsqs_heading_shows_ground_truth_sha256_prefix() -> None:
    run = {
        **_DSQS_RUN,
        "ground_truth_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcd",
    }

    text = render(run)

    assert "## DSQS-JP（Ground Truth: groundtruth/sample.json / SHA-256: 0123456789ab…）" in text


def test_dsqs_section_has_cf_sed_tsf_roa_notes() -> None:
    text = render(_DSQS_RUN)

    assert "- CF = 文書の全テキスト（表の行を含む）の再現率" in text
    assert "SED = 表を検出できたか（表数の F1）／" in text
    assert "TSF = 検出できた表の中身の再現率（分割・結合には寛容）" in text
    assert "ROA = 行の並び順（一致した行だけで計算）。Total に入れた値を示す" in text
    assert "対応率（roa_match_rate）が 0.15 未満、または対応行が無いときは 0" in text
    assert "生の値は metrics/<adapter>.json の roa" in text


def test_no_dsqs_section_without_ground_truth() -> None:
    text = render(_RUN)

    assert "## DSQS-JP" not in text
    # DSQS 列（Notes の 1 つ手前）は全アダプタで `-`
    assert all(row.split(" | ")[8] == "-" for row in _comparison_rows(text))


def test_jnf_breakdown_shows_denominators() -> None:
    text = render(_DSQS_RUN)
    section = text.split("### JNF の内訳\n", 1)[1]

    # 列は 1 件でも数のあるクラスだけ。セルは 表記差 / 分母（exact + folded）
    assert "| Parser | JNF | 全体 | fullwidth-alnum | kyujitai |" in section
    assert "| GT | - | - | 4 | 0 |" in section
    assert "| scored | 0.750 | 2/5 | 1/4 | 1/1 |" in section
    assert "| no_table_gt | 1.000 | 0/4 | 0/4 | 0/0 |" in section
    assert "| not_evaluated |" not in section
    assert "JNF = 1 − 表記差 / 分母" in section
    assert "JNF = 日本語表記の忠実度" in text


def test_jnf_breakdown_is_omitted_without_jnf() -> None:
    run = {
        **_DSQS_RUN,
        "adapters": {
            "old": {
                **_RUN["adapters"]["ok_adapter"],
                # JNF 導入前の run.json（jnf が無い）でも壊れない
                "dsqs": {"cf": 1.0, "sed": 1.0, "tsf": None, "roa_effective": 1.0, "total": 100.0},
            }
        },
    }
    text = render(run)

    assert "| old | 1.000 | 1.000 | - | 1.000 | 100.0 | - |" in text
    assert "### JNF の内訳" not in text
