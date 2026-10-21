"""Level 2 アダプタ: Docling（Document AI / MIT）。

レイアウト解析と TableFormer をローカルのモデルで動かす（外部 API は呼ばない。企画書 §54-1）。
pdfplumber と同じく `python -m jdocbench.adapters.docling` で子プロセス起動し、
`perf.run_command` で Level 0 / 1 と同じ土台の Wall time / Peak RSS を測る。

OCR は既定でオフ。8GB 機で OCR オンの初回実行が異常終了したため、対象はテキスト PDF に限る。
モデルは初回実行時に Hugging Face から自動取得される（約 500MB）。`HF_HOME` は触らず、
環境変数があればそれが効く。
"""

import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from jdocbench.perf import run_command

# レイアウト / TableFormer に一度に渡すページ数（docling 既定は各 4）。1 にすると出力は変わらず
# Peak RSS が 4〜8 ページの文書で 16〜23% 下がる（実測は docs/spikes/docling-memory.md）。
# 以前は settings.perf.page_batch_size = 1 を指定していたが、docling 2.129 の StandardPdfPipeline
# （スレッド版）はこの値を読まず効いていなかった
_LAYOUT_BATCH_SIZE = 1
_TABLE_BATCH_SIZE = 1


def _models_version() -> str:
    """レイアウト / TableFormer モデルを提供する docling-ibm-models のバージョン。"""
    try:
        return importlib.metadata.version("docling-ibm-models")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


class DoclingAdapter:
    """Docling で PDF を DoclingDocument に変換する。出力は加工せず raw のまま保存する。"""

    name = "docling"

    def version(self) -> str:
        try:
            return importlib.metadata.version("docling")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    def available(self) -> bool:
        return importlib.util.find_spec("docling") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        # RuntimeWarning の抑止理由は pdfplumber アダプタと同じ（runpy の二重 import 警告）
        cmd = [
            sys.executable,
            "-W",
            "ignore::RuntimeWarning:runpy",
            "-m",
            "jdocbench.adapters.docling",
            str(pdf),
            str(out_dir),
        ]
        result = run_command(cmd)
        # Docling は正常時も進捗ログを stderr に出す。pdfplumber と同じく残すだけにする
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        if not (out_dir / "document.json").is_file():
            raise RuntimeError("docling が document.json を出力しませんでした")
        return {
            "wall_time_sec": result.wall_time_sec,
            "peak_rss_kb": result.peak_rss_kb,
            # 実験条件の記録（企画書 §49）。同じ結果を再現するのに要る設定だけ入れる
            "extra": {
                "ocr": False,
                "layout_batch_size": _LAYOUT_BATCH_SIZE,
                "table_batch_size": _TABLE_BATCH_SIZE,
                "models": _models_version(),
            },
        }


def _pipeline_options():
    """変換設定。OCR オフ、表構造はオン（既定）、バッチは 1 ページずつ。"""
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    return PdfPipelineOptions(
        do_ocr=False,
        layout_batch_size=_LAYOUT_BATCH_SIZE,
        table_batch_size=_TABLE_BATCH_SIZE,
    )


def _extract(pdf: Path, out_dir: Path) -> None:
    """子プロセス側。Docling で変換し、raw を無加工で保存する。"""
    # 親プロセスに torch のメモリを載せないため、import はここで行う
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=_pipeline_options())}
    )
    result = converter.convert(str(pdf))

    out_dir.mkdir(parents=True, exist_ok=True)
    # export_to_dict() は bbox の Enum など JSON にそのまま落ちない値を含むため default=str で逃がす
    (out_dir / "document.json").write_text(
        json.dumps(result.document.export_to_dict(), ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    (out_dir / "document.md").write_text(result.document.export_to_markdown(), encoding="utf-8")


if __name__ == "__main__":
    _extract(Path(sys.argv[1]), Path(sys.argv[2]))
