"""公開サイト用の数値だけの data.json を作る（jdoc-bench#269）。

純関数だけを置く。ファイル I/O をしない（読み書きは cli.py が行う）。
summary.json と各文書の run.json から、allowlist に挙げたキーだけを取り出す。
原文の断片を含みうる値（run.json の `input` / `ground_truth` / `reason` / `extra`、
failures の `message` など）は読まず、出力にも入れない。文字列の値は形を検査し、
想定外の文字列（ステータスや失敗の種類が既知でないもの等）は落とす。
出力は summary.json と互換のスキーマ（`documents` / `missing` / `failures`）に、
実行環境のメタ（`run`）を足したもの。
"""

from collections.abc import Iterable, Mapping
from datetime import datetime

# summary.json のアダプタ 1 件のうち数値として通すキー（summary.py の _summary_adapter と同順）
_ADAPTER_NUMBERS = (
    "roa",
    "total",
    "cf",
    "sed",
    "tsf",
    "roa_effective",
    "jnf",
    "wall_time_sec",
    "peak_rss_kb",
)
_STATUSES = frozenset({"ok", "error", "skipped"})
# summary.find_failures が付ける kind
_FAILURE_KINDS = frozenset(
    {"error", "normalize_error", "dsqs_error", "not available", "missing", "invalid"}
)
# run.json から読む実行環境のメタ
_RUN_META = (
    "jdocbench_version",
    "python",
    "platform",
    "cpu",
    "ram_total_bytes",
    "started_at",
    "fonts_ja",
)
_RUN_META_NUMBERS = frozenset({"ram_total_bytes"})
# 文字列の値の長さの上限（版数・CPU 名・日時などに十分で、文章は入らない長さ）
_TEXT_LIMIT = 120


def _number(value: object) -> int | float | None:
    """bool を除く数値ならそのまま、それ以外は None。"""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    return None


def _text(value: object) -> str | None:
    """1 行で短い文字列ならそのまま、それ以外は None。"""
    if isinstance(value, str) and len(value) <= _TEXT_LIMIT and value.isprintable():
        return value
    return None


def _name(value: object) -> str | None:
    """文書の stem・アダプタ名として使える文字列（パス区切りを含まない）か。"""
    text = _text(value)
    if text and not text.startswith(".") and "/" not in text and "\\" not in text:
        return text
    return None


def _sha256(value: object) -> str | None:
    """16 進 64 桁の文字列ならそのまま、それ以外は None。"""
    if isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value):
        return value
    return None


def _dict(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _meta_value(key: str, value: object) -> object:
    """run.json の実行環境のメタ 1 件を検査する。"""
    if key in _RUN_META_NUMBERS:
        return _number(value)
    if key == "started_at":
        text = _text(value)
        try:
            datetime.fromisoformat(text or "")
        except ValueError:
            return None
        return text
    if key == "fonts_ja":
        if not isinstance(value, list):
            return None
        return [font for item in value if (font := _text(item)) is not None]
    return _text(value)


def _run_meta(run: Mapping) -> dict:
    """run.json 1 件から実行環境のメタだけを取り出す。"""
    return {key: _meta_value(key, run.get(key)) for key in _RUN_META}


def _adapter(entry: Mapping, run_entry: Mapping) -> dict:
    """data.json のアダプタ 1 件分。summary.json の値に run.json の version を足す。"""
    status = entry.get("status")
    if status is None:
        status = run_entry.get("status")
    result: dict[str, object] = {"status": status if status in _STATUSES else None}
    result.update({key: _number(entry.get(key)) for key in _ADAPTER_NUMBERS})
    result["version"] = _text(run_entry.get("version"))
    return result


def _common(values: list) -> object:
    """全要素が同じ値ならその値、違う値が混じるか空なら None。"""
    return values[0] if values and all(value == values[0] for value in values) else None


def build_site_data(
    summary: Mapping, runs: Mapping[str, Mapping], exclude: Iterable[str] = ()
) -> dict:
    """summary.json と run.json（stem → 中身）から data.json の中身を作る。

    `exclude` に挙げたアダプタは documents と failures から列ごと落とす。
    pages / ground_truth_sha256 / status は summary.json を正とし、無いときだけ run.json を使う。
    実行環境のメタは `documents[].run` に文書ごとの値を常に全キー置く（run.json が無ければ None）。
    トップの `run` は、各キーと各アダプタの version（`adapter_versions`）について、全文書で同じなら
    その値、違えば None にした派生値（サイトの見出し用）。
    """
    excluded = set(exclude)
    documents: list[dict] = []
    for entry in summary.get("documents") or []:
        entry = _dict(entry)
        stem = _name(entry.get("stem"))
        if stem is None:
            continue
        run = _dict(runs.get(stem))
        run_adapters = _dict(run.get("adapters"))
        pages = _number(entry.get("pages"))
        sha = _sha256(entry.get("ground_truth_sha256"))
        documents.append(
            {
                "stem": stem,
                "pages": pages if pages is not None else _number(run.get("pages")),
                "ground_truth_sha256": sha or _sha256(run.get("ground_truth_sha256")),
                "adapters": {
                    name: _adapter(_dict(value), _dict(run_adapters.get(name)))
                    for raw_name, value in _dict(entry.get("adapters")).items()
                    if (name := _name(raw_name)) is not None and name not in excluded
                },
                "run": _run_meta(run),
            }
        )

    data: dict[str, object] = {"documents": documents}
    missing = [name for item in summary.get("missing") or [] if (name := _name(item)) is not None]
    if missing:
        data["missing"] = missing
    failures = []
    for item in summary.get("failures") or []:
        item = _dict(item)
        stem, adapter, kind = _name(item.get("stem")), _name(item.get("adapter")), item.get("kind")
        if stem is None or adapter is None or adapter in excluded or kind not in _FAILURE_KINDS:
            continue
        # message は原文の断片（パスや抽出テキスト）を含みうるので入れない
        failures.append({"stem": stem, "adapter": adapter, "kind": kind})
    if failures:
        data["failures"] = failures

    names = list(dict.fromkeys(name for d in documents for name in d["adapters"]))
    common: dict[str, object] = {
        key: _common([d["run"][key] for d in documents]) for key in _RUN_META
    }
    common["adapter_versions"] = {
        name: _common([d["adapters"][name]["version"] for d in documents if name in d["adapters"]])
        for name in names
    }
    data["run"] = common
    return data
