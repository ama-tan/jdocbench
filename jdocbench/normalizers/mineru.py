"""MinerU の raw 出力（`middle_json.json`、schema `docvortex.middle`）を IR に正規化する。

middle JSON は `pages[]`（`page_idx` は 0 始まり）→ `blocks[]` の 2 段で、ブロックの並びが
MinerU の読み順。ブロックの `content` は次の 3 通り。

- 行内要素の配列（`text` / `paragraph_title` / `doc_title` / `header` / `footer` /
  `page_number` / `ref_text` / キャプション・脚注など）: `{"type": "text", "content": 文字列}`、
  `equation_inline` / `code_inline`（文字列）、`hyperlink`（`content` に行内要素の配列）を
  出現順に連結して 1 ブロック = 1 paragraph にする
- 子ブロックの配列（`table` / `image` / `chart` / `code` / `list` / `index`）: 子を配列の順に
  辿る（表は キャプション → 本体 → 脚注 の順で並んでいる）
- 文字列（`table_body` / `code_body` / `image_body` / `chart_body` / `equation`）:
  `table_body` は HTML の `<table>` を行 × 列の table ブロックにし（HTML の分解は
  PP-Structure の正規化と共通。結合セルは範囲の先頭にだけ文字列を置く）、`code_body` は
  paragraph にする。`image_body` / `chart_body` は画像（文字列は空）、`equation`（行間数式の
  LaTeX）は PP-Structure の正規化と同じく落とす

ヘッダー・フッター・ページ番号も段落として残す（GT はノンブル・柱を段落に残す規約。
`groundtruth/README.md`）。表の中身は `table_body` にだけあり段落に重ならないため、
`drop_table_content_lines` は使わない。

bbox はページの幅・高さで割った 0〜1 の値（左上原点・y 下向き）で返るため、
`extensions.docvortex_layout.pages[].width_pt / height_pt` を掛けて PDF ポイントに直す
（pdfplumber と同じ左上原点）。ページの大きさが無ければ 0〜1 のまま載せる。
どちらかは `extra["bbox_unit"]`（`pt` / `ratio`）に記録する。
raw は読むだけで変更しない（企画書 §54-2）。
"""

import json
from pathlib import Path

from jdocbench.ir import Block, Document, Page
from jdocbench.normalizers._html_table import table_rows as html_table_rows

NAME = "mineru"

# 文字列の content を持つが段落にしないブロック（画像と行間数式）
_DROPPED_TYPES = frozenset({"image_body", "chart_body", "equation"})


def _inline_text(spans: list) -> str:
    """行内要素の配列を出現順に連結した文字列。`hyperlink` は中の要素を辿る。"""
    parts: list[str] = []
    for span in spans:
        if not isinstance(span, dict):
            continue
        content = span.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.append(_inline_text(content))
    return "".join(parts)


def _is_block(item: object) -> bool:
    """子ブロックか（行内要素ではないか）。子ブロックは `bbox` か `index` を持つ。"""
    return isinstance(item, dict) and ("bbox" in item or "index" in item)


def _bbox(
    value: object, size: tuple[float, float] | None
) -> tuple[float, float, float, float] | None:
    """`[x0, y0, x1, y1]`（0〜1）を IR の bbox にする。ページの大きさがあれば PDF ポイントに。"""
    if not isinstance(value, list) or len(value) != 4:
        return None
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in value):
        return None
    width, height = size if size else (1.0, 1.0)
    return (value[0] * width, value[1] * height, value[2] * width, value[3] * height)


def _blocks(block: dict, size: tuple[float, float] | None) -> list[Block]:
    """middle JSON のブロック 1 つを IR のブロックの列にする（子ブロックは再帰で展開）。"""
    kind = block.get("type")
    content = block.get("content")
    bbox = _bbox(block.get("bbox"), size)
    extra: dict[str, object] = {"type": kind, "bbox_unit": "pt" if size else "ratio"}
    if isinstance(content, list) and any(_is_block(item) for item in content):
        return [child for item in content if _is_block(item) for child in _blocks(item, size)]
    if kind in _DROPPED_TYPES:
        return []
    if kind == "table_body":
        rows = html_table_rows(content) if isinstance(content, str) else []
        return [Block(type="table", rows=rows, bbox=bbox, extra=extra)] if rows else []
    text = content if isinstance(content, str) else _inline_text(content or [])
    if not text.strip():
        return []
    if isinstance(block.get("level"), int):
        extra["level"] = block["level"]  # 見出し（doc_title = 1、paragraph_title = 2〜6）
    return [Block(type="paragraph", text=text, bbox=bbox, extra=extra)]


def _page_sizes(data: dict) -> dict[int, tuple[float, float]]:
    """`page_idx` → (幅, 高さ)（PDF ポイント）。`extensions.docvortex_layout` から取る。"""
    extensions = data.get("extensions")
    layout = extensions.get("docvortex_layout") if isinstance(extensions, dict) else None
    pages = layout.get("pages") if isinstance(layout, dict) else None
    sizes: dict[int, tuple[float, float]] = {}
    for page in pages if isinstance(pages, list) else []:
        if not isinstance(page, dict):
            continue
        index, width, height = page.get("page_idx"), page.get("width_pt"), page.get("height_pt")
        if isinstance(index, int) and all(isinstance(v, int | float) for v in (width, height)):
            sizes[index] = (float(width), float(height))
    return sizes


def normalize(raw_dir: Path) -> Document:
    """middle_json.json を読み、ページ × （段落 + 表）の Document にする。

    ページは `page_idx` + 1。ブロックの無いページも空ページとして残す（番号がずれると
    他アダプタと比較できないため）。ページ数は `pages[]` の最大の `page_idx` と
    `metadata.document.page_count` の大きいほう。
    """
    data = json.loads((raw_dir / "middle_json.json").read_text(encoding="utf-8", errors="replace"))
    sizes = _page_sizes(data)

    by_index: dict[int, list[Block]] = {}
    for page in data.get("pages") or []:
        if not isinstance(page, dict) or not isinstance(page.get("page_idx"), int):
            continue
        index = page["page_idx"]
        blocks = by_index.setdefault(index, [])
        for block in page.get("blocks") or []:
            if isinstance(block, dict):
                blocks.extend(_blocks(block, sizes.get(index)))

    metadata = data.get("metadata")
    document = metadata.get("document") if isinstance(metadata, dict) else None
    page_count = document.get("page_count") if isinstance(document, dict) else None
    count = max(
        [*(index + 1 for index in by_index), page_count if isinstance(page_count, int) else 0]
    )
    pages = [Page(number=index + 1, blocks=by_index.get(index, [])) for index in range(count)]
    return Document(adapter=NAME, pages=pages)
