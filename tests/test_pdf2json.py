import json
import shutil
import subprocess
from pathlib import Path

import pytest
from _samples import SAMPLES

from jdocbench.adapters.pdf2json import Pdf2jsonAdapter
from jdocbench.normalizers.pdf2json import normalize


def _completed(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["pdf2json", "--version"], 0, stdout=stdout, stderr="")


def test_version_returns_first_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed("4.1.0\n"))
    assert Pdf2jsonAdapter().version() == "4.1.0"


def test_version_is_unknown_without_pdf2json(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_oserror(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        raise FileNotFoundError("pdf2json")

    monkeypatch.setattr(subprocess, "run", raise_oserror)
    assert Pdf2jsonAdapter().version() == "unknown"


def test_version_is_unknown_on_empty_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed("\n"))
    assert Pdf2jsonAdapter().version() == "unknown"


def test_available_follows_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert Pdf2jsonAdapter().available() is False


@pytest.mark.skipif(shutil.which("pdf2json") is None, reason="pdf2json が無い環境ではスキップ")
def test_pdf2json_writes_json(tmp_path: Path) -> None:
    # samples/ の先頭（辞書順）は AES-256 で暗号化された jacn-* で、pdf2json は 4.0.3 / 4.1.0 の
    # どちらも `unsupported encryption algorithm` で失敗する。アダプタの疎通を見るテストなので、
    # 他のアダプタのテストと同じく暗号化していないサンプルを名指しする
    pdf = SAMPLES / "mof-budget-summary-fy2026.pdf"
    if not pdf.is_file():
        pytest.skip("samples/ に対象 PDF が無い")

    out_dir = tmp_path / "raw" / "pdf2json"
    metrics = Pdf2jsonAdapter().run(pdf, out_dir)
    assert metrics["wall_time_sec"] > 0
    assert metrics["peak_rss_kb"] > 0

    # 出力は <入力の basename>.json。加工していないので Transcoder / Meta / Pages を持つ
    jsons = sorted(out_dir.glob("*.json"))
    assert jsons, "JSON が出力されていない"
    assert jsons[0].name == f"{pdf.stem}.json"

    data = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert len(data["Pages"]) >= 1

    # raw をそのまま正規化できる（ページ数は raw と一致し、テキストが IR に載る）
    doc = normalize(out_dir)
    assert len(doc.pages) == len(data["Pages"])
    assert doc.char_count() > 0, "テキストが IR に載っていない"
