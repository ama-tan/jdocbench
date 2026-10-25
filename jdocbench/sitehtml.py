"""公開サイトの静的 HTML を data.json と documents.json から作る（jdoc-bench#270）。

純関数だけを置く。ファイル I/O をしない（読み書きは cli.py が行う）。
入力は `jdocbench site-data` の data.json（数値だけ）と、文書の公開情報 documents.json の 2 つだけ。
出力は JS・外部読み込みを使わない HTML（CSS は `<style>` にインライン）で、生成時刻を埋めず、
同じ入力からは同じバイト列になる。ページ画像・抽出テキスト・Ground Truth は載せない。
数値の桁と Parser 平均の式は summary.py と同じものを使う。
"""

import html
from collections.abc import Mapping
from string import Formatter

from jdocbench.summary import (
    _JNF_DIGITS,
    _MEAN_DIGITS,
    _MEAN_NOTE,
    _MISSING,
    _RSS_DIGITS,
    _TOTAL_DIGITS,
    _WALL_DIGITS,
    _round,
    mean_values,
)

# data.json 側で除外済みでも、ライセンス上載せられないアダプタは念のためここでも落とす
HIDDEN_ADAPTERS = frozenset({"yomitoku"})
# DSQS-JP を計算したかの判定に使うキー（summary.py の dsqs dict の中身に当たる）
_SCORE_KEYS = ("total", "cf", "sed", "tsf", "roa", "roa_effective", "jnf")
_ADAPTER_COLUMNS = (
    "Parser",
    "Status",
    "Total",
    "JNF",
    "CF",
    "SED",
    "TSF",
    "ROA",
    "Wall [s]",
    "Peak RSS [MiB]",
    "Version",
)
_MEAN_COLUMNS = ("Parser", "n", "Total", "JNF", "CF", "SED", "TSF", "ROA")
_GIB = 1024**3

_STYLE = """
body { font-family: sans-serif; margin: 0 auto; padding: 1rem; max-width: 72rem;
  line-height: 1.6; color: #222; background: #fff; }
h1 { font-size: 1.4rem; }
h2 { font-size: 1.1rem; margin-top: 1.5rem; border-bottom: 1px solid #ccc; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; font-size: 0.85rem; }
th, td { border: 1px solid #999; padding: 2px 6px; vertical-align: top; }
th { background: #f0f0f0; text-align: left; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.2rem 1rem; }
dt { font-weight: bold; }
dd { margin: 0; word-break: break-word; }
.note { font-size: 0.85rem; color: #444; }
"""

_DESCRIPTION = (
    "JDocBench は、日本語の実務文書（PDF）を対象に、文書パーサーごとの構造化品質（DSQS-JP）・"
    "日本語表記の忠実度（JNF）・計算コスト（Wall・Peak RSS）を同じ条件で実測して比べる"
    "ベンチマークです。このサイトには計測した数値だけを載せ、評価に使った PDF・抽出テキスト・"
    "Ground Truth は載せません。原本は各文書ページの出典 URL から入手できます。"
)
_MISSING_NOTE = (
    "は値が無いこと（パーサーの失敗・未導入・対象外、"
    "または Ground Truth が無く DSQS-JP を計算していないこと）を表します。"
)
_NOTES = (
    "ROA は Total に入れた値です。Ground Truth の行の対応率が 0.15 未満、"
    "または対応する行が無い文書では 0 として数えます。",
    "JNF（日本語表記の忠実度）は Total に入れない別の指標です。",
    "YomiToku は非商用ライセンス（CC BY-NC-SA 4.0）のため、結果を載せていません。",
)
_NO_TEXT_NOTE = (
    "原本（PDF）は出典 URL から入手してください。このページには数値だけを載せ、"
    "ページ画像・抽出テキスト・Ground Truth は載せていません。"
)
# 値を埋め込む固定文（{} の中身は数値・documents.json の値・アダプタ名）
_RUN_NOTE = "数値はすべて run {run_id}（計測日 {measured_at}）の値です。"
_BEST_TOTAL = "{total}（{names}）"
_BREAKDOWN_ITEM = "{value}（{count} 文書）"
_RAM_GIB = "{gib:.1f} GiB"
_DOCUMENT_TITLE = "{title} - JDocBench"
_REPO_LINK = "公開リポジトリ: {link}"
_ARTICLE_LINK = "記事: {link}"
_LICENSE = "{name}（{link}）"
_INDEX_TITLE = "JDocBench 計測結果"
_MEAN_HEADING = "Parser 平均（Ground Truth のある文書のみ）"
_DOC_COLUMNS = ("文書", "ドメイン", "ページ数", "構造特性", "最良 Total")
# 文書ページの見出しやラベルなど、本文にそのまま出す固定の文言
_LABELS = (
    "run_id",
    "run",
    "計測日",
    "コミット",
    "tool SHA",
    "CPU",
    "RAM",
    "GPU",
    "なし",
    "Python",
    "jdocbench",
    "計測条件",
    "DSQS Total",
    "Document",
    "注記",
    "発行元",
    "出典 URL",
    "ライセンス",
    "出典表示",
    "抜粋",
    "Ground Truth の版",
    "Platform",
    "計測日時",
    "一覧へ戻る",
    "パーサーごとの結果",
    "計測環境",
    "共通の注記",
    " / ",
    "、",
)


def _esc(value: object) -> str:
    """HTML の本文・属性に入れられる文字列にする。None は `-`。"""
    return _MISSING if value is None else html.escape(str(value), quote=True)


def _link(url: object, text: object) -> str:
    """http(s) の URL ならリンク。それ以外（data: / javascript: 等）はリンクも文字列も出さない。"""
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return f'<a href="{_esc(url)}">{_esc(text)}</a>'
    return _MISSING


def _table(header: tuple[str, ...], rows: list[list[tuple[str, bool]]]) -> str:
    """表を作る。セルは（エスケープ済みの HTML, 数値か）。"""
    head = "".join(f"<th>{_esc(name)}</th>" for name in header)
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="num">{cell}</td>' if num else f"<td>{cell}</td>" for cell, num in row
        )
        + "</tr>\n"
        for row in rows
    )
    return f'<div class="scroll"><table>\n<tr>{head}</tr>\n{body}</table></div>'


def _dl(items: list[tuple[str, str]]) -> str:
    """定義リスト。値はエスケープ済みの HTML。"""
    body = "".join(f"<dt>{_esc(key)}</dt><dd>{value}</dd>\n" for key, value in items)
    return f"<dl>\n{body}</dl>"


def _ul(items: list[str]) -> str:
    """箇条書き。要素はエスケープ済みの HTML。"""
    return "<ul>\n" + "".join(f"<li>{item}</li>\n" for item in items) + "</ul>"


def _page(title: str, body: str) -> str:
    """HTML 文書全体。"""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ja">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def _dict(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _adapters(doc: Mapping) -> dict[str, Mapping]:
    """文書 1 件のアダプタ（載せないものを除く）。"""
    return {
        name: _dict(entry)
        for name, entry in _dict(doc.get("adapters")).items()
        if name not in HIDDEN_ADAPTERS
    }


def _adapter_names(documents: list[Mapping]) -> list[str]:
    """全文書に出てくるアダプタ名を出現順にそろえる（列の並び）。"""
    return list(dict.fromkeys(name for doc in documents for name in _adapters(doc)))


def _scores(entry: Mapping) -> dict:
    """アダプタ 1 件の DSQS-JP スコア。全キーが None（評価していない）なら空 dict。"""
    scores = {key: entry.get(key) for key in _SCORE_KEYS}
    return scores if any(value is not None for value in scores.values()) else {}


def _rss_mib(entry: Mapping) -> float | None:
    """Peak RSS を KB から MiB にする（summary.py と同じ）。"""
    kb = entry.get("peak_rss_kb")
    return kb / 1024 if isinstance(kb, int | float) and not isinstance(kb, bool) else None


def _best_total(doc: Mapping) -> str:
    """最良の Total とそのパーサー（同点は「 / 」で併記）。Total が 1 つも無ければ `-`。"""
    totals = {
        name: total
        for name, entry in _adapters(doc).items()
        if isinstance(total := entry.get("total"), int | float) and not isinstance(total, bool)
    }
    if not totals:
        return _MISSING
    best = max(totals.values())
    names = " / ".join(name for name, total in totals.items() if total == best)
    return _BEST_TOTAL.format(total=_round(best, _TOTAL_DIGITS), names=_esc(names))


def _breakdown(values: list[str]) -> str:
    """全文書で同じならその値、違えば「値（n 文書）」を出現順に並べる（CPU の内訳など）。"""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    if len(counts) <= 1:
        return _esc(values[0]) if values else _MISSING
    return "、".join(
        _BREAKDOWN_ITEM.format(value=_esc(value), count=count) for value, count in counts.items()
    )


def _ram(value: object) -> str:
    """RAM のバイト数を GiB 1 桁にする。"""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return _RAM_GIB.format(gib=value / _GIB)
    return _MISSING


def _run_text(doc: Mapping, key: str) -> str:
    value = _dict(doc.get("run")).get(key)
    return _MISSING if value is None else str(value)


def _fixed_notes(source: Mapping) -> list[str]:
    return [
        f"<code>{_MISSING}</code> {_esc(_MISSING_NOTE)}",
        *map(_esc, _NOTES),
        _RUN_NOTE.format(
            run_id=_esc(source.get("run_id")), measured_at=_esc(source.get("measured_at"))
        ),
    ]


def render_index(data: Mapping, meta: Mapping) -> str:
    """トップページ（index.html）。"""
    documents = [_dict(doc) for doc in _list(data.get("documents"))]
    infos = {info.get("stem"): info for info in map(_dict, _list(meta.get("documents")))}
    source = _dict(meta.get("source"))
    names = _adapter_names(documents)

    links = [_REPO_LINK.format(link=_link(source.get("repo_url"), source.get("repo_url")))]
    if source.get("article_url"):
        article = _link(source.get("article_url"), source.get("article_url"))
        links.append(_ARTICLE_LINK.format(link=article))

    conditions = [("run_id", _esc(source.get("run_id")))]
    if source.get("run_url"):
        conditions.append(("run", _link(source.get("run_url"), source.get("run_url"))))
    conditions += [
        ("計測日", _esc(source.get("measured_at"))),
        ("コミット", _esc(source.get("commit"))),
        ("tool SHA", _esc(source.get("tool_sha"))),
        ("CPU", _breakdown([_run_text(doc, "cpu") for doc in documents])),
        (
            "RAM",
            _breakdown([_ram(_dict(doc.get("run")).get("ram_total_bytes")) for doc in documents]),
        ),
        ("GPU", "なし"),
        ("Python", _breakdown([_run_text(doc, "python") for doc in documents])),
        ("jdocbench", _breakdown([_run_text(doc, "jdocbench_version") for doc in documents])),
    ]

    doc_rows = []
    total_rows = []
    for doc in documents:
        stem = doc["stem"]
        info = _dict(infos.get(stem))
        page = f'<a href="documents/{_esc(stem)}.html">'
        doc_rows.append(
            [
                (f"{page}{_esc(info.get('title'))}</a>", False),
                (_esc(info.get("domain")), False),
                (_esc(doc.get("pages")), True),
                (_esc(" ".join(map(str, _list(info.get("features"))))), False),
                (_best_total(doc), False),
            ]
        )
        adapters = _adapters(doc)
        total_rows.append(
            [(f"{page}{_esc(stem)}</a>", False)]
            + [(_round(_dict(adapters.get(n)).get("total"), _TOTAL_DIGITS), True) for n in names]
        )

    mean_rows = []
    for name in names:
        values = mean_values([_scores(_adapters(doc).get(name, {})) for doc in documents])
        if values is not None:
            mean_rows.append([(_esc(name), False)] + [(value, True) for value in values])

    body = [
        f"<h1>{_esc(_INDEX_TITLE)}</h1>",
        f"<p>{_esc(_DESCRIPTION)}</p>",
        _ul(links),
        "<h2>計測条件</h2>",
        _dl(conditions),
    ]
    if notes := [_esc(note) for note in _list(source.get("notes"))]:
        body.append(_ul(notes))
    body += [
        "<h2>文書</h2>",
        _table(_DOC_COLUMNS, doc_rows),
        "<h2>DSQS Total</h2>",
        _table(("Document", *names), total_rows),
    ]
    if mean_rows:
        body += [
            f"<h2>{_esc(_MEAN_HEADING)}</h2>",
            _table(_MEAN_COLUMNS, mean_rows),
            f'<p class="note">{_esc(_MEAN_NOTE.strip())}</p>',
        ]
    body += ["<h2>注記</h2>", _ul(_fixed_notes(source))]
    return _page(_INDEX_TITLE, "\n".join(body))


def render_document(doc: Mapping, info: Mapping, source: Mapping) -> str:
    """文書ページ（documents/<stem>.html）。"""
    license_ = _dict(info.get("license"))
    sha = doc.get("ground_truth_sha256")
    fields = [
        ("発行元", _esc(info.get("publisher"))),
        ("出典 URL", _link(info.get("source_url"), info.get("source_url"))),
        (
            "ライセンス",
            _LICENSE.format(
                name=_esc(license_.get("name")),
                link=_link(license_.get("url"), license_.get("url")),
            ),
        ),
        ("出典表示", _esc(info.get("attribution"))),
        ("ドメイン", _esc(info.get("domain"))),
        ("ページ数", _esc(doc.get("pages"))),
        ("構造特性", _esc(" ".join(map(str, _list(info.get("features")))))),
        ("抜粋", _esc(info.get("excerpt_note"))),
        ("Ground Truth の版", _esc(sha[:12]) if isinstance(sha, str) else _MISSING),
    ]
    rows = [
        [
            (_esc(name), False),
            (_esc(entry.get("status")), False),
            (_round(entry.get("total"), _TOTAL_DIGITS), True),
            (_round(entry.get("jnf"), _JNF_DIGITS), True),
            (_round(entry.get("cf"), _MEAN_DIGITS), True),
            (_round(entry.get("sed"), _MEAN_DIGITS), True),
            (_round(entry.get("tsf"), _MEAN_DIGITS), True),
            (_round(entry.get("roa_effective"), _MEAN_DIGITS), True),
            (_round(entry.get("wall_time_sec"), _WALL_DIGITS), True),
            (_round(_rss_mib(entry), _RSS_DIGITS), True),
            (_esc(entry.get("version")), False),
        ]
        for name, entry in _adapters(doc).items()
    ]
    environment = [
        ("CPU", _esc(_run_text(doc, "cpu"))),
        ("RAM", _ram(_dict(doc.get("run")).get("ram_total_bytes"))),
        ("GPU", "なし"),
        ("Platform", _esc(_run_text(doc, "platform"))),
        ("計測日時", _esc(_run_text(doc, "started_at"))),
    ]
    body = [
        '<p><a href="../index.html">一覧へ戻る</a></p>',
        f"<h1>{_esc(info.get('title'))}</h1>",
        _dl(fields),
        "<h2>パーサーごとの結果</h2>",
        _table(_ADAPTER_COLUMNS, rows),
        "<h2>計測環境</h2>",
        _dl(environment),
    ]
    if notes := [_esc(note) for note in _list(info.get("notes"))]:
        body += ["<h2>注記</h2>", _ul(notes)]
    body += [
        f"<p>{_esc(_NO_TEXT_NOTE)}</p>",
        "<h2>共通の注記</h2>",
        _ul(_fixed_notes(source)),
    ]
    return _page(_DOCUMENT_TITLE.format(title=info.get("title")), "\n".join(body))


def _stems(documents: list[Mapping], label: str) -> list[str]:
    """stem の列。ファイル名に使えない・重複する stem があれば ValueError。"""
    stems = [doc.get("stem") for doc in documents]
    for stem in stems:
        if (
            not isinstance(stem, str)
            or not stem
            or stem.startswith(".")
            or "/" in stem
            or "\\" in stem
        ):
            raise ValueError(f"{label} にファイル名に使えない stem があります: {stem!r}")
    if len(set(stems)) != len(stems):
        raise ValueError(f"{label} に重複した stem があります")
    return stems


def build_site(data: Mapping, meta: Mapping) -> dict[str, str]:
    """data.json と documents.json の中身から、相対パス → HTML の dict を作る。

    両者の stem の集合が一致しなければ ValueError（数値と公開情報の取り違えを防ぐ）。
    """
    documents = [_dict(doc) for doc in _list(data.get("documents"))]
    infos = [_dict(info) for info in _list(meta.get("documents"))]
    data_stems = _stems(documents, "data.json")
    meta_stems = _stems(infos, "documents.json")
    if set(data_stems) != set(meta_stems):
        only_data = sorted(set(data_stems) - set(meta_stems))
        only_meta = sorted(set(meta_stems) - set(data_stems))
        raise ValueError(
            "data.json と documents.json の stem が一致しません"
            f"（data.json だけ: {only_data}、documents.json だけ: {only_meta}）"
        )
    by_stem = dict(zip(meta_stems, infos, strict=True))
    source = _dict(meta.get("source"))
    pages = {"index.html": render_index(data, meta)}
    for stem, doc in zip(data_stems, documents, strict=True):
        pages[f"documents/{stem}.html"] = render_document(doc, by_stem[stem], source)
    return pages


def _fragments(template: str) -> list[str]:
    """値を埋め込む固定文から、固定の部分（{} の外側）だけを取り出す。"""
    return [literal for literal, _, _, _ in Formatter().parse(template) if literal]


# HTML の本文に出る固定の文言の一覧（流出ガードのテキスト出所テストが使う。jdoc-bench#271）。
# 本文に新しい文言を足すときは、ここにも足さないと tests/test_sitehtml.py が落ちる
FIXED_TEXTS = frozenset(
    {
        _DESCRIPTION,
        _MISSING_NOTE,
        *_NOTES,
        _NO_TEXT_NOTE,
        _MEAN_NOTE.strip(),
        _INDEX_TITLE,
        _MEAN_HEADING,
        *_DOC_COLUMNS,
        *_ADAPTER_COLUMNS,
        *_MEAN_COLUMNS,
        *_LABELS,
        *(
            fragment
            for template in (
                _RUN_NOTE,
                _BEST_TOTAL,
                _BREAKDOWN_ITEM,
                _RAM_GIB,
                _DOCUMENT_TITLE,
                _REPO_LINK,
                _ARTICLE_LINK,
                _LICENSE,
            )
            for fragment in _fragments(template)
        ),
    }
)
