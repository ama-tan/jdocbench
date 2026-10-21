r"""pypdfium2 の raw 出力（pages.json）を Common Document IR に正規化する。

pdftotext と同じく **段落 = 非空行**。PDFium の行区切り（`\r\n`）で分割し、strip して空行を
除いた 1 行を 1 つの paragraph ブロックにする。表・bbox は出さない（テキストしか取らないため）。
中身が空のページ（スキャン等）もページとして残し、blocks を空にする（ページ番号を揃えるため）。
raw は読むだけで変更しない（企画書 §54-2）。
"""

import json
from pathlib import Path

from jdocbench.ir import Block, Document, Page

NAME = "pypdfium2"


def _lines(page_text: str) -> list[Block]:
    """非空行ごとに 1 つの paragraph ブロックにする。"""
    return [
        Block(type="paragraph", text=text)
        for line in page_text.splitlines()
        if (text := line.strip())
    ]


def normalize(raw_dir: Path) -> Document:
    """`raw_dir / "pages.json"` を読み、ページ × 行（= 段落）の Document にする。"""
    data = json.loads((raw_dir / "pages.json").read_text(encoding="utf-8"))
    pages = [
        Page(number=number, blocks=_lines(text))
        for number, text in enumerate(data.get("pages") or [], start=1)
    ]
    return Document(adapter=NAME, pages=pages)
