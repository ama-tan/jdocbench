import sys

from jdocbench.perf import run_command

_ALLOC_MIB = 64


def test_run_command_measures_time_and_peak_rss() -> None:
    result = run_command(
        [sys.executable, "-c", f"x = bytearray({_ALLOC_MIB} * 1024 * 1024); print('ok')"]
    )

    assert result.returncode == 0
    assert "ok" in result.stdout
    assert result.wall_time_sec > 0
    # 64 MiB を確保した子プロセスなので、その分は Peak RSS に現れる
    assert result.peak_rss_kb >= _ALLOC_MIB * 1024


def test_run_command_returns_failure_without_raising() -> None:
    result = run_command(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]
    )

    assert result.returncode == 3
    assert result.stderr == "boom"


def test_run_command_env_adds_to_current_environment() -> None:
    # env は置き換えではなく追加。渡した変数が見え、既存の PATH も残る
    code = "import os; print(os.environ['JDOCBENCH_TEST'], bool(os.environ.get('PATH')))"
    result = run_command([sys.executable, "-c", code], env={"JDOCBENCH_TEST": "x"})

    assert result.returncode == 0
    assert result.stdout.strip() == "x True"
