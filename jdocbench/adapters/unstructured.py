"""変換ツール系アダプタ: Unstructured（Apache-2.0）の `partition_pdf`。PDF → 要素の列。

`strategy="fast"` 固定（pdfminer のテキスト層を使う。モデル・OCR を使わない Level 1 相当）。
要素（Title / NarrativeText / ListItem / Header / Footer / Table …）を
`[element.to_dict() for element in elements]` の形で加工せず `elements.json` に保存する。
要素の無いページを正規化で残すため、ページ数と設定を `meta.json` に別に保存する。
fast は表を Table 要素として返さない（表の中身は段落相当の要素として出る）。

`hi_res`（YOLOX のレイアウトモデル + Table Transformer + OCR）は入れていない。
hi_res は OCR エージェント（Tesseract または PaddleOCR）を必須とし、Tesseract の日本語データの
導入は #196 の範囲。判断と実測は README のアダプタ表と PR #193 を参照。

**外部 API を呼ばない**（企画書 §54-1）。Unstructured の API（`unstructured-client`）は使わず、
ローカルの `partition_pdf` だけを呼ぶ。利用状況の送信（Scarf）も環境変数で止める。

pdfplumber と同じく `python -m jdocbench.adapters.unstructured` で自分自身を子プロセス起動し、
`perf.run_command` で計測する（unstructured は import 時に torch を読み込むため、
親プロセスに載せない）。
"""

import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from jdocbench.perf import run_command

_STRATEGY = "fast"
_LANGUAGES = ["jpn"]
"""文書の言語。未指定だと英語と仮定する旨の警告が出るため、全文書で日本語に固定する。"""

_ENV = {
    # 利用状況の送信（Scarf のピクセル）を止める。どちらか一方で止まるが両方指定する
    "SCARF_NO_ANALYTICS": "true",
    "DO_NOT_TRACK": "true",
    # 商用版の案内を stderr に毎回出さない
    "UNSTRUCTURED_DISABLE_NOTICE": "1",
}


class UnstructuredAdapter:
    """`partition_pdf(strategy="fast")` で要素を抽出する。要素は加工せず raw のまま保存する。"""

    name = "unstructured"

    def version(self) -> str:
        try:
            return importlib.metadata.version("unstructured")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    def available(self) -> bool:
        return importlib.util.find_spec("unstructured") is not None

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        # RuntimeWarning の抑止理由は pdfplumber アダプタと同じ（runpy の二重 import 警告）
        cmd = [
            sys.executable,
            "-W",
            "ignore::RuntimeWarning:runpy",
            "-m",
            "jdocbench.adapters.unstructured",
            str(pdf),
            str(out_dir),
        ]
        result = run_command(cmd, env=_ENV)
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        if not (out_dir / "elements.json").is_file():
            raise RuntimeError("unstructured が elements.json を出力しませんでした")
        return {
            "wall_time_sec": result.wall_time_sec,
            "peak_rss_kb": result.peak_rss_kb,
            # 実験条件の記録（企画書 §49）
            "extra": {"strategy": _STRATEGY, "languages": list(_LANGUAGES)},
        }


def _extract(pdf: Path, out_dir: Path) -> None:
    """子プロセス側。partition_pdf で要素を抽出し、無加工で保存する。"""
    # 親プロセスに unstructured（torch を含む）のメモリを載せないため、import はここで行う
    import pypdf
    from unstructured.partition.pdf import partition_pdf

    elements = partition_pdf(filename=str(pdf), strategy=_STRATEGY, languages=_LANGUAGES)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 要素の無いページ（白紙・テキスト層の無いページ）には要素が 1 つも出ず、末尾のページは
    # 要素の page_number からは分からない。正規化でページ数を揃えるため、ページ数を別に残す
    # （pypdf は unstructured の PDF 処理の依存）
    meta = {
        "page_count": len(pypdf.PdfReader(str(pdf)).pages),
        "strategy": _STRATEGY,
        "languages": _LANGUAGES,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (out_dir / "elements.json").write_text(
        json.dumps([element.to_dict() for element in elements], ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    _extract(Path(sys.argv[1]), Path(sys.argv[2]))
