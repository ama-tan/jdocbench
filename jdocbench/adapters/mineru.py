"""Level 2 アダプタ: MinerU（opendatalab）の `basic` tier（pipeline 相当。VLM を使わない）。

レイアウト（PP-DocLayoutV2）・OCR（PP-OCRv6）・表（SLANet+ / UNet / PP-LCNet）・数式
（PP-FormulaNet plus-M）の個別モデルを ONNX Runtime の CPU で動かす（torch は使わない）。
`standard` 以上の tier は VLM（llama.cpp / vLLM）を使うため対象外（Level 3 の 3GB 基準外）。
`ocr_mode="auto"` は文書単位で `txt`（テキスト層を使う）/ `ocr` を決め、middle JSON の
`extensions.mineru.parse_mode` に残る。`txt` でもテキスト層の無いページは OCR される（テキスト層の
ページとスキャンのページを混ぜた PDF で確認。`docs/spikes/mineru.md`）。

**外部 API を呼ばない**（企画書 §54-1）。MinerU の公式リモート解析（mineru.net）・VLM サーバーは
使わず、ローカルの `mineru.parser.parse` だけを呼ぶ。LLM 補助の後処理（`llm_aided`。OpenAI 互換の
API に文書の内容を送る）は既定で無効だが、MinerU の設定ファイル・環境変数で有効にできるため、
環境変数で明示的に無効にし、子プロセス側でも無効であることを確かめてから解析する。
利用状況の送信（telemetry）は `mineru` CLI の文書ライブラリ側の機能で、このアダプタが使う経路には
無い（`docs/spikes/mineru.md`）。

**オプトイン**: `available()` は環境変数 `JDOCBENCH_MINERU=1` が無いと False を返す。
コードのライセンスが Apache-2.0 に追加条項（一定規模以上の商用利用は別ライセンス、
オンラインサービスでの利用表示義務）を付けた独自ライセンスのため（`docs/licenses.md`）、
PyMuPDF（AGPL）と同じく明示的に選んだときだけ動かす。

使うモデルだけが初回実行時に Hugging Face の `opendatalab/MinerU-4_models_onnx` から自動取得される
（全部で約 820MB、保存先は `MINERU_MODEL_BASE_DIR`（既定 `~/.mineru/models`））。

Docling アダプタと同じく `python -m jdocbench.adapters.mineru` で子プロセス起動し、
`perf.run_command` で Wall time / Peak RSS を測る。MinerU は表認識を multiprocessing の
子プロセスで動かすため、Peak RSS は `ru_maxrss`（最も大きい 1 プロセスの値）で、
プロセスツリー全体の合計より小さい（スパイクの実測で合計は最大 2.5GB）。
"""

import importlib.metadata
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from jdocbench.perf import run_command

# このアダプタを有効にする環境変数。未設定なら available() が False（オプトイン）
_OPT_IN_ENV = "JDOCBENCH_MINERU"

# 解析の tier。basic = 個別モデルだけ（pipeline 相当）。standard 以上は VLM を使う
_TIER = "basic"

# テキスト層を使うか OCR するか。auto は MinerU がページの文字の有無で決める
_OCR_MODE = "auto"

# 小モデルの推論バックエンド。Linux の既定（auto）は ONNX だが、torch（level2 の extra）が
# 同じ環境にあると選び方が変わりうるため固定する
_SMALL_BACKEND = "onnx"

# LLM 補助の後処理（見出しレベルの推定・ページをまたぐ表のセル結合）を切る環境変数。
# MinerU の設定 `llm_aided.features.*` に対応する（`MINERU_` + 設定のパスを `_` でつないだ大文字）
_LLM_AIDED_ENV = (
    "MINERU_LLM_AIDED_FEATURES_TITLE_LEVELING",
    "MINERU_LLM_AIDED_FEATURES_CROSS_PAGE_TABLE_CELL_MERGE",
)


def _package_version(name: str) -> str:
    """インストール済みパッケージのバージョン。取れなければ "unknown"。"""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _env() -> dict[str, str]:
    """子プロセスに足す環境変数。利用者が設定済みの値は上書きしない（モデルの置き場所など）。"""
    return {
        "MINERU_MODEL_SMALL_BACKEND": _SMALL_BACKEND,
        # auto は Hugging Face への疎通確認をしたうえで取得元を設定ファイルに書き込むため、
        # 取得元を明示する。モデルが揃っていれば取得はしない（`local` を設定すれば通信もしない）
        "MINERU_MODEL_SOURCE": os.environ.get("MINERU_MODEL_SOURCE", "huggingface"),
        # LLM 補助の後処理（外部 API）を無効にする。環境変数は設定ファイルより優先される
        **dict.fromkeys(_LLM_AIDED_ENV, "false"),
    }


class MineruAdapter:
    """MinerU の basic tier で PDF を解析する。出力は加工せず raw のまま保存する。"""

    name = "mineru"

    def version(self) -> str:
        return _package_version("mineru")

    def available(self) -> bool:
        """mineru があり、かつ `JDOCBENCH_MINERU` が設定されているとき。

        オプトインにしているのはライセンスのため（Apache-2.0 + 追加条項の独自ライセンス）。
        """
        if not os.environ.get(_OPT_IN_ENV):
            return False
        return importlib.util.find_spec("mineru") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        # RuntimeWarning の抑止理由は pdfplumber アダプタと同じ（runpy の二重 import 警告）
        cmd = [
            sys.executable,
            "-W",
            "ignore::RuntimeWarning:runpy",
            "-m",
            "jdocbench.adapters.mineru",
            str(pdf),
            str(out_dir),
        ]
        result = run_command(cmd, env=_env())
        # MinerU は正常時も進捗ログを stderr に出す。docling と同じく残すだけにする
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        if not (out_dir / "middle_json.json").is_file():
            raise RuntimeError("mineru が middle_json.json を出力しませんでした")
        return {
            "wall_time_sec": result.wall_time_sec,
            "peak_rss_kb": result.peak_rss_kb,
            # 実験条件の記録（企画書 §49）。同じ結果を再現するのに要る設定だけ入れる
            "extra": {
                "tier": _TIER,
                "ocr_mode": _OCR_MODE,
                "small_backend": _SMALL_BACKEND,
                "llm_aided": False,
                "docvortex": _package_version("docvortex"),
                "onnxruntime": _package_version("onnxruntime"),
            },
        }


def _extract(pdf: Path, out_dir: Path) -> None:
    """子プロセス側。MinerU で解析し、raw を無加工で保存する。"""
    # 親プロセスに onnxruntime のメモリを載せないため、import はここで行う
    from mineru.config import config
    from mineru.parser import parse
    from mineru.parser.writer import FileBasedDataWriter

    # 環境変数が効かない形で有効にされていたら、外部 API に送る前に止める（企画書 §54-1）
    features = config.llm_aided.features
    if features.title_leveling or features.cross_page_table_cell_merge:
        raise RuntimeError("MinerU の LLM 補助の後処理（llm_aided）が有効になっています")

    result = parse(pdf, tier=_TIER, ocr_mode=_OCR_MODE, image_analysis=False)
    # middle_json.json（ページ × ブロックの構造。正規化の入力）・markdown.md・
    # structured_content.json・model_output.json（モデルの生出力）・images/ を書く
    out_dir.mkdir(parents=True, exist_ok=True)
    result.save(FileBasedDataWriter(str(out_dir)))


if __name__ == "__main__":
    _extract(Path(sys.argv[1]), Path(sys.argv[2]))
