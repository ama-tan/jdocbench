"""Common Document IR の最小スキーマ（企画書 §12）。

Level 0 / 1 の出力を載せるのに必要な要素だけを持つ。企画書 §12 の概念モデルは
Section / Figure / Relation 等を含むが、今それらを使う処理が無いため作らない
（必要になった時点で追加する）。
"""

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Literal

BlockType = Literal["paragraph", "table"]

_WHITESPACE = re.compile(r"[\s\u3000]+")


def collapse_whitespace(text: str) -> str:
    """全角スペースを含む連続する空白類を 1 つの半角スペースにし、前後を strip する。

    NFKC 正規化はしない（全角/半角の差は評価対象。企画書 §16 / §54-1）。
    """
    return _WHITESPACE.sub(" ", text).strip()


def _table_row_line(row: list[str]) -> str:
    """表の 1 行を 1 つの行文字列にする。空セルを落とし、残りを半角スペースで連結する。

    連続空白を圧縮すると空になるセルだけを除く。セル内の改行は `collapse_whitespace` で
    空白になる。**同じ文字列のセルが隣り合っても（横・縦とも）落とさない**。結合セルは
    IR の規約で先頭セル（左上）だけが文字列を持ち、範囲内の他の位置は空文字なので
    （`Block.rows`）、同値の独立セル（`○ | ○`）と区別するための畳み込みは要らない。
    """
    cells = (collapse_whitespace(cell) if isinstance(cell, str) else "" for cell in row)
    return " ".join(cell for cell in cells if cell)


@dataclass
class Block:
    """ページ内の 1 要素。段落か表のどちらか。"""

    type: BlockType
    text: str | None = None
    """paragraph の本文。"""

    bbox: tuple[float, float, float, float] | None = None
    """(x0, y0, x1, y1)。座標を取れないパーサーは None。"""

    rows: list[list[str]] | None = None
    """table の行 × 列（セル文字列）。

    結合セルは範囲の先頭（左上）の位置だけに文字列を入れ、範囲内の他の位置は空文字にする
    （pdfplumber / PyMuPDF / Camelot の出力と同じ形。span を返すパーサーは正規化でこの形に
    揃え、Ground Truth も同じ規約で書く）。隣り合う同じ文字列は独立したセルとして扱う。
    """

    extra: dict[str, object] = field(default_factory=dict)
    """Format / パーサー固有情報の逃がし先（企画書 §54-4）。"""


@dataclass
class Page:
    """1 ページ。番号は 1 始まり。"""

    number: int
    blocks: list[Block] = field(default_factory=list)

    def lines(self) -> list[str]:
        """ページの全テキストを読み順（ブロックの出現順）で平坦化した行の列。

        - paragraph: `text` を `\n` で分割 → `collapse_whitespace` → 空行を捨てる
        - table: 各行を `_table_row_line` で 1 行にする（空セルを除いて半角スペースで
          連結）。空行は捨てる

        設計理由: 表の中身をどのブロックに入れるかはパーサーの流儀であり、文書の内容
        再現を測る CF はそれに依存してはならない。結合セルの文字列は先頭セルにだけ
        入っている（`Block.rows`）ので、テキスト系パーサー（ラベルは 1 回だけ印字）と
        同じ形になる。
        """
        lines: list[str] = []
        for block in self.blocks:
            if block.type == "paragraph" and block.text:
                lines.extend(
                    collapsed
                    for line in block.text.split("\n")
                    if (collapsed := collapse_whitespace(line))
                )
            elif block.type == "table" and block.rows:
                lines.extend(line for row in block.rows if (line := _table_row_line(row)))
        return lines


@dataclass
class Document:
    """1 つのアダプタ出力を正規化した文書。"""

    adapter: str
    pages: list[Page] = field(default_factory=list)

    def to_dict(self) -> dict:
        """入れ子の dataclass ごと dict にする。"""
        return asdict(self)

    def to_json(self) -> str:
        """normalized/<adapter>.json に書く JSON 文字列（末尾に改行を付ける）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"

    def lines(self) -> list[str]:
        """全ページの `Page.lines()` を連結したもの（段落と表の行を読み順に平坦化した列）。

        段落が「1 段落 = 1 行」でも「1 段落に改行を含む」でも、表の中身が paragraph でも
        table でも同じ列になるため、`text()` / `char_count()` がアダプタごとの段落粒度・
        表の扱いに依存しなくなる（企画書 §18 の CF / ROA を同じ単位で測るための土台）。
        """
        return [line for page in self.pages for line in page.lines()]

    def char_count(self) -> int:
        """`lines()` の総文字数（段落粒度・表の扱いに依存しない）。

        `pdftotext -layout` の桁揃え空白でパーサー間の比較が壊れないようにする
        （CF（DSQS-JP）の前処理と揃える）。
        """
        return sum(len(line) for line in self.lines())

    def text(self) -> str:
        """`lines()` を `\n` で連結したもの（CF（DSQS-JP）の比較対象）。"""
        return "\n".join(self.lines())

    def table_count(self) -> int:
        """table ブロックの数。"""
        return sum(1 for page in self.pages for block in page.blocks if block.type == "table")
