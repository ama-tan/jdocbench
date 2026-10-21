"""Tabula の raw 出力（tables.json）を Common Document IR に正規化する。

Tabula は表だけを抽出するので、Document は **table ブロックだけ**で構成する（camelot と同じ。
段落を他パーサーのテキストで補うことはしない）。

tables.json は `{"mode": ..., "pages": [{"page": N, "tables": [<tabula-java の JSON の表>]}]}`。
セルは各セルの `text` をそのまま使い、文字列でないものだけを `""` にする（Tabula がセル内の
改行に使う `\\r` や、Tabula 自身が行う全角→半角の変換も含めて加工しない）。bbox は Tabula の
`left / top / right / bottom`（PDF ポイント、原点は左上）をそのまま `(x0, top, x1, bottom)` に
載せる（pymupdf / camelot の正規化と同じ上原点）。
ページは tables.json の並びのまま（表の無いページも空の Page として残す）。`page` が 1 未満・
整数でないページは置く位置が決まらないので捨てる。raw は読むだけで変更しない（企画書 §54-2）。
"""

import json
from pathlib import Path

from jdocbench.ir import Block, Document, Page

NAME = "tabula"


def _bbox(table: dict) -> tuple[float, float, float, float] | None:
    values = [table.get(key) for key in ("left", "top", "right", "bottom")]
    if not all(isinstance(value, int | float) for value in values):
        return None
    x0, top, x1, bottom = values
    return (x0, top, x1, bottom)


def _table_block(table: dict, mode: object) -> Block:
    """tabula-java の表 1 つを table ブロックにする。"""
    rows = [
        [text if isinstance(text := cell.get("text"), str) else "" for cell in row]
        for row in table.get("data", [])
    ]
    return Block(type="table", rows=rows, bbox=_bbox(table), extra={"mode": mode})


def normalize(raw_dir: Path) -> Document:
    """tables.json を読み、ページ × 表の Document にする（ページ内は出現順）。"""
    raw = json.loads((raw_dir / "tables.json").read_text(encoding="utf-8"))
    raw_pages = [
        page
        for page in raw.get("pages", [])
        if isinstance(page.get("page"), int) and page["page"] >= 1
    ]
    page_count = max([0] + [page["page"] for page in raw_pages])
    pages = [Page(number=number) for number in range(1, page_count + 1)]
    for raw_page in raw_pages:
        blocks = pages[raw_page["page"] - 1].blocks
        blocks.extend(_table_block(table, raw.get("mode")) for table in raw_page.get("tables", []))
    return Document(adapter=NAME, pages=pages)
