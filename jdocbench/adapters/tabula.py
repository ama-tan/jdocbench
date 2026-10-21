"""Level 1 アダプタ: Tabula（tabula-java / 表抽出専業 / MIT）。

tabula-py は使わず、版を固定した tabula-java の jar を直接呼ぶ。tabula-py 2.10.0 も同じ jar
（tabula-1.0.5-jar-with-dependencies.jar。SHA-256 一致を確認済み）を同梱して呼ぶだけなので、
Python 依存（pandas 等）を増やさずに同じ処理になる。

tabula-java の CLI の JSON（`-f JSON -p all`）には表のページ番号が無いため、CLI と同じ手順を
ライブラリから呼ぶ小さな Java（`TabulaPages.java`）を単一ソースファイル実行で動かし、
ページごとに CLI と同じ JSONWriter の出力を包んで tables.json に書く（表の中身と並びは CLI の
出力と同一）。単一ソースファイル実行には JDK（Java 11 以上）が要る。

Camelot と同じく mode はアダプタごとに固定する（文書によって切り替えない。#135）。

- `tabula`: 罫線を使う lattice（CLI の `-l`）だけ。罫線の無い文書では 0 表になる
- `tabula_stream`: 空白の並びで表を推定する stream（CLI の `-t`）だけ。ページ全体を 1 表にする

表だけを抽出し、段落テキストを出さないので CF / ROA は低く出る（camelot と同じ）。
"""

import json
import shutil
import subprocess
from pathlib import Path

from jdocbench.adapters._java import (
    JVM_XMX,
    command_output,
    jar_path,
    java_cmd,
    java_ok,
    java_version,
)
from jdocbench.perf import run_command

TABULA_VERSION = "1.0.5"
JAR_ENV = "JDOCBENCH_TABULA_JAR"
JAR_NAME = f"tabula-{TABULA_VERSION}-jar-with-dependencies.jar"
PAGES_SOURCE = Path(__file__).with_name("TabulaPages.java")


def parse_version(output: str) -> str:
    """`--version` の出力からバージョンを取り出す。

    例: "tabula 1.0.5 (c) 2012-2020 Manuel Aristarán" → "1.0.5"。
    """
    for line in output.splitlines():
        words = line.split()
        if len(words) >= 2 and words[0] == "tabula":
            return words[1]
    return "unknown"


class TabulaAdapter:
    """ページごとに表を抽出する（mode は lattice 固定）。セルは加工せず raw のまま保存する。"""

    name = "tabula"
    mode = "lattice"

    def _jar(self) -> Path:
        return jar_path(JAR_ENV, JAR_NAME)

    def version(self) -> str:
        return parse_version(command_output(["java", "-jar", str(self._jar()), "--version"]))

    def available(self) -> bool:
        # 単一ソースファイル実行はコンパイラを使うので JRE だけでは動かない（javac で JDK を判定）
        # jar の有無を先に見る（jar が無ければ java を起動しない）
        return self._jar().is_file() and shutil.which("javac") is not None and java_ok()

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        tables_json = out_dir / "tables.json"
        cmd = java_cmd(
            "-cp",
            str(self._jar()),
            str(PAGES_SOURCE),
            str(pdf),
            self.mode,
            str(tables_json),
        )
        result = run_command(cmd)
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        if not tables_json.is_file():
            raise RuntimeError("tabula が tables.json を出力しませんでした")
        raw = json.loads(tables_json.read_text(encoding="utf-8"))
        return {
            "wall_time_sec": result.wall_time_sec,
            "peak_rss_kb": result.peak_rss_kb,
            # 実験条件の記録（企画書 §49）
            "extra": {"mode": raw["mode"], "java": java_version(), "jvm_xmx": JVM_XMX},
        }


class TabulaStreamAdapter(TabulaAdapter):
    """mode を stream に固定した Tabula。raw は raw/tabula_stream/ に分かれる。"""

    name = "tabula_stream"
    mode = "stream"
