"""変換ツール系アダプタ: MarkItDown（Microsoft / MIT）。PDF → Markdown。

LLM の前処理として広く使われる「何でも Markdown にする」ツール。PDF は pdfplumber で
ページごとに「列の揃った語の並び（フォーム・表）」を探し、1 ページでも見つかれば
pdfplumber の結果をページごとに `\n\n` で連結し（Markdown の表を含む。**改ページ文字は入らない**）、
1 ページも見つからなければ文書全体を pdfminer の `extract_text` で取る（ページは `\f` 区切り）。

**外部 API・LLM 連携の機能は使わない**（企画書 §54-1）。`llm_client` / Azure Document
Intelligence / プラグインはいずれも指定しない（`enable_plugins=False`）。

pdfplumber と同じく `python -m jdocbench.adapters.markitdown` で自分自身を子プロセス起動し、
`perf.run_command` で計測する。出力の Markdown は加工せず `output.md` に保存する。
"""

import importlib.metadata
import importlib.util
import subprocess
import sys
from pathlib import Path

from jdocbench.perf import run_command

_SETTINGS = {"enable_plugins": False, "llm_client": None, "docintel_endpoint": None}
"""MarkItDown に渡す設定（実験条件として run.json の extra に記録する）。"""


class MarkitdownAdapter:
    """MarkItDown で PDF を Markdown にする。出力は加工せず raw のまま保存する。"""

    name = "markitdown"

    def version(self) -> str:
        try:
            return importlib.metadata.version("markitdown")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    def available(self) -> bool:
        return importlib.util.find_spec("markitdown") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        # RuntimeWarning の抑止理由は pdfplumber アダプタと同じ（runpy の二重 import 警告）
        cmd = [
            sys.executable,
            "-W",
            "ignore::RuntimeWarning:runpy",
            "-m",
            "jdocbench.adapters.markitdown",
            str(pdf),
            str(out_dir),
        ]
        result = run_command(cmd)
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        if not (out_dir / "output.md").is_file():
            raise RuntimeError("markitdown が output.md を出力しませんでした")
        return {
            "wall_time_sec": result.wall_time_sec,
            "peak_rss_kb": result.peak_rss_kb,
            # 実験条件の記録（企画書 §49）。外部 API 連携を使っていないことも残す
            "extra": dict(_SETTINGS),
        }


def _extract(pdf: Path, out_dir: Path) -> None:
    """子プロセス側。MarkItDown で変換し、Markdown を無加工で保存する。"""
    # 親プロセスに markitdown（magika の onnxruntime 等）のメモリを載せないため、import はここで行う
    from markitdown import MarkItDown

    result = MarkItDown(enable_plugins=_SETTINGS["enable_plugins"]).convert(str(pdf))
    out_dir.mkdir(parents=True, exist_ok=True)
    # newline="" で改行・改ページ文字を変換せずに書く
    (out_dir / "output.md").write_text(result.text_content, encoding="utf-8", newline="")


if __name__ == "__main__":
    _extract(Path(sys.argv[1]), Path(sys.argv[2]))
