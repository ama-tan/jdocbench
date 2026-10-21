"""Unstructured の raw 出力（elements.json）を Common Document IR に正規化する。

elements.json は `partition_pdf` の要素を `to_dict()` した配列（出現順 = Unstructured の読み順）。

- **ページ**: `metadata.page_number`。要素の無いページも空ページとして残す（番号がずれると
  他アダプタと比較できないため）。ページ数は `meta.json` の `page_count`（アダプタが PDF から
  数えたもの）と要素の最大のページ番号の大きいほう。ページ番号の無い要素は直前の要素のページに置く
- **表**: `type == "Table"` で `metadata.text_as_html` があれば、その HTML を行 × 列にした
  table ブロック（HTML の分解は PP-Structure の正規化と共通。結合セルは範囲の先頭にだけ
  文字列を置く）。
  HTML が無い・行にならない Table は `text` を paragraph にする
- **段落**: それ以外の要素（Title / NarrativeText / ListItem / Header / Footer / UncategorizedText
  など、要素の種類は問わない）は `text` を 1 要素 = 1 paragraph にする。`text` が空の要素
  （PageBreak・文字の無い Image など）は捨てる。要素の種類は `extra.type` に残す

bbox は `metadata.coordinates.points` の外接矩形 `(x0, y0, x1, y1)`。fast は PDF ポイント座標、
hi_res はページ画像のピクセル座標（`coordinates.system`）で、正規化では変換しない。
Unstructured の Table 要素は表の中身を段落に重ねて出さないので、
`drop_table_content_lines` は使わない。
raw は読むだけで変更しない（企画書 §54-2）。
"""

import json
from pathlib import Path

from jdocbench.ir import Block, Document, Page
from jdocbench.normalizers._html_table import table_rows as html_table_rows

NAME = "unstructured"


def _bbox(metadata: dict) -> tuple[float, float, float, float] | None:
    """`coordinates.points`（頂点の列）の外接矩形。形が違えば None。"""
    coordinates = metadata.get("coordinates")
    points = coordinates.get("points") if isinstance(coordinates, dict) else None
    if not isinstance(points, list) or not points:
        return None
    try:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
    except (TypeError, ValueError, IndexError):
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _block(element: dict) -> Block | None:
    """要素 1 つを table / paragraph ブロックにする。文字の無い要素は None。"""
    metadata = element.get("metadata") if isinstance(element.get("metadata"), dict) else {}
    kind = element.get("type")
    bbox = _bbox(metadata)
    extra = {"type": kind}
    if kind == "Table":
        html = metadata.get("text_as_html")
        if isinstance(html, str) and (rows := html_table_rows(html)):
            return Block(type="table", bbox=bbox, rows=rows, extra=extra)
    text = element.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return Block(type="paragraph", text=text, bbox=bbox, extra=extra)


def normalize(raw_dir: Path) -> Document:
    """`elements.json`（と、あれば `meta.json`）を読み、ページ × 要素の Document にする。"""
    elements = json.loads((raw_dir / "elements.json").read_text(encoding="utf-8"))
    by_page: dict[int, list[Block]] = {}
    page = 1
    for element in elements:
        metadata = element.get("metadata") if isinstance(element.get("metadata"), dict) else {}
        number = metadata.get("page_number")
        if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
            page = number
        block = _block(element)
        blocks = by_page.setdefault(page, [])
        if block is not None:
            blocks.append(block)
    meta_path = raw_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    page_count = meta.get("page_count") if isinstance(meta, dict) else None
    if not isinstance(page_count, int) or isinstance(page_count, bool):
        page_count = 0
    last = max(max(by_page, default=0), page_count)
    pages = [Page(number=number, blocks=by_page.get(number, [])) for number in range(1, last + 1)]
    return Document(adapter=NAME, pages=pages)
