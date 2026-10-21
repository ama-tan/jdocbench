"""外部コマンドの実行時間と Peak RSS を計測するヘルパー。"""

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    """`run_command` の結果。`peak_rss_kb` はそのプロセス固有の `ru_maxrss`。"""

    returncode: int
    stdout: str
    stderr: str
    wall_time_sec: float
    peak_rss_kb: int


def run_command(cmd: list[str], env: dict[str, str] | None = None) -> CommandResult:
    """外部コマンドを実行し、wall time とそのプロセスの Peak RSS を返す。

    `returncode != 0` でも例外は出さない（呼び出し側が結果を見て判断する）。

    `env` を渡すと、その中身を現在の環境変数に**足した**環境で実行する（置き換えではない。
    `PATH` や `HOME` を呼び出し側が組み直さずに済ませるため）。None なら現在の環境のまま。

    `resource.getrusage(RUSAGE_CHILDREN)` は「それまでに待機した全子プロセスの最大値」を返す
    累積値で、直前に実行した重いコマンドの値を引きずるため使わない。代わりに `os.wait4()` で
    そのプロセス固有の rusage を取る。

    `Popen.communicate()` は内部で `wait()` を呼んでプロセスを回収してしまい `os.wait4()` が
    `ChildProcessError` になるため使わない。stdout / stderr は一時ファイルに向ける
    （パイプのデッドロックも避けられる）。

    `ru_maxrss` の単位は Linux では KB。macOS ではバイトだが、本プロジェクトは Linux 前提と
    するため分岐は入れない。
    """
    with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
        start = time.perf_counter()
        proc = subprocess.Popen(
            cmd,
            stdout=out_file,
            stderr=err_file,
            env=None if env is None else {**os.environ, **env},
        )
        _, status, rusage = os.wait4(proc.pid, 0)
        wall_time_sec = time.perf_counter() - start
        # 終了状態を Popen にも伝え、回収済みであることを示す
        proc.returncode = os.waitstatus_to_exitcode(status)

        out_file.seek(0)
        err_file.seek(0)
        return CommandResult(
            returncode=proc.returncode,
            stdout=out_file.read().decode("utf-8", errors="replace"),
            stderr=err_file.read().decode("utf-8", errors="replace"),
            wall_time_sec=wall_time_sec,
            peak_rss_kb=int(rusage.ru_maxrss),
        )
