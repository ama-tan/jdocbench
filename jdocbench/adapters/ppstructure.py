"""Level 2 アダプタ: PP-StructureV3（PaddleOCR / Apache-2.0）。

Docling（レイアウト + TableFormer）とは別系統のレイアウト解析（PP-DocLayout）と
表認識（SLANeXt）をローカルのモデルで動かす（外部 API は呼ばない。企画書 §54-1）。
Docling アダプタと同じく `python -m jdocbench.adapters.ppstructure` で子プロセス起動し、
`perf.run_command` で Level 0 / 1 と同じ土台の Wall time / Peak RSS を測る。

**オプトイン**: `available()` は環境変数 `JDOCBENCH_PPSTRUCTURE=1` が無いと False を返す。
6 ページの文書で約 5 分半かかり（開発機実測 329 秒）、CI の全サンプル実行に入れると 15 分枠を
超えるため（スパイク: `docs/spikes/pp-structure.md`）。CI では 1 文書だけこの変数付きで実行する。

入力は PDF のページを 150dpi で PNG 化したもの（pypdfium2）。8GB 機で動かすための構成は
スパイクで確定したもの（`docs/spikes/pp-structure.md`）をそのまま固定で埋めている。特に
`enable_mkldnn=False`（既定の oneDNN 実行器が PP-DocLayout の属性に未対応で落ちる）と
mobile 版の det / rec（server 版はテキスト検出で 11.4GB を確保しようとして MemoryError）、
`text_det_limit_type="max"` / `text_det_limit_side_len=1280` は動作要件。

モデルは初回実行時に自動取得される（10 モデル・約 770MB、保存先は `PADDLE_PDX_CACHE_HOME`
（既定 `~/.paddlex`））。
"""

import importlib.metadata
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from jdocbench.perf import run_command

# このアダプタを有効にする環境変数。未設定なら available() が False（オプトイン）
_OPT_IN_ENV = "JDOCBENCH_PPSTRUCTURE"

# ページ画像の解像度。スパイクで 150dpi と 200dpi の結果がほぼ同じだったため軽い方を採る
_DPI = 150

# paddle のスレッド数。8GB 機ではメモリのため 1 に固定する
_CPU_THREADS = 1

# テキスト検出 / 認識のモデル。server 版は 8GB 機で MemoryError になるため mobile 固定
_TEXT_DET_MODEL = "PP-OCRv5_mobile_det"
_TEXT_REC_MODEL = "PP-OCRv5_mobile_rec"

# テキスト検出の入力サイズ上限（長辺）。既定の min 側だと入力が縮小されず巨大テンソルになる
_TEXT_DET_LIMIT_TYPE = "max"
_TEXT_DET_LIMIT_SIDE_LEN = 1280

# オフにするモジュール（`use_<名前>=False`）。モデル数とメモリを削る。表認識だけは残す
_DISABLED = [
    "doc_orientation_classify",
    "doc_unwarping",
    "formula_recognition",
    "chart_recognition",
    "seal_recognition",
    "region_detection",
    "textline_orientation",
]


def _package_version(name: str) -> str:
    """インストール済みパッケージのバージョン。取れなければ "unknown"。"""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


class PpStructureAdapter:
    """PP-StructureV3 で PDF のページ画像を解析する。出力は加工せず raw のまま保存する。"""

    name = "ppstructure"

    def version(self) -> str:
        return _package_version("paddleocr")

    def available(self) -> bool:
        """paddleocr があり、かつ `JDOCBENCH_PPSTRUCTURE` が設定されているとき。

        オプトインにしているのは実行時間のため（6 ページで約 5 分半）。既定で全サンプルに
        かけると CI の 15 分枠に収まらない。
        """
        if not os.environ.get(_OPT_IN_ENV):
            return False
        return importlib.util.find_spec("paddleocr") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        # RuntimeWarning の抑止理由は pdfplumber アダプタと同じ（runpy の二重 import 警告）
        cmd = [
            sys.executable,
            "-W",
            "ignore::RuntimeWarning:runpy",
            "-m",
            "jdocbench.adapters.ppstructure",
            str(pdf),
            str(out_dir),
        ]
        result = run_command(
            cmd,
            env={
                # モデルホスタへの疎通確認を省く（キャッシュ済みなら接続不要）
                "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
                # paddle は cpu_threads とは別に OpenMP のスレッドも張るため合わせて 1 にする
                "OMP_NUM_THREADS": "1",
            },
        )
        # PP-Structure は正常時も進捗ログを stderr に出す。docling と同じく残すだけにする
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        if not sorted((out_dir / "json").glob("*.json")):
            raise RuntimeError("ppstructure が json を出力しませんでした")
        return {
            "wall_time_sec": result.wall_time_sec,
            "peak_rss_kb": result.peak_rss_kb,
            # 実験条件の記録（企画書 §49）。同じ結果を再現するのに要る設定だけ入れる
            "extra": {
                "dpi": _DPI,
                "cpu_threads": _CPU_THREADS,
                "enable_mkldnn": False,
                "text_detection_model": _TEXT_DET_MODEL,
                "text_recognition_model": _TEXT_REC_MODEL,
                "text_det_limit_type": _TEXT_DET_LIMIT_TYPE,
                "text_det_limit_side_len": _TEXT_DET_LIMIT_SIDE_LEN,
                "disabled": _DISABLED,
                "paddlex": _package_version("paddlex"),
                "paddlepaddle": _package_version("paddlepaddle"),
            },
        }


def _render_pages(pdf: Path, pages_dir: Path) -> list[Path]:
    """PDF の各ページを 150dpi の PNG にする。ファイル名は `p001.png` … （ページ順）。"""
    import pypdfium2 as pdfium

    pages_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    document = pdfium.PdfDocument(pdf)
    try:
        for index, page in enumerate(document, start=1):
            path = pages_dir / f"p{index:03d}.png"
            # pypdfium2 の scale は 72dpi 基準の倍率
            page.render(scale=_DPI / 72).to_pil().save(path)
            paths.append(path)
    finally:
        document.close()
    return paths


def _extract(pdf: Path, out_dir: Path) -> None:
    """子プロセス側。ページごとに PP-StructureV3 をかけ、raw を無加工で保存する。"""
    # 親プロセスに paddle のメモリを載せないため、import はここで行う
    from paddleocr import PPStructureV3

    page_images = _render_pages(pdf, out_dir / "pages")

    pipeline = PPStructureV3(
        lang="japan",
        device="cpu",
        cpu_threads=_CPU_THREADS,
        # 既定（True）だと oneDNN 実行器が PP-DocLayout（RT-DETR）の属性に未対応で落ちる
        enable_mkldnn=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_formula_recognition=False,
        use_chart_recognition=False,
        use_seal_recognition=False,
        use_region_detection=False,
        use_textline_orientation=False,
        use_table_recognition=True,
        text_detection_model_name=_TEXT_DET_MODEL,
        text_recognition_model_name=_TEXT_REC_MODEL,
        text_det_limit_type=_TEXT_DET_LIMIT_TYPE,
        text_det_limit_side_len=_TEXT_DET_LIMIT_SIDE_LEN,
    )

    json_dir = out_dir / "json"
    markdown_dir = out_dir / "markdown"
    json_dir.mkdir(parents=True, exist_ok=True)
    markdown_dir.mkdir(parents=True, exist_ok=True)
    for image in page_images:
        # 1 ページずつ処理する（まとめて渡すとページ画像を同時に持ちメモリが伸びる）
        for res in pipeline.predict(str(image)):
            # 保存名は入力画像の stem から決まるため `p001_res.json` / `p001.md` になる
            res.save_to_json(str(json_dir))
            res.save_to_markdown(str(markdown_dir))


if __name__ == "__main__":
    _extract(Path(sys.argv[1]), Path(sys.argv[2]))
