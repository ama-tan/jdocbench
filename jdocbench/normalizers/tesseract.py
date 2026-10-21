"""Tesseract の raw 出力（ページごとの `p001.txt` …）を IR に正規化する。

**段落 = 空行で区切られたまとまり**。Tesseract の txt 出力は行ごとに改行し、段落の終わりに
空行を入れる（ページ分割で決まった段落）。段落内の行は `\\n` のまま残す
（`Document.lines()` が行に分けるため、CF は段落の粒度に依存しない）。

テキストは Tesseract 自身が単語を連結したもの（txt）を使う。tsv にも単語と bbox があるが、
日本語の単語の間に空白を入れるかどうかの情報（Tesseract 内部の空白判定）を持たないため、
tsv から文字列を組み立てると連結の規則をこちらで決めることになる。特定パーサーに手を
加えない（企画書 §54）ため txt をそのまま使い、tsv は raw として残すだけにする。

表は作らない（Tesseract は表構造を出さない。表の中身は行のテキストとして段落に入る）。
bbox は None。ページ番号はファイル名順の連番で、空のページも blocks を空にして残す
（ページ番号がずれると他アダプタと比較できなくなるため）。末尾のフォームフィード（`\\f`。
Tesseract がページの終わりに付ける）は空白として扱われ消える。raw は読むだけで変更しない
（企画書 §54-2）。
"""

import re
from pathlib import Path

from jdocbench.ir import Block, Document, Page

NAME = "tesseract"

# 空白だけの行を 1 つ以上挟む区切り
_PARAGRAPH_BREAK = re.compile(r"\n[ \t\f　]*\n")


def _blocks(text: str) -> list[Block]:
    """空行区切りの段落ごとに 1 つの paragraph ブロックにする。空白だけの段落は捨てる。"""
    blocks = []
    for chunk in _PARAGRAPH_BREAK.split(text.replace("\r\n", "\n")):
        lines = [line.strip(" \t\f　") for line in chunk.split("\n")]
        paragraph = "\n".join(line for line in lines if line)
        if paragraph:
            blocks.append(Block(type="paragraph", text=paragraph))
    return blocks


def normalize(raw_dir: Path) -> Document:
    """`p*.txt` をファイル名順に読み、ページ × 段落の Document にする。"""
    paths = sorted(raw_dir.glob("p*.txt"))
    pages = [
        Page(
            number=number,
            blocks=_blocks(path.read_text(encoding="utf-8", errors="replace")),
        )
        for number, path in enumerate(paths, start=1)
    ]
    return Document(adapter=NAME, pages=pages)
