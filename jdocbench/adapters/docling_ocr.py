"""Level 2 アダプタ: Docling（OCR オン・省メモリ設定）。

テキスト層のある PDF には OCR をかけない（無駄で、CI 時間も増える）。
スキャン文書で OCR が必要になる境界（企画書 Q5）を測るためのアダプタ。

`docling` アダプタ（OCR オフ）と同じ子プロセスパターンで動き、raw の形式も同じ
（`document.json` / `document.md`）なので正規化は docling のものを流用する。
OCR エンジンは RapidOCR。バックエンドは `onnxruntime` が入っていればそれを使い、
無ければ docling 既定の torch に落とす。認識言語は PP-OCR コードの `japan`
（PP-OCRv6 の多言語 recognizer が日本語を含む）。

省メモリのため OCR / レイアウト / 表の推論を 1 ページずつ行う（docling アダプタと同じ）。
スレッド数は `OMP_NUM_THREADS` に従う（docling の `AcceleratorOptions` が環境変数を読む）。
"""

import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from jdocbench.perf import run_command

# OCR / レイアウト / TableFormer に一度に渡すページ数（docling 既定は各 4）。docling と同じく 1。
# 以前は settings.perf.page_batch_size = 1 を指定していたが、docling 2.129 の StandardPdfPipeline
# （スレッド版）はこの値を読まず効いていなかった（docs/spikes/docling-memory.md、#157）
_OCR_BATCH_SIZE = 1
_LAYOUT_BATCH_SIZE = 1
_TABLE_BATCH_SIZE = 1

# 認識言語（PP-OCR コード）。`iso:ja` でも同じ recognizer に解決される
_OCR_LANG = "japan"

# テキスト層ありと見なす文字数の下限。全ページ合計がこれ未満なら「テキスト層なし」とする
_TEXT_LAYER_MIN_CHARS = 20


def _models_version() -> str:
    """レイアウト / TableFormer モデルを提供する docling-ibm-models のバージョン。"""
    try:
        return importlib.metadata.version("docling-ibm-models")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _ocr_backend() -> str:
    """RapidOCR の推論バックエンド。onnxruntime があればそれ、無ければ torch。"""
    return "onnxruntime" if importlib.util.find_spec("onnxruntime") is not None else "torch"


def _text_layer_chars(pdf: Path) -> int:
    """全ページのテキスト層の文字数合計。

    pypdfium2 は pdfplumber（extra `level1`）の依存として入る。判定だけに使い、
    抽出結果は捨てる（このアダプタの出力は必ず OCR 由来にする）。
    """
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf)
    try:
        # get_text_range() は UCS-2 までで、CJK 統合漢字拡張 B などの補助面の文字を落とす。
        # get_text_bounded() は全 Unicode を扱う（pypdfium2 アダプタと同じ判断、#191）
        return sum(len(page.get_textpage().get_text_bounded()) for page in document)
    finally:
        document.close()


class DoclingOcrAdapter:
    """Docling を OCR オンで動かす。テキスト層のある PDF はスキップする。"""

    name = "docling_ocr"

    def version(self) -> str:
        try:
            return importlib.metadata.version("docling")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    def available(self) -> bool:
        return importlib.util.find_spec("docling") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        chars = _text_layer_chars(pdf)
        if chars >= _TEXT_LAYER_MIN_CHARS:
            # 例外ではなく skipped で返す（失敗ではなく「対象外」なので run.json でも区別する）
            return {"skipped": f"text layer present ({chars} chars)"}

        out_dir.mkdir(parents=True, exist_ok=True)
        # RuntimeWarning の抑止理由は pdfplumber アダプタと同じ（runpy の二重 import 警告）
        cmd = [
            sys.executable,
            "-W",
            "ignore::RuntimeWarning:runpy",
            "-m",
            "jdocbench.adapters.docling_ocr",
            str(pdf),
            str(out_dir),
        ]
        result = run_command(cmd)
        # Docling は正常時も進捗ログを stderr に出す。docling アダプタと同じく残すだけにする
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        if not (out_dir / "document.json").is_file():
            raise RuntimeError("docling_ocr が document.json を出力しませんでした")
        return {
            "wall_time_sec": result.wall_time_sec,
            "peak_rss_kb": result.peak_rss_kb,
            # 実験条件の記録（企画書 §49）。同じ結果を再現するのに要る設定だけ入れる
            "extra": {
                "ocr": True,
                "ocr_engine": "rapidocr",
                "ocr_backend": _ocr_backend(),
                "ocr_lang": _OCR_LANG,
                "ocr_batch_size": _OCR_BATCH_SIZE,
                "layout_batch_size": _LAYOUT_BATCH_SIZE,
                "table_batch_size": _TABLE_BATCH_SIZE,
                "models": _models_version(),
            },
        }


def _pipeline_options():
    """変換設定。OCR オン（RapidOCR）、表構造はオン（既定）、バッチは 1 ページずつ。"""
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions

    return PdfPipelineOptions(
        do_ocr=True,
        ocr_options=RapidOcrOptions(backend=_ocr_backend(), lang=[_OCR_LANG]),
        ocr_batch_size=_OCR_BATCH_SIZE,
        layout_batch_size=_LAYOUT_BATCH_SIZE,
        table_batch_size=_TABLE_BATCH_SIZE,
    )


def _extract(pdf: Path, out_dir: Path) -> None:
    """子プロセス側。Docling を OCR オンで動かし、raw を無加工で保存する。"""
    # 親プロセスに torch / onnxruntime のメモリを載せないため、import はここで行う
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
