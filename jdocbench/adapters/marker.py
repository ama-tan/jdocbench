"""Level 2 アダプタ: Marker 2.0.0（marker-pdf）の VLM を使わないモード。

ライセンスはコードが Apache-2.0、自動取得するモデルの重みが modified AI Pubs Open RAIL-M
（`docs/licenses.md`）。

**モード**: `--mode fast --disable_ocr`。レイアウト検出（rf-detr、`datalab-to/surya_layout2`）と
PDF のテキスト層（pdftext）だけでブロック・表を組み立てる。VLM（surya-ocr-2 を llama.cpp で
動かす推論サーバー）は起動しない（スパイク `docs/spikes/marker.md` で VLM 経路は実効 Peak RSS
4.1〜5.1GB となり 3GB 基準を超えたため）。CPU の既定の fast は文字化け判定（ocr_error サーバー、
約 1.1GB）も動かすが、判定の結果を使う OCR は VLM でしか行えないため、VLM を使わない構成では
判定だけが残って意味が無い。スパイクでも fast と `--disable_ocr` の出力は同一だった。
**テキスト層の無いスキャン PDF は OCR しないので文字が取れない**（0 になりうる。記録として残す）。

**外部 API は呼ばない**（企画書 §54-1）。marker-pdf は `anthropic` / `openai` / `google-genai` を
依存に持つが、それらは `--use_llm` 用で、このアダプタは `--use_llm` を渡さない。

**常駐サーバーの扱い**: marker はレイアウト検出を別プロセスのサーバー
（`surya.fast_layout.server`）に任せ、自分で起動した場合は marker 終了後も常駐させる
（`start_new_session=True` で親子関係も切れる）。そのままでは Peak RSS を過小評価し、サーバーも
残り続けるため、子プロセス側で**サーバーを自分の子として起動し**、`FAST_LAYOUT_SERVER_URL` で
marker をそこに繋ぎ、marker の終了後に止める。VLM・ocr_error・detector の各サーバーは
`*_AUTOSTART=false` で自動起動を禁じる（呼ばれたら起動せずエラーで止まる。黙って計測外の
メモリを使わせない）。`~/.cache/datalab/surya/` の sentinel も使わない（書かれない）。

**Peak RSS**: 子プロセス（このモジュール）・marker・レイアウトサーバーの 3 プロセスそれぞれの
`ru_maxrss` の**和**を記録する。3 つは処理の間ずっと並存するので、和は同時に使ったメモリの上限
（各プロセスの最大が同時に来たと仮定した値）になる。marker とサーバーの値は子プロセス側で
`os.wait4` で取り（`perf.run_command` と同じ方法）、子プロセス自身の値は `RUSAGE_SELF` で取る。
`perf.run_command` が返す子プロセスの値は使わない（Linux の `wait4` は回収済みの子孫の最大値も
含めて返すため、サーバーの値と重複する）。内訳は `extra["peak_rss_kb_by_process"]` に残す。

**オプトイン**: `available()` は環境変数 `JDOCBENCH_MARKER=1` が無いと False を返す（重みの
ライセンスが本体の MIT と異なり、torch を読むサーバーと marker 本体で 2GB 弱を使うため）。

モデル（`datalab-to/surya_layout2`、約 140MB）は初回実行時に HF Hub から自動取得される（`HF_HOME`、
既定 `~/.cache/huggingface`）。
"""

import contextlib
import ctypes
import importlib.metadata
import importlib.util
import json
import os
import resource
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from jdocbench.perf import run_command

# このアダプタを有効にする環境変数。未設定なら available() が False（オプトイン）
_OPT_IN_ENV = "JDOCBENCH_MARKER"

# marker に渡すオプション。`--disable_image_extraction` は図の base64 JPEG を JSON に埋め込まない
# （raw が数 MB に膨らむのを避ける。ブロックの構造と文字列は変わらない）。
# `--disable_multiprocessing` は pdftext を marker のプロセス内で動かす（既定は 20 ページ以上で
# ワーカープロセスを並列に起動し、その同時使用分が下の Peak RSS の和から漏れるため）
_MARKER_OPTIONS = [
    "--output_format",
    "json",
    "--mode",
    "fast",
    "--disable_ocr",
    "--disable_image_extraction",
    "--disable_multiprocessing",
]

# marker が自動起動しうるサーバーのうち、自分で起動しないもの。起動を禁じる
_NO_AUTOSTART = {
    "SURYA_INFERENCE_AUTOSTART": "false",  # VLM（llama-server / vllm）
    "OCR_ERROR_SERVER_AUTOSTART": "false",  # 文字化け判定（--disable_ocr では使わない）
    "DETECTOR_SERVER_AUTOSTART": "false",  # テキスト行検出（OCR 用）
    "FAST_LAYOUT_SERVER_AUTOSTART": "false",  # 自前で起動したものに繋ぐので、別に起動させない
}

# レイアウトサーバーの起動待ち。初回はモデルの取得を含むため長めにとる
_SERVER_STARTUP_TIMEOUT_SEC = 600.0
# サーバーの停止待ち（SIGTERM の後、これを過ぎたら SIGKILL）
_SERVER_STOP_TIMEOUT_SEC = 10.0

# 子プロセスが raw と並べて書く、プロセスごとの Peak RSS
_PEAK_RSS_FILE = "peak_rss.json"


def _package_version(name: str) -> str:
    """インストール済みパッケージのバージョン。取れなければ "unknown"。"""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


class MarkerAdapter:
    """Marker を VLM なし（fast + `--disable_ocr`）で動かす。出力は加工せず raw のまま保存する。"""

    name = "marker"

    def version(self) -> str:
        return _package_version("marker-pdf")

    def available(self) -> bool:
        """marker があり、かつ `JDOCBENCH_MARKER` が設定されているとき。"""
        if not os.environ.get(_OPT_IN_ENV):
            return False
        return importlib.util.find_spec("marker") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        # RuntimeWarning の抑止理由は pdfplumber アダプタと同じ（runpy の二重 import 警告）
        cmd = [
            sys.executable,
            "-W",
            "ignore::RuntimeWarning:runpy",
            "-m",
            "jdocbench.adapters.marker",
            str(pdf),
            str(out_dir),
        ]
        result = run_command(cmd)
        # marker は正常時も進捗ログを stderr に出す。docling と同じく残すだけにする
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        if not find_document_json(out_dir):
            raise RuntimeError("marker が JSON を出力しませんでした")

        # result.peak_rss_kb は子孫の最大値を含みサーバーと重複するため使わない（モジュール冒頭）
        by_process = json.loads((out_dir / _PEAK_RSS_FILE).read_text(encoding="utf-8"))
        return {
            "wall_time_sec": result.wall_time_sec,
            "peak_rss_kb": sum(by_process.values()),
            # 実験条件の記録（企画書 §49）。同じ結果を再現するのに要る設定だけ入れる
            "extra": {
                "options": _MARKER_OPTIONS,
                "vlm": False,
                "use_llm": False,
                "server_autostart_disabled": sorted(_NO_AUTOSTART),
                "peak_rss_kb_by_process": by_process,
                "peak_rss_method": "sum of ru_maxrss (wrapper + marker + fast_layout server)",
                "surya-ocr": _package_version("surya-ocr"),
                "pdftext": _package_version("pdftext"),
                "torch": _package_version("torch"),
            },
        }


def find_document_json(raw_dir: Path) -> Path | None:
    """marker の本体 JSON（`<stem>/<stem>.json`）。`<stem>_meta.json` は除く。無ければ None。"""
    documents = [
        path
        for path in sorted(raw_dir.glob("*/*.json"))
        if path.name != f"{path.parent.name}_meta.json"
    ]
    return documents[0] if len(documents) == 1 else None


def _free_port() -> int:
    """空いている TCP ポート。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _die_with_parent() -> None:
    """（preexec_fn）親が SIGKILL 等で死んでもサーバー・marker が残らないようにする（Linux）。"""
    pr_set_pdeathsig = 1
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(pr_set_pdeathsig, signal.SIGKILL)


def _wait_health(proc: subprocess.Popen, url: str) -> None:
    """サーバーが `/health` に 200 を返すまで待つ。先に終了したら・時間切れなら例外。"""
    deadline = time.monotonic() + _SERVER_STARTUP_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"レイアウトサーバーが起動中に終了しました（{proc.returncode}）")
        with contextlib.suppress(OSError):
            with urllib.request.urlopen(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        time.sleep(1)
    raise RuntimeError("レイアウトサーバーの起動待ちが時間切れになりました")


def _wait_rss_kb(proc: subprocess.Popen) -> int:
    """プロセスを回収し、その `ru_maxrss`（KB）を返す。理由は `perf.run_command` を参照。"""
    _, status, rusage = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    return int(rusage.ru_maxrss)


def _stop(proc: subprocess.Popen) -> int:
    """サーバーを SIGTERM（待って駄目なら SIGKILL）で止めて回収し、`ru_maxrss`（KB）を返す。

    起動中に自分で終了し `Popen.poll()` が回収済みのときは rusage を取れないため 0 を返す
    （その場合は marker を動かす前に例外で止まっており、計測値は使われない）。
    """
    if proc.returncode is not None:
        return 0
    proc.terminate()
    deadline = time.monotonic() + _SERVER_STOP_TIMEOUT_SEC
    while time.monotonic() < deadline:
        pid, status, rusage = os.wait4(proc.pid, os.WNOHANG)
        if pid:
            proc.returncode = os.waitstatus_to_exitcode(status)
            return int(rusage.ru_maxrss)
        time.sleep(0.2)
    proc.kill()
    return _wait_rss_kb(proc)


def _extract(pdf: Path, out_dir: Path) -> None:
    """子プロセス側。レイアウトサーバーを起動し、marker を繋いで JSON を出力させ、サーバーを止める。

    torch を読まない（親のメモリを増やさない）ため、marker / surya はここでも import せず
    別プロセスで動かす。
    """
    # 親のタイムアウト等で SIGTERM を受けても finally でサーバーを止める
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))

    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    bin_dir = Path(sys.executable).parent
    peak_rss: dict[str, int] = {}
    with open(out_dir / "fast_layout_server.log", "wb") as server_log:
        # --checkpoint は渡さない（サーバーと marker の双方が surya の既定のモデル名を使い、
        # 接続時のモデル名の照合が一致する。ここで surya の設定を読むと torch が import される）
        server = subprocess.Popen(
            [sys.executable, "-m", "surya.fast_layout.server", "--host", "127.0.0.1"]
            + ["--port", str(port)],
            stdout=server_log,
            stderr=subprocess.STDOUT,
            preexec_fn=_die_with_parent,
        )
        try:
            _wait_health(server, url)
            marker = subprocess.Popen(
                [str(bin_dir / "marker_single"), str(pdf), "--output_dir", str(out_dir)]
                + _MARKER_OPTIONS,
                env={**os.environ, **_NO_AUTOSTART, "FAST_LAYOUT_SERVER_URL": f"{url}/v1"},
                preexec_fn=_die_with_parent,
            )
            peak_rss["marker"] = _wait_rss_kb(marker)
        finally:
            peak_rss["fast_layout_server"] = _stop(server)
    peak_rss["wrapper"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    (out_dir / _PEAK_RSS_FILE).write_text(json.dumps(peak_rss), encoding="utf-8")
    if marker.returncode != 0:
        sys.exit(marker.returncode)


if __name__ == "__main__":
    _extract(Path(sys.argv[1]), Path(sys.argv[2]))
