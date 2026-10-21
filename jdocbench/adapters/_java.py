"""Java 系アダプタ（tika / tabula）の共通処理。

jar は Python パッケージに含めず、版を固定した公式の jar を各自が取得して置く（取得手順と
チェックサムは README）。置き場所は環境変数で指定でき、未指定なら `~/.cache/jdocbench/`。
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

JAR_DIR = Path.home() / ".cache" / "jdocbench"

MIN_JAVA = 11
"""tika-app 3.x と単一ソースファイル実行（tabula）のどちらも Java 11 以上が要る。"""

JVM_XMX = "1536m"
"""tika / tabula / tabula_stream の JVM ヒープ上限（#208）。

上限が無いと JVM の既定（物理メモリの 1/4。8GB マシンなら 2GB）に依存し、実行環境ごとに
計測条件が変わってしまう。Sprint 16 の CI 実測（run 39211548651 の ci-results-full、
全 9 文書）での Peak RSS の最大値は tabula（lattice）の mof-financial-statements-fy2024-excerpt
で 1,134,260 KB（約 1.08GB）。Peak RSS はヒープ以外（スレッドスタックやメタスペース等）も含む
プロセス全体の値で -Xmx が制限するヒープ本体より大きいため、素の実測値をそのまま上限にはせず、
結果を変えない余裕を見て 1536m（1.5GB）に固定する。8GB マシンでも重いモデルと同時に動かせる。
"""

_VERSION = re.compile(r'version "(\d+)(?:\.(\d+))?')


def jar_path(env_var: str, filename: str) -> Path:
    """環境変数 `env_var` の値（あれば）か、`~/.cache/jdocbench/<filename>`。"""
    value = os.environ.get(env_var)
    return Path(value).expanduser() if value else JAR_DIR / filename


def java_cmd(*args: str, xmx: str = JVM_XMX) -> list[str]:
    """`java -Xmx<xmx> <args...>` の起動コマンドを組み立てる。"""
    return ["java", f"-Xmx{xmx}", *args]


def command_output(cmd: list[str]) -> str:
    """コマンドの stdout と stderr をつないだ文字列。実行できなければ空文字。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout + proc.stderr


def java_version() -> str:
    """`java -version` の 1 行目（実験条件として extra に記録する）。取れなければ "unknown"。"""
    lines = command_output(["java", "-version"]).strip().splitlines()
    return lines[0].strip() if lines else "unknown"


def parse_java_major(output: str) -> int | None:
    """`java -version` の出力から主バージョンを取り出す。

    例: `openjdk version "21.0.12.1"` → 21、`openjdk version "1.8.0_492"` → 8（1.x は x が主版）。
    """
    match = _VERSION.search(output)
    if match is None:
        return None
    major = int(match.group(1))
    if major == 1 and match.group(2):
        return int(match.group(2))
    return major


def java_ok() -> bool:
    """java が PATH にあり、主バージョンが MIN_JAVA 以上か。"""
    if shutil.which("java") is None:
        return False
    major = parse_java_major(command_output(["java", "-version"]))
    return major is not None and major >= MIN_JAVA
