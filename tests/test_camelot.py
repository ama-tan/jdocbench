import importlib.metadata
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jdocbench.adapters import ADAPTERS
from jdocbench.adapters.camelot import CamelotAdapter, CamelotStreamAdapter, _table_record
from jdocbench.normalizers import NORMALIZERS
from jdocbench.normalizers.camelot import normalize

_SAMPLES = Path(__file__).resolve().parents[1] / "samples"

_needs_camelot = pytest.mark.skipif(
    importlib.util.find_spec("camelot") is None,
    reason="camelot が無い環境ではスキップ（オプトイン extra）",
)


def _fake_table(**overrides: object) -> SimpleNamespace:
    """camelot の Table を模した最小のオブジェクト（_table_record が読む属性だけ）。"""
    fields: dict[str, object] = {
        "page": "2",
        "flavor": "lattice",
        "_bbox": (10, 20, 30, 40),
        "pdf_size": (595.0, 842.0),
        "parsing_report": {"accuracy": 99.0, "whitespace": 0.0},
        "df": SimpleNamespace(values=SimpleNamespace(tolist=lambda: [["a", "b"]])),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_available_follows_find_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert CamelotAdapter().available() is False


def test_adapters_have_fixed_flavors() -> None:
    """flavor はアダプタごとに固定（文書によって切り替えない）。両方とも ADAPTERS に載る。"""
    assert (CamelotAdapter.name, CamelotAdapter.flavor) == ("camelot", "lattice")
    assert (CamelotStreamAdapter.name, CamelotStreamAdapter.flavor) == ("camelot_stream", "stream")
    names = [adapter.name for adapter in ADAPTERS]
    assert "camelot" in names
    assert "camelot_stream" in names
    assert "camelot_stream" in NORMALIZERS


def test_table_record_keeps_bbox() -> None:
    record = _table_record(_fake_table())
    assert record == {
        "page": 2,
        "flavor": "lattice",
        "bbox": [10.0, 20.0, 30.0, 40.0],
        "page_height": 842.0,
        "accuracy": 99.0,
        "whitespace": 0.0,
        "cells": [["a", "b"]],
    }


@pytest.mark.parametrize("bbox", [None, (1, 2, 3), ("x", 2, 3, 4)])
def test_table_record_without_valid_bbox_keeps_table(bbox: object) -> None:
    """非公開属性 `_bbox` の形が変わっても、表は捨てずに bbox を None にする。"""
    record = _table_record(_fake_table(_bbox=bbox))
    assert record["bbox"] is None
    assert record["cells"] == [["a", "b"]]


def test_table_record_without_bbox_attribute_keeps_table() -> None:
    """`_bbox` 属性そのものが無くなっても、表は捨てずに bbox を None にする。"""
    table = _fake_table()
    del table._bbox
    record = _table_record(table)
    assert record["bbox"] is None
    assert record["page"] == 2


def test_version_is_unknown_without_camelot(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    assert CamelotAdapter().version() == "unknown"


@_needs_camelot
def test_camelot_writes_raw_tables(tmp_path: Path) -> None:
    pdf = _SAMPLES / "geps-manual-internet-excerpt.pdf"
    if not pdf.is_file():
        pytest.skip("samples/geps-manual-internet-excerpt.pdf が無い")

    out_dir = tmp_path / "raw" / "camelot"
    metrics = CamelotAdapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0
    assert metrics["extra"] == {"flavor": "lattice"}

    raw = json.loads((out_dir / "tables.json").read_text(encoding="utf-8"))
    assert raw["flavor"] == "lattice"
    assert raw["page_count"] == 7
    assert len(raw["tables"]) >= 1, "表が 1 つも検出されていない"
    first = raw["tables"][0]
    assert first["page"] == 1
    assert first["flavor"] == "lattice"
    assert len(first["bbox"]) == 4
    # 日本語セルが文字化けせず無加工で入っている
    assert first["cells"][0] == ["書類", "法人", "個人"]

    doc = normalize(out_dir)
    assert len(doc.pages) == 7
    assert doc.table_count() == len(raw["tables"])
    assert all(block.type == "table" for page in doc.pages for block in page.blocks)


@_needs_camelot
def test_camelot_does_not_fall_back_to_stream(tmp_path: Path) -> None:
    """罫線の無い文書（2 段組み論文）では lattice のまま 0 表になる（stream に切り替えない）。"""
    pdf = _SAMPLES / "jacn-29-0039-katakana-transliteration-2025.pdf"
    if not pdf.is_file():
        pytest.skip("samples/jacn-29-0039-katakana-transliteration-2025.pdf が無い")

    out_dir = tmp_path / "raw" / "camelot"
    metrics = CamelotAdapter().run(pdf, out_dir)
    assert metrics["extra"] == {"flavor": "lattice"}

    raw = json.loads((out_dir / "tables.json").read_text(encoding="utf-8"))
    assert raw["flavor"] == "lattice"
    assert raw["tables"] == []
    assert normalize(out_dir).table_count() == 0


@_needs_camelot
def test_camelot_stream_writes_raw_tables(tmp_path: Path) -> None:
    """stream 固定。罫線の無い文書でも、空白の並びから表（本文を含む）を推定する。"""
    pdf = _SAMPLES / "jacn-29-0039-katakana-transliteration-2025.pdf"
    if not pdf.is_file():
        pytest.skip("samples/jacn-29-0039-katakana-transliteration-2025.pdf が無い")

    out_dir = tmp_path / "raw" / "camelot_stream"
    metrics = CamelotStreamAdapter().run(pdf, out_dir)
    assert metrics["extra"] == {"flavor": "stream"}

    raw = json.loads((out_dir / "tables.json").read_text(encoding="utf-8"))
    assert raw["flavor"] == "stream"
    assert len(raw["tables"]) >= 1, "stream で表が 1 つも検出されていない"
    assert all(table["flavor"] == "stream" for table in raw["tables"])
    assert all(len(table["bbox"]) == 4 for table in raw["tables"])

    doc = NORMALIZERS["camelot_stream"](out_dir)
    assert doc.adapter == "camelot_stream"
    assert doc.table_count() == len(raw["tables"])
