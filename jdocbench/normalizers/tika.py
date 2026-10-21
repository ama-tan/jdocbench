"""Tika の raw 出力（content.xhtml）を Common Document IR に正規化する。

ページは `<div class="page">`（文書順）。**段落 = 非空行**（pdftotext と同じ粒度）。Tika の
`<p>` は複数行を含むことがあるので、ブロック要素（`<p>` / `<div>` 等）の境界と `<br>` を改行として
テキストをつなぎ、非空行ごとに 1 つの paragraph ブロックにする。ページ内の注釈
（`<div class="annotation">`。リンクの文字列等）も Tika の出力どおり残す。

Tika の PDF パーサーは表を表として返さないので table ブロックは作らない。bbox も取れない。
ページ div の外（添付ファイルの中身等）は対象にしない。中身が空のページも空の Page として残す。
raw は読むだけで変更しない（企画書 §54-2）。
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from jdocbench.ir import Block, Document, Page

NAME = "tika"

_XHTML = "{http://www.w3.org/1999/xhtml}"
_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "table", "tr"}
_BLOCK_TAGS |= {"td", "th", "pre", "blockquote"}


def _local(tag: object) -> str:
    return tag.removeprefix(_XHTML) if isinstance(tag, str) else ""


def _collect(element: ET.Element, parts: list[str]) -> None:
    """要素のテキストを文書順に parts へ足す。ブロック要素の前後と `<br>` には改行を入れる。"""
    tag = _local(element.tag)
    is_block = tag in _BLOCK_TAGS
    if is_block or tag == "br":
        parts.append("\n")
    if element.text:
        parts.append(element.text)
    for child in element:
        _collect(child, parts)
        if child.tail:
            parts.append(child.tail)
    if is_block:
        parts.append("\n")


def _page_blocks(page_div: ET.Element) -> list[Block]:
    """1 ページ分の div を非空行ごとの paragraph ブロックにする（行は strip のみ）。"""
    parts: list[str] = []
    _collect(page_div, parts)
    lines = "".join(parts).splitlines()
    return [Block(type="paragraph", text=text) for line in lines if (text := line.strip())]


def normalize(raw_dir: Path) -> Document:
    """`raw_dir / "content.xhtml"` を読み、ページ × 行（= 段落）の Document にする。"""
    root = ET.parse(raw_dir / "content.xhtml").getroot()
    body = root.find(f"{_XHTML}body")
    # ページ div は body の直下だけ（添付ファイルの中身などの入れ子のページは対象にしない）
    divs = [] if body is None else body.findall(f"{_XHTML}div")
    page_divs = [div for div in divs if div.get("class") == "page"]
    pages = [
        Page(number=number, blocks=_page_blocks(div))
        for number, div in enumerate(page_divs, start=1)
    ]
    return Document(adapter=NAME, pages=pages)
