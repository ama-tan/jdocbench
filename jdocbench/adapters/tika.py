"""Level 0 アダプタ: Apache Tika（tika-app の jar を外部コマンドとして呼ぶ / Apache-2.0）。

`java -jar tika-app-<ver>.jar --xml` の XHTML を raw として無加工で保存する。XHTML はページを
`<div class="page">`、段落を `<p>` で表すので、テキスト出力（`--text`）と違ってページ境界が残る。
Tika の PDF パーサーは PDFBox のテキスト抽出で、表を表として返さない（pdftotext と同じ扱い）。

設定は Tika の既定から **OCR だけを切る**（`ocrStrategy=no_ocr` と TesseractOCRParser の除外）。
Tika は Tesseract が PATH にあると自動で OCR するため、環境によって結果が変わるのを防ぎ、
AI を使わない Baseline に留める。tika-app の CLI が独自に有効にする機能（埋め込み画像の抽出等。
TIKA-4472）も、自前の設定ファイルを渡すことで外れる。設定ファイルは raw に保存する（実験条件）。

Tika 3.x は Java 11 以上が必要。
"""

import subprocess
from pathlib import Path

from jdocbench.adapters._java import (
    JVM_XMX,
    command_output,
    jar_path,
    java_cmd,
    java_ok,
    java_version,
)
from jdocbench.perf import run_command

TIKA_VERSION = "3.3.3"
JAR_ENV = "JDOCBENCH_TIKA_JAR"
JAR_NAME = f"tika-app-{TIKA_VERSION}.jar"

TIKA_CONFIG = """\
<?xml version="1.0" encoding="UTF-8"?>
<!-- jdocbench: Tika の既定から OCR だけを切る（Tesseract の有無で結果を変えない） -->
<properties>
  <parsers>
    <parser class="org.apache.tika.parser.DefaultParser">
      <parser-exclude class="org.apache.tika.parser.pdf.PDFParser"/>
      <parser-exclude class="org.apache.tika.parser.ocr.TesseractOCRParser"/>
    </parser>
    <parser class="org.apache.tika.parser.pdf.PDFParser">
      <params>
        <param name="ocrStrategy" type="string">no_ocr</param>
      </params>
    </parser>
  </parsers>
</properties>
"""


def parse_version(output: str) -> str:
    """`--version` の出力からバージョンを取り出す。例: "Apache Tika 3.3.3" → "3.3.3"。"""
    for line in output.splitlines():
        if line.startswith("Apache Tika "):
            return line.removeprefix("Apache Tika ").strip() or "unknown"
    return "unknown"


class TikaAdapter:
    """tika-app で XHTML を出力する。出力は加工せず raw のまま保存する。"""

    name = "tika"

    def _jar(self) -> Path:
        return jar_path(JAR_ENV, JAR_NAME)

    def version(self) -> str:
        return parse_version(command_output(["java", "-jar", str(self._jar()), "--version"]))

    def available(self) -> bool:
        # jar の有無を先に見る（jar が無ければ java を起動しない）
        return self._jar().is_file() and java_ok()

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        config = out_dir / "tika-config.xml"
        config.write_text(TIKA_CONFIG, encoding="utf-8")
        # 出力の文字コードはロケールに任せず UTF-8 に固定する
        cmd = java_cmd(
            "-jar",
            str(self._jar()),
            f"--config={config}",
            "--xml",
            "--encoding=UTF-8",
            str(pdf),
        )
        result = run_command(cmd)
        if result.stderr:
            (out_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
        (out_dir / "content.xhtml").write_text(result.stdout, encoding="utf-8")
        return {
            "wall_time_sec": result.wall_time_sec,
            "peak_rss_kb": result.peak_rss_kb,
            # 実験条件の記録（企画書 §49）
            "extra": {"java": java_version(), "ocr_strategy": "no_ocr", "jvm_xmx": JVM_XMX},
        }
