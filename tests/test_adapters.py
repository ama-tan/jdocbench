import json
import shutil
import subprocess
from pathlib import Path

import pytest
from _samples import SAMPLES

from jdocbench.adapters.pdftotext import PdftotextAdapter, parse_version
from jdocbench.cli import _run_one
from jdocbench.ir import Block, Document, Page
from jdocbench.metrics import JNF_CLASSES, JNF_VERSION

_DUMMY_PDF = b"%PDF-1.4\n"


class FakeAdapter:
    """テスト用のアダプタ。available / run の挙動を差し替えられる。"""

    def __init__(
        self,
        name: str,
        *,
        available: bool = True,
        error: Exception | None = None,
        extra: dict | None = None,
        skipped: str | None = None,
    ):
        self.name = name
        self._available = available
        self._error = error
        self._extra = extra
        self._skipped = skipped
        self.ran = False

    def version(self) -> str:
        return "1.2.3"

    def available(self) -> bool:
        return self._available

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        self.ran = True
        if self._error is not None:
            raise self._error
        if self._skipped is not None:
            return {"skipped": self._skipped}
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "text.txt").write_text("ダミー出力", encoding="utf-8")
        metrics: dict[str, object] = {"wall_time_sec": 0.5, "peak_rss_kb": 1234}
        if self._extra is not None:
            metrics["extra"] = self._extra
        return metrics


def _fake_document(_raw_dir: Path) -> Document:
    """固定の Document を返すフェイク正規化関数。raw の中身は見ない。"""
    return Document(
        adapter="fake",
        pages=[
            Page(
                number=1,
                blocks=[
                    Block(type="paragraph", text="あいうえお"),
                    Block(type="table", rows=[["A", "B"]]),
                ],
            )
        ],
    )


def _three_page_document(_raw_dir: Path) -> Document:
    """3 ページの Document を返すフェイク正規化関数（pages 補完の検証用）。"""
    return Document(
        adapter="fake",
        pages=[Page(number=number, blocks=[]) for number in (1, 2, 3)],
    )


def _failing_normalizer(_raw_dir: Path) -> Document:
    raise ValueError("正規化に失敗しました")


def _run_with(
    tmp_path: Path, adapters: list, normalizers: dict | None = None, gt: Path | None = None
) -> tuple[int, dict, Path]:
    """ダミー PDF に対して _run_one を実行し、終了コードと run.json を返す。"""
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(_DUMMY_PDF)
    out = tmp_path / "results"

    code = _run_one(
        pdf,
        out,
        None if gt is None else str(gt),
        adapters,
        {} if normalizers is None else normalizers,
    )
    run = json.loads((out / "sample" / "run.json").read_text(encoding="utf-8"))
    return code, run, out / "sample"


def test_adapter_ok_writes_raw_output(tmp_path: Path) -> None:
    code, run, doc_dir = _run_with(tmp_path, [FakeAdapter("fake")])

    assert code == 0
    assert (doc_dir / "raw" / "fake" / "text.txt").is_file()
    assert run["adapters"]["fake"]["status"] == "ok"
    assert run["adapters"]["fake"]["version"] == "1.2.3"
    assert run["adapters"]["fake"]["output_dir"] == "raw/fake"
    assert run["adapters"]["fake"]["wall_time_sec"] == 0.5
    assert run["adapters"]["fake"]["peak_rss_kb"] == 1234


def test_adapter_extra_is_recorded_separately(tmp_path: Path) -> None:
    """run() が返した extra は run.json の extra にそのまま入り、計測値とは混ざらない。"""
    extra = {"ocr": False, "models": "1.2.3"}
    _code, run, _doc_dir = _run_with(tmp_path, [FakeAdapter("fake", extra=extra)])

    result = run["adapters"]["fake"]
    assert result["extra"] == extra
    # 計測値と同じ階層にばら撒かれない
    assert "ocr" not in result
    assert "models" not in result
    assert result["wall_time_sec"] == 0.5
    assert result["peak_rss_kb"] == 1234


def test_adapter_without_extra_has_no_extra_key(tmp_path: Path) -> None:
    _code, run, _doc_dir = _run_with(tmp_path, [FakeAdapter("fake")])

    assert "extra" not in run["adapters"]["fake"]


def test_run_writes_report_md(tmp_path: Path) -> None:
    _code, _run, doc_dir = _run_with(tmp_path, [FakeAdapter("fake")])

    report = doc_dir / "report.md"
    assert report.is_file()
    assert report.read_text(encoding="utf-8").startswith("# JDocBench Report")


def test_unavailable_adapter_is_skipped(tmp_path: Path) -> None:
    adapter = FakeAdapter("fake", available=False)
    code, run, doc_dir = _run_with(tmp_path, [adapter])

    assert code == 0
    assert adapter.ran is False
    assert run["adapters"]["fake"]["status"] == "skipped"
    assert not (doc_dir / "raw").exists()


def test_adapter_returning_skipped_is_recorded_with_reason(tmp_path: Path) -> None:
    """run() が skipped を返したら error ではなく理由つきの skipped になる。"""
    adapter = FakeAdapter("fake", skipped="text layer present (5382 chars)")
    code, run, doc_dir = _run_with(tmp_path, [adapter])

    assert code == 0
    assert adapter.ran is True
    result = run["adapters"]["fake"]
    assert result["status"] == "skipped"
    assert result["reason"] == "text layer present (5382 chars)"
    # 計測値も正規化結果も残らない
    assert "wall_time_sec" not in result
    assert "chars" not in result
    assert not (doc_dir / "normalized").exists()


def test_adapter_error_does_not_stop_other_adapters(tmp_path: Path) -> None:
    error = subprocess.CalledProcessError(1, ["failing"], stderr="変換に失敗しました")
    failing = FakeAdapter("failing", error=error)
    healthy = FakeAdapter("healthy")

    code, run, doc_dir = _run_with(tmp_path, [failing, healthy])

    assert code == 0
    assert run["adapters"]["failing"]["status"] == "error"
    assert "変換に失敗しました" in run["adapters"]["failing"]["message"]
    assert healthy.ran is True
    assert run["adapters"]["healthy"]["status"] == "ok"
    assert (doc_dir / "raw" / "healthy" / "text.txt").is_file()


def test_normalizer_writes_normalized_json_and_counts(tmp_path: Path) -> None:
    code, run, doc_dir = _run_with(
        tmp_path, [FakeAdapter("fake")], normalizers={"fake": _fake_document}
    )

    assert code == 0
    assert run["adapters"]["fake"]["status"] == "ok"
    # 段落 5 文字 + 表の行「A B」3 文字（chars は Document.lines() と同じ平坦化で数える）
    assert run["adapters"]["fake"]["chars"] == 8
    assert run["adapters"]["fake"]["tables"] == 1

    doc = json.loads((doc_dir / "normalized" / "fake.json").read_text(encoding="utf-8"))
    assert doc["pages"][0]["blocks"][0]["text"] == "あいうえお"
    # raw は正規化で変更されない
    assert (doc_dir / "raw" / "fake" / "text.txt").read_text(encoding="utf-8") == "ダミー出力"


def test_adapter_without_normalizer_has_no_chars(tmp_path: Path) -> None:
    code, run, doc_dir = _run_with(tmp_path, [FakeAdapter("fake")], normalizers={})

    assert code == 0
    assert run["adapters"]["fake"]["status"] == "ok"
    assert "chars" not in run["adapters"]["fake"]
    assert "normalize_error" not in run["adapters"]["fake"]
    assert not (doc_dir / "normalized").exists()


def test_normalize_error_is_recorded_and_does_not_stop_others(tmp_path: Path) -> None:
    code, run, doc_dir = _run_with(
        tmp_path,
        [FakeAdapter("broken"), FakeAdapter("healthy")],
        normalizers={"broken": _failing_normalizer, "healthy": _fake_document},
    )

    assert code == 0
    # 正規化に失敗してもアダプタ自体は ok のまま
    assert run["adapters"]["broken"]["status"] == "ok"
    assert "正規化に失敗しました" in run["adapters"]["broken"]["normalize_error"]
    assert "chars" not in run["adapters"]["broken"]
    assert not (doc_dir / "normalized" / "broken.json").exists()

    assert run["adapters"]["healthy"]["chars"] == 8
    assert (doc_dir / "normalized" / "healthy.json").is_file()


def test_pages_backfilled_from_normalized_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pdfinfo が無く pages が None でも、正規化結果からページ数を補完する。"""
    monkeypatch.setattr("jdocbench.cli._page_count", lambda _pdf: None)

    code, run, _doc_dir = _run_with(
        tmp_path,
        [FakeAdapter("normalizing"), FakeAdapter("plain")],
        normalizers={"normalizing": _three_page_document},
    )

    assert code == 0
    assert run["pages"] == 3
    assert run["pages_source"] == "normalizing"
    assert run["adapters"]["normalizing"]["sec_per_page"] == round(0.5 / 3, 6)
    # 正規化を持たないアダプタにも補完後の pages が効く（sec/page はループ後にまとめて計算する）
    assert run["adapters"]["plain"]["sec_per_page"] == round(0.5 / 3, 6)


def test_pages_stays_none_without_pdfinfo_and_normalizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """補完元が無ければ pages / pages_source は None のままで sec_per_page も入らない。"""
    monkeypatch.setattr("jdocbench.cli._page_count", lambda _pdf: None)

    code, run, _doc_dir = _run_with(tmp_path, [FakeAdapter("fake")], normalizers={})

    assert code == 0
    assert run["pages"] is None
    assert run["pages_source"] is None
    assert "sec_per_page" not in run["adapters"]["fake"]


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("pdftotext version 21.01.0\nCopyright 2005-2021 The Poppler Developers\n", "21.01.0"),
        ("pdftotext version 24.02.0", "24.02.0"),
        ("", "unknown"),
        ("pdftotext\n", "unknown"),
    ],
)
def test_parse_version(stderr: str, expected: str) -> None:
    assert parse_version(stderr) == expected


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext が無い環境ではスキップ")
def test_pdftotext_extracts_text(tmp_path: Path) -> None:
    pdf = SAMPLES / "stat-go-jp-children-population-2026.pdf"
    if not pdf.exists():
        pytest.skip("samples/ に対象 PDF が無い")
    out = tmp_path / "results"
    # 既定のアダプタ一覧（= 実 pdftotext）で CLI を通して実行する
    assert _run_one(pdf, out, None, None, None) == 0

    doc_dir = out / pdf.stem
    run = json.loads((doc_dir / "run.json").read_text(encoding="utf-8"))
    # pdftotext がある環境には pdfinfo もあるため、ページ数は pdfinfo 由来になる
    assert run["pages"] == 6
    assert run["pages_source"] == "pdfinfo"
    result = run["adapters"][PdftotextAdapter.name]
    assert result["status"] == "ok"
    assert result["version"] != "unknown"
    assert result["output_dir"] == "raw/pdftotext"
    assert result["wall_time_sec"] > 0
    assert result["peak_rss_kb"] > 0
    assert result["chars"] > 0
    assert "normalize_error" not in result

    text_file = doc_dir / "raw" / "pdftotext" / "text.txt"
    assert text_file.is_file()
    assert text_file.read_text(encoding="utf-8", errors="replace").strip() != ""

    report = (doc_dir / "report.md").read_text(encoding="utf-8")
    assert any(line.startswith("| pdftotext |") for line in report.splitlines())

    view = (doc_dir / "view.html").read_text(encoding="utf-8")
    assert "<h1>" in view
    assert view.count("<section") == 6

    normalized = doc_dir / "normalized" / "pdftotext.json"
    assert normalized.is_file()
    doc = json.loads(normalized.read_text(encoding="utf-8"))
    assert doc["adapter"] == "pdftotext"
    assert doc["pages"]
    assert any(
        block["type"] == "paragraph" and block["text"]
        for page in doc["pages"]
        for block in page["blocks"]
    )


def _ground_truth(tmp_path: Path, blocks: list[Block]) -> Path:
    """テスト用の Ground Truth ファイルを書いてパスを返す。"""
    gt = Document(adapter="ground_truth", pages=[Page(number=1, blocks=blocks)])
    path = tmp_path / "gt.json"
    path.write_text(gt.to_json(), encoding="utf-8")
    return path


def test_dsqs_is_calculated_when_ground_truth_is_given(tmp_path: Path) -> None:
    # フェイク正規化と同じ内容の GT なので満点になる
    gt = _ground_truth(
        tmp_path,
        [Block(type="paragraph", text="あいうえお"), Block(type="table", rows=[["A", "B"]])],
    )

    code, run, doc_dir = _run_with(
        tmp_path, [FakeAdapter("fake")], normalizers={"fake": _fake_document}, gt=gt
    )

    assert code == 0
    assert run["ground_truth"] == str(gt)
    assert run["adapters"]["fake"]["dsqs"]["total"] == 100.0

    scores = json.loads((doc_dir / "metrics" / "fake.json").read_text(encoding="utf-8"))
    assert scores == {
        "cf": 1.0,
        "sed": 1.0,
        "tsf": 1.0,
        "tsf_tables": [
            {
                "page": 1,
                "gt_index": 0,
                "doc_index": 0,
                "doc_indices": [0],
                "shared": False,
                "score": 1.0,
            }
        ],
        # GT の行は段落 1 行 + 表 1 行の 2 行で、順序対が作れるので ROA が出る
        "roa": 1.0,
        "roa_match_rate": 1.0,
        "roa_exact_rate": 1.0,
        "roa_effective": 1.0,
        "total": 100.0,
        # JNF は metrics/<adapter>.json に別に記録する（表記対象文字が無いので None）
        "jnf": None,
        "jnf_classes": {name: {"gt": 0, "exact": 0, "folded": 0} for name in JNF_CLASSES},
        "jnf_version": JNF_VERSION,
    }


def test_dsqs_reflects_difference_from_ground_truth(tmp_path: Path) -> None:
    gt = _ground_truth(
        tmp_path,
        [Block(type="paragraph", text="あいうえお"), Block(type="table", rows=[["A", "X"]])],
    )

    _code, run, _doc_dir = _run_with(
        tmp_path, [FakeAdapter("fake")], normalizers={"fake": _fake_document}, gt=gt
    )

    dsqs = run["adapters"]["fake"]["dsqs"]
    # 表の行も text に入るので「A X」対「A B」の 1 文字差が CF に出る（9 文字中 1 置換）
    assert dsqs["cf"] == round(1 - 1 / 9, 4)
    assert dsqs["sed"] == 1.0
    assert dsqs["tsf"] == 0.5  # GT の 2 セルのうち 1 つだけ再現できている
    assert dsqs["tsf_tables"] == [
        {
            "page": 1,
            "gt_index": 0,
            "doc_index": 0,
            "doc_indices": [0],
            "shared": False,
            "score": 0.5,
        }
    ]
    assert dsqs["total"] < 100.0


def test_no_dsqs_without_ground_truth(tmp_path: Path) -> None:
    code, run, doc_dir = _run_with(
        tmp_path, [FakeAdapter("fake")], normalizers={"fake": _fake_document}
    )

    assert code == 0
    assert run["ground_truth"] is None
    assert "dsqs" not in run["adapters"]["fake"]
    assert not (doc_dir / "metrics").exists()


def test_broken_ground_truth_is_recorded_and_does_not_stop_run(tmp_path: Path) -> None:
    gt = tmp_path / "broken.json"
    gt.write_text("{壊れた JSON", encoding="utf-8")

    code, run, doc_dir = _run_with(
        tmp_path, [FakeAdapter("fake")], normalizers={"fake": _fake_document}, gt=gt
    )

    assert code == 0
    assert run["adapters"]["fake"]["status"] == "ok"
    assert "dsqs" not in run["adapters"]["fake"]
    assert run["adapters"]["fake"]["dsqs_error"]
    assert not (doc_dir / "metrics").exists()
    assert (doc_dir / "report.md").is_file()
