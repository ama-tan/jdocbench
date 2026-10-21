"""Router の評価（#163）: Router の選択と単一パーサー・oracle を DSQS Total と Wall で比べる。

使い方（CI アーティファクト `ci-results-full` を展開したディレクトリを渡す）:

    uv run python scripts/router_eval.py <results-dir> [--samples samples]

入力は `<results-dir>/summary.json`（文書 × アダプタの status / total / wall_time_sec）と、
文書ごとの Document Profile。Profile は `<results-dir>/<stem>/profile.json` があればそれを使い、
無ければ `<samples>/<stem>.pdf` を `profile_pdf` で計算する（PyMuPDF が要る）。
Router に渡す実行環境の日本語フォント（C3 の分岐が使う。#173）は `<results-dir>/<stem>/run.json` の
`fonts_ja`（その結果を出した環境の値）。run.json やその項目が無ければ None（調べられなかった扱い）。
出力は Markdown（docs/router.md「Router の評価」の表の元）を標準出力へ。

戦略:
- Router（quality / cost）: `jdocbench.router.route` の推奨。推奨パーサーがその文書で ok でなければ
  フォールバックを順に使い、注記する
- 単一パーサー: 常に同じパーサー。ok でない文書は Total 0・Wall 0 として数え、注記する
- oracle（最良）: 文書ごとに Total 最大のアダプタ（同点は Wall の短い方）
- oracle（cost）: 最良から 1 点以内で Wall が最も短いアダプタ

「最良から 1 点以内」の文書数は、全アダプタの最良 Total との差が 1 点以内の文書を数える。
外部 API もモデルも使わない。CLI に入れないのは、CI の結果を読む一回限りの分析で、
ベンチマーク本体（run / route）の機能ではないため。
"""

import argparse
import json
import sys
from pathlib import Path

from jdocbench.router import route

SINGLES = ("pdfplumber", "pymupdf", "camelot_stream", "docling", "ppstructure")
TOLERANCE = 1.0  # 「最良から 1 点以内」


def _ok(adapters: dict, name: str) -> bool:
    entry = adapters.get(name) or {}
    return entry.get("status") == "ok" and isinstance(entry.get("total"), int | float)


def _pick(adapters: dict, name: str) -> dict:
    entry = adapters[name]
    return {"parser": name, "total": entry["total"], "wall": entry.get("wall_time_sec") or 0.0}


def _routed(adapters: dict, recommendation: dict) -> dict:
    """推奨パーサー、それが ok でなければ最初に ok なフォールバックを選ぶ。"""
    for i, name in enumerate([recommendation["parser"], *recommendation["fallbacks"]]):
        if _ok(adapters, name):
            choice = _pick(adapters, name)
            if i:
                choice["note"] = f"{recommendation['parser']} が ok でないため fallback"
            return choice
    return {
        "parser": recommendation["parser"],
        "total": 0.0,
        "wall": 0.0,
        "note": "候補が全て無い",
        "failed": True,
    }


def _single(adapters: dict, name: str) -> dict:
    if _ok(adapters, name):
        return _pick(adapters, name)
    status = (adapters.get(name) or {}).get("status", "なし")
    return {
        "parser": name,
        "total": 0.0,
        "wall": 0.0,
        "note": f"{name} が {status}",
        "failed": True,
    }


def evaluate(
    documents: list[dict], profiles: dict[str, dict], fonts: dict[str, list | None] | None = None
) -> dict:
    """文書ごと・戦略ごとの選択と集計を返す。

    `documents` は summary.json の `documents`、`profiles` は stem → Profile、
    `fonts` は stem → 実行環境の日本語フォント（run.json の `fonts_ja`。無い stem は None）。
    戻り値は `{"stems", "strategies": {名前: {"choices": [...], "mean", "wall", "within"}}}`。
    """
    names = [
        "router-quality",
        "router-cost",
        *SINGLES,
        "oracle",
        "oracle-cost",
    ]
    choices: dict[str, list[dict]] = {name: [] for name in names}
    for doc in documents:
        adapters = doc["adapters"]
        ok = [name for name in adapters if _ok(adapters, name)]
        if not ok:
            raise ValueError(f"ok のアダプタが無い文書: {doc['stem']}")
        best = max(ok, key=lambda n: (adapters[n]["total"], -_pick(adapters, n)["wall"]))
        best_total = adapters[best]["total"]
        near = [n for n in ok if adapters[n]["total"] >= best_total - TOLERANCE - 1e-9]
        cheapest = min(near, key=lambda n: _pick(adapters, n)["wall"])

        profile = profiles[doc["stem"]]
        fonts_ja = (fonts or {}).get(doc["stem"])
        picked = {
            "router-quality": _routed(adapters, route(profile, "quality", fonts_ja)),
            "router-cost": _routed(adapters, route(profile, "cost", fonts_ja)),
            **{name: _single(adapters, name) for name in SINGLES},
            "oracle": _pick(adapters, best),
            "oracle-cost": _pick(adapters, cheapest),
        }
        for name, choice in picked.items():
            # 結果が無い（ok でない）選択は、最良が 1 点以下の文書でも「1 点以内」に数えない
            choice["within"] = (
                not choice.get("failed") and choice["total"] >= best_total - TOLERANCE - 1e-9
            )
            choices[name].append(choice)

    strategies = {}
    for name, rows in choices.items():
        strategies[name] = {
            "choices": rows,
            "mean": round(sum(r["total"] for r in rows) / len(rows), 1),
            "wall": round(sum(r["wall"] for r in rows), 1),
            "within": sum(r["within"] for r in rows),
        }
    return {"stems": [doc["stem"] for doc in documents], "strategies": strategies}


def render(result: dict) -> str:
    """評価結果を Markdown にする（行 = 文書、列 = 戦略）。"""
    names = list(result["strategies"])
    strategies = result["strategies"]
    lines = [
        "## DSQS Total",
        "",
        "| 文書 | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]
    for i, stem in enumerate(result["stems"]):
        cells = []
        for name in names:
            choice = strategies[name]["choices"][i]
            cells.append(f"{choice['total']:.1f}" + ("*" if "note" in choice else ""))
        lines.append(f"| {stem} | " + " | ".join(cells) + " |")
    lines.append("| **平均 Total** | " + " | ".join(f"{strategies[n]['mean']:.1f}" for n in names))
    lines[-1] += " |"
    lines.append(
        "| **Wall 合計 [s]** | " + " | ".join(f"{strategies[n]['wall']:.1f}" for n in names) + " |"
    )
    lines.append(
        f"| **最良から {TOLERANCE:g} 点以内** | "
        + " | ".join(f"{strategies[n]['within']}/{len(result['stems'])}" for n in names)
        + " |"
    )

    chosen = ["router-quality", "router-cost", "oracle", "oracle-cost"]
    lines += ["", "## 選ばれたパーサー", "", "| 文書 | " + " | ".join(chosen) + " |"]
    lines.append("|---|" + "---|" * len(chosen))
    for i, stem in enumerate(result["stems"]):
        cells = [strategies[n]["choices"][i]["parser"] for n in chosen]
        lines.append(f"| {stem} | " + " | ".join(cells) + " |")

    notes = [
        f"- {stem} / {name}: {strategies[name]['choices'][i]['note']}"
        for name in names
        for i, stem in enumerate(result["stems"])
        if "note" in strategies[name]["choices"][i]
    ]
    if notes:
        lines += ["", "注記（表の `*`）:", "", *notes]
    return "\n".join(lines) + "\n"


def _load_profiles(results: Path, samples: Path, stems: list[str]) -> dict[str, dict]:
    profiles = {}
    for stem in stems:
        path = results / stem / "profile.json"
        if path.is_file():
            profiles[stem] = json.loads(path.read_text(encoding="utf-8"))
        else:
            from jdocbench.profile import profile_pdf

            profiles[stem] = profile_pdf(samples / f"{stem}.pdf")
    return profiles


def _load_fonts(results: Path, stems: list[str]) -> dict[str, list | None]:
    fonts = {}
    for stem in stems:
        path = results / stem / "run.json"
        run = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        fonts[stem] = run.get("fonts_ja")
    return fonts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", help="summary.json のある結果ディレクトリ（ci-results-full）")
    parser.add_argument(
        "--samples", default="samples", help="profile.json が無いときに使う PDF の場所"
    )
    args = parser.parse_args(argv)

    results = Path(args.results)
    summary = results / "summary.json"
    if not summary.is_file():
        print(f"summary.json が見つかりません: {summary}", file=sys.stderr)
        return 2
    documents = json.loads(summary.read_text(encoding="utf-8"))["documents"]
    stems = [d["stem"] for d in documents]
    profiles = _load_profiles(results, Path(args.samples), stems)
    print(render(evaluate(documents, profiles, _load_fonts(results, stems))), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
