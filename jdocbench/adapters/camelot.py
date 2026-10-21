"""Level 1 アダプタ: Camelot（表抽出専業 / MIT）。

pdfplumber と同じく `python -m jdocbench.adapters.camelot` で自分自身を子プロセス起動し、
`perf.run_command` で計測する（親プロセスに opencv / pandas のメモリを載せない）。

Camelot は表だけを抽出し、段落テキストを出さない。段落を他パーサーから補うことは
しない（特定パーサーに有利な正規化をしない）ため、CF / ROA は低く出る。

flavor はアダプタごとに固定する（文書によって設定を変えない。実験条件の一貫性のため）。

- `camelot`: 罫線を使う `lattice` だけ。罫線の無い文書では 0 表になる
- `camelot_stream`: 空白の並びで表を推定する `stream` だけ。罫線の無い表も取れるが、
  段組みの本文なども表セルにするため、CF / ROA の意味が `camelot` と異なる

flavor は raw（tables.json）と run.json の `extra` に記録する（report.md の Notes に出る）。
"""

import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from jdocbench.perf import run_command


class CamelotAdapter:
    """ページごとに表を抽出する（flavor は lattice 固定）。セルは加工せず raw のまま保存する。"""

    name = "camelot"
    flavor = "lattice"

    def version(self) -> str:
        try:
            return importlib.metadata.version("camelot-py")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    def available(self) -> bool:
        return importlib.util.find_spec("camelot") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        # RuntimeWarning の抑止理由は pdfplumber アダプタと同じ（runpy の二重 import 警告）
        cmd = [
            sys.executable,
            "-W",
            "ignore::RuntimeWarning:runpy",
            "-m",
            "jdocbench.adapters.camelot",
            str(pdf),
            str(out_dir),
            self.flavor,
        ]
        result = run_command(cmd)
        # Camelot の警告（テキストの無いページ等）は stderr.txt に残すだけで tables.json に混ぜない
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        tables_json = out_dir / "tables.json"
        if not tables_json.is_file():
            raise RuntimeError("camelot が tables.json を出力しませんでした")
        raw = json.loads(tables_json.read_text(encoding="utf-8"))
        return {
            "wall_time_sec": result.wall_time_sec,
            "peak_rss_kb": result.peak_rss_kb,
            # 実験条件の記録（企画書 §49）
            "extra": {"flavor": raw["flavor"]},
        }


class CamelotStreamAdapter(CamelotAdapter):
    """flavor を stream に固定した Camelot。raw は raw/camelot_stream/ に分かれる。"""

    name = "camelot_stream"
    flavor = "stream"


def _bbox(table: Any) -> list[float] | None:
    """表の bbox（PDF 座標系、原点は左下の (x0, y0, x1, y1)）。取れなければ None。

    Camelot は bbox を公開 API で持たず、非公開属性 `_bbox` にしかない（uv.lock で固定した
    camelot-py 2.0.0 で確認）。更新で消えたり形が変わったりしても、表そのものは捨てずに
    bbox を null として保存する。
    """
    try:
        values = [float(value) for value in table._bbox]
    except (AttributeError, TypeError, ValueError):
        return None
    return values if len(values) == 4 else None


def _table_record(table: Any) -> dict:
    """Camelot の Table 1 つを tables.json の 1 要素にする（セルは無加工）。"""
    return {
        "page": int(table.page),
        "flavor": table.flavor,
        "bbox": _bbox(table),
        # bbox を上原点に反転するためのページ高さ（pt）
        "page_height": float(table.pdf_size[1]),
        "accuracy": table.parsing_report.get("accuracy"),
        "whitespace": table.parsing_report.get("whitespace"),
        "cells": table.df.values.tolist(),
    }


def _extract(pdf: Path, out_dir: Path, flavor: str) -> None:
    """子プロセス側。Camelot で表を抽出し、無加工で保存する。"""
    # 親プロセスに opencv / pandas のメモリを載せないため、import はここで行う
    import camelot
    import pypdfium2

    # 表の無いページも Document に残すため、ページ数を記録する（pypdfium2 は camelot の依存）
    document = pypdfium2.PdfDocument(str(pdf))
    page_count = len(document)
    document.close()

    tables = camelot.read_pdf(str(pdf), pages="all", flavor=flavor)
    raw = {
        "flavor": flavor,
        "page_count": page_count,
        "tables": [_table_record(table) for table in tables],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    # numpy の数値型が混ざる可能性があるため default=float で数値に落とす
    (out_dir / "tables.json").write_text(
        json.dumps(raw, ensure_ascii=False, default=float), encoding="utf-8"
    )


if __name__ == "__main__":
    _extract(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
