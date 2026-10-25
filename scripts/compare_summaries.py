"""2 つの summary.json（`jdocbench.summary.to_summary` の出力。別エージェントが #269 で作る
公開側の data.json も同じスキーマの予定）を全指標で照合する（#273）。

使い方:

    uv run python scripts/compare_summaries.py <a.json> <b.json> \
        [--allow STEM:ADAPTER] [--include-perf]

文書 stem × アダプタの全組について、既定で status / total / cf / sed / tsf / roa_effective /
jnf を比べる（`--include-perf` で wall_time_sec / peak_rss_kb も加える）。トップレベルの
missing（結果の無い文書のファイル名）・failures（失敗したアダプタ。#207 等）の集合も比べる。
アダプタの結果自体が片方にしか無い組は、全フィールドが差として出る。

`--allow STEM:ADAPTER` で指定した組は、差があっても表には出すが終了コードには数えない
（既知の揺れを許す。例: kanpo × markitdown。docs/project.md #267 の縦書き順の非決定性）。
繰り返し指定できる。missing の差は STEM:ADAPTER に紐づかないため `--allow` の対象外。

差（`--allow` で許していないもの）が 1 件でもあれば終了コード 1、無ければ 0。
"""

import argparse
import json
import math
import sys
from pathlib import Path

_FIELDS = ("status", "total", "cf", "sed", "tsf", "roa_effective", "jnf")
_PERF_FIELDS = ("wall_time_sec", "peak_rss_kb")
_MISSING = "-"


def _is_number(value: object) -> bool:
    """bool を除く数値かどうか。"""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _equal(a: object, b: object) -> bool:
    """数値は浮動小数の誤差を許して比べ、それ以外（文字列・None）は厳密に比べる。"""
    if _is_number(a) and _is_number(b):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def _docs_by_stem(summary: dict) -> dict[str, dict]:
    """summary.json の `documents` を stem → アダプタ別結果 の dict にする。"""
    result: dict[str, dict] = {}
    for doc in summary.get("documents", []):
        adapters = doc.get("adapters")
        result[doc.get("stem")] = adapters if isinstance(adapters, dict) else {}
    return result


def field_diffs(a: dict, b: dict, *, include_perf: bool = False) -> list[dict]:
    """文書 stem × アダプタの全組について、指定フィールドの差を列挙する。

    要素は `{"stem", "adapter", "field", "a", "b"}`。アダプタの結果自体が片方に無ければ
    空 dict として扱うため、その組は全フィールドが差として出る
    （「片方にしか無い組も差として出す」）。
    """
    fields = (*_FIELDS, *_PERF_FIELDS) if include_perf else _FIELDS
    a_docs = _docs_by_stem(a)
    b_docs = _docs_by_stem(b)
    diffs = []
    for stem in sorted(set(a_docs) | set(b_docs)):
        a_adapters = a_docs.get(stem, {})
        b_adapters = b_docs.get(stem, {})
        for adapter in sorted(set(a_adapters) | set(b_adapters)):
            a_entry = a_adapters.get(adapter)
            a_entry = a_entry if isinstance(a_entry, dict) else {}
            b_entry = b_adapters.get(adapter)
            b_entry = b_entry if isinstance(b_entry, dict) else {}
            for field in fields:
                av, bv = a_entry.get(field), b_entry.get(field)
                if not _equal(av, bv):
                    diffs.append(
                        {"stem": stem, "adapter": adapter, "field": field, "a": av, "b": bv}
                    )
    return diffs


def _failure_key(failure: dict) -> tuple[str, str, str, str]:
    return (
        str(failure.get("stem")),
        str(failure.get("adapter")),
        str(failure.get("kind")),
        str(failure.get("message")),
    )


def failure_diffs(a: dict, b: dict) -> list[dict]:
    """トップレベル `failures`（#207 等）の集合の差。要素は失敗の dict に `side` を加えたもの。"""
    a_failures = {_failure_key(f): f for f in a.get("failures", [])}
    b_failures = {_failure_key(f): f for f in b.get("failures", [])}
    diffs = [{**a_failures[key], "side": "A"} for key in sorted(set(a_failures) - set(b_failures))]
    diffs += [{**b_failures[key], "side": "B"} for key in sorted(set(b_failures) - set(a_failures))]
    return diffs


def missing_diffs(a: dict, b: dict) -> list[dict]:
    """トップレベル `missing`（結果の無い文書）の集合の差。要素は `{"document", "side"}`。"""
    a_missing = set(a.get("missing", []))
    b_missing = set(b.get("missing", []))
    diffs = [{"document": doc, "side": "A"} for doc in sorted(a_missing - b_missing)]
    diffs += [{"document": doc, "side": "B"} for doc in sorted(b_missing - a_missing)]
    return diffs


def _allowed(diff: dict, allow: set[tuple[str, str]]) -> bool:
    """差が `--allow` の組に当たるか（stem・adapter の両方が要る。missing は常に False）。"""
    return (diff.get("stem"), diff.get("adapter")) in allow


def _cell(value: object) -> str:
    return _MISSING if value is None else str(value).replace("\n", " ").replace("|", r"\|")


def _table(header: tuple[str, ...], rows: list[tuple[object, ...]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * len(header)) + "|",
    ]
    lines.extend("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows)
    return "\n".join(lines)


def render(
    field_diff_list: list[dict],
    failure_diff_list: list[dict],
    missing_diff_list: list[dict],
    allow: set[tuple[str, str]],
) -> str:
    """差を Markdown にする。差が 1 件も無ければその旨の 1 行にする。"""
    sections = []
    if field_diff_list:
        rows = [
            (
                d["stem"],
                d["adapter"],
                d["field"],
                d["a"],
                d["b"],
                "許可" if _allowed(d, allow) else "",
            )
            for d in field_diff_list
        ]
        sections.append(
            f"## 値の差（{len(field_diff_list)} 件）\n\n"
            + _table(("Stem", "Adapter", "Field", "A", "B", "Allow"), rows)
        )
    if failure_diff_list:
        rows = [
            (d["side"], d.get("stem"), d.get("adapter"), d.get("kind"), d.get("message"))
            for d in failure_diff_list
        ]
        sections.append(
            f"## failures の差（{len(failure_diff_list)} 件）\n\n"
            + _table(("片方のみ", "Stem", "Adapter", "Kind", "Message"), rows)
        )
    if missing_diff_list:
        rows = [(d["side"], d["document"]) for d in missing_diff_list]
        sections.append(
            f"## missing の差（{len(missing_diff_list)} 件）\n\n"
            + _table(("片方のみ", "Document"), rows)
        )
    if not sections:
        return "差はありません。\n"
    return "\n\n".join(sections) + "\n"


def _parse_allow(values: list[str]) -> set[tuple[str, str]]:
    allow = set()
    for value in values:
        if ":" not in value:
            raise ValueError(f"--allow は STEM:ADAPTER の形式で指定してください: {value}")
        stem, adapter = value.split(":", 1)
        allow.add((stem, adapter))
    return allow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("a", help="比べる summary.json（1 つめ）")
    parser.add_argument("b", help="比べる summary.json（2 つめ）")
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="STEM:ADAPTER",
        help="差があっても終了コードに数えない組（繰り返し指定できる）",
    )
    parser.add_argument(
        "--include-perf",
        action="store_true",
        help="wall_time_sec / peak_rss_kb も比べる（既定では比べない）",
    )
    args = parser.parse_args(argv)

    try:
        allow = _parse_allow(args.allow)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    a = json.loads(Path(args.a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))

    diffs = field_diffs(a, b, include_perf=args.include_perf)
    failures = failure_diffs(a, b)
    missing = missing_diffs(a, b)

    print(render(diffs, failures, missing, allow))

    # missing は STEM:ADAPTER に紐づかないため --allow の対象外（常に終了コードに数える）
    unallowed = (
        [d for d in diffs if not _allowed(d, allow)]
        + [f for f in failures if not _allowed(f, allow)]
        + missing
    )
    return 1 if unallowed else 0


if __name__ == "__main__":
    raise SystemExit(main())
