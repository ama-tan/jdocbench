"""Camelot の raw 出力（tables.json）を Common Document IR に正規化する。

Camelot は表だけを抽出するので、Document は **table ブロックだけ**で構成する。段落は
作らず、他パーサーのテキストで補うこともしない（特定パーサーに有利な正規化をしない）。
段落が無いので `_tables.drop_table_content_lines` による二重計上の解消も要らない。

セルは pdfplumber / pymupdf と同じく文字列でないもの（`None` 等）を `""` にするだけで、
内容は加工しない。bbox は Camelot の PDF 座標系（原点は左下、`(x0, y0, x1, y1)` で
y0 < y1）を、pymupdf と同じ上原点の `(x0, top, x1, bottom)` にページ高さで反転して載せる。
ページ数は tables.json の `page_count` に合わせる（表の無いページも空の Page として残す）。
`page` が 1 未満・整数でない表は置くページが決まらないので捨てる（負の index で最終ページに
紛れ込ませない）。`page_count` を超える表はページを増やして置く。
raw は読むだけで変更しない（企画書 §54-2）。
"""

import json
from pathlib import Path

from jdocbench.ir import Block, Document, Page

NAME = "camelot"


def _bbox(table: dict) -> tuple[float, float, float, float] | None:
    """左下原点の bbox を上原点に反転する。形が違えば None。"""
    bbox = table.get("bbox")
    height = table.get("page_height")
    if not (isinstance(bbox, list) and len(bbox) == 4) or not isinstance(height, int | float):
        return None
    x0, y0, x1, y1 = bbox
    return (x0, height - y1, x1, height - y0)


def _table_block(table: dict) -> Block:
    """tables.json の 1 表を table ブロックにする。"""
    rows = [[cell if isinstance(cell, str) else "" for cell in row] for row in table["cells"]]
    extra = {key: table.get(key) for key in ("flavor", "accuracy", "whitespace")}
    return Block(type="table", rows=rows, bbox=_bbox(table), extra=extra)


def normalize(raw_dir: Path) -> Document:
    """tables.json を読み、ページ × 表の Document にする（ページ内は出現順）。"""
    raw = json.loads((raw_dir / "tables.json").read_text(encoding="utf-8"))
    # 置くページが決まらない表は捨てる（Camelot の page は 1 始まり）
    tables = [
        table
        for table in raw.get("tables", [])
        if isinstance(table.get("page"), int) and table["page"] >= 1
    ]
    page_count = max([raw.get("page_count", 0)] + [table["page"] for table in tables])
    pages = [Page(number=number) for number in range(1, page_count + 1)]
    for table in tables:
        pages[table["page"] - 1].blocks.append(_table_block(table))
    return Document(adapter=NAME, pages=pages)
