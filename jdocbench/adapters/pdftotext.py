"""Level 0 アダプタ: Poppler の pdftotext。"""

import shutil
import subprocess
from pathlib import Path

from jdocbench.perf import run_command


def parse_version(stderr: str) -> str:
    """`pdftotext -v` の stderr からバージョン文字列を取り出す。

    例: "pdftotext version 21.01.0" → "21.01.0"。取れなければ "unknown"。
    """
    for raw_line in stderr.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        _, sep, version = line.rpartition("version")
        if sep and version.strip():
            return version.strip()
        return "unknown"
    return "unknown"


class PdftotextAdapter:
    """`pdftotext -layout` でテキストを抽出する。出力は加工せず raw のまま保存する。"""

    name = "pdftotext"

    def version(self) -> str:
        try:
            proc = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True, check=False)
        except OSError:
            return "unknown"
        return parse_version(proc.stderr)

    def available(self) -> bool:
        return shutil.which("pdftotext") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, float | int]:
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["pdftotext", "-layout", str(pdf), str(out_dir / "text.txt")]
        result = run_command(cmd)
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        return {"wall_time_sec": result.wall_time_sec, "peak_rss_kb": result.peak_rss_kb}
