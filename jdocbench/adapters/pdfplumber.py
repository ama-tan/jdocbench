"""Level 1 アダプタ: pdfplumber（純 Python / MIT）。

Python ライブラリだが、`python -m jdocbench.adapters.pdfplumber` で自分自身を子プロセス起動し、
pdftotext / pdf2json と同じ `perf.run_command` で計測する。親プロセスに pdfplumber の
メモリを載せないので Peak RSS が外部コマンドと同じ土台で比較できる。
"""

import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from jdocbench.perf import run_command


class PdfplumberAdapter:
    """ページごとにテキスト・単語・表を抽出する。出力は加工せず raw のまま保存する。"""

    name = "pdfplumber"

    def version(self) -> str:
        try:
            return importlib.metadata.version("pdfplumber")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    def available(self) -> bool:
        return importlib.util.find_spec("pdfplumber") is not None

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
            "jdocbench.adapters.pdfplumber",
            str(pdf),
            str(out_dir),
        ]
        result = run_command(cmd)
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        if not (out_dir / "text.txt").is_file():
            raise RuntimeError("pdfplumber が text.txt を出力しませんでした")
        return {"wall_time_sec": result.wall_time_sec, "peak_rss_kb": result.peak_rss_kb}


def _distinct(chars: list[dict]) -> list[dict]:
    """`extract_text_lines()` の `chars` から、同じ文字オブジェクトの連続した重複を除く。

    `chars` は行の text の 1 文字ごとに元の文字オブジェクトを指すため、text が複数文字の
    オブジェクト（`(cid:12256)` など）は文字数ぶん繰り返して現れる。
    """
    return [char for i, char in enumerate(chars) if i == 0 or char is not chars[i - 1]]


def _extract(pdf: Path, out_dir: Path) -> None:
    """子プロセス側。pdfplumber を import してページごとに抽出し、無加工で保存する。"""
    # 親プロセスに pdfplumber のメモリを載せないため、import はここで行う
    import pdfplumber

    texts: list[str] = []
    lines: list[object] = []
    words: list[object] = []
    tables: list[object] = []
    table_bboxes: list[object] = []
    with pdfplumber.open(pdf) as doc:
        for page in doc.pages:
            texts.append(page.extract_text() or "")
            # text.txt の各行の座標（表の中身の行を位置で判定するため。#186）と、行を構成する
            # 文字ごとの [text, x0, top, x1, bottom]（行の一部だけが表の中にある行で、表の中の
            # 語だけを除くため。#199）。words.json は行との対応を持たないので別に保存する
            lines.append(
                [
                    {
                        **{key: line[key] for key in ("text", "x0", "top", "x1", "bottom")},
                        "chars": [
                            [char[key] for key in ("text", "x0", "top", "x1", "bottom")]
                            for char in _distinct(line["chars"])
                        ],
                    }
                    for line in page.extract_text_lines()
                ]
            )
            words.append(page.extract_words())
            # extract_tables() は find_tables() の各表の extract() と同じ。bbox を取るため分けて呼ぶ
            found = page.find_tables()
            tables.append([table.extract() for table in found])
            table_bboxes.append([list(table.bbox) for table in found])

    out_dir.mkdir(parents=True, exist_ok=True)
    # ページ区切りは pdftotext と同じ改ページ文字（\f）
    (out_dir / "text.txt").write_text("\f".join(texts), encoding="utf-8")
    # Decimal など JSON にそのまま落ちない数値型が混ざるため default=float で数値に落とす
    (out_dir / "words.json").write_text(
        json.dumps(words, ensure_ascii=False, default=float), encoding="utf-8"
    )
    (out_dir / "tables.json").write_text(
        json.dumps(tables, ensure_ascii=False, default=float), encoding="utf-8"
    )
    (out_dir / "lines.json").write_text(
        json.dumps(lines, ensure_ascii=False, default=float), encoding="utf-8"
    )
    # tables.json と同じ並び。各表の (x0, top, x1, bottom)（上原点）
    (out_dir / "table_bboxes.json").write_text(
        json.dumps(table_bboxes, ensure_ascii=False, default=float), encoding="utf-8"
    )


if __name__ == "__main__":
    _extract(Path(sys.argv[1]), Path(sys.argv[2]))
