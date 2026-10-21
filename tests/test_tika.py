import shutil
from pathlib import Path

import pytest
from _samples import SAMPLES

from jdocbench import perf
from jdocbench.adapters import ADAPTERS, _java
from jdocbench.adapters import tika as tika_module
from jdocbench.adapters._java import JVM_XMX, java_cmd
from jdocbench.adapters.tika import JAR_ENV, JAR_NAME, TikaAdapter, parse_version
from jdocbench.normalizers import NORMALIZERS
from jdocbench.normalizers.tika import normalize

_needs_tika = pytest.mark.skipif(
    not TikaAdapter().available(),
    reason="Java か tika-app の jar（JDOCBENCH_TIKA_JAR）が無い環境ではスキップ",
)

# tika-app 3.3.3 の `--xml` の出力を縮めたもの（meta は省略）
_XHTML = """<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml">
<head><meta name="xmpTPg:NPages" content="3"/><title/></head>
<body><div class="page"><p/>
<p>1.3 申請に必要な書類
</p>
<p>
書類 法人 個人
</p>
<p>納税証明書（その２）又は
税法上、申告義務のない
</p>
<img src="embedded:image0.png" alt="image0.png"/><div class="annotation"><a href="https://example.jp/">https://example.jp/</a></div>
</div>
<div class="page"><p/>
</div>
<div class="page"><p>見出し<br/>本文</p>末尾</div>
</body></html>"""


def _raw_dir(tmp_path: Path, xhtml: str = _XHTML) -> Path:
    raw_dir = tmp_path / "raw" / "tika"
    raw_dir.mkdir(parents=True)
    (raw_dir / "content.xhtml").write_text(xhtml, encoding="utf-8")
    return raw_dir


def test_normalize_pages_and_lines(tmp_path: Path) -> None:
    """ページは div.page ごと、段落は非空行ごと。空のページも残し、表は作らない。"""
    doc = normalize(_raw_dir(tmp_path))
    assert doc.adapter == "tika"
    assert [page.number for page in doc.pages] == [1, 2, 3]
    assert [block.text for block in doc.pages[0].blocks] == [
        "1.3 申請に必要な書類",
        "書類 法人 個人",
        "納税証明書（その２）又は",
        "税法上、申告義務のない",
        "https://example.jp/",
    ]
    assert doc.pages[1].blocks == []
    # <br> は改行、ブロック要素の後ろのテキスト（tail）も別の行になる
    assert [block.text for block in doc.pages[2].blocks] == ["見出し", "本文", "末尾"]
    assert all(
        block.type == "paragraph" and block.bbox is None
        for page in doc.pages
        for block in page.blocks
    )
    assert doc.table_count() == 0


def test_normalize_ignores_nested_page_divs(tmp_path: Path) -> None:
    """body 直下の div.page だけをページにする（添付ファイルの中身の入れ子のページは数えない）。"""
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<div class="page"><p>本体</p></div>'
        '<div class="embedded"><div class="page"><p>添付</p></div></div>'
        "</body></html>"
    )
    doc = normalize(_raw_dir(tmp_path, xhtml))
    assert len(doc.pages) == 1
    assert [block.text for block in doc.pages[0].blocks] == ["本体"]


def test_registered() -> None:
    assert "tika" in [adapter.name for adapter in ADAPTERS]
    assert NORMALIZERS["tika"] is normalize


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Apache Tika 3.3.3\n", "3.3.3"),
        ("", "unknown"),
        ("Error: Unable to access jarfile", "unknown"),
    ],
)
def test_parse_version(output: str, expected: str) -> None:
    assert parse_version(output) == expected


_JAVA_21 = 'openjdk version "21.0.12.1" 2026-08-18 LTS\n'
_JAVA_8 = 'openjdk version "1.8.0_492"\n'


def _fake_java(monkeypatch: pytest.MonkeyPatch, version_output: str) -> None:
    """`java -version` の出力を差し替える（java_ok が読む）。"""
    monkeypatch.setattr(_java, "command_output", lambda cmd: version_output)


def test_available_needs_java11_and_jar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """jar と Java 11 以上が揃って初めて使える。jar の場所は環境変数で変えられる。"""
    jar = tmp_path / "tika-app.jar"
    monkeypatch.setenv(JAR_ENV, str(jar))
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    _fake_java(monkeypatch, _JAVA_21)
    assert TikaAdapter().available() is False  # jar が無い

    jar.write_bytes(b"")
    assert TikaAdapter().available() is True

    _fake_java(monkeypatch, _JAVA_8)
    assert TikaAdapter().available() is False  # Java 8 では tika-app 3.x が動かない

    _fake_java(monkeypatch, _JAVA_21)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert TikaAdapter().available() is False  # java が無い


@pytest.mark.parametrize(
    ("output", "expected"),
    [(_JAVA_21, 21), (_JAVA_8, 8), ('java version "11" 2018-09-25\n', 11), ("", None)],
)
def test_parse_java_major(output: str, expected: int | None) -> None:
    assert _java.parse_java_major(output) == expected


def test_default_jar_is_under_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(JAR_ENV, raising=False)
    assert TikaAdapter()._jar() == Path.home() / ".cache" / "jdocbench" / JAR_NAME


def test_java_cmd_inserts_xmx_right_after_java() -> None:
    """`-Xmx` は `java` の直後に入る（#208。JVM の既定が環境ごとに変わるのを防ぐ）。"""
    assert java_cmd("-jar", "tika.jar") == ["java", f"-Xmx{JVM_XMX}", "-jar", "tika.jar"]


def test_tika_run_passes_xmx_and_records_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """jar や実機の java が無くても、run() が組み立てるコマンドと extra を確認する。"""
    monkeypatch.setenv(JAR_ENV, "/fake/tika-app.jar")
    captured: dict[str, list[str]] = {}

    def fake_run_command(cmd: list[str], env: dict[str, str] | None = None) -> perf.CommandResult:
        captured["cmd"] = cmd
        return perf.CommandResult(
            returncode=0, stdout="<html/>", stderr="", wall_time_sec=0.1, peak_rss_kb=100
        )

    monkeypatch.setattr(tika_module, "run_command", fake_run_command)
    monkeypatch.setattr(tika_module, "java_version", lambda: 'openjdk version "21"')

    out_dir = tmp_path / "raw" / "tika"
    metrics = TikaAdapter().run(Path("dummy.pdf"), out_dir)

    assert captured["cmd"][0] == "java"
    assert captured["cmd"][1] == f"-Xmx{JVM_XMX}"
    assert metrics["extra"]["jvm_xmx"] == JVM_XMX


@_needs_tika
def test_tika_writes_raw_xhtml(tmp_path: Path) -> None:
    pdf = SAMPLES / "geps-manual-internet-excerpt.pdf"
    if not pdf.is_file():
        pytest.skip("samples/geps-manual-internet-excerpt.pdf が無い")

    out_dir = tmp_path / "raw" / "tika"
    metrics = TikaAdapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0
    assert metrics["extra"]["ocr_strategy"] == "no_ocr"
    assert metrics["extra"]["jvm_xmx"] == JVM_XMX
    assert (out_dir / "tika-config.xml").is_file()

    xhtml = (out_dir / "content.xhtml").read_text(encoding="utf-8")
    assert xhtml.count('<div class="page">') == 7
    # 自前の設定で CLI 独自の埋め込み画像の抽出が外れている
    assert "embedded:image" not in xhtml

    doc = normalize(out_dir)
    assert len(doc.pages) == 7
    assert "1.3 申請に必要な書類" in doc.pages[0].lines()
