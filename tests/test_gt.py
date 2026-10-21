import json
from pathlib import Path

from jdocbench.cli import main

_PAGES = [
    {
        "number": 1,
        "blocks": [
            {"type": "paragraph", "text": "本文", "bbox": None, "rows": None, "extra": {}},
            {
                "type": "table",
                "text": None,
                "bbox": None,
                "rows": [["年", "人口"], ["2026", "1,401"]],
                "extra": {},
            },
        ],
    }
]


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    """ダミー PDF と results/<stem>/normalized/fake.json を用意する。"""
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    results = tmp_path / "results"
    normalized = results / "sample" / "normalized"
    normalized.mkdir(parents=True)
    (normalized / "fake.json").write_text(
        json.dumps({"adapter": "fake", "pages": _PAGES}, ensure_ascii=False),
        encoding="utf-8",
    )
    return pdf, results, tmp_path / "groundtruth"


def test_gt_init_writes_ground_truth(tmp_path: Path) -> None:
    pdf, results, out = _setup(tmp_path)

    exit_code = main(
        ["gt", "init", str(pdf), "--from", "fake", "--results", str(results), "--out", str(out)]
    )

    assert exit_code == 0
    gt_json = out / "sample.json"
    assert gt_json.is_file()
    gt = json.loads(gt_json.read_text(encoding="utf-8"))
    assert gt["adapter"] == "ground_truth"
    assert gt["pages"] == _PAGES


def test_gt_init_does_not_overwrite(tmp_path: Path) -> None:
    pdf, results, out = _setup(tmp_path)
    out.mkdir()
    existing = out / "sample.json"
    existing.write_text("既存の GT\n", encoding="utf-8")

    exit_code = main(
        ["gt", "init", str(pdf), "--from", "fake", "--results", str(results), "--out", str(out)]
    )

    assert exit_code == 2
    assert existing.read_text(encoding="utf-8") == "既存の GT\n"


def test_gt_init_without_normalized_exits_2(tmp_path: Path) -> None:
    pdf, results, out = _setup(tmp_path)

    exit_code = main(
        ["gt", "init", str(pdf), "--from", "missing", "--results", str(results), "--out", str(out)]
    )

    assert exit_code == 2
    assert not (out / "sample.json").exists()
