r"""pdfplumber の raw 出力（text.txt / tables.json）を Common Document IR に正規化する。

text.txt はページ区切りが `\f` で pdftotext と同じ形式なので、段落分割（**段落 = 非空行**）は
`normalizers.pdftotext._lines` をそのまま使う（同じ規則を 2 か所に書かない）。
tables.json は `extract_tables()` の結果をページごとに並べたもので、各表を
table ブロックにしてそのページの段落の後ろに置く。`None` セルは `""` にするだけで、
セル内容は加工しない（特定パーサーに有利な正規化をしない）。
text.txt は表の中身も普通の行として持つため、表の bbox の中にあり表のセルだけで構成できる
段落は `drop_table_content_lines` で除く（表の中身を段落と table の両方で数えないため。#186）。
そのための座標として、段落の bbox は lines.json（`extract_text_lines()`。text.txt と同じ行を
同じ順に持つ）、表の bbox は table_bboxes.json（tables.json と同じ並び）から取る。
行の一部だけが表の中にある段落（本文と表の同じ高さの文字が 1 行に連結された行）は、
lines.json の各行の `chars`（文字ごとの座標）から語の座標を作り、表の中の語だけを除く（#199）。
どちらかが無い raw（#186 より前の raw）、`chars` の無い raw（#199 より前の raw）や、行・表の数・
文字が食い違う raw はエラーにする（位置が分からないまま正規化すると、表の中身を段落と table の
両方で数えた結果を黙って出すため）。
words.json は今回使わない。
raw は読むだけで変更しない（企画書 §54-2）。
"""

import json
from pathlib import Path

from jdocbench.ir import Block, Document, Page
from jdocbench.normalizers._tables import Token, drop_table_content_lines, tokens_from_chars
from jdocbench.normalizers.pdftotext import _lines

NAME = "pdfplumber"


def _page_texts(raw_dir: Path) -> list[str]:
    """text.txt をページごとの文字列にする（pdftotext と同じ規則）。"""
    text = (raw_dir / "text.txt").read_text(encoding="utf-8", errors="replace")
    if text.endswith("\f"):
        text = text[:-1]
    return text.split("\f") if text else []


def _read_positions(path: Path) -> list:
    """raw の座標ファイル（lines.json / table_bboxes.json）を読む。無ければ理由つきでエラー。"""
    if not path.is_file():
        raise FileNotFoundError(
            f"{path.name} がありません（#186 より前の raw）。アダプタを再実行してください"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _bbox(value: object) -> tuple[float, float, float, float] | None:
    """4 要素の数値列なら bbox にする。それ以外は None。"""
    if (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(v, int | float) and not isinstance(v, bool) for v in value)
    ):
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    return None


def _with_line_bboxes(
    blocks: list[Block], lines: list[dict], number: int
) -> tuple[list[Block], list[list[Token]]]:
    """text.txt 由来の段落に lines.json の座標を付け、段落ごとの語の座標（Token 列）も返す。

    lines.json の行（strip して空でないもの）は段落と 1 対 1・同じ文字列で並ぶ（同じ
    テキストマップから作られる）。食い違えば誤った位置で判定しないようエラーにする。
    """
    located = [line for line in lines if str(line.get("text", "")).strip()]
    texts = [str(line["text"]).strip() for line in located]
    if texts != [block.text for block in blocks]:
        raise ValueError(f"p{number}: lines.json の行が text.txt の行と一致しません")
    tokens = []
    for line, block in zip(located, blocks, strict=True):
        block.bbox = _bbox([line.get(key) for key in ("x0", "top", "x1", "bottom")])
        if not isinstance(line.get("chars"), list):
            raise ValueError(
                f"p{number}: lines.json に chars がありません（#199 より前の raw）。"
                "アダプタを再実行してください"
            )
        chars = []
        for char in line["chars"]:
            box = _bbox(char[1:5]) if isinstance(char, list) else None
            if box is None:
                raise ValueError(f"p{number}: lines.json の chars の座標が不正です")
            chars.append((str(char[0]), box))
        tokens.append(tokens_from_chars(block.text or "", chars))
    return blocks, tokens


def _table_block(table: list[list[str | None]], bbox: object = None) -> Block:
    """`extract_tables()` の 1 表を table ブロックにする。空セル（None）は空文字にする。"""
    return Block(
        type="table", bbox=_bbox(bbox), rows=[[cell or "" for cell in row] for row in table]
    )


def normalize(raw_dir: Path) -> Document:
    """text.txt と tables.json を読み、ページ × （段落 + 表）の Document にする。

    ページ数は text.txt と tables.json の多いほうに合わせる（テキストが空のページでも
    表を取りこぼさないため）。
    """
    page_texts = _page_texts(raw_dir)
    page_tables = json.loads((raw_dir / "tables.json").read_text(encoding="utf-8"))
    page_lines = _read_positions(raw_dir / "lines.json")
    page_bboxes = _read_positions(raw_dir / "table_bboxes.json")

    pages = []
    for number in range(1, max(len(page_texts), len(page_tables)) + 1):
        text = page_texts[number - 1] if number <= len(page_texts) else ""
        tables = page_tables[number - 1] if number <= len(page_tables) else []
        lines = page_lines[number - 1] if number <= len(page_lines) else []
        bboxes = page_bboxes[number - 1] if number <= len(page_bboxes) else []
        if len(bboxes) != len(tables):
            raise ValueError(f"p{number}: table_bboxes.json の表の数が tables.json と一致しません")
        paragraphs, tokens = _with_line_bboxes(_lines(text), lines, number)
        table_blocks = [
            _table_block(table, bbox) for table, bbox in zip(tables, bboxes, strict=True)
        ]
        blocks = drop_table_content_lines(
            paragraphs + table_blocks, [*tokens, *([None] * len(table_blocks))]
        )
        pages.append(Page(number=number, blocks=blocks))
    return Document(adapter=NAME, pages=pages)
