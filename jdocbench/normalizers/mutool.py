"""mutool の raw 出力（stext XML）を Common Document IR に正規化する。

stext は `<page>` → `<block>` → `<line>` → `<font>` → `<char c="…">` の入れ子で、ブロック・行は
MuPDF の読み順に並ぶ。**1 行 = 1 paragraph**（他の Level 0 と粒度を揃える）とし、行の文字を
`<char>` の並び順のまま連結する。bbox は `<line bbox="x0 y0 x1 y1">`（pt、左上原点）。
行からブロック（段落）への統合はしない（粒度をアダプタ間で揃え ROA の単位にするため）。
画像ブロックなど `<line>` を持たない要素は文字が無いので捨てる。表は作らない。
空白だけの行は捨てる。中身が空のページもページとして残し、blocks を空にする。
raw は読むだけで変更しない（企画書 §54-2）。
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from jdocbench.adapters.mutool import STEXT_FILE
from jdocbench.ir import Block, Document, Page

NAME = "mutool"

_INVALID_CHAR_REF = re.compile(
    r"&#(?:x0*(?:[0-8bcef]|1[0-9a-f])|0*(?:[0-8]|1[124-9]|2[0-9]|3[01]));", re.I
)
"""XML 1.0 で許されない制御文字の文字参照（タブ・改行・復帰以外の U+0000〜U+001F）。

MuPDF は PDF 中の制御文字をそのまま `&#x1;` のように書き出すことがあり、ElementTree は
それを解析エラーにする。文字としての意味は無いので、解析前にメモリ上でだけ取り除く。
"""


def _bbox(value: str | None) -> tuple[float, float, float, float] | None:
    """`"x0 y0 x1 y1"` を tuple にする。4 つの数値でなければ None。"""
    parts = (value or "").split()
    if len(parts) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(part) for part in parts)
    except ValueError:
        return None
    return (x0, y0, x1, y1)


def _page_blocks(page: ET.Element) -> list[Block]:
    """1 ページ分の `<line>` を出現順に paragraph ブロックにする。"""
    blocks: list[Block] = []
    for line in page.iter("line"):
        text = "".join(char.get("c", "") for char in line.iter("char")).strip()
        if text:
            blocks.append(Block(type="paragraph", text=text, bbox=_bbox(line.get("bbox"))))
    return blocks


def normalize(raw_dir: Path) -> Document:
    """`raw_dir / STEXT_FILE` を読み、ページ × 行（= 段落）の Document にする。"""
    xml = (raw_dir / STEXT_FILE).read_text(encoding="utf-8", errors="replace")
    root = ET.fromstring(_INVALID_CHAR_REF.sub("", xml))
    pages = [
        Page(number=number, blocks=_page_blocks(page))
        for number, page in enumerate(root.iter("page"), start=1)
    ]
    return Document(adapter=NAME, pages=pages)
