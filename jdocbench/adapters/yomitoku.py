"""Level 2 アダプタ: YomiToku（日本語特化の Document AI。外部コマンド）。

レイアウト解析・文字検出・文字認識・表構造認識の 4 モデルで、ページを DPI 200 の画像にして
OCR する。テキスト層の有無で分けず、デジタル PDF も描画して OCR する（ppstructure と同じく
Document AI としての評価）。モードは通常モード（`--lite` ではない）、device は CPU。

**ライセンス注意**: コードとモデルの重みの両方が **CC BY-NC-SA 4.0（非商用のみ）**。
商用案件では有効にしない（`docs/licenses.md`）。そのため次の 2 点で本体から切り離す。

- **オプトイン**: `available()` は環境変数 `JDOCBENCH_YOMITOKU=1` が無いと False を返す
- **本体の venv に入れない**: 別環境（`uv tool install`）の CLI を subprocess で呼ぶだけで、
  コードは取り込まない。コマンドは `JDOCBENCH_YOMITOKU_BIN`（既定は PATH 上の `yomitoku`）。
  pyproject の extras と uv.lock には入れない（yomitoku は `pypdfium2==4.30.0` を固定で要求し、
  同じ環境に入れると他のアダプタの依存が下がる。`docs/spikes/yomitoku.md`）

**1 ページ 1 プロセスで呼ぶ**（`--pages N` を N = 1..ページ数で繰り返す）。文書をまとめて
1 プロセスで処理すると Peak RSS がページ数とともに増え、4 ページで 3GB を超える（#213 の実測）。
ページごとに分けると Peak RSS はページ数に依らず 2.2〜2.4GB。Wall time はページの合計
（ページごとのモデル読み込み約 10 秒を含む）、Peak RSS はページの最大を記録する。

**外部 API を呼ばない**（企画書 §54-1）。**実行時は通信もしない**。重みは
`scripts/install_yomitoku.sh --models` がコミット SHA で固定して HF のキャッシュ
（`$HF_HOME`）に取得し、キャッシュの `refs/main` をその SHA に向けておく（#227）。
実行時は `refs/main` が固定した SHA（`_REVISIONS`）と一致することを確かめ、違えば起動前に
失敗する（#245）。

**重みのリビジョンの固定**: YomiToku は `Net.from_pretrained(<リポジトリ名>)` を
revision なしで呼ぶため、そのままでは HF Hub の main を問い合わせ、上流が進んでいれば
新しい重みを取ってくる。`HF_HUB_OFFLINE=1` だけでは、各リポジトリに無い `config.json` の
探索がオフライン時の例外（`LocalEntryNotFoundError`。YomiToku 側の huggingface_hub が
捕まえない）で落ちて動かない。そこで `refs/main` が指すスナップショットのディレクトリ
（`snapshots/<SHA>/`）を、`--td_cfg` などの設定 YAML の `hf_hub_repo` に渡す。
`from_pretrained` はディレクトリを渡されるとその中の `model.safetensors` を直接読み、
Hub に問い合わせない。読んだ SHA を `extra.models` に `{リポジトリ: SHA}` で記録する。
念のため `HF_HUB_OFFLINE=1` も付け、上書きが効かずに Hub へ取りに行く場合は、
黙って取得せず失敗させる。
`HF_HOME` は利用者の値をそのまま使う（設定しない）。

raw は CLI が書いた 1 ページ 1 ファイルの JSON（`<親ディレクトリ名>_<stem>_p<N>.json`）を
無加工で残す。接頭辞が入力 PDF の親ディレクトリ名に依存するので `_p<N>.json` で拾う。
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from jdocbench.perf import run_command

# このアダプタを有効にする環境変数。`1` でなければ available() が False（オプトイン）
_OPT_IN_ENV = "JDOCBENCH_YOMITOKU"

# YomiToku のコマンドを指す環境変数。未設定なら PATH 上の `yomitoku`
_BIN_ENV = "JDOCBENCH_YOMITOKU_BIN"
_DEFAULT_BIN = "yomitoku"

DPI = 200
"""ページ画像の解像度（YomiToku の既定）。座標はこの解像度のピクセルなので、正規化が
PDF ポイントに直すのに使う。既定が変わっても座標の換算がずれないよう明示して渡す。"""

_DEVICE = "cpu"
_FORMAT = "json"
_MODE = "normal"

# 通常モードで使うモデル（HF Hub のリポジトリ）。キーは設定 YAML を渡すオプションの役割
_MODELS = {
    "text_detector": "KotaroKinoshita/yomitoku-text-detector-dbnet-v2_1",
    "text_recognizer": "KotaroKinoshita/yomitoku-text-recognizer-parseq-middle-dynw-v5",
    "layout_parser": "KotaroKinoshita/yomitoku-layout-parser-rtdtrv2-v2",
    "table_structure_recognizer": (
        "KotaroKinoshita/yomitoku-table-structure-recognizer-rtdtrv2-open-beta"
    ),
}

# 固定するコミット SHA（#227）。scripts/install_yomitoku.sh の MODELS と同じ値を
# テストで突き合わせる。実行時に refs/main がこれと違えば YomiToku を起動せずに失敗させる（#245）。
# スクリプト側にも残すのは、CI の HF キャッシュのキーがスクリプトのハッシュで、
# SHA を変えるとキャッシュも作り直されるため
_REVISIONS = {
    "KotaroKinoshita/yomitoku-text-detector-dbnet-v2_1": "3ac8375d1fd124e8074b013a32f2085d1199654c",
    "KotaroKinoshita/yomitoku-text-recognizer-parseq-middle-dynw-v5": (
        "cfe941a992ba3eedbdd3d54ef22ef34d39a88fa7"
    ),
    "KotaroKinoshita/yomitoku-layout-parser-rtdtrv2-v2": "6772f1b6ea5b694da2e8fe19cf6d38f453822616",
    "KotaroKinoshita/yomitoku-table-structure-recognizer-rtdtrv2-open-beta": (
        "91103cbcefc7f9d0ba8234fd3caba28b6d564a46"
    ),
}

# 役割ごとの設定 YAML を渡す YomiToku のオプション
_CFG_OPTIONS = {
    "text_detector": "--td_cfg",
    "text_recognizer": "--tr_cfg",
    "layout_parser": "--lp_cfg",
    "table_structure_recognizer": "--tsr_cfg",
}

# 上書きが効かずに HF Hub へ取りに行く場合は、取得せず失敗させる（仕掛け線）
_ENV = {"HF_HUB_OFFLINE": "1"}

_WEIGHTS_FILE = "model.safetensors"
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_INSTALL_HINT = "bash scripts/install_yomitoku.sh --models を実行してください"

_PAGE_FILE = re.compile(r"_p(\d+)\.json$")


def _bin() -> str | None:
    """YomiToku のコマンドの実行可能なパス。見つからない・実行できなければ None。"""
    return shutil.which(os.environ.get(_BIN_ENV) or _DEFAULT_BIN)


def _interpreter(bin_path: str) -> list[str] | None:
    """コマンド（uv tool の起動スクリプト）の 1 行目の shebang から Python の起動コマンドを取る。

    shebang の引数ごと返す（`#!/usr/bin/env python3` なら `["/usr/bin/env", "python3"]`）。
    """
    try:
        with open(bin_path, "rb") as file:
            first = file.readline().decode("utf-8", errors="replace").strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    parts = first[2:].split()
    return parts or None


def _page_count(bin_path: str, pdf: Path) -> tuple[int, str]:
    """PDF のページ数と、数えた pypdfium2 の版。

    本体の venv の pypdfium2 は extra（`level1`）にしか無いので使わず、YomiToku の環境
    （yomitoku の必須依存に pypdfium2 がある）の Python で数える。数えられなければ RuntimeError。
    """
    interpreter = _interpreter(bin_path)
    if interpreter is None:
        raise RuntimeError(
            f"YomiToku のコマンドの shebang から Python を特定できません: {bin_path}"
        )
    code = (
        "import sys, importlib.metadata as m, pypdfium2 as p; d = p.PdfDocument(sys.argv[1]); "
        "print(len(d), m.version('pypdfium2')); d.close()"
    )
    proc = subprocess.run(
        [*interpreter, "-c", code, str(pdf)], capture_output=True, text=True, check=False
    )
    parts = proc.stdout.split()
    if proc.returncode != 0 or len(parts) != 2 or not parts[0].isdigit():
        raise RuntimeError(
            f"YomiToku の環境でページ数を数えられませんでした: {proc.stderr.strip()}"
        )
    return int(parts[0]), parts[1]


def _hf_hub_cache(bin_path: str) -> Path:
    """YomiToku の環境の huggingface_hub が使う HF のキャッシュ（`HF_HUB_CACHE`）の場所。

    `HF_HOME` / `HF_HUB_CACHE` / `XDG_CACHE_HOME` の解決を huggingface_hub 自身にさせる。
    取れなければ RuntimeError。
    """
    interpreter = _interpreter(bin_path)
    if interpreter is None:
        raise RuntimeError(
            f"YomiToku のコマンドの shebang から Python を特定できません: {bin_path}"
        )
    code = "from huggingface_hub import constants; print(constants.HF_HUB_CACHE)"
    proc = subprocess.run([*interpreter, "-c", code], capture_output=True, text=True, check=False)
    path = proc.stdout.strip()
    if proc.returncode != 0 or not path:
        raise RuntimeError(
            f"YomiToku の環境で HF のキャッシュの場所を取れませんでした: {proc.stderr.strip()}"
        )
    return Path(path)


def model_snapshots(hub_cache: Path) -> dict[str, Path]:
    """役割ごとに、`refs/main` が指すスナップショットのディレクトリ（名前がコミット SHA）。

    `refs/main` が無い・コミット SHA でない・固定した SHA（`_REVISIONS`）と違う・
    そのスナップショットに重みが無いときは、取得し直す方法を添えて RuntimeError
    （YomiToku を起動する前に止め、通信もしない）。
    別の取得処理が `refs/main` を進めても、違う重みで測った値を結果に混ぜない（#245）。
    """
    snapshots: dict[str, Path] = {}
    for role, repo in _MODELS.items():
        repo_dir = hub_cache / ("models--" + repo.replace("/", "--"))
        ref = repo_dir / "refs" / "main"
        try:
            sha = ref.read_text(encoding="utf-8").strip()
        except OSError:
            raise RuntimeError(
                f"YomiToku のモデルが HF のキャッシュにありません: {repo}（{ref}）。{_INSTALL_HINT}"
            ) from None
        if not _COMMIT_SHA.fullmatch(sha):
            raise RuntimeError(f"{ref} がコミット SHA ではありません: {sha!r}。{_INSTALL_HINT}")
        if sha != _REVISIONS[repo]:
            raise RuntimeError(
                f"YomiToku のモデルが固定した版と違います: {repo} の refs/main は {sha}、"
                f"期待は {_REVISIONS[repo]}（{ref}）。{_INSTALL_HINT}"
            )
        snapshot = repo_dir / "snapshots" / sha
        if not (snapshot / _WEIGHTS_FILE).is_file():
            raise RuntimeError(
                f"YomiToku のモデルの重みがありません: {snapshot / _WEIGHTS_FILE}。{_INSTALL_HINT}"
            )
        snapshots[role] = snapshot
    return snapshots


def write_model_configs(snapshots: dict[str, Path], cfg_dir: Path) -> dict[str, Path]:
    """役割ごとに `hf_hub_repo` をスナップショットのディレクトリにした設定 YAML を書く。

    YomiToku は既定の設定にこの YAML を重ねる（`hf_hub_repo` 以外は既定のまま）。値は JSON の
    文字列で書く（YAML のダブルクォートのスカラーとしてそのまま読める。pyyaml を足さない）。
    """
    paths: dict[str, Path] = {}
    for role, snapshot in snapshots.items():
        path = cfg_dir / f"{role}.yaml"
        path.write_text(f"hf_hub_repo: {json.dumps(str(snapshot))}\n", encoding="utf-8")
        paths[role] = path
    return paths


def page_files(raw_dir: Path) -> list[tuple[int, Path]]:
    """raw のページファイルを (ページ番号, パス) の列にする。番号の昇順（p10 は p2 の後）。"""
    found = [
        (int(m.group(1)), path)
        for path in raw_dir.glob("*.json")
        if (m := _PAGE_FILE.search(path.name))
    ]
    return sorted(found)


def page_command(
    bin_path: str, pdf: Path, out_dir: Path, page: int, configs: dict[str, Path]
) -> list[str]:
    """1 ページ分の YomiToku の呼び出し。`configs` は役割ごとの設定 YAML（write_model_configs）。"""
    cfg_args = [arg for role, path in configs.items() for arg in (_CFG_OPTIONS[role], str(path))]
    return [
        bin_path,
        str(pdf),
        "-f",
        _FORMAT,
        "-o",
        str(out_dir),
        "-d",
        _DEVICE,
        "--dpi",
        str(DPI),
        "--pages",
        str(page),
        *cfg_args,
    ]


class YomitokuAdapter:
    """YomiToku（通常モード・CPU）を 1 ページ 1 プロセスで呼ぶ。出力は raw のまま保存する。"""

    name = "yomitoku"

    def version(self) -> str:
        """YomiToku の版。CLI に `--version` が無いので、tool 環境の Python でメタデータを読む。"""
        bin_path = _bin()
        interpreter = _interpreter(bin_path) if bin_path else None
        if interpreter is None:
            return "unknown"
        code = "import importlib.metadata as m; print(m.version('yomitoku'))"
        try:
            proc = subprocess.run(
                [*interpreter, "-c", code], capture_output=True, text=True, check=False, timeout=60
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        version = proc.stdout.strip()
        return version if proc.returncode == 0 and version else "unknown"

    def available(self) -> bool:
        """`JDOCBENCH_YOMITOKU` が設定され、YomiToku のコマンドが実行できるとき。

        オプトインにしているのはライセンスのため（CC BY-NC-SA 4.0。非商用のみ）。
        """
        # `0` / `false` などで有効にならないよう、値は `1` だけを受け付ける
        if os.environ.get(_OPT_IN_ENV) != "1":
            return False
        return _bin() is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        bin_path = _bin()
        if bin_path is None:
            raise RuntimeError(f"YomiToku のコマンドが見つかりません（{_BIN_ENV}）")
        out_dir.mkdir(parents=True, exist_ok=True)
        # 同じ出力先で前回よりページの少ない PDF を回すと古いページが残り、正規化に混ざるため消す
        for stale in [path for _, path in page_files(out_dir)]:
            stale.unlink()
        stderr_file = out_dir / "stderr.txt"
        stderr_file.unlink(missing_ok=True)

        snapshots = model_snapshots(_hf_hub_cache(bin_path))
        pages, pdfium_version = _page_count(bin_path, pdf)
        with tempfile.TemporaryDirectory(prefix="jdocbench-yomitoku-") as cfg_dir:
            configs = write_model_configs(snapshots, Path(cfg_dir))
            wall, peak, per_page = self._run_pages(bin_path, pdf, out_dir, pages, configs)
        return {
            "wall_time_sec": wall,
            "peak_rss_kb": peak,
            # 実験条件の記録（企画書 §49）。同じ結果を再現するのに要る設定だけ入れる
            "extra": {
                "invocation": "per-page",
                "mode": _MODE,
                "device": _DEVICE,
                "dpi": DPI,
                "format": _FORMAT,
                # 実際に読んだ重みのリビジョン（HF Hub のコミット SHA。#227）
                "models": {_MODELS[role]: path.name for role, path in snapshots.items()},
                "pages": pages,
                "per_page": per_page,
                # torch のスレッド数は OMP_NUM_THREADS に従う（未設定なら torch の既定）
                "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
                "page_counter": f"pypdfium2 {pdfium_version}（YomiToku の環境）",
            },
        }

    def _run_pages(
        self, bin_path: str, pdf: Path, out_dir: Path, pages: int, configs: dict[str, Path]
    ) -> tuple[float, int, list[dict[str, object]]]:
        """1 ページ 1 プロセスで回し、Wall の合計・Peak RSS の最大・ページごとの値を返す。"""
        stderr_file = out_dir / "stderr.txt"
        wall = 0.0
        peak = 0
        per_page: list[dict[str, object]] = []
        for page in range(1, pages + 1):
            cmd = page_command(bin_path, pdf, out_dir, page, configs)
            result = run_command(cmd, env=_ENV)
            # YomiToku は正常時もログを stderr に出す。ページの見出しを付けて残すだけにする
            if result.stderr:
                with stderr_file.open("a", encoding="utf-8") as file:
                    file.write(f"--- page {page} ---\n{result.stderr}")
            if result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
            # `--pages` が範囲外だと何も書かずに終了コード 0 で終わるため、出力の有無で確かめる
            written = [path for number, path in page_files(out_dir) if number == page]
            if len(written) != 1:
                raise RuntimeError(f"yomitoku が p{page} の JSON を出力しませんでした")
            wall += result.wall_time_sec
            peak = max(peak, result.peak_rss_kb)
            per_page.append(
                {
                    "page": page,
                    "wall_time_sec": result.wall_time_sec,
                    "peak_rss_kb": result.peak_rss_kb,
                }
            )
        return wall, peak, per_page
