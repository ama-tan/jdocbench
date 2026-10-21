import json
import shutil
from pathlib import Path

import pytest

from jdocbench import perf
from jdocbench.adapters import ADAPTERS, _java
from jdocbench.adapters import tabula as tabula_module
from jdocbench.adapters._java import JVM_XMX
from jdocbench.adapters.tabula import (
    JAR_ENV,
    TabulaAdapter,
    TabulaStreamAdapter,
    parse_version,
)
from jdocbench.normalizers import NORMALIZERS
from jdocbench.normalizers.tabula import normalize

_SAMPLES = Path(__file__).resolve().parents[1] / "samples"

_needs_tabula = pytest.mark.skipif(
    not TabulaAdapter().available(),
    reason="JDK か tabula の jar（JDOCBENCH_TABULA_JAR）が無い環境ではスキップ",
)


def _cell(text: object) -> dict:
    return {"top": 0.0, "left": 0.0, "width": 1.0, "height": 1.0, "text": text}


# TabulaPages.java の出力を縮めたもの（表は tabula-java の JSONWriter の形）
_RAW = {
    "mode": "lattice",
    "pages": [
        {
            "page": 1,
            "tables": [
                {
                    "extraction_method": "lattice",
                    "top": 178.6,
                    "left": 79.7,
                    "width": 459.1,
                    "height": 210.2,
                    "right": 538.8,
                    "bottom": 388.8,
                    "data": [
                        [_cell("書類"), _cell(""), _cell("法人")],
                        [_cell("登記事項証明書\r(履歴事項全部証明書)"), _cell(None), _cell("○")],
                    ],
                }
            ],
        },
        {"page": 2, "tables": []},
    ],
}


def _raw_dir(tmp_path: Path, raw: dict, name: str = "tabula") -> Path:
    raw_dir = tmp_path / "raw" / name
    raw_dir.mkdir(parents=True)
    (raw_dir / "tables.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return raw_dir


def test_normalize_tables_only(tmp_path: Path) -> None:
    """table ブロックだけ。セルは無加工（文字列でないものだけ ""）、bbox は上原点のまま。"""
    doc = normalize(_raw_dir(tmp_path, _RAW))
    assert doc.adapter == "tabula"
    assert [page.number for page in doc.pages] == [1, 2]
    assert doc.pages[1].blocks == []
    [block] = doc.pages[0].blocks
    assert block.type == "table"
    assert block.rows == [["書類", "", "法人"], ["登記事項証明書\r(履歴事項全部証明書)", "", "○"]]
    assert block.bbox == (79.7, 178.6, 538.8, 388.8)
    assert block.extra == {"mode": "lattice"}
    assert doc.pages[0].lines() == ["書類 法人", "登記事項証明書 (履歴事項全部証明書) ○"]


def test_normalize_drops_pages_without_valid_number(tmp_path: Path) -> None:
    raw = {"mode": "stream", "pages": [{"page": 0, "tables": [{"data": [[_cell("x")]]}]}]}
    doc = normalize(_raw_dir(tmp_path, raw))
    assert doc.pages == []


def test_normalize_missing_bbox_keeps_table(tmp_path: Path) -> None:
    raw = {"mode": "stream", "pages": [{"page": 1, "tables": [{"data": [[_cell("x")]]}]}]}
    [block] = normalize(_raw_dir(tmp_path, raw)).pages[0].blocks
    assert block.bbox is None
    assert block.rows == [["x"]]


def test_tabula_stream_reuses_normalizer(tmp_path: Path) -> None:
    doc = NORMALIZERS["tabula_stream"](_raw_dir(tmp_path, _RAW, "tabula_stream"))
    assert doc.adapter == "tabula_stream"
    assert doc.table_count() == 1


def test_adapters_have_fixed_modes() -> None:
    """mode はアダプタごとに固定（文書によって切り替えない。camelot と同じ #135）。"""
    assert (TabulaAdapter.name, TabulaAdapter.mode) == ("tabula", "lattice")
    assert (TabulaStreamAdapter.name, TabulaStreamAdapter.mode) == ("tabula_stream", "stream")
    names = [adapter.name for adapter in ADAPTERS]
    assert "tabula" in names
    assert "tabula_stream" in names
    assert NORMALIZERS["tabula"] is normalize


@pytest.mark.parametrize(
    ("output", "expected"),
    [("tabula 1.0.5 (c) 2012-2020 Manuel Aristarán\n", "1.0.5"), ("", "unknown")],
)
def test_parse_version(output: str, expected: str) -> None:
    assert parse_version(output) == expected


def test_available_needs_jdk_and_jar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Java 11 以上と javac（単一ソースファイル実行に JDK が要る）と jar が揃って初めて使える。"""
    jar = tmp_path / "tabula.jar"
    jar.write_bytes(b"")
    monkeypatch.setenv(JAR_ENV, str(jar))
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(_java, "command_output", lambda cmd: 'openjdk version "21.0.12.1"\n')
    assert TabulaAdapter().available() is True

    monkeypatch.setattr(_java, "command_output", lambda cmd: 'openjdk version "1.8.0_492"\n')
    assert TabulaAdapter().available() is False  # Java 8 には単一ソースファイル実行が無い
    monkeypatch.setattr(_java, "command_output", lambda cmd: 'openjdk version "21.0.12.1"\n')

    monkeypatch.setattr(shutil, "which", lambda name: None if name == "javac" else "/usr/bin/java")
    assert TabulaAdapter().available() is False  # JRE だけ

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert TabulaAdapter().available() is False  # Java が無い

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    jar.unlink()
    assert TabulaStreamAdapter().available() is False  # jar が無い


def test_tabula_run_passes_xmx_and_records_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """jar や実機の java が無くても、run() が組み立てるコマンドと extra を確認する（#208）。"""
    monkeypatch.setenv(JAR_ENV, "/fake/tabula.jar")
    captured: dict[str, list[str]] = {}

    def fake_run_command(cmd: list[str], env: dict[str, str] | None = None) -> perf.CommandResult:
        captured["cmd"] = cmd
        # tables.json は実引数の末尾（run() が実際の java プロセスに書かせるのと同じ場所）
        Path(cmd[-1]).write_text(json.dumps(_RAW), encoding="utf-8")
        return perf.CommandResult(
            returncode=0, stdout="", stderr="", wall_time_sec=0.1, peak_rss_kb=100
        )

    monkeypatch.setattr(tabula_module, "run_command", fake_run_command)
    monkeypatch.setattr(tabula_module, "java_version", lambda: 'openjdk version "21"')

    out_dir = tmp_path / "raw" / "tabula"
    metrics = TabulaAdapter().run(Path("dummy.pdf"), out_dir)

    assert captured["cmd"][0] == "java"
    assert captured["cmd"][1] == f"-Xmx{JVM_XMX}"
    assert metrics["extra"]["jvm_xmx"] == JVM_XMX


@_needs_tabula
@pytest.mark.parametrize(
    ("adapter", "mode"), [(TabulaAdapter(), "lattice"), (TabulaStreamAdapter(), "stream")]
)
def test_tabula_writes_raw_tables(tmp_path: Path, adapter: TabulaAdapter, mode: str) -> None:
    pdf = _SAMPLES / "geps-manual-internet-excerpt.pdf"
    if not pdf.is_file():
        pytest.skip("samples/geps-manual-internet-excerpt.pdf が無い")

    out_dir = tmp_path / "raw" / adapter.name
    metrics = adapter.run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0
    assert metrics["extra"]["mode"] == mode
    assert metrics["extra"]["jvm_xmx"] == JVM_XMX

    raw = json.loads((out_dir / "tables.json").read_text(encoding="utf-8"))
    assert raw["mode"] == mode
    # 表の無いページも含めて全ページが並ぶ
    assert [page["page"] for page in raw["pages"]] == list(range(1, 8))
    assert all(
        table["extraction_method"] == mode for page in raw["pages"] for table in page["tables"]
    )

    doc = normalize(out_dir)
    assert len(doc.pages) == 7
    assert doc.table_count() >= 1
    assert all(block.type == "table" for page in doc.pages for block in page.blocks)


@_needs_tabula
def test_tabula_lattice_finds_ruled_table(tmp_path: Path) -> None:
    """罫線つきの表（GEPS p1 の提出書類の表）を lattice で取れる。"""
    pdf = _SAMPLES / "geps-manual-internet-excerpt.pdf"
    if not pdf.is_file():
        pytest.skip("samples/geps-manual-internet-excerpt.pdf が無い")

    out_dir = tmp_path / "raw" / "tabula"
    TabulaAdapter().run(pdf, out_dir)
    lines = normalize(out_dir).pages[0].lines()
    assert "書類 法人 個人" in lines
