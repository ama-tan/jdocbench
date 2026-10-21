"""run.json から人が読む report.md を作る（企画書 §30）。純関数。ファイル I/O をしない。"""

from pathlib import PurePath

_MISSING = "-"
_SHA_PREFIX = 12
_NOTE_LIMIT = 80
_COLUMNS = (
    "Parser",
    "Version",
    "Status",
    "Wall [s]",
    "sec/page",
    "Peak RSS [MiB]",
    "Chars",
    "Tables",
    "DSQS",
    "Notes",
)
_DSQS_COLUMNS = ("Parser", "CF", "SED", "TSF", "ROA", "Total", "JNF")
_DSQS_NOTES = (
    "- CF = 文書の全テキスト（表の行を含む）の再現率\n"
    "- SED = 表を検出できたか（表数の F1）／"
    "TSF = 検出できた表の中身の再現率（分割・結合には寛容）\n"
    "- ROA = 行の並び順（一致した行だけで計算）。Total に入れた値を示す: "
    "GT 行の対応率（roa_match_rate）が 0.15 未満、または対応行が無いときは 0。"
    "生の値は metrics/<adapter>.json の roa\n"
    "- JNF = 日本語表記の忠実度（全角/半角・丸数字・旧字体・△ など。docs/jnf.md）。"
    "**Total には入れない**。`-` は表記対象文字が無い（None）\n"
)
_JNF_NOTES = (
    "各セルは 表記差（folded）/ 分母（exact + folded）。JNF = 1 − 表記差 / 分母。"
    "GT 行はクラス文字の数（整列前）。"
    "出力側だけの表記差も数えるため、表記差が GT の数を超えることがある。"
    "欠落した文字は分母に入れない（CF の領域）\n"
)


def _cell(value: object) -> str:
    """Markdown の表に入れても壊れない文字列にする（改行は空白、`|` はエスケープ）。"""
    return str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("|", r"\|")


def _note_text(value: object, prefix: str = "") -> str:
    """Notes 用に先頭 80 文字へ切り詰め、表を壊さない形にする。"""
    text = str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return _cell(prefix + text[:_NOTE_LIMIT])


def _is_number(value: object) -> bool:
    """bool を除く数値かどうか。"""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _round(value: object, digits: int) -> str:
    """数値を指定桁で丸める。数値でなければ `-`。"""
    return f"{value:.{digits}f}" if _is_number(value) else _MISSING


def _integer(value: object) -> str:
    """整数として表示する。数値でなければ `-`。"""
    return str(int(value)) if _is_number(value) else _MISSING


def _mib(kb: object) -> str:
    """KB を MiB（小数 1 桁）にする。数値でなければ `-`。"""
    return f"{kb / 1024:.1f}" if _is_number(kb) else _MISSING


def _kb(size_bytes: object) -> str:
    """バイト数を KB（小数 1 桁）にする。数値でなければ `-`。"""
    return f"{size_bytes / 1024:.1f} KB" if _is_number(size_bytes) else _MISSING


def _gib(size_bytes: object) -> str:
    """バイト数を GiB（小数 1 桁）にする。数値でなければ `-`。"""
    return f"{size_bytes / 1024**3:.1f} GiB" if _is_number(size_bytes) else _MISSING


def _fonts(families: object) -> str:
    """日本語フォントの一覧。空リストは「なし」（未記録の `-` と区別する）。"""
    if not isinstance(families, list):
        return _MISSING
    return _cell(", ".join(families)) if families else "なし"


def _text(value: object) -> str:
    """文字列として表示する。空・None なら `-`。"""
    if value is None or value == "":
        return _MISSING
    return _cell(value)


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    """ヘッダと区切り行つきの Markdown 表を作る。"""
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * len(header)) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _format_extra(extra: dict) -> str:
    """extra（実験条件）を `key=value, key=value` の形にする。bool は小文字。"""
    parts = []
    for key, value in extra.items():
        if isinstance(value, bool):
            value = "true" if value else "false"
        parts.append(f"{key}={value}")
    return _note_text(", ".join(parts))


def _notes(result: dict) -> str:
    """アダプタ 1 件の Notes セルを作る。"""
    status = result.get("status")
    if status == "skipped":
        return _text(result.get("reason"))
    if status == "error":
        return _note_text(result.get("message", ""))
    normalize_error = result.get("normalize_error")
    if normalize_error:
        return _note_text(normalize_error, prefix="正規化失敗: ")
    extra = result.get("extra")
    if isinstance(extra, dict) and extra:
        return _format_extra(extra)
    return _MISSING


def _dsqs(result: dict) -> dict:
    """アダプタ 1 件の DSQS-JP スコア。評価していなければ空 dict。"""
    scores = result.get("dsqs")
    return scores if isinstance(scores, dict) else {}


def _adapter_row(name: str, result: dict) -> tuple[str, ...]:
    """アダプタ 1 件の比較表の行を作る。"""
    return (
        _cell(name),
        _text(result.get("version")),
        _text(result.get("status")),
        _round(result.get("wall_time_sec"), 3),
        _round(result.get("sec_per_page"), 3),
        _mib(result.get("peak_rss_kb")),
        _integer(result.get("chars")),
        _integer(result.get("tables")),
        _round(_dsqs(result).get("total"), 1),
        _notes(result),
    )


def _dsqs_section(run: dict, adapters: dict) -> str:
    """DSQS-JP のサブスコア表（企画書 §18「Sub-score を必ず公開する」）。

    Ground Truth が無い run では空文字を返し、節ごと出さない。
    """
    ground_truth = run.get("ground_truth")
    if not ground_truth:
        return ""
    gt_sha256 = str(run.get("ground_truth_sha256") or "")
    gt_label = (
        f"{ground_truth} / SHA-256: {gt_sha256[:_SHA_PREFIX]}…" if gt_sha256 else str(ground_truth)
    )
    rows = [
        (
            _cell(name),
            _round(scores.get("cf"), 3),
            _round(scores.get("sed"), 3),
            _round(scores.get("tsf"), 3),
            _round(scores.get("roa_effective"), 3),
            _round(scores.get("total"), 1),
            _round(scores.get("jnf"), 3),
        )
        for name, result in adapters.items()
        if (scores := _dsqs(result))
    ]
    if not rows:
        return ""
    return (
        f"\n## DSQS-JP（Ground Truth: {_cell(gt_label)}）\n"
        f"{_table(_DSQS_COLUMNS, rows)}\n"
        "\n"
        f"{_DSQS_NOTES}"
        f"{_jnf_section(adapters)}"
    )


def _jnf_classes(result: dict) -> dict:
    """アダプタ 1 件の JNF の内訳（クラス → {gt, exact, folded}）。無ければ空 dict。"""
    classes = _dsqs(result).get("jnf_classes")
    return classes if isinstance(classes, dict) else {}


def _count(counts: object, key: str) -> int:
    """内訳 1 クラスの数。数値でなければ 0。"""
    value = counts.get(key) if isinstance(counts, dict) else None
    return int(value) if _is_number(value) else 0


def _jnf_section(adapters: dict) -> str:
    """JNF の内訳表（分母が見えるように）。どのクラスにも数が無ければ空文字。

    列は 1 件でも数のあるクラスだけ。各セルは `folded/(exact + folded)`。
    """
    evaluated = {name: classes for name, r in adapters.items() if (classes := _jnf_classes(r))}
    names: list[str] = []
    for classes in evaluated.values():
        for klass, counts in classes.items():
            total = sum(_count(counts, key) for key in ("gt", "exact", "folded"))
            if total and klass not in names:
                names.append(klass)
    if not names:
        return ""

    def cell(counts: object) -> str:
        folded = _count(counts, "folded")
        return f"{folded}/{_count(counts, 'exact') + folded}"

    # GT 側のクラス文字の数は GT だけで決まる（どのアダプタの内訳でも同じ）
    first = next(iter(evaluated.values()))
    rows = [("GT", "-", "-", *(str(_count(first.get(k), "gt")) for k in names))]
    for name, classes in evaluated.items():
        folded = sum(_count(counts, "folded") for counts in classes.values())
        exact = sum(_count(counts, "exact") for counts in classes.values())
        rows.append(
            (
                _cell(name),
                _round(_dsqs(adapters[name]).get("jnf"), 3),
                f"{folded}/{exact + folded}",
                *(cell(classes.get(k)) for k in names),
            )
        )
    return (
        "\n### JNF の内訳\n"
        f"{_table(('Parser', 'JNF', '全体', *(_cell(k) for k in names)), rows)}\n"
        "\n"
        f"{_JNF_NOTES}"
    )


def render(run: dict) -> str:
    """run.json 相当の dict から report.md の本文を作る。"""
    name = PurePath(str(run.get("input", ""))).name or _MISSING
    sha256 = str(run.get("sha256", ""))
    pages = run.get("pages")
    ground_truth = run.get("ground_truth")
    gt_sha256 = str(run.get("ground_truth_sha256") or "")

    profile = _table(
        ("項目", "値"),
        [
            ("ファイル", _cell(name)),
            ("SHA-256", f"{sha256[:_SHA_PREFIX]}…" if sha256 else _MISSING),
            ("ページ数", _integer(pages) if _is_number(pages) else "不明"),
            ("サイズ", _kb(run.get("size_bytes"))),
            # GT が無い run（または評価が全滅した run）でも版が追えるよう常に出す（企画書 §49）
            ("Ground Truth", _cell(ground_truth) if ground_truth else _MISSING),
            (
                "GT SHA-256",
                f"{gt_sha256[:_SHA_PREFIX]}…" if gt_sha256 else _MISSING,
            ),
        ],
    )
    environment = _table(
        ("項目", "値"),
        [
            ("jdocbench", _text(run.get("jdocbench_version"))),
            ("Python", _text(run.get("python"))),
            ("OS", _text(run.get("platform"))),
            ("CPU", _text(run.get("cpu"))),
            ("RAM", _gib(run.get("ram_total_bytes"))),
            ("日本語フォント", _fonts(run.get("fonts_ja"))),
            ("実行日時", _text(run.get("started_at"))),
        ],
    )
    adapters: dict = run.get("adapters") or {}
    comparison = _table(
        _COLUMNS,
        [_adapter_row(adapter_name, result) for adapter_name, result in adapters.items()],
    )

    return (
        f"# JDocBench Report: {_cell(name)}\n"
        "\n"
        "## Document Profile\n"
        f"{profile}\n"
        "\n"
        "## 実行環境\n"
        f"{environment}\n"
        "\n"
        "## Parser 比較\n"
        f"{comparison}\n"
        f"{_dsqs_section(run, adapters)}"
        "\n"
        "raw 出力は raw/<adapter>/、正規化結果は normalized/<adapter>.json を参照。\n"
    )
