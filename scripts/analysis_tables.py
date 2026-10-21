"""分析レポート（#175、結果側リポジトリ（ama-tan/jdoc-bench、非公開）の docs/analysis.md）の
表の元を CI の結果から出す。

使い方（CI アーティファクト `ci-results-full` を展開したディレクトリを渡す）:

    uv run python scripts/analysis_tables.py <results-dir>

入力は `<results-dir>/summary.json`（文書 × アダプタの status / total / cf / sed / tsf /
roa_effective / wall_time_sec / peak_rss_kb）と、あれば
`<results-dir>/<stem>/metrics/<adapter>.json` の `roa_match_rate`。出力は Markdown を標準出力へ:

- Level ごとの最良 Total（文書ごと。Level 内で Total 最大のアダプタ）
- 文書 × アダプタのサブスコア（CF / SED / TSF / Total 用の ROA / ROA の対応率）
- アダプタごとの Wall・Peak RSS の範囲と、docling・ppstructure の Wall の pdfplumber 比

結果側リポジトリの docs/analysis.md の数値は run 37876279369（Sprint 13 最終）の
`ci-results-full` から出した。
外部 API もモデルも使わない。router_eval.py と同じく、CI の結果を読む一回限りの分析なので
CLI には入れない。
"""

import argparse
import json
import sys
from pathlib import Path

# 企画書 §7 の技術レベル
LEVELS = {
    "pdftotext": 0,
    "pdf2json": 0,
    "pdfplumber": 1,
    "pymupdf": 1,
    "camelot": 1,
    "camelot_stream": 1,
    "docling": 2,
    "docling_ocr": 2,
    "ppstructure": 2,
}


def _ok(entry: dict | None) -> bool:
    return bool(entry) and entry.get("status") == "ok" and entry.get("total") is not None


def _f(value, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def level_best(adapters: dict) -> dict[int, tuple[str, float] | None]:
    """Level ごとに Total 最大のアダプタ（同点は先に出たもの）。ok が無い Level は None。"""
    best: dict[int, tuple[str, float] | None] = {0: None, 1: None, 2: None}
    for name, level in LEVELS.items():
        entry = adapters.get(name)
        if _ok(entry) and (best[level] is None or entry["total"] > best[level][1]):
            best[level] = (name, entry["total"])
    return best


def _match_rate(results: Path, stem: str, name: str) -> float | None:
    path = results / stem / "metrics" / f"{name}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("roa_match_rate")


def render(documents: list[dict], results: Path) -> str:
    lines = [
        "## Level ごとの最良 Total",
        "",
        "| 文書 | Level 0 | Level 1 | Level 2 |",
        "|---|---|---|---|",
    ]
    for doc in documents:
        best = level_best(doc["adapters"])
        cells = [
            "-" if best[lv] is None else f"{best[lv][0]} {best[lv][1]:.1f}" for lv in (0, 1, 2)
        ]
        lines.append(f"| {doc['stem']} | " + " | ".join(cells) + " |")

    for doc in documents:
        lines += [
            "",
            f"## {doc['stem']}",
            "",
            "| アダプタ | Total | CF | SED | TSF | ROA（Total 用） | 対応率 |",
            "|---|---|---|---|---|---|---|",
        ]
        for name in LEVELS:
            entry = doc["adapters"].get(name)
            if not _ok(entry):
                continue
            lines.append(
                f"| {name} | {entry['total']:.1f} | {_f(entry.get('cf'))} | {_f(entry.get('sed'))}"
                f" | {_f(entry.get('tsf'))} | {_f(entry.get('roa_effective'))}"
                f" | {_f(_match_rate(results, doc['stem'], name))} |"
            )

    lines += [
        "",
        "## コスト（ok の文書の範囲）",
        "",
        "| アダプタ | n | Wall [s] | Peak RSS [MiB] |",
        "|---|---|---|---|",
    ]
    for name in LEVELS:
        rows = [d["adapters"][name] for d in documents if _ok(d["adapters"].get(name))]
        if not rows:
            continue
        walls = [r["wall_time_sec"] for r in rows]
        rss = [r["peak_rss_kb"] / 1024 for r in rows]
        lines.append(
            f"| {name} | {len(rows)} | {min(walls):.3f}〜{max(walls):.3f}"
            f" | {min(rss):.1f}〜{max(rss):.1f} |"
        )

    lines += [
        "",
        "## Wall の pdfplumber 比",
        "",
        "| 文書 | docling | ppstructure |",
        "|---|---|---|",
    ]
    for doc in documents:
        a = doc["adapters"]
        base = a.get("pdfplumber")
        # スキャン（pdfplumber が 0 点）は比べる意味が無いので外す
        if not _ok(base) or base["total"] == 0:
            continue
        cells = [
            f"{a[n]['wall_time_sec'] / base['wall_time_sec']:.0f} 倍" if _ok(a.get(n)) else "-"
            for n in ("docling", "ppstructure")
        ]
        lines.append(f"| {doc['stem']} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", help="summary.json のある結果ディレクトリ（ci-results-full）")
    args = parser.parse_args(argv)

    results = Path(args.results)
    summary = results / "summary.json"
    if not summary.is_file():
        print(f"summary.json が見つかりません: {summary}", file=sys.stderr)
        return 2
    documents = json.loads(summary.read_text(encoding="utf-8"))["documents"]
    print(render(documents, results), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
