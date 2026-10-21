#!/usr/bin/env bash
# YomiToku（yomitoku アダプタ）を本体の venv と別の uv tool 環境に、版と CPU 版 torch を固定して導入する（#215）。
#
#   bash scripts/install_yomitoku.sh [--models] [TOOL_DIR [BIN_DIR]]
#
# - TOOL_DIR / BIN_DIR は uv tool の環境とコマンドの置き場所（引数 > 環境変数 UV_TOOL_DIR / UV_TOOL_BIN_DIR >
#   uv の既定）。本体の pyproject.toml / uv.lock は変えない（docs/spikes/yomitoku.md）
# - --models を付けると、通常モードで使うモデル 4 つ（約 617MB）を HF Hub から $HF_HOME（未指定なら
#   ~/.cache/huggingface）に先に取得する。アダプタの初回実行時の取得が Wall time に入るのを避ける
# - 標準出力には yomitoku コマンドのパスだけを出す（ログは標準エラー）。CI はこれを
#   JDOCBENCH_YOMITOKU_BIN として $GITHUB_ENV に書く
# - モデルは HF Hub のコミット SHA で固定して取得し、キャッシュの refs/main をその SHA に向ける（#227）。
#   アダプタは refs/main が指すスナップショットのディレクトリを YomiToku に直接渡して読ませ、run.json の
#   extra.models に {リポジトリ: SHA} を記録する。HF Hub の main が進んでも、ここを変えない限り同じ重みで測る
# - CI の bench もこれを使い、HF モデルのキャッシュキーにこのファイルのハッシュを含める。
#   版やモデル（SHA）を変えるときはこのファイルを編集すればキャッシュも作り直される
#
# YomiToku はコード・モデルの重みとも CC BY-NC-SA 4.0（非商用）。導入しても JDOCBENCH_YOMITOKU=1 のときだけ動く
set -euo pipefail

YOMITOKU_VERSION=0.15.0
# 通常モード（--lite なし）で読み込むモデル（yomitoku 0.15.0 の既定の設定の hf_hub_repo。各リポジトリ
# model.safetensors 1 ファイル）と、固定するコミット SHA（2026-10-20 時点の main。#227）。
# 「リポジトリ@SHA」の形で書く。jdocbench/adapters/yomitoku.py の _REVISIONS と同じ値にする（テストで突き合わせる。
# アダプタは refs/main がそれと違えば YomiToku を起動せずに失敗する。#245）
MODELS=(
  KotaroKinoshita/yomitoku-text-detector-dbnet-v2_1@3ac8375d1fd124e8074b013a32f2085d1199654c
  KotaroKinoshita/yomitoku-text-recognizer-parseq-middle-dynw-v5@cfe941a992ba3eedbdd3d54ef22ef34d39a88fa7
  KotaroKinoshita/yomitoku-layout-parser-rtdtrv2-v2@6772f1b6ea5b694da2e8fe19cf6d38f453822616
  KotaroKinoshita/yomitoku-table-structure-recognizer-rtdtrv2-open-beta@91103cbcefc7f9d0ba8234fd3caba28b6d564a46
)

models=0
if [ "${1:-}" = "--models" ]; then
  models=1
  shift
fi
if [ $# -ge 1 ]; then export UV_TOOL_DIR="$1"; fi
if [ $# -ge 2 ]; then export UV_TOOL_BIN_DIR="$2"; fi

# CPU 版 torch の index を優先し、そこに無い依存は PyPI から取る（nvidia-* / triton を引かない）
uv tool install "yomitoku==$YOMITOKU_VERSION" \
  --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match >&2

bin="$(uv tool dir --bin)/yomitoku"
python="$(uv tool dir)/yomitoku/bin/python"
[ -x "$bin" ] || { echo "yomitoku コマンドが見つからない: $bin" >&2; exit 1; }

if [ "$models" = 1 ]; then
  # 固定した SHA で取得し（取得済みなら通信しない）、refs/main をその SHA に向ける。revision に SHA を
  # 渡すと huggingface_hub は refs を書かないため自分で書く。アダプタは refs/main からスナップショットを決める
  "$python" - "${MODELS[@]}" >&2 <<'PY'
import re
import sys
from pathlib import Path

from huggingface_hub import constants, hf_hub_download
from huggingface_hub.file_download import repo_folder_name

for spec in sys.argv[1:]:
    repo, sha = spec.split("@")
    assert re.fullmatch(r"[0-9a-f]{40}", sha), f"コミット SHA ではない: {spec}"
    print(hf_hub_download(repo, "model.safetensors", revision=sha))
    ref = Path(constants.HF_HUB_CACHE) / repo_folder_name(repo_id=repo, repo_type="model") / "refs" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(sha)
PY
fi

echo "$bin"
