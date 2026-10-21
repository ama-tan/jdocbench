"""Level 1 アダプタ: Tesseract（日本語 `jpn`。外部コマンド）。

AI のレイアウト解析を持たない古典的な OCR の Baseline。ページを pypdfium2 で 300dpi の画像にし、
1 ページずつ `tesseract <png> <base> -l jpn --oem 1 --psm <n> --dpi 300 txt tsv` を実行して
raw を保存する。外部 API は呼ばない（企画書 §54-1）。

**テキスト層のある PDF には OCR をかけない**（`docling_ocr` と同じ判定・同じ閾値で skipped）。
OCR の比較は「テキスト層が無いので OCR が要る」文書（企画書 Q5）で行うもので、テキスト層のある
文書に OCR をかけた値は Level 0 / 1 の抽出と別の問いになる。OCR 系の列で対象文書を揃えると
docling_ocr と同じ母数で比べられる（ppstructure は全ページを OCR するが、レイアウト解析と表認識を
含む Document AI としての評価で、テキスト層の有無で分けていない）。

- 描画: pypdfium2 は CropBox を描画する（MediaBox が見開きで CropBox が片ページの実物スキャンでも
  表示どおりのページになる）。300dpi は実物スキャンの元画像の解像度（4288 px ÷ 1029.12 pt × 72）
- `--psm` はアダプタごとに固定: `tesseract` は 3（全自動のページ分割。Tesseract の既定）、
  `tesseract_psm6` は 6（ページ全体を 1 ブロックとして行ごとに読む）。
  比較は `docs/spikes/tesseract.md`
- 出力: `txt`（Tesseract 自身が単語を連結したテキスト。正規化に使う）と `tsv`（単語ごとの bbox と
  確信度。正規化では使わず raw として残す）を同じ認識結果から書く
- 表構造は出さない（Tesseract は表をセルに分けない）。表の中身は行のテキストとして段落に入る

ページ画像は一時ディレクトリに描画して捨てる（PDF と dpi から再現できる入力で、raw 出力ではない）。
Wall time / Peak RSS は描画と全ページの tesseract を含む子プロセス全体で測る
（`perf.run_command` の `wait4` は待機済みの孫プロセスの最大 RSS も含む）。
"""

import importlib.metadata
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from jdocbench.adapters.docling_ocr import _TEXT_LAYER_MIN_CHARS, _text_layer_chars
from jdocbench.perf import run_command

# ページ画像の解像度
_DPI = 300

# 認識言語（traineddata の名前）
_LANG = "jpn"

# OCR エンジン。1 = LSTM のみ。学習データ（tessdata_fast / tessdata）によって既定（3）の解決先が
# 変わらないよう明示する
_OEM = 1

# Tesseract のスレッド数。OpenMP 版は既定で全コアを使い、計測がランナーのコア数に
# 左右されるため 1 に固定
_OMP_THREAD_LIMIT = "1"


def parse_version(stdout: str) -> str:
    """`tesseract --version` の出力からバージョンを取り出す。

    例: "tesseract 5.3.4\\n leptonica-1.82.0 ..." → "5.3.4"。取れなければ "unknown"。
    """
    for line in stdout.splitlines():
        head, _, version = line.strip().partition(" ")
        if head == "tesseract" and version.strip():
            return version.strip()
    return "unknown"


def parse_langs(stdout: str) -> list[str]:
    """`tesseract --list-langs` の出力から言語名の一覧を取り出す（見出し行は除く）。"""
    return [
        line.strip()
        for line in stdout.splitlines()
        if line.strip() and not line.startswith("List of available languages")
    ]


def _pypdfium2_version() -> str:
    try:
        return importlib.metadata.version("pypdfium2")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _run_quiet(cmd: list[str]) -> str:
    """コマンドの stdout と stderr を連結して返す。起動できなければ空文字。

    Tesseract は版によって `--version` / `--list-langs` を stderr に出すため両方を見る。
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return ""
    return proc.stdout + proc.stderr


class TesseractAdapter:
    """Tesseract（jpn）でページ画像を OCR する。テキスト層のある PDF はスキップする。

    ページ分割モード（psm）はアダプタごとに固定する（文書によって切り替えない。camelot と同じ）。
    """

    name = "tesseract"
    # 3 = 全自動のページ分割（OSD なし）。Tesseract の既定値
    psm = 3

    def version(self) -> str:
        return parse_version(_run_quiet(["tesseract", "--version"]))

    def available(self) -> bool:
        """`tesseract` があり、`jpn` の学習データが入っていて、pypdfium2（描画）が使えるとき。"""
        if shutil.which("tesseract") is None:
            return False
        if _LANG not in parse_langs(_run_quiet(["tesseract", "--list-langs"])):
            return False
        try:
            import pypdfium2  # noqa: F401
        except ImportError:
            return False
        return True

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        chars = _text_layer_chars(pdf)
        if chars >= _TEXT_LAYER_MIN_CHARS:
            # docling_ocr と同じく、失敗ではなく「対象外」として skipped で返す
            return {"skipped": f"text layer present ({chars} chars)"}

        out_dir.mkdir(parents=True, exist_ok=True)
        # 同じ出力先で前回よりページの少ない PDF を回すと古いページが残り、正規化（p*.txt を
        # すべて読む）に混ざるため、前回のページ出力を消してから始める
        for stale in [*out_dir.glob("p*.txt"), *out_dir.glob("p*.tsv")]:
            stale.unlink()
        # RuntimeWarning の抑止理由は pdfplumber アダプタと同じ（runpy の二重 import 警告）
        cmd = [
            sys.executable,
            "-W",
            "ignore::RuntimeWarning:runpy",
            "-m",
            "jdocbench.adapters.tesseract",
            str(pdf),
            str(out_dir),
            str(self.psm),
        ]
        result = run_command(cmd, env={"OMP_THREAD_LIMIT": _OMP_THREAD_LIMIT})
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        if not sorted(out_dir.glob("p*.txt")):
            raise RuntimeError("tesseract が txt を出力しませんでした")
        return {
            "wall_time_sec": result.wall_time_sec,
            "peak_rss_kb": result.peak_rss_kb,
            # 実験条件の記録（企画書 §49）。同じ結果を再現するのに要る設定だけ入れる
            "extra": {
                "ocr": True,
                "ocr_engine": "tesseract",
                "lang": _LANG,
                "psm": self.psm,
                "oem": _OEM,
                "dpi": _DPI,
                "omp_thread_limit": int(_OMP_THREAD_LIMIT),
                "renderer": f"pypdfium2 {_pypdfium2_version()}",
            },
        }


class TesseractPsm6Adapter(TesseractAdapter):
    """psm を 6（ページ全体を 1 つの均一なテキストブロックとして読む）に固定した Tesseract。

    psm 3 のページ分割は、大きな題字などを文字の無い領域として落とすことがある
    （`docs/spikes/tesseract.md`）。psm 6 は分割せず上から行ごとに読む。
    psm 6 は段組みを区別しないので、2 段組みは左右の段が 1 行に混ざる。
    raw は raw/tesseract_psm6/ に分かれる。
    """

    name = "tesseract_psm6"
    psm = 6


def _extract(pdf: Path, out_dir: Path, psm: int) -> None:
    """子プロセス側。ページを 1 枚ずつ描画して tesseract にかけ、`p001.txt` / `p001.tsv` … を書く。

    ページ番号はファイル名の連番（1 始まり）。
    """
    import pypdfium2 as pdfium

    out_dir.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(pdf)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for index, page in enumerate(document, start=1):
                image = Path(tmp) / f"p{index:03d}.png"
                # pypdfium2 の scale は 72dpi 基準の倍率。描画範囲は CropBox（pypdfium2 の既定）
                page.render(scale=_DPI / 72).to_pil().save(image)
                base = out_dir / f"p{index:03d}"
                # 1 回の認識から txt と tsv の両方を書く。PNG に解像度を埋めないため --dpi で渡す
                # （渡さないと Tesseract が文字の大きさから推定し、ページごとに値が揺れる）
                cmd = [
                    "tesseract",
                    str(image),
                    str(base),
                    "-l",
                    _LANG,
                    "--oem",
                    str(_OEM),
                    "--psm",
                    str(psm),
                    "--dpi",
                    str(_DPI),
                    "txt",
                    "tsv",
                ]
                subprocess.run(cmd, check=True)
                image.unlink()
    finally:
        document.close()


if __name__ == "__main__":
    _extract(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]))
