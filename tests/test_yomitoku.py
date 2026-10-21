import importlib.metadata
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from jdocbench.adapters import ADAPTERS
from jdocbench.adapters import yomitoku as yomitoku_adapter
from jdocbench.adapters.yomitoku import YomitokuAdapter
from jdocbench.normalizers import NORMALIZERS
from jdocbench.normalizers.yomitoku import normalize
from jdocbench.perf import CommandResult

_SAMPLES = Path(__file__).resolve().parents[1] / "samples"
_OPT_IN_ENV = "JDOCBENCH_YOMITOKU"
_BIN_ENV = "JDOCBENCH_YOMITOKU_BIN"
_SCALE = 72 / 200


def _executable(tmp_path: Path, name: str = "yomitoku", body: str = "") -> Path:
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _raw_dir(tmp_path: Path, pages: dict[int, dict]) -> Path:
    raw_dir = tmp_path / "raw" / "yomitoku"
    raw_dir.mkdir(parents=True)
    for number, data in pages.items():
        path = raw_dir / f"samples_doc_p{number}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return raw_dir


def _para(contents: object, box: list[int], order: int, role: str | None = None, **kw) -> dict:
    return {
        "box": box,
        "contents": contents,
        "direction": kw.get("direction", "horizontal"),
        "order": order,
        "role": role,
    }


def _word(content: str, box: list[int], direction: str = "horizontal") -> dict:
    x0, y0, x1, y1 = box
    points = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return {"content": content, "direction": direction, "points": points}


def _cell(row: int, col: int, contents: str, row_span: int = 1, col_span: int = 1) -> dict:
    return {
        "row": row,
        "col": col,
        "row_span": row_span,
        "col_span": col_span,
        "contents": contents,
    }


def _page(paragraphs=(), tables=(), figures=(), words=()) -> dict:
    return {
        "paragraphs": list(paragraphs),
        "tables": list(tables),
        "figures": list(figures),
        "words": list(words),
    }


# --- アダプタ ---------------------------------------------------------------


def test_registered() -> None:
    assert "yomitoku" in [adapter.name for adapter in ADAPTERS]
    assert NORMALIZERS["yomitoku"] is normalize


def test_available_is_false_without_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # コマンドがあっても、環境変数が無ければ動かさない（非商用ライセンスのためオプトイン）
    monkeypatch.delenv(_OPT_IN_ENV, raising=False)
    monkeypatch.setenv(_BIN_ENV, str(_executable(tmp_path)))
    assert YomitokuAdapter().available() is False
    for value in ("", "0", "false"):
        monkeypatch.setenv(_OPT_IN_ENV, value)
        assert YomitokuAdapter().available() is False


def test_available_requires_executable_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_OPT_IN_ENV, "1")
    monkeypatch.setenv(_BIN_ENV, str(_executable(tmp_path)))
    assert YomitokuAdapter().available() is True

    not_executable = tmp_path / "plain"
    not_executable.write_text("", encoding="utf-8")
    monkeypatch.setenv(_BIN_ENV, str(not_executable))
    assert YomitokuAdapter().available() is False

    monkeypatch.setenv(_BIN_ENV, str(tmp_path / "missing"))
    assert YomitokuAdapter().available() is False


def test_available_uses_path_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_OPT_IN_ENV, "1")
    monkeypatch.delenv(_BIN_ENV, raising=False)
    monkeypatch.setenv("PATH", "")
    assert YomitokuAdapter().available() is False


def test_version_reads_metadata_with_tool_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_BIN_ENV, str(_executable(tmp_path)))
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "0.15.0\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert YomitokuAdapter().version() == "0.15.0"
    # shebang の Python で、yomitoku 本体を import せずにメタデータだけ読む
    assert calls[0][0] == sys.executable
    assert "importlib.metadata" in calls[0][2]


def test_version_is_unknown_without_shebang_or_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "yomitoku"
    binary.write_bytes(b"\x7fELF")
    binary.chmod(0o755)
    monkeypatch.setenv(_BIN_ENV, str(binary))
    assert YomitokuAdapter().version() == "unknown"

    monkeypatch.setenv(_BIN_ENV, str(_executable(tmp_path, "yomitoku2")))

    def raise_oserror(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        raise OSError("not found")

    monkeypatch.setattr(subprocess, "run", raise_oserror)
    assert YomitokuAdapter().version() == "unknown"


# 固定した SHA（アダプタの期待値）。refs/main がこれを指す正常なキャッシュを作るのに使う
_SHA = dict(yomitoku_adapter._REVISIONS)


def _fake_hub(tmp_path: Path, shas: dict[str, str] | None = None) -> Path:
    """HF のキャッシュの形（refs/main とスナップショットの重み）だけを作る。"""
    hub = tmp_path / "hub"
    for repo, sha in (shas or _SHA).items():
        repo_dir = hub / ("models--" + repo.replace("/", "--"))
        (repo_dir / "refs").mkdir(parents=True, exist_ok=True)
        (repo_dir / "refs" / "main").write_text(sha, encoding="utf-8")
        (repo_dir / "snapshots" / sha).mkdir(parents=True, exist_ok=True)
        (repo_dir / "snapshots" / sha / "model.safetensors").write_bytes(b"")
    return hub


@pytest.fixture
def fake_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hub = _fake_hub(tmp_path)
    monkeypatch.setattr(yomitoku_adapter, "_hf_hub_cache", lambda bin_path: hub)
    return hub


def _fake_runner(
    out_dir: Path,
    calls: list[list[str]],
    write: bool = True,
    code: int = 0,
    seen: list[dict[str, object]] | None = None,
):
    def fake_run_command(cmd: list[str], env: dict[str, str] | None = None) -> CommandResult:
        calls.append(cmd)
        if seen is not None:
            # 設定 YAML は run の間だけある一時ファイルなので、呼ばれた時点で中身を読んでおく
            configs = {
                option: Path(cmd[cmd.index(option) + 1]).read_text(encoding="utf-8")
                for option in yomitoku_adapter._CFG_OPTIONS.values()
            }
            seen.append({"env": env, "configs": configs})
        page = int(cmd[cmd.index("--pages") + 1])
        if write:
            (out_dir / f"samples_doc_p{page}.json").write_text("{}", encoding="utf-8")
        return CommandResult(code, "", f"log {page}\n", 10.0 * page, 1000 * (3 - page))

    return fake_run_command


@pytest.mark.skipif(
    importlib.util.find_spec("pypdfium2") is None,
    reason="代わりの Python に pypdfium2 が無い環境ではスキップ",
)
def test_page_count_uses_tool_interpreter(tmp_path: Path) -> None:
    """ページ数はコマンドの shebang の Python（YomiToku の環境）で数える。

    本体の venv の pypdfium2 は extra にしか無いため、本体側では import しない。
    ここでは shebang をテストを動かしている Python に向けて代わりにする。
    """
    pages, version = yomitoku_adapter._page_count(
        str(_executable(tmp_path)), _SAMPLES / "mof-budget-summary-fy2026.pdf"
    )
    assert pages == 3
    assert version == importlib.metadata.version("pypdfium2")


def test_interpreter_keeps_env_shebang_arguments(tmp_path: Path) -> None:
    script = tmp_path / "yomitoku"
    script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    assert yomitoku_adapter._interpreter(str(script)) == ["/usr/bin/env", "python3"]


def test_page_count_fails_without_interpreter(tmp_path: Path) -> None:
    binary = tmp_path / "yomitoku"
    binary.write_bytes(b"\x7fELF")
    with pytest.raises(RuntimeError, match="shebang"):
        yomitoku_adapter._page_count(str(binary), tmp_path / "doc.pdf")


def test_run_calls_one_process_per_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_hub: Path
) -> None:
    """1 ページ 1 プロセスで呼び、Wall は合計・Peak RSS は最大。古いページの出力は消す。"""
    out_dir = tmp_path / "raw" / "yomitoku"
    out_dir.mkdir(parents=True)
    (out_dir / "samples_doc_p3.json").write_text("{}", encoding="utf-8")  # 前回の 3 ページ目
    monkeypatch.setenv(_BIN_ENV, str(_executable(tmp_path)))
    monkeypatch.setattr(yomitoku_adapter, "_page_count", lambda bin_path, pdf: (2, "4.30.0"))
    calls: list[list[str]] = []
    monkeypatch.setattr(yomitoku_adapter, "run_command", _fake_runner(out_dir, calls))

    result = YomitokuAdapter().run(tmp_path / "doc.pdf", out_dir)

    assert [cmd[cmd.index("--pages") + 1] for cmd in calls] == ["1", "2"]
    assert all(cmd[cmd.index("-d") + 1] == "cpu" and "--lite" not in cmd for cmd in calls)
    assert all(cmd[cmd.index("--dpi") + 1] == "200" for cmd in calls)
    assert result["wall_time_sec"] == pytest.approx(30.0)
    assert result["peak_rss_kb"] == 2000
    extra = result["extra"]
    assert extra["invocation"] == "per-page"
    assert extra["mode"] == "normal"
    assert extra["device"] == "cpu"
    assert extra["pages"] == 2
    assert len(extra["per_page"]) == 2
    assert extra["models"] == _SHA
    assert sorted(p.name for p in out_dir.glob("*.json")) == [
        "samples_doc_p1.json",
        "samples_doc_p2.json",
    ]
    stderr = (out_dir / "stderr.txt").read_text(encoding="utf-8")
    assert "--- page 1 ---\nlog 1" in stderr
    assert "--- page 2 ---\nlog 2" in stderr


def test_run_raises_when_page_json_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_hub: Path
) -> None:
    # `--pages` が範囲外だと yomitoku は何も書かずに 0 で終わる。出力が無ければ失敗にする
    out_dir = tmp_path / "raw" / "yomitoku"
    monkeypatch.setenv(_BIN_ENV, str(_executable(tmp_path)))
    monkeypatch.setattr(yomitoku_adapter, "_page_count", lambda bin_path, pdf: (1, "4.30.0"))
    calls: list[list[str]] = []
    monkeypatch.setattr(yomitoku_adapter, "run_command", _fake_runner(out_dir, calls, write=False))
    with pytest.raises(RuntimeError, match="p1"):
        YomitokuAdapter().run(tmp_path / "doc.pdf", out_dir)


def test_run_raises_when_child_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_hub: Path
) -> None:
    out_dir = tmp_path / "raw" / "yomitoku"
    monkeypatch.setenv(_BIN_ENV, str(_executable(tmp_path)))
    monkeypatch.setattr(yomitoku_adapter, "_page_count", lambda bin_path, pdf: (2, "4.30.0"))
    calls: list[list[str]] = []
    monkeypatch.setattr(yomitoku_adapter, "run_command", _fake_runner(out_dir, calls, code=3))
    with pytest.raises(subprocess.CalledProcessError):
        YomitokuAdapter().run(tmp_path / "doc.pdf", out_dir)
    assert len(calls) == 1  # 失敗したページで止める


def test_run_loads_pinned_snapshots_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_hub: Path
) -> None:
    """重みは refs/main が指すスナップショットから読ませ、Hub には問い合わせない（#227）。

    YomiToku は revision なしで `from_pretrained(<リポジトリ名>)` を呼ぶので、設定 YAML の
    `hf_hub_repo` をスナップショットのディレクトリに差し替える。HF_HUB_OFFLINE=1 は仕掛け線。
    """
    out_dir = tmp_path / "raw" / "yomitoku"
    monkeypatch.setenv(_BIN_ENV, str(_executable(tmp_path)))
    monkeypatch.setattr(yomitoku_adapter, "_page_count", lambda bin_path, pdf: (2, "4.30.0"))
    calls: list[list[str]] = []
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(yomitoku_adapter, "run_command", _fake_runner(out_dir, calls, seen=seen))

    result = YomitokuAdapter().run(tmp_path / "doc.pdf", out_dir)

    assert len(seen) == 2
    expected = {
        yomitoku_adapter._CFG_OPTIONS[role]: (
            f'hf_hub_repo: "{fake_hub}/models--{repo.replace("/", "--")}/snapshots/{_SHA[repo]}"\n'
        )
        for role, repo in yomitoku_adapter._MODELS.items()
    }
    for call in seen:
        assert call["env"] == {"HF_HUB_OFFLINE": "1"}
        assert call["configs"] == expected
    assert result["extra"]["models"] == _SHA
    # 一時ファイルは run の後に残さない（raw も汚さない）
    assert not Path(calls[0][calls[0].index("--td_cfg") + 1]).exists()
    assert not list(out_dir.glob("*.yaml"))


def test_model_snapshots_follow_refs_main(tmp_path: Path) -> None:
    """スナップショットが複数あっても refs/main が指すものを使う（古い版を読まない）。"""
    newer = {repo: "f" * 40 for repo in _SHA}
    _fake_hub(tmp_path, newer)
    hub = _fake_hub(tmp_path)  # refs/main を _SHA に戻す（newer のスナップショットは残る）
    snapshots = yomitoku_adapter.model_snapshots(hub)
    assert {yomitoku_adapter._MODELS[role]: path.name for role, path in snapshots.items()} == _SHA


def test_model_snapshots_fail_without_cache_with_install_hint(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="install_yomitoku.sh --models"):
        yomitoku_adapter.model_snapshots(tmp_path / "empty")


def test_model_snapshots_fail_when_ref_points_to_missing_snapshot(tmp_path: Path) -> None:
    hub = _fake_hub(tmp_path)
    repo, sha = next(iter(_SHA.items()))
    repo_dir = hub / ("models--" + repo.replace("/", "--"))
    (repo_dir / "snapshots" / sha / "model.safetensors").unlink()
    with pytest.raises(RuntimeError, match="重みがありません"):
        yomitoku_adapter.model_snapshots(hub)


def test_run_fails_before_measuring_when_ref_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """別の取得処理が refs/main を進めたら、YomiToku を起動する前に失敗する（#245）。

    進んだ先のスナップショットにも重みがある（そのままなら読めてしまう）状態で確かめる。
    """
    repo = next(iter(_SHA))
    moved = {**_SHA, repo: "e" * 40}
    hub = _fake_hub(tmp_path)  # 固定した SHA のスナップショットも残っている
    _fake_hub(tmp_path, moved)  # refs/main だけを別の SHA に進める
    monkeypatch.setattr(yomitoku_adapter, "_hf_hub_cache", lambda bin_path: hub)
    monkeypatch.setenv(_BIN_ENV, str(_executable(tmp_path)))
    counted: list[Path] = []
    monkeypatch.setattr(
        yomitoku_adapter, "_page_count", lambda bin_path, pdf: counted.append(pdf) or (1, "4.30.0")
    )
    calls: list[list[str]] = []
    out_dir = tmp_path / "raw" / "yomitoku"
    monkeypatch.setattr(yomitoku_adapter, "run_command", _fake_runner(out_dir, calls))

    with pytest.raises(RuntimeError, match="固定した版と違います") as excinfo:
        YomitokuAdapter().run(tmp_path / "doc.pdf", out_dir)

    message = str(excinfo.value)
    assert repo in message and "e" * 40 in message and _SHA[repo] in message
    assert "install_yomitoku.sh --models" in message
    assert calls == []  # YomiToku を一度も起動していない（計測していない）
    assert counted == []
    assert not list(out_dir.glob("*.json"))


def test_revisions_match_install_script() -> None:
    """アダプタの期待する SHA と、install_yomitoku.sh が取得する SHA が同じ（#245）。"""
    script = (Path(__file__).parents[1] / "scripts" / "install_yomitoku.sh").read_text(
        encoding="utf-8"
    )
    block = re.search(r"^MODELS=\((.*?)^\)", script, re.S | re.M)
    assert block is not None
    pinned = dict(line.strip().split("@") for line in block.group(1).splitlines() if line.strip())
    assert pinned == yomitoku_adapter._REVISIONS
    assert set(pinned) == set(yomitoku_adapter._MODELS.values())


def test_model_snapshots_reject_non_commit_ref(tmp_path: Path) -> None:
    hub = _fake_hub(tmp_path)
    repo = next(iter(_SHA))
    (hub / ("models--" + repo.replace("/", "--")) / "refs" / "main").write_text("main")
    with pytest.raises(RuntimeError, match="コミット SHA ではありません"):
        yomitoku_adapter.model_snapshots(hub)


def test_hf_hub_cache_asks_tool_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "/cache/hub\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert yomitoku_adapter._hf_hub_cache(str(_executable(tmp_path))) == Path("/cache/hub")
    assert calls[0][0] == sys.executable
    assert "constants.HF_HUB_CACHE" in calls[0][2]


# --- 正規化 -----------------------------------------------------------------


def test_normalize_orders_blocks_and_converts_coordinates(tmp_path: Path) -> None:
    """order の順に段落・表・図中段落を並べ、座標を DPI 200 のピクセルから pt に直す。"""
    page1 = _page(
        paragraphs=[
            _para("- 1 -", [700, 2200, 800, 2240], 5, "page_footer"),
            _para("令和8年度予算", [200, 100, 1000, 140], 1, "section_headings"),
            _para("総務省", [1200, 20, 1400, 60], 0, "page_header"),
            _para("本文の1行目\n本文の2行目", [200, 200, 1000, 300], 2),
            _para(None, [0, 0, 10, 10], 6),
            _para("  ", [0, 0, 10, 10], 7),
            _para("縦書き", [1500, 400, 1540, 600], 8, direction="vertical"),
        ],
        tables=[
            {
                "box": [200, 400, 1400, 800],
                "n_row": 3,
                "n_col": 3,
                "order": 3,
                "cells": [
                    _cell(1, 1, "区分", col_span=2),
                    _cell(1, 3, "金額"),
                    _cell(2, 1, "歳入"),
                    _cell(2, 2, "租税"),
                    _cell(2, 3, "778,190"),
                    _cell(3, 1, "合計", col_span=2),
                    _cell(3, 3, ""),
                ],
            }
        ],
        figures=[
            {
                "box": [200, 900, 1400, 1500],
                "order": 4,
                "paragraphs": [
                    _para("凡例B", [300, 1000, 400, 1040], 1),
                    _para("凡例A", [300, 950, 400, 990], 0),
                ],
            }
        ],
    )
    # p2 は無い（空ページとして補う）。p10 は文字列順では p2 より前に来るが、最後のページ
    raw_dir = _raw_dir(
        tmp_path,
        {
            1: page1,
            3: _page([_para("3頁", [0, 0, 100, 40], 0)]),
            10: _page([_para("10頁", [0, 0, 100, 40], 0)]),
        },
    )

    doc = normalize(raw_dir)

    assert [page.number for page in doc.pages] == list(range(1, 11))
    assert doc.pages[1].blocks == []
    assert doc.pages[2].lines() == ["3頁"]
    assert doc.pages[9].lines() == ["10頁"]

    blocks = doc.pages[0].blocks
    assert [(b.type, b.extra.get("role"), b.text) for b in blocks] == [
        ("paragraph", "page_header", "総務省"),
        ("paragraph", "section_headings", "令和8年度予算"),
        ("paragraph", None, "本文の1行目\n本文の2行目"),
        ("table", None, None),
        ("paragraph", None, "凡例A"),
        ("paragraph", None, "凡例B"),
        ("paragraph", "page_footer", "- 1 -"),
        ("paragraph", None, "縦書き"),
    ]
    assert blocks[4].extra["figure"] is True and blocks[5].extra["figure"] is True
    assert blocks[7].extra["direction"] == "vertical"
    assert blocks[0].bbox == pytest.approx((1200 * _SCALE, 20 * _SCALE, 1400 * _SCALE, 60 * _SCALE))
    assert blocks[3].bbox == pytest.approx((72.0, 144.0, 504.0, 288.0))
    # 結合セルは範囲の先頭にだけ文字列を置く（IR の規約）
    assert blocks[3].rows == [
        ["区分", "", "金額"],
        ["歳入", "租税", "778,190"],
        ["合計", "", ""],
    ]


def test_normalize_drops_paragraph_made_of_own_table_cells(tmp_path: Path) -> None:
    """表の領域内でセルと同じ文字の段落は落とし、セルに無い文字の段落は残す（#186）。"""
    table = {
        "box": [100, 100, 1100, 500],
        "n_row": 2,
        "n_col": 2,
        "order": 0,
        "cells": [_cell(1, 1, "歳入"), _cell(1, 2, "778,190"), _cell(2, 1, ""), _cell(2, 2, "")],
    }
    page = _page(
        paragraphs=[
            _para("778,190", [600, 120, 800, 160], 1),  # セルにある → 二重計上なので落とす
            _para("226,001", [600, 300, 800, 340], 2),  # 空セルの位置の数字 → 表に無いので残す
            _para("778,190", [100, 900, 300, 940], 3),  # 表の外 → 残す
        ],
        tables=[table],
        words=[
            _word("778,190", [600, 120, 800, 160]),
            _word("226,001", [600, 300, 800, 340]),
            _word("778,190", [100, 900, 300, 940]),
        ],
    )
    doc = normalize(_raw_dir(tmp_path, {1: page}))
    assert doc.pages[0].lines() == ["歳入 778,190", "226,001", "778,190"]


def test_normalize_strips_table_words_from_mixed_paragraph(tmp_path: Path) -> None:
    """本文と表の見出しが 1 つの段落に入ったら、表の中の語だけを除く（#199）。

    段落の各行を words（行の座標）に対応付け、行を文字数で按分した語の座標を渡す。
    """
    table = {
        "box": [800, 400, 1500, 900],
        "n_row": 2,
        "n_col": 2,
        "order": 1,
        "cells": [
            _cell(1, 1, "2026年"),
            _cell(1, 2, "2025年"),
            _cell(2, 1, "1329"),
            _cell(2, 2, "1365"),
        ],
    }
    page = _page(
        paragraphs=[
            _para("こどもの数は減少\n2026年 2025年", [200, 300, 1500, 480], 0),
        ],
        tables=[table],
        words=[
            _word("こどもの数は減少", [200, 300, 700, 340]),
            _word("2026年 2025年", [820, 420, 1300, 460]),
            _word("2026年", [820, 420, 1000, 460]),  # 表のセル側の行（段落の箱の中にあってもよい）
        ],
    )
    doc = normalize(_raw_dir(tmp_path, {1: page}))
    assert doc.pages[0].lines() == ["こどもの数は減少", "2026年 2025年", "1329 1365"]
    paragraph = doc.pages[0].blocks[0]
    assert paragraph.text == "こどもの数は減少"


def test_normalize_falls_back_to_paragraph_rule_without_words(tmp_path: Path) -> None:
    """words と対応が取れない段落は、段落単位の規則（#186）で判定する（一部だけは除かない）。"""
    table = {
        "box": [800, 400, 1500, 900],
        "n_row": 1,
        "n_col": 2,
        "order": 1,
        "cells": [_cell(1, 1, "2026年"), _cell(1, 2, "2025年")],
    }
    page = _page(
        paragraphs=[_para("こどもの数は減少\n2026年 2025年", [200, 300, 1500, 480], 0)],
        tables=[table],
    )
    doc = normalize(_raw_dir(tmp_path, {1: page}))
    assert doc.pages[0].blocks[0].text == "こどもの数は減少\n2026年 2025年"


@pytest.mark.skipif(
    not YomitokuAdapter().available(),
    reason=(
        f"{_OPT_IN_ENV} / yomitoku が無い環境ではスキップ"
        "（モデルは install_yomitoku.sh --models で取得済みのこと。"
        "1 ページ約 25 秒・Peak RSS 約 2.4GB）"
    ),
)
def test_yomitoku_real_pdf(tmp_path: Path) -> None:
    pdf = _SAMPLES / "mof-budget-summary-fy2026.pdf"
    adapter = YomitokuAdapter()
    out_dir = tmp_path / "raw" / "yomitoku"
    result = adapter.run(pdf, out_dir)

    assert result["extra"]["pages"] == 3
    assert len(result["extra"]["models"]) == 4
    assert all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in result["extra"]["models"].values())
    assert result["peak_rss_kb"] > 0
    assert len(list(out_dir.glob("*_p*.json"))) == 3
    assert adapter.version() != "unknown"
    doc = normalize(out_dir)
    assert [page.number for page in doc.pages] == [1, 2, 3]
    tables = [b for page in doc.pages for b in page.blocks if b.type == "table"]
    assert [(len(t.rows), len(t.rows[0])) for t in tables] == [(13, 5), (28, 5), (17, 5)]
    assert all(t.bbox is not None for t in tables)
    assert doc.char_count() > 0
