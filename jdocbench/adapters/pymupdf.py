"""Level 1 アダプタ: PyMuPDF（pymupdf）。

**ライセンス注意**: PyMuPDF は AGPL-3.0-only と Artifex Commercial License のデュアル
ライセンスで、MIT の本体とは条件が異なる。そのため既定の依存には入れず、オプトインの
extra（`uv sync --extra pymupdf` または `--all-extras`）でのみ任意導入する。未導入環境では
`available()` が False を返しスキップされる。詳細は `docs/licenses.md`（企画書 §47）。

pdfplumber と同じく `python -m jdocbench.adapters.pymupdf` で自分自身を子プロセス起動し、
pdftotext / pdf2json と同じ `perf.run_command` で計測する。
"""

import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from jdocbench.perf import run_command


class PymupdfAdapter:
    """ページごとにテキスト・ブロック・表を抽出する。出力は加工せず raw のまま保存する。"""

    name = "pymupdf"

    def version(self) -> str:
        try:
            return importlib.metadata.version("pymupdf")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    def available(self) -> bool:
        return importlib.util.find_spec("pymupdf") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, float | int]:
        out_dir.mkdir(parents=True, exist_ok=True)
        # `jdocbench.adapters` の __init__ がこのモジュールを先に import 済みのため、runpy が
        # 「sys.modules に既にある」と RuntimeWarning を出す。挙動に影響はないが stderr.txt が
        # 毎回できて紛らわしいので、runpy 由来の RuntimeWarning だけ抑止する
        cmd = [
            sys.executable,
            "-W",
            "ignore::RuntimeWarning:runpy",
            "-m",
            "jdocbench.adapters.pymupdf",
            str(pdf),
            str(out_dir),
        ]
        result = run_command(cmd)
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        if not (out_dir / "text.txt").is_file():
            raise RuntimeError("pymupdf が text.txt を出力しませんでした")
        return {"wall_time_sec": result.wall_time_sec, "peak_rss_kb": result.peak_rss_kb}


def _extract(pdf: Path, out_dir: Path) -> None:
    """子プロセス側。pymupdf を import してページごとに抽出し、無加工で保存する。"""
    # 親プロセスに pymupdf のメモリを載せないため、import はここで行う
    import pymupdf

    texts: list[str] = []
    blocks: list[object] = []
    lines: list[object] = []
    tables: list[object] = []
    doc = pymupdf.open(pdf)
    try:
        for number, page in enumerate(doc, start=1):
            texts.append(page.get_text("text"))
            blocks.append(page.get_text("blocks"))
            # blocks の各行の座標（表の中身の行を位置で判定するため。#186）と、行を構成する
            # 文字ごとの [c, x0, y0, x1, y1]（行の一部だけが表の中にある行で、表の中の語だけを
            # 除くため。#199）。blocks と同じフラグで取るので、ブロック番号と行の並びが blocks と
            # 一致する。rawdict の文字を連結した行は dict の span を連結した行と同じ
            text_dict = page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_BLOCKS)
            lines.append(
                [
                    [
                        *line["bbox"],
                        "".join(char["c"] for span in line["spans"] for char in span["chars"]),
                        block["number"],
                        [
                            [char["c"], *char["bbox"]]
                            for span in line["spans"]
                            for char in span["chars"]
                        ],
                    ]
                    for block in text_dict["blocks"]
                    for line in block.get("lines", [])
                ]
            )
            try:
                page_tables = [
                    {"bbox": list(table.bbox), "rows": table.extract()}
                    for table in page.find_tables()
                ]
            except Exception as exc:  # 1 ページの失敗で run 全体を止めない
                # 一部の PDF では表検出が例外を出す。そのページの表は空として続行し、記録だけ残す
                print(f"page {number}: find_tables に失敗しました: {exc}", file=sys.stderr)
                page_tables = []
            tables.append(page_tables)
    finally:
        doc.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    # ページ区切りは pdftotext と同じ改ページ文字（\f）
    (out_dir / "text.txt").write_text("\f".join(texts), encoding="utf-8")
    (out_dir / "blocks.json").write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")
    # 各要素は [x0, y0, x1, y1, text, block_no]（blocks.json と同じ並び）
    (out_dir / "lines.json").write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    (out_dir / "tables.json").write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    _extract(Path(sys.argv[1]), Path(sys.argv[2]))
