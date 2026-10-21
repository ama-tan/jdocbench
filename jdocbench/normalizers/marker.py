"""Marker の raw 出力（`<stem>/<stem>.json`、`--output_format json`）を IR に正規化する。

構造は `Document` → `Page` → ブロック。グループ（`TableGroup` / `FigureGroup` / `ListGroup` 等）は
さらに子を持つ。**読み順は `Page.children` の並び**（`Page.html` の `<content-ref>` の順と同じ。
スパイク `docs/spikes/marker.md` で確認）で、子を持つブロックは深さ優先で子を辿る。

- `Table` → table。`html` の `<table>` を `_html_table` で行 × 列にする（`rowspan` / `colspan` を
  解釈。結合セルは範囲の先頭にだけ文字列を置く IR の規約どおり）。表の子（セル）は辿らない。
  表の中身は Table にだけあり段落と重複しないので、`drop_table_content_lines` は使わない
- 子を持つブロック（グループ）→ 自分の `html`（子への `<content-ref>` の並び）は使わず子を辿る
- それ以外の葉（`Text` / `SectionHeader` / `Caption` / `ListItem` / `Footnote` 等）→ `html` の
  文字列が空でなければ paragraph。種別で落とさない（図中のラベル等も GT は段落に残す規約のため）。
  `PageHeader` / `PageFooter` / `Picture` / `Figure` は既定で `html` が空なので結果として落ちる

`html` の文字列はタグを除いて取り出し、`<br>` と段落系の閉じタグを改行にする（文字参照は戻す）。
見出しの段（`<h1>`〜）は IR に持たないので `extra["block_type"]` に種別だけ残す。

ページ番号は `Page.id`（`/page/<0 始まり>/Page/<n>`）の 0 始まりの番号 + 1。取れなければ出現順。
bbox は `polygon`（4 点）の外接矩形、無ければ `bbox` をそのまま使う。**PDF ポイント・左上原点・
y 下向き**（pdfplumber と同じ向き）。raw は読むだけで変更しない（企画書 §54-2）。
"""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

from jdocbench.adapters.marker import find_document_json
from jdocbench.ir import Block, Document, Page
from jdocbench.normalizers._html_table import table_rows

NAME = "marker"

_PAGE_ID = re.compile(r"^/page/(\d+)/")

# 改行にするタグ（開始・終了のどちらでも区切りとして扱う）
_BREAK_TAGS = frozenset({"br", "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"})


class _TextHtmlParser(HTMLParser):
    """ブロックの `html` から文字列だけを取り出す。改行系のタグは `\\n` にする。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _text(html: str) -> str:
    """`html` のタグを除いた文字列。行ごとに前後の空白を落とし、空行を除く。"""
    parser = _TextHtmlParser()
    parser.feed(html)
    parser.close()
    lines = (line.strip() for line in "".join(parser.parts).split("\n"))
    return "\n".join(line for line in lines if line)


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _bbox(block: dict) -> tuple[float, float, float, float] | None:
    """`polygon` の外接矩形。形が違えば `bbox`（`[x0, y0, x1, y1]`）。どちらも無ければ None。"""
    polygon = block.get("polygon")
    if (
        isinstance(polygon, list)
        and polygon
        and all(
            isinstance(point, list) and len(point) == 2 and all(_number(v) for v in point)
            for point in polygon
        )
    ):
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        return (min(xs), min(ys), max(xs), max(ys))
    bbox = block.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4 and all(_number(v) for v in bbox):
        return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    return None


def _blocks(block: dict, out: list[Block]) -> None:
    """ブロック 1 つ（と、その子孫）を読み順に IR のブロックにして `out` に足す。"""
    block_type = block.get("block_type")
    html = block.get("html")
    html = html if isinstance(html, str) else ""
    extra: dict[str, object] = {"block_type": block_type}
    if block_type == "Table":
        if rows := table_rows(html):
            out.append(Block(type="table", rows=rows, bbox=_bbox(block), extra=extra))
        return
    children = [child for child in block.get("children") or [] if isinstance(child, dict)]
    if children:
        for child in children:
            _blocks(child, out)
        return
    if text := _text(html):
        out.append(Block(type="paragraph", text=text, bbox=_bbox(block), extra=extra))


def _page_number(page: dict, fallback: int) -> int:
    """`Page.id` の 0 始まりの番号 + 1。取れなければ fallback。"""
    page_id = page.get("id")
    match = _PAGE_ID.match(page_id) if isinstance(page_id, str) else None
    return int(match.group(1)) + 1 if match else fallback


def normalize(raw_dir: Path) -> Document:
    """marker の本体 JSON を読み、ページ × （段落 + 表）の Document にする。"""
    path = find_document_json(raw_dir)
    if path is None:
        raise FileNotFoundError(f"{raw_dir} に marker の JSON（<stem>/<stem>.json）が無い")
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    pages: list[Page] = []
    for index, page in enumerate(data.get("children") or [], start=1):
        if not isinstance(page, dict):
            continue
        blocks: list[Block] = []
        for child in page.get("children") or []:
            if isinstance(child, dict):
                _blocks(child, blocks)
        pages.append(Page(number=_page_number(page, index), blocks=blocks))
    return Document(adapter=NAME, pages=pages)
