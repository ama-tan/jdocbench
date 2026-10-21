r"""pdftotext の raw 出力（text.txt）を Common Document IR に正規化する。

**段落 = 非空行**。1 行を 1 つの paragraph ブロックにする。空行区切りで段落にまとめると
アダプタごとに粒度が変わるため、粒度をアダプタ間で揃え ROA（読み順）の単位にする。

`pdftotext` は改ページを `\f` で表し、最終ページの末尾にも `\f` を付ける。そのため
末尾の `\f` 1 つだけを落としてから分割する。**中身が空のページもページとして残し、
blocks を空にする**（ページ番号がずれると他アダプタと比較できなくなるため）。
raw は読むだけで変更しない（企画書 §54-2）。
"""

from pathlib import Path

from jdocbench.ir import Block, Document, Page

NAME = "pdftotext"


def _lines(page_text: str) -> list[Block]:
    """非空行ごとに 1 つの paragraph ブロックにする。

    text は行を strip しただけのもの（連続空白の圧縮は `Document.lines()` が行う。
    `pdftotext -layout` の桁揃え空白を raw に近い形で normalized に残すため）。
    """
    blocks = []
    for line in page_text.split("\n"):
        text = line.strip()
        if text:
            blocks.append(Block(type="paragraph", text=text))
    return blocks


def normalize(raw_dir: Path) -> Document:
    """`raw_dir / "text.txt"` を読み、ページ × 行（= 段落）の Document にする。

    bbox は pdftotext から取れないため常に None。
    """
    text = (raw_dir / "text.txt").read_text(encoding="utf-8", errors="replace")
    if text.endswith("\f"):
        text = text[:-1]
    page_texts = text.split("\f") if text else []
    pages = [
        Page(number=number, blocks=_lines(page_text))
        for number, page_text in enumerate(page_texts, start=1)
    ]
    return Document(adapter=NAME, pages=pages)
