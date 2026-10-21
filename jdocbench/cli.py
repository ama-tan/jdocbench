"""jdocbench の CLI エントリポイント。"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from jdocbench import __version__
from jdocbench.adapters import ADAPTERS
from jdocbench.adapters.base import Adapter
from jdocbench.ir import Block, Document, Page
from jdocbench.metrics import evaluate
from jdocbench.normalizers import NORMALIZERS, Normalizer
from jdocbench.perf import run_command
from jdocbench.profile import profile_pdf
from jdocbench.report import render
from jdocbench.router import MODES, route_all
from jdocbench.summary import find_failures, short_message, to_summary
from jdocbench.summary import render as render_summary
from jdocbench.view import render as render_view
from jdocbench.view import render_pages

_CHUNK_SIZE = 1024 * 1024
_GT_DIR = "groundtruth"
_MESSAGE_LIMIT = 500
_SEC_PER_PAGE_DIGITS = 6


def _sha256(path: Path) -> str:
    """ファイルの SHA-256 を求める。"""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _error_message(exc: Exception) -> str:
    """例外から run.json に残すメッセージを作る（stderr があればそれを優先）。"""
    stderr = getattr(exc, "stderr", None)
    text = stderr if isinstance(stderr, str) and stderr.strip() else str(exc)
    return text[:_MESSAGE_LIMIT]


def _page_count(pdf: Path) -> int | None:
    """`pdfinfo` からページ数を得る。pdfinfo が無い・読めない場合は None。"""
    if shutil.which("pdfinfo") is None:
        return None
    result = run_command(["pdfinfo", str(pdf)])
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "Pages":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def _cpu_model() -> str:
    """/proc/cpuinfo の最初の model name。取れなければ platform.processor() か "unknown"。"""
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        cpuinfo = ""
    for line in cpuinfo.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "model name" and value.strip():
            return value.strip()
    return platform.processor() or "unknown"


def _ram_total_bytes() -> int:
    """搭載メモリの総量（バイト）。"""
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def _fonts_ja() -> list[str] | None:
    """日本語を描画できるフォントのファミリー名（fontconfig の `fc-list :lang=ja`）。

    フォントを埋め込んでいない PDF は、ページ画像にするとき（Docling のレイアウト推論、
    PP-Structure の OCR、view.html）にこれらで代替される。無いと日本語が空白で描かれ、
    結果が大きく変わる（#157、docs/spikes/docling-repro.md）。fc-list が無ければ None。
    """
    fc_list = shutil.which("fc-list")
    if fc_list is None:
        return None
    try:
        result = subprocess.run(
            [fc_list, ":lang=ja", "family"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    # 1 行は "Noto Sans CJK JP,Noto Sans CJK JP Bold" のような別名の列。先頭だけ残す
    families = {line.split(",")[0].strip() for line in result.stdout.splitlines()}
    return sorted(family for family in families if family)


def _normalize(
    adapter_name: str,
    out_dir: Path,
    normalizer: Normalizer,
    result: dict[str, object],
) -> Document | None:
    """raw を読んで normalized/<adapter>.json を書き、chars / tables を result に足す。

    raw は読むだけで変更しない（企画書 §54-2）。失敗しても run は止めず
    `normalize_error` に残し None を返す。成功したら Document を返す
    （呼び出し側が pages の補完に使う）。
    """
    try:
        doc = normalizer(out_dir / "raw" / adapter_name)
    except Exception as exc:
        result["normalize_error"] = _error_message(exc)
        return None
    normalized_dir = out_dir / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    (normalized_dir / f"{adapter_name}.json").write_text(doc.to_json(), encoding="utf-8")
    result["chars"] = doc.char_count()
    result["tables"] = doc.table_count()
    return doc


def _document_from_dict(data: dict) -> Document:
    """normalized / Ground Truth の JSON を Document に戻す。

    `ir.py` に `from_dict` を置かないのは、IR を「書く側の最小スキーマ」に留めたいため
    （読み戻しが要るのは今のところ評価だけ）。未知のキーは無視する。
    """
    return Document(
        adapter=str(data.get("adapter", "")),
        pages=[
            Page(
                number=int(page.get("number", 0)),
                blocks=[
                    Block(
                        type=block.get("type"),
                        text=block.get("text"),
                        bbox=tuple(block["bbox"]) if block.get("bbox") else None,
                        rows=block.get("rows"),
                        extra=block.get("extra") or {},
                    )
                    for block in page.get("blocks") or []
                ],
            )
            for page in data.get("pages") or []
        ],
    )


def _evaluate_adapters(
    gt_path: Path,
    out_dir: Path,
    results: dict[str, dict[str, object]],
) -> bytes | None:
    """GT と各アダプタの正規化結果から DSQS-JP を計算し metrics/<adapter>.json に書く。

    GT ファイルはここで 1 度だけ読み、読めた生のバイト列を返す（呼び出し側が
    `ground_truth_sha256` に使う）。評価とハッシュ化を同じバイト列に対して行うことで、
    実行中に GT が書き換わっても評価内容とハッシュがずれないようにする。

    対象は normalized/<adapter>.json があるアダプタだけ。GT が読めない・壊れている
    場合も run は止めず `dsqs_error` に残す（読めなければハッシュ無しの None を返す）。
    """
    normalized_dir = out_dir / "normalized"
    targets = [name for name in results if (normalized_dir / f"{name}.json").is_file()]

    try:
        gt_bytes = gt_path.read_bytes()
    except OSError as exc:
        message = _error_message(exc)
        for name in targets:
            results[name]["dsqs_error"] = message
        return None

    try:
        gt = _document_from_dict(json.loads(gt_bytes.decode("utf-8")))
    except Exception as exc:
        message = _error_message(exc)
        for name in targets:
            results[name]["dsqs_error"] = message
        return gt_bytes

    if not targets:
        return gt_bytes

    metrics_dir = out_dir / "metrics"
    for name in targets:
        try:
            data = json.loads((normalized_dir / f"{name}.json").read_text(encoding="utf-8"))
            scores = evaluate(gt, _document_from_dict(data))
        except Exception as exc:
            results[name]["dsqs_error"] = _error_message(exc)
            continue
        metrics_dir.mkdir(parents=True, exist_ok=True)
        (metrics_dir / f"{name}.json").write_text(
            json.dumps(scores, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        results[name]["dsqs"] = scores
    return gt_bytes


def _run_adapters(
    adapters: list[Adapter],
    pdf: Path,
    out_dir: Path,
    pages: int | None,
    normalizers: dict[str, Normalizer] | None = None,
) -> tuple[dict[str, dict[str, object]], int | None, str | None]:
    """全アダプタを順に実行し、結果と計測値を記録する。1 つの失敗で全体を止めない。

    戻り値は (アダプタ別の結果, ページ数, ページ数の取得元)。`pages` が None
    （pdfinfo が無い等）のときは最初に正規化できたアダプタの Document から補完し、
    取得元にそのアダプタ名を入れる。**補完値は近似**で、正規化の実装次第では
    pdfinfo の値と一致しないことがある。
    """
    normalizers = NORMALIZERS if normalizers is None else normalizers
    pages_source = "pdfinfo" if pages is not None else None
    results: dict[str, dict[str, object]] = {}
    for adapter in adapters:
        if not adapter.available():
            results[adapter.name] = {"status": "skipped", "reason": "not available"}
            continue

        version = "unknown"
        try:
            version = adapter.version()
            metrics = adapter.run(pdf, out_dir / "raw" / adapter.name) or {}
        except Exception as exc:
            results[adapter.name] = {
                "status": "error",
                "version": version,
                "message": _error_message(exc),
            }
        else:
            # アダプタが「対象外」と申告した場合（docling_ocr がテキスト層ありの PDF を見たとき
            # 等）。失敗ではないので error にせず、理由つきの skipped として残す
            if "skipped" in metrics:
                results[adapter.name] = {
                    "status": "skipped",
                    "reason": str(metrics["skipped"]),
                }
                continue
            result: dict[str, object] = {
                "status": "ok",
                "version": version,
                "output_dir": f"raw/{adapter.name}",
            }
            # extra（実験条件。企画書 §49）は計測値と混ぜず独立したキーに残す
            extra = metrics.pop("extra", None)
            result.update(metrics)
            if extra:
                result["extra"] = extra
            normalizer = normalizers.get(adapter.name)
            if normalizer is not None:
                doc = _normalize(adapter.name, out_dir, normalizer, result)
                if pages is None and doc is not None and doc.pages:
                    pages = len(doc.pages)
                    pages_source = adapter.name
            results[adapter.name] = result

    # sec/page は後から補完した pages も全アダプタに効かせたいのでループの後でまとめて計算する
    if pages:
        for result in results.values():
            wall_time_sec = result.get("wall_time_sec")
            if result.get("status") == "ok" and isinstance(wall_time_sec, int | float):
                result["sec_per_page"] = round(wall_time_sec / pages, _SEC_PER_PAGE_DIGITS)
    return results, pages, pages_source


def _run_one(
    pdf: Path,
    out: Path,
    gt: str | None,
    adapters: list[Adapter] | None,
    normalizers: dict[str, Normalizer] | None,
) -> int:
    """PDF を 1 件処理して出力一式を書く。入力が不正なら 2、成功なら 0。"""
    if not pdf.is_file():
        print(f"入力ファイルが見つかりません: {pdf}", file=sys.stderr)
        return 2
    if pdf.suffix.lower() != ".pdf":
        print(f"PDF ファイルではありません: {pdf}", file=sys.stderr)
        return 2

    out_dir = out / pdf.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    # 再現性メタデータ（企画書 §49 の最小セット）
    pages = _page_count(pdf)
    run = {
        "jdocbench_version": __version__,
        "input": str(pdf.resolve()),
        "sha256": _sha256(pdf),
        "size_bytes": pdf.stat().st_size,
        "pages": pages,
        "pages_source": "pdfinfo" if pages is not None else None,
        "ground_truth": None,
        "ground_truth_sha256": None,
        "started_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu": _cpu_model(),
        "ram_total_bytes": _ram_total_bytes(),
        "fonts_ja": _fonts_ja(),
    }
    run["adapters"], pages, pages_source = _run_adapters(
        ADAPTERS if adapters is None else adapters,
        pdf,
        out_dir,
        pages,
        normalizers=normalizers,
    )
    # pdfinfo が無い環境では正規化結果から補完されるため、実行後の値で上書きする
    run["pages"] = pages
    run["pages_source"] = pages_source
    # Ground Truth があれば DSQS-JP を計算する（企画書 §18〜19）。無ければ何もしない
    gt_path = Path(gt) if gt else Path(_GT_DIR) / f"{pdf.stem}.json"
    if gt_path.is_file():
        gt_bytes = _evaluate_adapters(gt_path, out_dir, run["adapters"])
        run["ground_truth"] = str(gt_path)
        # GT の版を記録する（企画書 §49）。評価に使ったのと同じバイト列をハッシュする。
        # GT が読めなかった場合（権限・実行中の削除など）は None のままにし、run は止めない
        run["ground_truth_sha256"] = (
            hashlib.sha256(gt_bytes).hexdigest() if gt_bytes is not None else None
        )
    (out_dir / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # 人が読む比較レポート（企画書 §30）
    (out_dir / "report.md").write_text(render(run), encoding="utf-8")
    # Original と各 Parser を並べる確認画面（企画書 §28）
    normalized_dir = out_dir / "normalized"
    docs = (
        {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(normalized_dir.glob("*.json"))
        }
        if normalized_dir.is_dir()
        else {}
    )
    image_paths = render_pages(pdf, out_dir, pages)
    (out_dir / "view.html").write_text(render_view(run, docs, image_paths), encoding="utf-8")

    print(out_dir)
    return 0


def _run(
    args: argparse.Namespace,
    adapters: list[Adapter] | None = None,
    normalizers: dict[str, Normalizer] | None = None,
) -> int:
    """指定された PDF を順に処理する。1 件が失敗しても残りは処理する。

    `--gt` は文書ごとに別の Ground Truth が要るため、複数文書のときは指定できない
    （複数文書では既定パス `groundtruth/<stem>.json` だけを見る）。
    """
    pdfs = [Path(pdf) for pdf in args.pdfs]
    if args.gt and len(pdfs) > 1:
        print("--gt は 1 文書のときだけ指定できます", file=sys.stderr)
        return 2

    out = Path(args.out)
    succeeded: list[Path] = []
    for pdf in pdfs:
        if _run_one(pdf, out, args.gt, adapters, normalizers) == 0:
            succeeded.append(pdf)
    _write_summary(out, pdfs)
    # Profile はアダプタを全て走らせた後に取る。pymupdf を親プロセスに載せると、以降に起動する
    # アダプタの子プロセスの Peak RSS（fork 時点の RSS を含む）を押し上げうるため
    # 対象は今回成功した文書だけ（前回の run.json が残る文書の profile.json を上書きしない）
    _write_profiles(out, succeeded)
    return 2 if len(succeeded) < len(pdfs) else 0


def _profile_one(pdf: Path) -> dict:
    """1 件の Document Profile を取る。失敗しても例外にせず `error` を入れて返す。

    どの PDF の特性かを結果から検証できるよう、成否にかかわらず入力のパス（`source`）と
    SHA-256（`sha256`。run.json の `sha256` と同じ値。読めなければ null）を先頭に入れる。
    """
    identity: dict = {"source": str(pdf), "sha256": None}
    try:
        identity["sha256"] = _sha256(pdf)
        return {**identity, **profile_pdf(pdf)}
    except Exception as exc:  # 読めない PDF（権限など）でも他の PDF を続ける
        return {**identity, "error": _error_message(exc)}


def _write_profiles(out: Path, pdfs: list[Path]) -> bool:
    """各 PDF の Document Profile を results/<stem>/profile.json に書く（Router の入力。#152）。

    取得に失敗しても（pymupdf が無い・PDF が壊れている等）止めず、`error` を書く
    （中身は `_profile_one`）。全て取れたら True。
    """
    ok = True
    for pdf in pdfs:
        profile = _profile_one(pdf)
        ok = ok and "error" not in profile
        out_dir = out / pdf.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "profile.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return ok


def _profile(args: argparse.Namespace) -> int:
    """アダプタを走らせずに Document Profile だけを書く。1 件でも取れなければ 2。

    `run` と違い Profile がこのコマンドの唯一の成果物なので、取得失敗を終了コードに出す。
    """
    pdfs = [Path(pdf) for pdf in args.pdfs]
    missing = [pdf for pdf in pdfs if not pdf.is_file()]
    for pdf in missing:
        print(f"入力ファイルが見つかりません: {pdf}", file=sys.stderr)
    ok = _write_profiles(Path(args.out), [pdf for pdf in pdfs if pdf.is_file()])
    return 2 if missing or not ok else 0


def _route(args: argparse.Namespace) -> int:
    """Profile を取り、Router の推奨を results/<stem>/route.json に書いて表を表示する（#162）。

    Profile が取れない PDF も止めず、Router の安全側の既定（rule `default-error`）を書く。
    入力ファイルが無いものだけ 2 を返す。
    """
    modes = MODES if args.mode == "both" else (args.mode,)
    out = Path(args.out)
    # C3（日本語フォント非埋め込み）の分岐が実行環境の日本語フォントに依存する（#173）
    fonts_ja = _fonts_ja()
    missing = False
    print("| Document | Mode | Parser | Fallbacks | Rule |")
    print("|---|---|---|---|---|")
    for pdf in (Path(p) for p in args.pdfs):
        if not pdf.is_file():
            print(f"入力ファイルが見つかりません: {pdf}", file=sys.stderr)
            missing = True
            continue
        data = route_all(_profile_one(pdf), modes, fonts_ja)
        out_dir = out / pdf.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "route.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for mode, result in data["routes"].items():
            fallbacks = ", ".join(result["fallbacks"]) or "-"
            print(f"| {pdf.stem} | {mode} | {result['parser']} | {fallbacks} | {result['rule']} |")
    return 2 if missing else 0


def _write_summary(
    out: Path, pdfs: list[Path], *, write_empty: bool = False, strict: bool = False
) -> dict:
    """全文書の run.json を読み直して summary.md / summary.json を書く（企画書 §4.3）。

    `_run_one` の戻り値を変えずに済ませるため run.json を読み直す。run.json の無い文書
    （失敗した文書、CI の分割ジョブで結果が届かなかった文書）は表から除き、ファイル名を
    summary に「結果の無い文書」として載せる。失敗したアダプタ（#207）も一覧にする。
    未導入（not available）、登録アダプタ（`jdocbench.adapters.ADAPTERS`）が run.json から
    丸ごと欠けている場合（missing、#226）、値があっても dict でない場合（invalid、#246）は
    `strict` のときだけ失敗として載せる。
    戻り値は summary.json の中身（呼び出し側が `missing` / `failures` を見る）。
    run.json が 1 件も無ければ、`write_empty` のときだけ（結果の無い文書の一覧として）書く。
    標準出力には出さない。
    """
    runs: list[dict] = []
    missing: list[str] = []
    for stem, name in {pdf.stem: pdf.name for pdf in pdfs}.items():
        run_json = out / stem / "run.json"
        if run_json.is_file():
            runs.append(json.loads(run_json.read_text(encoding="utf-8")))
        else:
            missing.append(name)
    failures = find_failures(
        runs,
        not_available=strict,
        adapter_names=[adapter.name for adapter in ADAPTERS] if strict else (),
    )
    summary = to_summary(runs, missing, failures)
    if not runs and not write_empty:
        return summary
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(render_summary(runs, missing, failures), encoding="utf-8")
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _summary(args: argparse.Namespace) -> int:
    """既存の結果ディレクトリから summary.md / summary.json を作り直す（#190）。

    CI の bench は文書ごとの並列ジョブで `run` し、集約ジョブで結果を 1 つのディレクトリに
    まとめてからこれを呼ぶ。PDF を渡すとその順に並べ、run.json の無い文書を「結果の無い文書」
    として載せて 2 を返す（`run` に同じ PDF を渡したときと同じ summary になる）。
    PDF を省くと run.json のある文書を名前順に並べる。

    `--strict`（CI 用。#207、#226、#246）では、`--known-failure <stem>:<adapter>[:<kind>]` に
    無いアダプタの失敗（error / normalize_error / dsqs_error / not available / missing /
    invalid）があっても 2 を返す。kind を省くと `error` とみなす（既知の組の失敗の種類が
    変わったら、それも想定外として拾うため）。
    起きなかった既知の失敗は警告だけ出す（直った瞬間に CI を赤にしない）。
    """
    out = Path(args.results)
    if args.pdfs:
        pdfs = [Path(pdf) for pdf in args.pdfs]
    else:
        stems = sorted(p.parent.name for p in out.glob("*/run.json")) if out.is_dir() else []
        pdfs = [Path(f"{stem}.pdf") for stem in stems]
    if not pdfs:
        print(f"run.json のある文書がありません: {out}", file=sys.stderr)
        return 2
    # 全文書の結果が届かなかったときも、その一覧を summary に残す（CI のアーティファクトで見られる）
    summary = _write_summary(out, pdfs, write_empty=True, strict=args.strict)
    missing = summary.get("missing", [])
    for name in missing:
        print(f"結果がありません: {name}", file=sys.stderr)
    unexpected = False
    if args.strict:
        known = {key if key.count(":") >= 2 else f"{key}:error" for key in args.known_failure}
        seen: set[str] = set()
        for failure in summary.get("failures", []):
            key = f"{failure['stem']}:{failure['adapter']}:{failure['kind']}"
            seen.add(key)
            if key in known:
                continue
            unexpected = True
            message = short_message(failure["message"] or "")
            print(
                f"想定外の失敗: {failure['stem']} × {failure['adapter']}"
                f"（{failure['kind']}）: {message}",
                file=sys.stderr,
            )
        for key in sorted(known - seen):
            print(
                f"既知の失敗が起きていません（--known-failure を見直してください）: {key}",
                file=sys.stderr,
            )
    print(out / "summary.md")
    return 2 if missing or unexpected else 0


def _gt_init(args: argparse.Namespace) -> int:
    """normalized/<adapter>.json を Ground Truth の seed として groundtruth/ に写す。

    `adapter` を `"ground_truth"` に変えるだけで中身は触らない。Document へ復元せず
    dict のまま書き換える（seed は人が手で直す前提なので、IR の解釈を挟まない）。
    既存の GT は上書きしない。
    """
    pdf = Path(args.pdf)
    src = Path(args.results) / pdf.stem / "normalized" / f"{args.from_adapter}.json"
    if not src.is_file():
        print(
            f"正規化結果が見つかりません: {src}\n先に jdocbench run を実行してください",
            file=sys.stderr,
        )
        return 2

    dest = Path(args.out) / f"{pdf.stem}.json"
    if dest.exists():
        print(f"既に Ground Truth があります（上書きしません）: {dest}", file=sys.stderr)
        return 2

    doc = json.loads(src.read_text(encoding="utf-8"))
    doc["adapter"] = "ground_truth"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(dest)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jdocbench",
        description="日本語実務文書のドキュメントパーサを評価するベンチマーク",
    )
    parser.add_argument("--version", action="version", version=f"jdocbench {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="文書を処理し、実行メタデータを記録する")
    run_parser.add_argument("pdfs", nargs="+", help="入力 PDF のパス（複数可）")
    run_parser.add_argument("--out", default="results", help="出力先ディレクトリ（既定: results）")
    run_parser.add_argument(
        "--gt",
        default=None,
        help=(
            f"Ground Truth の JSON（既定: {_GT_DIR}/<PDF の stem>.json。無ければ評価しない）。"
            "1 文書のときだけ指定できる"
        ),
    )
    run_parser.set_defaults(func=_run)

    profile_parser = subparsers.add_parser(
        "profile", help="Document Profile（構造特性）だけを results/<stem>/profile.json に書く"
    )
    profile_parser.add_argument("pdfs", nargs="+", help="入力 PDF のパス（複数可）")
    profile_parser.add_argument(
        "--out", default="results", help="出力先ディレクトリ（既定: results）"
    )
    profile_parser.set_defaults(func=_profile)

    route_parser = subparsers.add_parser(
        "route", help="Document Profile から推奨パーサーを results/<stem>/route.json に書く"
    )
    route_parser.add_argument("pdfs", nargs="+", help="入力 PDF のパス（複数可）")
    route_parser.add_argument(
        "--mode",
        choices=[*MODES, "both"],
        default="both",
        help="quality（品質重視）/ cost（コスト重視）/ both（既定）",
    )
    route_parser.add_argument(
        "--out", default="results", help="出力先ディレクトリ（既定: results）"
    )
    route_parser.set_defaults(func=_route)

    summary_parser = subparsers.add_parser(
        "summary", help="既存の結果から summary.md / summary.json を作り直す"
    )
    summary_parser.add_argument("results", help="run の出力先ディレクトリ（results/<stem>/ の親）")
    summary_parser.add_argument(
        "pdfs",
        nargs="*",
        help=(
            "対象 PDF のパス（並び順と、結果の無い文書の検出に使う。"
            "省略時は run.json のある全文書）"
        ),
    )
    summary_parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "想定外のアダプタ失敗があれば 2 を返す（CI 用）。--known-failure に無い "
            "error / normalize_error / dsqs_error と、未導入（not available）、"
            "登録アダプタが run.json から丸ごと欠けている場合（missing）、"
            "値があっても dict でない場合（invalid）を失敗とみなす"
        ),
    )
    summary_parser.add_argument(
        "--known-failure",
        action="append",
        default=[],
        metavar="STEM:ADAPTER[:KIND]",
        help=(
            "既知の失敗。--strict でもこれは失敗にしない（繰り返し可。--strict のときだけ効く）。"
            "KIND は error / normalize_error / dsqs_error / not available / missing / invalid"
            "（省略時は error）"
        ),
    )
    summary_parser.set_defaults(func=_summary)

    gt_parser = subparsers.add_parser("gt", help="Ground Truth を操作する")
    gt_subparsers = gt_parser.add_subparsers(dest="gt_command", required=True)
    gt_init_parser = gt_subparsers.add_parser(
        "init", help="正規化結果から Ground Truth の seed を作る"
    )
    gt_init_parser.add_argument("pdf", help="対象 PDF のパス（ファイル名だけ使う）")
    gt_init_parser.add_argument(
        "--from",
        dest="from_adapter",  # from は予約語なので dest を変える
        required=True,
        help="seed にする正規化結果のアダプタ名（例: pdfplumber）",
    )
    gt_init_parser.add_argument(
        "--results", default="results", help="run の出力先ディレクトリ（既定: results）"
    )
    gt_init_parser.add_argument(
        "--out", default=_GT_DIR, help=f"出力先ディレクトリ（既定: {_GT_DIR}）"
    )
    gt_init_parser.set_defaults(func=_gt_init)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
