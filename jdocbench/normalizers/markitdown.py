r"""MarkItDown の raw 出力（output.md）を Common Document IR に正規化する。

- **ページ**: `\f` があればそれで分割する（文書全体を pdfminer で取った場合。末尾の `\f` 1 つは
  pdftotext と同じく落とす）。`\f` が無い場合（pdfplumber で 1 ページでもフォーム・表を検出した
  場合）、MarkItDown の出力にはページ境界が残っていないので**文書全体を 1 ページ**にする。
  ページ境界を他パーサーから補うことはしない（特定パーサーに有利な正規化をしない）
- **表**: `|` で始まる連続行を 1 つの table ブロックにする。見出し行の直後の区切り行
  （`| --- |`）は捨て、各行の先頭・末尾の `|` を外して `|` で分割し、セルを strip する
  （MarkItDown は列幅を揃えるため空白で埋める）。セル内容はそれ以外加工しない
- **段落**: それ以外の非空行を 1 行 = 1 paragraph にする（pdftotext と同じ粒度）。見出し行
  （`# ` 〜 `###### `）は Markdown の記号だけを外す

MarkItDown の表は表の中身だけを持ち、同じ中身を段落にも出さないので、
`drop_table_content_lines`（二重計上の解消）は使わない。bbox は取れないため常に None。
raw は読むだけで変更しない（企画書 §54-2）。
"""

import re
from pathlib import Path

from jdocbench.ir import Block, Document, Page

NAME = "markitdown"

_HEADING = re.compile(r"^#{1,6}\s+")
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


def _cells(line: str) -> list[str]:
    """Markdown の表の 1 行をセルの列にする（先頭・末尾の `|` を外して分割し strip）。"""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.strip().replace("\\|", "|") for cell in _CELL_SPLIT.split(body)]


def _is_separator(cells: list[str]) -> bool:
    """区切り行（`| --- | :---: |`）か。

    MarkItDown は区切りの `-` を列幅ぶん並べるので、全セルが空の列では区切り行のその位置も
    空になる（`| --- |  | --- |`）。空のセルを許し、`-` のセルが 1 つ以上あれば区切り行とみなす。
    """
    return any(cell for cell in cells) and all(
        not cell or _SEPARATOR_CELL.match(cell) for cell in cells
    )


def _table_rows(lines: list[str]) -> list[list[str]]:
    """連続する `|` 行を行 × 列にする。

    区切り行は見出し行の直後（2 行目）にだけ出るので、その位置のものだけを捨てる
    （データ行の `-`（該当なし）だけの行を区切りと誤らないため）。
    """
    rows = [_cells(line) for line in lines]
    if len(rows) >= 2 and _is_separator(rows[1]):
        del rows[1]
    return rows


def _blocks(page_text: str) -> list[Block]:
    """1 ページ分の Markdown を paragraph / table ブロックの列にする（出現順）。"""
    blocks: list[Block] = []
    table_lines: list[str] = []

    def flush_table() -> None:
        if table_lines:
            blocks.append(Block(type="table", rows=_table_rows(table_lines)))
            table_lines.clear()

    for line in page_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|"):
            table_lines.append(stripped)
            continue
        flush_table()
        text = _HEADING.sub("", stripped)
        if text:
            blocks.append(Block(type="paragraph", text=text))
    flush_table()
    return blocks


def normalize(raw_dir: Path) -> Document:
    """`raw_dir / "output.md"` を読み、ページ × （段落 + 表）の Document にする。"""
    text = (raw_dir / "output.md").read_text(encoding="utf-8", errors="replace")
    if text.endswith("\f"):
        text = text[:-1]
    page_texts = text.split("\f") if text.strip() else []
    pages = [
        Page(number=number, blocks=_blocks(page_text))
        for number, page_text in enumerate(page_texts, start=1)
    ]
    return Document(adapter=NAME, pages=pages)
