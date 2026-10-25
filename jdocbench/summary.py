"""複数文書の run.json から文書横断の summary.md / summary.json を作る（企画書 §4.3）。

純関数だけを置く。ファイル I/O をしない（書き出しは cli.py が行う）。
評価単位は Document × Parser で、「Parser A が 1 位」ではなく
「どの文書にどの Parser か」を 1 表で見られるようにするのが目的。
"""

from collections.abc import Callable, Mapping, Sequence
from pathlib import PurePath

_MISSING = "-"
_TOTAL_DIGITS = 1
_WALL_DIGITS = 3
_RSS_DIGITS = 1
_MEAN_DIGITS = 3
# Parser 平均の ROA は Total に入れた値（roa_effective）の平均。生の roa は summary.json に残す
_SUBSCORES = ("total", "cf", "sed", "tsf", "roa_effective")
# JNF は Total に入れない別の列（docs/jnf.md）。平均は値のある文書だけで取る（None は除く）
_MEAN_KEYS = ("total", "jnf", "cf", "sed", "tsf", "roa_effective")
_MEAN_COLUMNS = ("Parser", "n", "Total", "JNF", "CF", "SED", "TSF", "ROA")
_JNF_DIGITS = 3
_NOT_AVAILABLE = "not available"  # cli.py が未導入のアダプタに付ける reason
_MISSING_ADAPTER = "missing"  # アダプタ自体が run.json から欠けている（#226）
_INVALID_ADAPTER = "invalid"  # 登録アダプタの値が dict でない（#246）
_FAILURE_MESSAGE_LIMIT = 80  # report.md の Notes と同じ長さ
_FAILURE_COLUMNS = ("Document", "Parser", "Kind", "Message")
_FAILURE_NOTE = (
    "Kind: error = アダプタの実行失敗／normalize_error = 正規化の失敗／"
    "dsqs_error = DSQS-JP 計算の失敗／"
    "not available = 未導入／"
    "missing = run.json にアダプタの結果が丸ごと無い／"
    "invalid = run.json のアダプタの値が dict でない"
    "（not available / missing / invalid は `summary --strict` のときだけ載せる）。"
    f"Message は先頭 {_FAILURE_MESSAGE_LIMIT} 文字（全文は各文書の run.json）。\n"
)
_MEAN_NOTE = (
    "CF = 文書の全テキスト（表の行を含む）の再現率／"
    "SED = 表を検出できたか（表数の F1）／"
    "TSF = 検出できた表の中身の再現率（分割・結合には寛容）／"
    "ROA = 行の並び順（一致した行だけで計算）。Total に入れた値の平均: "
    "GT 行の対応率が 0.15 未満、または対応行が無い文書では 0 として数える。"
    "JNF = 日本語表記の忠実度（全角/半角・丸数字・旧字体・△ など。docs/jnf.md）。"
    "Total には入れない。表記対象文字の無い文書（None）は平均から除く。\n"
)


def _cell(value: object) -> str:
    """Markdown の表に入れても壊れない文字列にする（改行は空白、`|` はエスケープ）。"""
    return str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("|", r"\|")


def _is_number(value: object) -> bool:
    """bool を除く数値かどうか。"""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _round(value: object, digits: int) -> str:
    """数値を指定桁で丸める。数値でなければ `-`。"""
    return f"{value:.{digits}f}" if _is_number(value) else _MISSING


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    """ヘッダと区切り行つきの Markdown 表を作る。"""
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * len(header)) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _adapters(run: dict) -> dict:
    """run 1 件のアダプタ別の結果。"""
    adapters = run.get("adapters")
    return adapters if isinstance(adapters, dict) else {}


def _result(run: dict, name: str) -> dict:
    """run 1 件の特定アダプタの結果。無ければ空 dict。"""
    result = _adapters(run).get(name)
    return result if isinstance(result, dict) else {}


def _dsqs(result: dict) -> dict:
    """アダプタ 1 件の DSQS-JP スコア。評価していなければ空 dict。"""
    scores = result.get("dsqs")
    return scores if isinstance(scores, dict) else {}


def _adapter_names(runs: list[dict]) -> list[str]:
    """全 run に出てくるアダプタ名を出現順にそろえる（列の並び）。"""
    names: list[str] = []
    for run in runs:
        for name in _adapters(run):
            if name not in names:
                names.append(name)
    return names


def _document_name(run: dict) -> str:
    """run の `input` からファイル名を取る。"""
    return PurePath(str(run.get("input", ""))).name or _MISSING


def _matrix(
    runs: list[dict],
    names: list[str],
    value: Callable[[dict], object],
    digits: int,
) -> str:
    """行 = 文書、列 = Parser の表を作る。`value` は結果 dict から値を取り出す関数。"""
    rows = [
        (
            _cell(_document_name(run)),
            *(_round(value(_result(run, name)), digits) for name in names),
        )
        for run in runs
    ]
    return _table(("Document", *(_cell(name) for name in names)), rows)


def _peak_rss_mib(result: dict) -> float | None:
    """Peak RSS を KB から MiB にする。数値でなければ None。"""
    kb = result.get("peak_rss_kb")
    return kb / 1024 if _is_number(kb) else None


def _mean(values: list[float]) -> str:
    """平均を小数 3 桁で表す。値が無ければ `-`。"""
    return _round(sum(values) / len(values), _MEAN_DIGITS) if values else _MISSING


def mean_values(scores: Sequence[Mapping]) -> tuple[str, ...] | None:
    """Parser 1 件の平均（n / Total / JNF / CF / SED / TSF / ROA の文字列）。

    `scores` は文書ごとの DSQS-JP スコア（評価していない文書は空 dict）。空でない文書が
    1 件も無ければ None。公開サイト（jdocbench.sitehtml）も同じ式を使う。
    """
    scores = [score for score in scores if score]
    if not scores:
        return None
    means = [
        _mean([value for score in scores if _is_number(value := score.get(key))])
        for key in _MEAN_KEYS
    ]
    return (str(len(scores)), *means)


def _mean_row(runs: list[dict], name: str) -> tuple[str, ...] | None:
    """Parser 1 件の平均行。DSQS を計算した文書が 1 件も無ければ None。"""
    values = mean_values([_dsqs(_result(run, name)) for run in runs])
    return None if values is None else (_cell(name), *values)


def find_failures(
    runs: list[dict], *, not_available: bool = False, adapter_names: Sequence[str] = ()
) -> list[dict]:
    """失敗したアダプタを文書順 × アダプタ順に列挙する（#207、#226、#246）。

    要素は `{"stem", "adapter", "kind", "message"}`。kind は `error` / `normalize_error` /
    `dsqs_error` / `not available` / `missing` / `invalid`。未導入（not available）は
    `not_available` のときだけ含める（ローカルでは未導入が普通のため）。理由つきの
    skipped（テキスト層ありの OCR 系等）は失敗ではないので含めない。normalize_error /
    dsqs_error は `status: ok` の結果に付くため status とは独立に見る。
    `adapter_names` を渡すと、各 run の `adapters` に無い名前を `missing`（message は None）
    として含める。さらに `adapter_names` にある名前の値が dict でなければ `invalid`
    （message はその値の型名。`#226` の欠落検出はキーの有無しか見ず、値が null などの
    壊れた run.json を見逃していた）として含める。どちらも登録済みアダプタが対象で、
    CI の `--strict` のときだけ渡す。
    """
    failures: list[dict] = []
    for run in runs:
        stem = PurePath(str(run.get("input", ""))).stem
        adapters = _adapters(run)
        for name, result in adapters.items():
            if not isinstance(result, dict):
                if name in adapter_names:
                    failures.append(
                        {
                            "stem": stem,
                            "adapter": name,
                            "kind": _INVALID_ADAPTER,
                            "message": type(result).__name__,
                        }
                    )
                continue
            status = result.get("status")
            found: list[tuple[str, object]] = []
            if status == "error":
                found.append(("error", result.get("message")))
            elif status == "skipped" and result.get("reason") == _NOT_AVAILABLE and not_available:
                found.append((_NOT_AVAILABLE, None))
            for key in ("normalize_error", "dsqs_error"):
                # 例外の文字列が空でも失敗は失敗なので、値ではなくキーの有無で見る
                if key in result:
                    found.append((key, result[key]))
            failures.extend(
                {"stem": stem, "adapter": name, "kind": kind, "message": message}
                for kind, message in found
            )
        failures.extend(
            {"stem": stem, "adapter": name, "kind": _MISSING_ADAPTER, "message": None}
            for name in adapter_names
            if name not in adapters
        )
    return failures


def short_message(message: object) -> str:
    """失敗の message を 1 行に詰めて先頭だけにする（空白の連続は 1 つにする）。"""
    return " ".join(str(message).split())[:_FAILURE_MESSAGE_LIMIT]


def _failure_section(failures: Sequence[dict]) -> str:
    """失敗したアダプタの節。失敗が無ければ空文字。"""
    if not failures:
        return ""
    rows = [
        (
            _cell(failure["stem"]),
            _cell(failure["adapter"]),
            _cell(failure["kind"]),
            # エスケープ後に切ると `\|` が壊れるため、先に切ってからエスケープする
            _cell(short_message(message))
            if (message := failure.get("message")) is not None
            else _MISSING,
        )
        for failure in failures
    ]
    return (
        f"## 失敗したアダプタ（{len(failures)} 件）\n"
        f"{_table(_FAILURE_COLUMNS, rows)}\n"
        "\n"
        f"{_FAILURE_NOTE}"
        "\n"
    )


def render(runs: list[dict], missing: Sequence[str] = (), failures: Sequence[dict] = ()) -> str:
    """run.json 相当の dict のリストから summary.md の本文を作る。

    `missing` は結果（run.json）の無い文書のファイル名。あれば概要の下に載せる。
    `failures` は `find_failures` の結果。あれば概要の直後に節を設ける（無ければ節を出さない）。
    """
    names = _adapter_names(runs)
    parsers = "、".join(names) if names else _MISSING
    overview = f"文書 {len(runs)} 件 × Parser {len(names)} 件（{parsers}）"
    if missing:
        overview += f"\n\n**結果の無い文書 {len(missing)} 件**: " + "、".join(
            _cell(name) for name in missing
        )

    total = _matrix(runs, names, lambda r: _dsqs(r).get("total"), _TOTAL_DIGITS)
    jnf = _matrix(runs, names, lambda r: _dsqs(r).get("jnf"), _JNF_DIGITS)
    wall = _matrix(runs, names, lambda r: r.get("wall_time_sec"), _WALL_DIGITS)
    rss = _matrix(runs, names, _peak_rss_mib, _RSS_DIGITS)
    mean_rows = [row for name in names if (row := _mean_row(runs, name)) is not None]
    means = (
        f"\n## Parser 平均（Ground Truth のある文書のみ）\n{_table(_MEAN_COLUMNS, mean_rows)}\n"
        f"\n{_MEAN_NOTE}"
        if mean_rows
        else ""
    )

    return (
        "# JDocBench Summary\n"
        "\n"
        f"{overview}\n"
        "\n"
        f"{_failure_section(failures)}"
        "## DSQS Total\n"
        f"{total}\n"
        "\n"
        "## JNF（Total に入れない）\n"
        f"{jnf}\n"
        "\n"
        "## Wall [s]\n"
        f"{wall}\n"
        "\n"
        "## Peak RSS [MiB]\n"
        f"{rss}\n"
        f"{means}"
        "\n"
        "詳細は各文書の report.md を参照。\n"
    )


def _summary_adapter(result: dict) -> dict:
    """summary.json のアダプタ 1 件分。無い値は None。"""
    scores = _dsqs(result)
    entry: dict[str, object] = {"status": result.get("status")}
    entry.update({key: scores.get(key) for key in ("roa", *_SUBSCORES, "jnf")})
    entry["wall_time_sec"] = result.get("wall_time_sec")
    entry["peak_rss_kb"] = result.get("peak_rss_kb")
    return entry


def to_summary(
    runs: list[dict], missing: Sequence[str] = (), failures: Sequence[dict] = ()
) -> dict:
    """run.json 相当の dict のリストから summary.json の中身を作る。

    結果の無い文書があれば `missing` にファイル名を、失敗したアダプタがあれば `failures` に
    `find_failures` の結果（message は切り詰めない）を入れる（無ければキー自体を出さない）。
    """
    summary: dict[str, object] = {
        "documents": [
            {
                "stem": PurePath(str(run.get("input", ""))).stem,
                "pages": run.get("pages"),
                "ground_truth_sha256": run.get("ground_truth_sha256"),
                "adapters": {
                    name: _summary_adapter(result if isinstance(result, dict) else {})
                    for name, result in _adapters(run).items()
                },
            }
            for run in runs
        ]
    }
    if missing:
        summary["missing"] = list(missing)
    if failures:
        summary["failures"] = [dict(failure) for failure in failures]
    return summary
