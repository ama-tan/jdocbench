"""Level 0 アダプタ: MuPDF の mutool（外部コマンド）。

`mutool draw -F stext` で構造化テキスト（stext XML。ページ → ブロック → 行 → 文字、各 bbox 付き）
を出力し、加工せず raw として保存する。

**ライセンス注意**: MuPDF は AGPL-3.0 / Artifex Commercial License のデュアルライセンス
（PyMuPDF と同じ）。jdocbench は mutool をリンクせず、実行形式を subprocess で呼ぶだけで
コードは取り込まない。未導入の環境では `available()` が False を返しスキップされる。
詳細は `docs/licenses.md`。
"""

import shutil
import subprocess
from pathlib import Path

from jdocbench.adapters.pdftotext import parse_version
from jdocbench.perf import run_command

STEXT_FILE = "page.stext.xml"
"""raw の出力ファイル名。全ページを 1 つの XML（`<document>` 配下に `<page>` が並ぶ）に書く。"""


class MutoolAdapter:
    """`mutool draw -F stext` で構造化テキストを生成する。出力は加工せず raw のまま保存する。"""

    name = "mutool"

    def version(self) -> str:
        # `mutool -v` は "mutool version 1.23.10" を stderr に出す（pdftotext -v と同じ形式）
        try:
            proc = subprocess.run(["mutool", "-v"], capture_output=True, text=True, check=False)
        except OSError:
            return "unknown"
        return parse_version(proc.stderr or proc.stdout)

    def available(self) -> bool:
        return shutil.which("mutool") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, float | int]:
        out_dir.mkdir(parents=True, exist_ok=True)
        # -q は進捗表示を止めるだけ。出力ファイル名に %d を含めないので全ページが 1 ファイルに入る
        cmd = ["mutool", "draw", "-q", "-F", "stext", "-o", str(out_dir / STEXT_FILE), str(pdf)]
        result = run_command(cmd)
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        if not (out_dir / STEXT_FILE).is_file():
            raise RuntimeError(f"mutool が {STEXT_FILE} を出力しませんでした")
        return {"wall_time_sec": result.wall_time_sec, "peak_rss_kb": result.peak_rss_kb}
