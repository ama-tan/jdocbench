"""Level 0 アダプタ: pypdfium2（PDFium の Python バインディング）のテキスト抽出。

PDFium（Chrome の PDF エンジン）でページごとのテキストを取るだけで、段落・表の推定はしない。
pdftotext と同じ Basic Extraction の位置付け（企画書 §7）。
pypdfium2 は extra `level1` で入る（pdfplumber の依存でもある）。未導入なら skip。

pdfplumber と同じく `python -m jdocbench.adapters.pypdfium2` で自分自身を子プロセス起動し、
他のアダプタと同じ `perf.run_command` で計測する。
"""

import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from jdocbench.perf import run_command


class Pypdfium2Adapter:
    """ページごとのテキストを抽出する。出力は加工せず raw のまま保存する。"""

    name = "pypdfium2"

    def version(self) -> str:
        try:
            return importlib.metadata.version("pypdfium2")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    def available(self) -> bool:
        return importlib.util.find_spec("pypdfium2") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        # runpy の RuntimeWarning（__init__ が import 済み）を抑止する理由は pymupdf と同じ
        cmd = [
            sys.executable,
            "-W",
            "ignore::RuntimeWarning:runpy",
            "-m",
            "jdocbench.adapters.pypdfium2",
            str(pdf),
            str(out_dir),
        ]
        result = run_command(cmd)
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        pages_file = out_dir / "pages.json"
        if not pages_file.is_file():
            raise RuntimeError("pypdfium2 が pages.json を出力しませんでした")
        # 同梱の PDFium のビルドも実験条件として残す（pypdfium2 の版だけでは決まらない）
        info = json.loads(pages_file.read_text(encoding="utf-8"))
        return {
            "wall_time_sec": result.wall_time_sec,
            "peak_rss_kb": result.peak_rss_kb,
            "extra": {"pdfium": info.get("pdfium", "unknown")},
        }


def _extract(pdf: Path, out_dir: Path) -> None:
    """子プロセス側。pypdfium2 を import してページごとにテキストを取り、無加工で保存する。"""
    # 親プロセスに PDFium のメモリを載せないため、import はここで行う
    import pypdfium2
    import pypdfium2.version

    texts: list[str] = []
    doc = pypdfium2.PdfDocument(pdf)
    try:
        for page in doc:
            textpage = page.get_textpage()
            # get_text_range() は UCS-2 までで、CJK 統合漢字拡張 B などの補助面の文字を落とす。
            # get_text_bounded() は全 Unicode を扱う（範囲の既定はページの bbox = 表示領域全体）
            texts.append(textpage.get_text_bounded())
            textpage.close()
            page.close()
    finally:
        doc.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    # pages.json が正規化の入力。text.txt は人が読むための同じ内容（ページ区切りは \f）
    pages = {"pdfium": str(pypdfium2.version.PDFIUM_INFO), "pages": texts}
    (out_dir / "pages.json").write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    (out_dir / "text.txt").write_text("\f".join(texts), encoding="utf-8")


if __name__ == "__main__":
    _extract(Path(sys.argv[1]), Path(sys.argv[2]))
