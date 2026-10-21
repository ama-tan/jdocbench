"""確認画面 view.html を作る（企画書 §28、MVP §53「Original と Parser Result を比較」）。

サーバー・外部 JS なしの静的 HTML 1 ファイル。左に Original のページ画像、
右に各アダプタの段落・表を並べる。
"""

from html import escape
from pathlib import Path, PurePath

_PAGES_DIR = "pages"
_SCALE = 1  # pypdfium2 の scale=1 は 72dpi
_MISSING = "-"

_STYLE = """
body { font-family: sans-serif; margin: 1rem; line-height: 1.6; color: #222; }
h1 { font-size: 1.4rem; }
h2 { font-size: 1.1rem; margin: 0 0 0.5rem; }
h3 { font-size: 0.95rem; margin: 0 0 0.3rem; }
.summary li { font-size: 0.9rem; }
.page { border-top: 1px solid #ccc; padding-top: 1rem; margin-top: 1rem; }
.columns { display: flex; gap: 1rem; align-items: flex-start; flex-wrap: wrap; }
.original { flex: 1 1 18rem; margin: 0; }
.original img { max-width: 100%; border: 1px solid #ccc; }
.parsers { flex: 2 1 24rem; display: flex; gap: 1rem; flex-wrap: wrap; }
.adapter { flex: 1 1 16rem; min-width: 0; font-size: 0.85rem; }
.adapter p { margin: 0 0 0.4rem; white-space: pre-wrap; word-break: break-word; }
.status { color: #666; font-weight: normal; }
.empty { color: #999; }
table { border-collapse: collapse; margin: 0 0 0.5rem; }
td { border: 1px solid #999; padding: 2px 4px; vertical-align: top; }
"""


def render_pages(pdf: Path, out_dir: Path, page_count: int | None) -> list[str | None]:
    """各ページを 72dpi の PNG にして out_dir/pages/p<N>.png に書き、相対パスを返す。

    pypdfium2（pdfplumber の依存）が無ければ画像なしで続行する（`[None] * page_count`）。
    描画に失敗したページは None。`page_count` が None のときは PDF の実ページ数を使う。
    """
    try:
        import pypdfium2
    except ImportError:
        return [None] * (page_count or 0)

    try:
        document = pypdfium2.PdfDocument(str(pdf))
    except Exception:
        return [None] * (page_count or 0)

    count = len(document) if page_count is None else page_count
    pages_dir = out_dir / _PAGES_DIR
    pages_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str | None] = []
    for index in range(count):
        name = f"p{index + 1}.png"
        try:
            document[index].render(scale=_SCALE).to_pil().save(pages_dir / name)
        except Exception:
            paths.append(None)
        else:
            paths.append(f"{_PAGES_DIR}/{name}")
    return paths


def _text(value: object) -> str:
    """文字列として表示する。空・None なら `-`。"""
    if value is None or value == "":
        return _MISSING
    return escape(str(value))


def _block_html(block: dict) -> str:
    """1 ブロックを段落（`<p>`）か表（`<table>`）にする。テキストは全てエスケープする。"""
    if block.get("type") == "table":
        rows = block.get("rows") or []
        cells = "".join(
            "<tr>" + "".join(f"<td>{_cell(cell)}</td>" for cell in row) + "</tr>" for row in rows
        )
        return f"<table>{cells}</table>"
    return f"<p>{escape(str(block.get('text') or ''))}</p>"


def _cell(value: object) -> str:
    """表のセル 1 つ分。None は空文字にする。"""
    return escape(str(value)) if value is not None else ""


def _page_blocks(doc: dict, number: int) -> list[dict]:
    """正規化 JSON から指定ページの blocks を取り出す。ページが無ければ空リスト。"""
    for page in doc.get("pages") or []:
        if page.get("number") == number:
            return page.get("blocks") or []
    return []


def _adapter_html(name: str, result: dict, doc: dict | None, number: int) -> str:
    """ページ 1 枚分の、アダプタ 1 つの列を作る。"""
    heading = f'<h3>{escape(name)} <span class="status">{_text(result.get("status"))}</span></h3>'
    if doc is None:
        # skipped / error / 正規化なし。見出しに status だけ出す
        return f'<div class="adapter">{heading}</div>'
    blocks = _page_blocks(doc, number)
    body = "".join(_block_html(block) for block in blocks) or '<p class="empty">(なし)</p>'
    return f'<div class="adapter">{heading}{body}</div>'


def _page_html(number: int, image_path: str | None, run: dict, docs: dict[str, dict]) -> str:
    """ページ 1 枚分のセクション（左に Original、右に各アダプタ）を作る。"""
    if image_path is None:
        figure = '<figure class="original"><p class="empty">画像なし</p></figure>'
    else:
        src = escape(image_path)
        figure = f'<figure class="original"><img src="{src}" alt="ページ {number}"></figure>'
    adapters: dict = run.get("adapters") or {}
    parsers = "".join(
        _adapter_html(name, result or {}, docs.get(name), number)
        for name, result in adapters.items()
    )
    return (
        '<section class="page">'
        f"<h2>ページ {number}</h2>"
        f'<div class="columns">{figure}<div class="parsers">{parsers}</div></div>'
        "</section>"
    )


def _summary_html(run: dict, page_count: int) -> str:
    """先頭の要約（ファイル名・ページ数・実行日時・アダプタと status の一覧）。"""
    name = PurePath(str(run.get("input", ""))).name or _MISSING
    adapters: dict = run.get("adapters") or {}
    items = "".join(
        f"<li>{escape(adapter_name)}: {_text((result or {}).get('status'))}</li>"
        for adapter_name, result in adapters.items()
    )
    return (
        f"<h1>JDocBench 確認画面: {escape(name)}</h1>"
        '<div class="summary">'
        f"<p>ページ数: {page_count} / 実行日時: {_text(run.get('started_at'))}</p>"
        f"<ul>{items}</ul>"
        "</div>"
    )


def render(run: dict, docs: dict[str, dict], image_paths: list[str | None]) -> str:
    """run.json 相当の dict・正規化 JSON・ページ画像の相対パスから view.html の本文を作る。

    純関数。ファイル I/O をしない。`docs` はアダプタ名 → normalized/<adapter>.json の dict。
    """
    page_count = max([len(image_paths)] + [len(doc.get("pages") or []) for doc in docs.values()])
    images = list(image_paths) + [None] * (page_count - len(image_paths))
    sections = "".join(
        _page_html(number, images[number - 1], run, docs) for number in range(1, page_count + 1)
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ja">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>JDocBench 確認画面</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{_summary_html(run, page_count)}\n"
        f"{sections}\n"
        "</body>\n"
        "</html>\n"
    )
