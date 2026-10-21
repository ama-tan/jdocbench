import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from jdocbench import cli
from jdocbench.cli import main

_DUMMY_PDF = b"%PDF-1.4\n"


def test_run_writes_run_json(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(_DUMMY_PDF)
    out = tmp_path / "results"

    assert main(["run", str(pdf), "--out", str(out)]) == 0

    run_json = out / "sample" / "run.json"
    assert run_json.is_file()

    run = json.loads(run_json.read_text(encoding="utf-8"))
    assert run["sha256"] == hashlib.sha256(_DUMMY_PDF).hexdigest()
    assert run["input"] == str(pdf.resolve())
    assert run["size_bytes"] == len(_DUMMY_PDF)
    assert "pdftotext" in run["adapters"]
    # 実行環境（企画書 §49）。pages は pdfinfo も正規化結果も無ければ None
    assert run["cpu"]
    assert run["ram_total_bytes"] > 0
    # 埋め込みの無いフォントの代替先（#157）。fc-list が無い環境では None
    assert run["fonts_ja"] is None or isinstance(run["fonts_ja"], list)
    assert "pages" in run
    assert "pages_source" in run
    # Ground Truth が無い文書では DSQS-JP を計算しない
    assert run["ground_truth"] is None
    assert run["ground_truth_sha256"] is None
    # Profile が取れない PDF（ダミー）や pymupdf の無い環境でも run は止めず、error を記録する
    profile = json.loads((out / "sample" / "profile.json").read_text(encoding="utf-8"))
    assert "error" in profile
    # どの PDF の Profile かを検証できるよう、失敗時も入力のパスと SHA-256 を持つ（#158）
    assert profile["source"] == str(pdf)
    assert profile["sha256"] == run["sha256"]


def test_run_records_ground_truth_sha256(tmp_path: Path) -> None:
    """GT の SHA-256 が run.json に記録され、GT を 1 文字変えると値が変わる（#151 受入条件）。"""
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(_DUMMY_PDF)
    out = tmp_path / "results"
    gt = tmp_path / "gt.json"
    gt.write_text('{"adapter": "ground_truth", "pages": []}', encoding="utf-8")

    assert main(["run", str(pdf), "--out", str(out), "--gt", str(gt)]) == 0
    run = json.loads((out / "sample" / "run.json").read_text(encoding="utf-8"))

    assert run["ground_truth"] == str(gt)
    expected_sha256 = hashlib.sha256(gt.read_bytes()).hexdigest()
    assert run["ground_truth_sha256"] == expected_sha256

    # GT を 1 文字変えると SHA-256 が変わる
    gt.write_text('{"adapter": "ground_truth", "pages": [] }', encoding="utf-8")
    out2 = tmp_path / "results2"
    assert main(["run", str(pdf), "--out", str(out2), "--gt", str(gt)]) == 0
    run2 = json.loads((out2 / "sample" / "run.json").read_text(encoding="utf-8"))

    assert run2["ground_truth_sha256"] != expected_sha256
    assert run2["ground_truth_sha256"] == hashlib.sha256(gt.read_bytes()).hexdigest()


def test_run_continues_when_ground_truth_is_unreadable(tmp_path: Path) -> None:
    """GT が存在しても読めない（権限など）場合は run 全体を落とさず、SHA-256 は None にする。"""
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(_DUMMY_PDF)
    out = tmp_path / "results"
    gt = tmp_path / "gt.json"
    gt.write_text('{"adapter": "ground_truth", "pages": []}', encoding="utf-8")
    gt.chmod(0o000)

    try:
        exit_code = main(["run", str(pdf), "--out", str(out), "--gt", str(gt)])
    finally:
        gt.chmod(0o644)

    assert exit_code == 0
    run_json = out / "sample" / "run.json"
    assert run_json.is_file()
    run = json.loads(run_json.read_text(encoding="utf-8"))
    assert run["ground_truth"] == str(gt)
    assert run["ground_truth_sha256"] is None


def test_run_missing_file_exits_2(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"

    assert main(["run", str(missing), "--out", str(tmp_path / "results")]) == 2


def test_run_processes_multiple_pdfs(tmp_path: Path) -> None:
    pdfs = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    for pdf in pdfs:
        pdf.write_bytes(_DUMMY_PDF)
    out = tmp_path / "results"

    assert main(["run", *(str(pdf) for pdf in pdfs), "--out", str(out)]) == 0

    for pdf in pdfs:
        assert (out / pdf.stem / "run.json").is_file()


def test_run_continues_after_missing_file_and_exits_2(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"
    present = tmp_path / "present.pdf"
    present.write_bytes(_DUMMY_PDF)
    out = tmp_path / "results"

    assert main(["run", str(missing), str(present), "--out", str(out)]) == 2

    # 先頭が失敗しても後続は処理される
    assert (out / "present" / "run.json").is_file()
    assert not (out / "missing").exists()


def test_run_writes_summary(tmp_path: Path) -> None:
    pdfs = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    for pdf in pdfs:
        pdf.write_bytes(_DUMMY_PDF)
    out = tmp_path / "results"

    assert main(["run", *(str(pdf) for pdf in pdfs), "--out", str(out)]) == 0

    assert (out / "summary.md").read_text(encoding="utf-8").startswith("# JDocBench Summary\n")
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert [document["stem"] for document in summary["documents"]] == ["a", "b"]
    # GT の無い文書では None（#151）
    assert summary["documents"][0]["ground_truth_sha256"] is None


def _write_pdfs(tmp_path: Path, *stems: str) -> list[Path]:
    """ダミー PDF を作る。"""
    pdfs = [tmp_path / f"{stem}.pdf" for stem in stems]
    for pdf in pdfs:
        pdf.write_bytes(_DUMMY_PDF)
    return pdfs


def _read_summaries(out: Path) -> tuple[str, str]:
    """summary.md と summary.json の中身。"""
    return (
        (out / "summary.md").read_text(encoding="utf-8"),
        (out / "summary.json").read_text(encoding="utf-8"),
    )


def test_summary_rebuilds_same_summary_as_run(tmp_path: Path) -> None:
    # run が書いた summary と、同じ結果から summary サブコマンドで作り直したものが一致する（#190）
    pdfs = _write_pdfs(tmp_path, "b", "a")
    out = tmp_path / "results"
    assert main(["run", *(str(pdf) for pdf in pdfs), "--out", str(out)]) == 0
    expected = _read_summaries(out)
    (out / "summary.md").unlink()
    (out / "summary.json").unlink()

    assert main(["summary", str(out), *(str(pdf) for pdf in pdfs)]) == 0

    assert _read_summaries(out) == expected
    assert "結果の無い文書" not in expected[0]
    assert "missing" not in json.loads(expected[1])


def test_summary_merges_per_document_runs(tmp_path: Path) -> None:
    # CI の分割ジョブと同じく文書ごとに別ディレクトリで run し、まとめてから集約する
    pdfs = _write_pdfs(tmp_path, "a", "b")
    out = tmp_path / "merged"
    for pdf in pdfs:
        part = tmp_path / f"part-{pdf.stem}"
        assert main(["run", str(pdf), "--out", str(part)]) == 0
        (out / pdf.stem).parent.mkdir(parents=True, exist_ok=True)
        (part / pdf.stem).rename(out / pdf.stem)

    # PDF を省くと run.json のある文書を名前順に並べる
    assert main(["summary", str(out)]) == 0

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert [document["stem"] for document in summary["documents"]] == ["a", "b"]
    assert "文書 2 件" in (out / "summary.md").read_text(encoding="utf-8")


def test_summary_marks_missing_documents(tmp_path: Path) -> None:
    # 結果の届かなかった文書は summary に載せ、終了コード 2 で知らせる
    pdfs = _write_pdfs(tmp_path, "a", "b")
    out = tmp_path / "results"
    assert main(["run", str(pdfs[0]), "--out", str(out)]) == 0

    assert main(["summary", str(out), *(str(pdf) for pdf in pdfs)]) == 2

    markdown, raw = _read_summaries(out)
    assert "文書 1 件" in markdown
    assert "**結果の無い文書 1 件**: b.pdf" in markdown
    summary = json.loads(raw)
    assert [document["stem"] for document in summary["documents"]] == ["a"]
    assert summary["missing"] == ["b.pdf"]


def test_summary_without_results_fails(tmp_path: Path) -> None:
    out = tmp_path / "results"
    # PDF を省き、run.json も無ければ書くものが無い
    assert main(["summary", str(out)]) == 2
    assert not out.exists()

    # PDF を渡せば、全文書の結果が無くてもその一覧を summary に残す
    assert main(["summary", str(out), str(tmp_path / "a.pdf")]) == 2
    markdown, raw = _read_summaries(out)
    assert "文書 0 件" in markdown
    assert "**結果の無い文書 1 件**: a.pdf" in markdown
    assert json.loads(raw) == {"documents": [], "missing": ["a.pdf"]}


def _write_run(out: Path, stem: str, adapters: dict) -> None:
    """run.json だけを手で書く（アダプタの導入状況に依存させない）。"""
    (out / stem).mkdir(parents=True, exist_ok=True)
    run = {"input": f"/tmp/{stem}.pdf", "adapters": adapters}
    (out / stem / "run.json").write_text(json.dumps(run), encoding="utf-8")


def _patch_adapters(monkeypatch, *names: str) -> None:
    """`jdocbench.adapters.ADAPTERS` を指定した名前だけに絞る（#226 の missing 判定用）。

    `_write_run` で手書きした run.json は実際の ADAPTERS 全件を持たないため、絞らないと
    テストが意図しない adapter 欠け（missing）まで拾ってしまう。
    """
    monkeypatch.setattr(cli, "ADAPTERS", [SimpleNamespace(name=name) for name in names])


# error / 未導入 / 理由つきの skipped（失敗ではない）を 1 件ずつ持つ結果（#207）
_FAILED_ADAPTERS = {
    "pdftotext": {"status": "ok"},
    "pdf2json": {"status": "error", "message": "Error: unsupported\nencryption algorithm"},
    "pymupdf": {"status": "skipped", "reason": "not available"},
    "tesseract": {"status": "skipped", "reason": "text layer present (100 chars)"},
}


def test_summary_lists_failures_without_strict(tmp_path: Path) -> None:
    out = tmp_path / "results"
    _write_run(out, "a", _FAILED_ADAPTERS)

    # strict でなければ失敗があっても 0（ローカルの通常用途）。未導入は載せない
    assert main(["summary", str(out)]) == 0

    markdown, raw = _read_summaries(out)
    assert "## 失敗したアダプタ（1 件）" in markdown
    assert "| a | pdf2json | error | Error: unsupported encryption algorithm |" in markdown
    assert json.loads(raw)["failures"] == [
        {
            "stem": "a",
            "adapter": "pdf2json",
            "kind": "error",
            "message": "Error: unsupported\nencryption algorithm",
        }
    ]


def test_summary_strict_fails_on_unexpected_failure(tmp_path: Path, capsys, monkeypatch) -> None:
    out = tmp_path / "results"
    _write_run(out, "a", _FAILED_ADAPTERS)
    _patch_adapters(monkeypatch, *_FAILED_ADAPTERS)

    assert main(["summary", str(out), "--strict"]) == 2

    err = capsys.readouterr().err
    assert "想定外の失敗: a × pdf2json（error）: Error: unsupported encryption algorithm" in err
    assert "想定外の失敗: a × pymupdf（not available）" in err
    assert "tesseract" not in err
    # 赤になった理由は summary.md の表にも出る（未導入を含む）
    markdown, _ = _read_summaries(out)
    assert "## 失敗したアダプタ（2 件）" in markdown
    assert "| a | pymupdf | not available | - |" in markdown


def test_summary_strict_accepts_known_failures(tmp_path: Path, capsys, monkeypatch) -> None:
    out = tmp_path / "results"
    _write_run(out, "a", _FAILED_ADAPTERS)
    _write_run(out, "b", {"pdf2json": {"status": "ok", "normalize_error": "壊れた raw"}})
    # missing 判定は pdf2json だけ見る（b は pdf2json 以外を持たないため）
    _patch_adapters(monkeypatch, "pdf2json")

    # 既知の組は許可する（種類を省くと error）。同じアダプタでも別の文書の失敗や、
    # 同じ組でも別の種類の失敗は許可しない
    args = ["summary", str(out), "--strict", "--known-failure", "a:pdf2json"]
    args += ["--known-failure", "a:pymupdf:not available"]
    assert main([*args, "--known-failure", "b:pdf2json"]) == 2
    err = capsys.readouterr().err
    assert "想定外の失敗: b × pdf2json（normalize_error）" in err
    assert "想定外の失敗: a ×" not in err

    assert main([*args, "--known-failure", "b:pdf2json:normalize_error"]) == 0
    assert "想定外の失敗" not in capsys.readouterr().err


def test_summary_strict_warns_on_stale_known_failure(tmp_path: Path, capsys, monkeypatch) -> None:
    # 起きなくなった既知の失敗は警告だけ（直った瞬間に CI を赤にしない）
    out = tmp_path / "results"
    _write_run(out, "a", {"pdftotext": {"status": "ok"}})
    _patch_adapters(monkeypatch, "pdftotext")

    assert main(["summary", str(out), "--strict", "--known-failure", "a:pdf2json"]) == 0
    assert "既知の失敗が起きていません" in capsys.readouterr().err
    markdown, raw = _read_summaries(out)
    assert "失敗したアダプタ" not in markdown
    assert "failures" not in json.loads(raw)


def test_summary_strict_still_fails_on_missing_document(tmp_path: Path, monkeypatch) -> None:
    # 失敗が既知だけでも、結果の無い文書があれば 2（従来どおり）
    out = tmp_path / "results"
    _write_run(out, "a", {"pdftotext": {"status": "ok"}})
    _patch_adapters(monkeypatch, "pdftotext")

    assert main(["summary", str(out), "a.pdf", "b.pdf", "--strict"]) == 2


def test_summary_strict_fails_on_missing_adapter(tmp_path: Path, capsys, monkeypatch) -> None:
    # 登録アダプタ（ADAPTERS）が run.json から丸ごと欠けていれば strict で失敗にする（#226）。
    # 例えば古いコードの run.json が混ざった場合や、アダプタが登録から外れた場合に拾う
    out = tmp_path / "results"
    _write_run(out, "a", {"pdftotext": {"status": "ok"}})
    _patch_adapters(monkeypatch, "pdftotext", "docling")

    # strict でなければ欠けていても失敗にしない（ローカルの通常用途）
    assert main(["summary", str(out)]) == 0
    markdown, raw = _read_summaries(out)
    assert "失敗したアダプタ" not in markdown
    assert "failures" not in json.loads(raw)

    assert main(["summary", str(out), "--strict"]) == 2
    err = capsys.readouterr().err
    assert "想定外の失敗: a × docling（missing）" in err
    markdown, raw = _read_summaries(out)
    assert "## 失敗したアダプタ（1 件）" in markdown
    assert "| a | docling | missing | - |" in markdown
    assert json.loads(raw)["failures"] == [
        {"stem": "a", "adapter": "docling", "kind": "missing", "message": None}
    ]

    # --known-failure は missing も明示すれば許可できる
    known = ["--known-failure", "a:docling:missing"]
    assert main(["summary", str(out), "--strict", *known]) == 0
    assert "想定外の失敗" not in capsys.readouterr().err


def test_summary_strict_fails_on_invalid_adapter_value(tmp_path: Path, capsys, monkeypatch) -> None:
    # 登録アダプタのキーはあるが値が null（dict でない）なら strict で失敗にする（#246）。
    # #226 の欠落検出はキーの有無しか見ず、値が null の壊れた run.json を見逃していた
    out = tmp_path / "results"
    _write_run(out, "a", {"pdftotext": {"status": "ok"}, "pymupdf": None})
    _patch_adapters(monkeypatch, "pdftotext", "pymupdf")

    # strict でなければ dict でない値があっても失敗にしない（従来の挙動を変えない）
    assert main(["summary", str(out)]) == 0
    markdown, raw = _read_summaries(out)
    assert "失敗したアダプタ" not in markdown
    assert "failures" not in json.loads(raw)

    assert main(["summary", str(out), "--strict"]) == 2
    err = capsys.readouterr().err
    assert "想定外の失敗: a × pymupdf（invalid）: NoneType" in err
    markdown, raw = _read_summaries(out)
    assert "## 失敗したアダプタ（1 件）" in markdown
    assert "| a | pymupdf | invalid | NoneType |" in markdown
    assert json.loads(raw)["failures"] == [
        {"stem": "a", "adapter": "pymupdf", "kind": "invalid", "message": "NoneType"}
    ]

    # --known-failure は invalid も明示すれば許可できる
    known = ["--known-failure", "a:pymupdf:invalid"]
    assert main(["summary", str(out), "--strict", *known]) == 0
    assert "想定外の失敗" not in capsys.readouterr().err


def test_run_rejects_gt_with_multiple_pdfs(tmp_path: Path) -> None:
    pdfs = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    for pdf in pdfs:
        pdf.write_bytes(_DUMMY_PDF)
    out = tmp_path / "results"
    gt = tmp_path / "gt.json"
    gt.write_text("{}", encoding="utf-8")

    args = ["run", *(str(pdf) for pdf in pdfs), "--out", str(out), "--gt", str(gt)]
    assert main(args) == 2

    # 何も処理しない
    assert not out.exists()


def test_profile_continues_when_a_pdf_is_unreadable(tmp_path: Path) -> None:
    """読めない PDF（権限など）があっても profile は止まらず、error と source を書く（#158）。"""
    unreadable = tmp_path / "locked.pdf"
    unreadable.write_bytes(_DUMMY_PDF)
    unreadable.chmod(0o000)
    out = tmp_path / "results"
    try:
        exit_code = main(["profile", str(unreadable), "--out", str(out)])
    finally:
        unreadable.chmod(0o644)

    assert exit_code == 2
    profile = json.loads((out / "locked" / "profile.json").read_text(encoding="utf-8"))
    assert profile["source"] == str(unreadable)
    assert profile["sha256"] is None
    assert "error" in profile


def test_fonts_ja_is_none_without_fc_list(monkeypatch) -> None:
    """fc-list が無い環境では「日本語フォント無し」と区別して None を記録する（#157）。"""
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    assert cli._fonts_ja() is None
