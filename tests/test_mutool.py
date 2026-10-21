import shutil
import subprocess
from pathlib import Path

import pytest

from jdocbench.adapters.mutool import STEXT_FILE, MutoolAdapter
from jdocbench.normalizers.mutool import normalize

_SAMPLES = Path(__file__).resolve().parents[1] / "samples"

# `mutool draw -F stext` の出力の形（MuPDF 1.2x）。char の quad 等は省略している
_STEXT = """<?xml version="1.0"?>
<document name="in.pdf">
<page id="page1" width="595.22" height="842">
<block bbox="72 60 300 90">
<line bbox="72 60 200 72" wmode="0" dir="1 0">
<font name="MS-Mincho" size="10.5">
<char quad="" x="72" y="70" color="#000000" c="令"/>
<char quad="" x="82" y="70" color="#000000" c="和"/>
<char quad="" x="92" y="70" color="#000000" c=" "/>
<char quad="" x="97" y="70" color="#000000" c="&amp;"/>
<char quad="" x="102" y="70" color="#000000" c="&#x1;"/>
</font>
</line>
<line bbox="72 78 300 90" wmode="0" dir="1 0">
<font name="MS-Mincho" size="10.5"><char c=" "/></font>
</line>
<line bbox="80 78 300 90" wmode="0" dir="1 0">
<font name="MS-Mincho" size="10.5"><char c="資"/><char c="産"/></font>
<font name="Arial" size="10.5"><char c=" "/><char c="1"/></font>
</line>
</block>
<image bbox="300 300 400 400"/>
</page>
<page id="page2" width="595.22" height="842">
</page>
</document>
"""


def _completed(stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["mutool", "-v"], 0, stdout="", stderr=stderr)


def test_version_parses_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed("mutool version 1.23.10\n"))
    assert MutoolAdapter().version() == "1.23.10"


def test_version_is_unknown_without_mutool(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_oserror(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        raise FileNotFoundError("mutool")

    monkeypatch.setattr(subprocess, "run", raise_oserror)
    assert MutoolAdapter().version() == "unknown"


def test_available_follows_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert MutoolAdapter().available() is False


def test_normalize_builds_one_paragraph_per_line(tmp_path: Path) -> None:
    (tmp_path / STEXT_FILE).write_text(_STEXT, encoding="utf-8")
    doc = normalize(tmp_path)
    assert doc.adapter == "mutool"
    # 空ページもページとして残す
    assert [page.number for page in doc.pages] == [1, 2]
    blocks = doc.pages[0].blocks
    # 空白だけの行は捨て、制御文字の参照（&#x1;）は解析前に落とす。font をまたいで連結する
    assert [block.text for block in blocks] == ["令和 &", "資産 1"]
    assert all(block.type == "paragraph" for block in blocks)
    assert blocks[0].bbox == (72.0, 60.0, 200.0, 72.0)
    assert blocks[1].bbox == (80.0, 78.0, 300.0, 90.0)
    assert doc.pages[1].blocks == []


@pytest.mark.skipif(shutil.which("mutool") is None, reason="mutool が無い環境ではスキップ")
def test_mutool_writes_stext(tmp_path: Path) -> None:
    pdf = _SAMPLES / "stat-go-jp-children-population-2026.pdf"
    if not pdf.exists():
        pytest.skip("samples/ に対象 PDF が無い")

    out_dir = tmp_path / "raw" / "mutool"
    metrics = MutoolAdapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0
    assert (out_dir / STEXT_FILE).is_file()
    assert MutoolAdapter().version() != "unknown"

    doc = normalize(out_dir)
    assert len(doc.pages) == 6
    assert doc.char_count() > 0
    assert any(block.bbox is not None for page in doc.pages for block in page.blocks)
