"""Level 0 アダプタ: npm の pdf2json（PDF.js ベース）。"""

import shutil
import subprocess
from pathlib import Path

from jdocbench.perf import run_command


class Pdf2jsonAdapter:
    """`pdf2json -f <pdf> -o <dir>` で JSON を生成する。出力は加工せず raw のまま保存する。"""

    name = "pdf2json"

    def version(self) -> str:
        # 4.x の `--version` はバージョン文字列だけを stdout に出す（例: "4.1.0"）
        try:
            proc = subprocess.run(
                ["pdf2json", "--version"], capture_output=True, text=True, check=False
            )
        except OSError:
            return "unknown"
        for raw_line in proc.stdout.splitlines():
            line = raw_line.strip()
            if line:
                return line
        return "unknown"

    def available(self) -> bool:
        return shutil.which("pdf2json") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, float | int]:
        # -f は入力 PDF、-o は出力ディレクトリ。出力は <入力の basename>.json として書かれる。
        # -s（silent）は既定で stdout に大量に出る進捗ログを止めるだけで、出力 JSON には影響しない
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["pdf2json", "-s", "-f", str(pdf), "-o", str(out_dir)]
        result = run_command(cmd)
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        # pdf2json は解析に失敗しても終了コード 0 で返ることがあるため、出力の有無で判定する
        if not any(out_dir.glob("*.json")):
            raise RuntimeError("pdf2json が JSON を出力しませんでした")
        return {"wall_time_sec": result.wall_time_sec, "peak_rss_kb": result.peak_rss_kb}
