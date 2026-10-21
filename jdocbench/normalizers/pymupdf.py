r"""PyMuPDF の raw 出力（blocks.json / tables.json）を Common Document IR に正規化する。

段落の生成元は `text.txt`（`get_text("text")`）ではなく **`blocks.json`**
（`get_text("blocks")`）にする。`get_text("text")` は文書によって 1 つの視覚的な行を
細切れにする（予算のポイントの表の 1 行が `１．` `778,190` `837,350` … と 52 断片になり、
行単位で比べる CF / ROA が壊れる）。blocks には**ブロックの座標**が付くので、
「そのブロックが 1 行分の高さしか無い = 断片の集まり」を判定して行に戻せる。

ブロックは配列順（pymupdf の読み順）に辿り、`block_type == 0`（テキスト）だけを使う。
`block_type == 1`（画像）は捨てる。各ブロックの `text` を `\n` で分割 → strip → 空を除き、
**1 行 = 1 paragraph**（`extra` に `block_no`）にする。
ただしブロックの高さが行数に対して小さすぎる（1 行あたり `_MIN_LINE_HEIGHT` 未満）ときは、
その `\n` は行の区切りではなく断片の区切りなので 1 行に連結する。
段落の bbox は **行の座標**（lines.json。`get_text("dict")` をブロックと同じフラグで取った
各行の `(x0, y0, x1, y1)`）。連結した行は元の行の bbox を合わせたもの。ブロックの bbox を
各行に付けると、表をまたぐブロックの表の外の行まで表と重なって見えるため（#186）。

tables.json は `page.find_tables()` の結果をページごとに並べたもので、pdfplumber と違い
各表が `{"bbox": [x0, y0, x1, y1], "rows": [[...]]}` の形で bbox を持つ。各表を table
ブロックにしてそのページの段落の後ろに置く。`None` セルは `""` にするだけで、セル内容は
加工しない（特定パーサーに有利な正規化をしない）。
blocks 由来の段落は表の中身も普通の行として持つため、表の bbox と重なり表のセルだけで
構成できる段落は `drop_table_content_lines` で除く（表の中身を段落と table の両方で数えないため）。
行の一部だけが表の中にある段落（左右に並んだ表の片方だけを検出して連結された行など）は、
lines.json の各行の 7 要素目（`get_text("rawdict")` の文字ごとの `[c, x0, y0, x1, y1]`）から
語の座標を作り、表の中の語だけを除く（#199）。
lines.json が無い raw（#186 より前）、文字座標の無い raw（#199 より前）や、行がブロックの
`text` と食い違う raw はエラーにする
（位置が分からないまま正規化すると、表の中身を二重に数えた結果を黙って出すため）。
text.txt は raw に残すが正規化では読まない。
raw は読むだけで変更しない（企画書 §54-2）。
"""

import json
from pathlib import Path

from jdocbench.ir import Block, Document, Page
from jdocbench.normalizers._tables import Token, drop_table_content_lines, tokens_from_chars

NAME = "pymupdf"

_MIN_LINE_HEIGHT = 6.0
"""1 行が占める高さ（pt）の下限。これを下回る行送りの本文は実務文書にはまず無い。

ブロックの bbox は中の行の bbox の和なので、n 行が縦に積まれていれば高さは
n × 行の高さ以上になる。高さ ÷ 行数がこの値を下回るブロックは縦に積まれていない
= `\n` が断片の区切りなので、1 行に連結する。
"""


Bbox = tuple[float, float, float, float]


def _union(boxes: list[Bbox]) -> Bbox:
    """bbox の和（すべてを囲む最小の矩形）。"""
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


Char = tuple[str, Bbox]


def _block_lines(
    text: str, height: float, boxes: list[Bbox], chars: list[list[Char]]
) -> list[tuple[str, Bbox, list[Char]]]:
    """ブロックの `text` を (行, 行の bbox, 行の文字座標) の列にする。空行は捨てる。

    `boxes` / `chars` は `text` を `\n` で分割した各行（末尾の改行の後の空文字を除く）の
    bbox と文字座標。
    高さが行数に足りないブロック（= 断片の集まり）は 1 行に連結する。断片の間は
    元の PDF では横方向の隙間なので半角スペースでつなぐ（連続空白は
    `Document.lines()` が圧縮する）。
    """
    lines = [
        (stripped, box, found)
        for line, box, found in zip(text.split("\n"), boxes, chars, strict=False)
        if (stripped := line.strip())
    ]
    if len(lines) > 1 and height / len(lines) < _MIN_LINE_HEIGHT:
        return [
            (
                " ".join(line for line, _, _ in lines),
                _union([box for _, box, _ in lines]),
                [char for _, _, found in lines for char in found],
            )
        ]
    return lines


def _chars(line: list, number: int) -> list[Char]:
    """lines.json の 1 行の 7 要素目（文字ごとの `[c, x0, y0, x1, y1]`）を読む。"""
    if len(line) < 7 or not isinstance(line[6], list):
        raise ValueError(
            f"p{number}: lines.json に文字座標がありません（#199 より前の raw）。"
            "アダプタを再実行してください"
        )
    return [(str(char[0]), (char[1], char[2], char[3], char[4])) for char in line[6]]


def _paragraphs(
    raw_blocks: list, raw_lines: list, number: int
) -> tuple[list[Block], list[list[Token]]]:
    """1 ページ分の `get_text("blocks")` を paragraph ブロックの列にする（読み順のまま）。

    各要素は `[x0, y0, x1, y1, text, block_no, block_type]`。画像ブロック
    （`block_type == 1`）はテキストを持たないので捨てる。`raw_lines` は同じページの
    lines.json（`[x0, y0, x1, y1, text, block_no]`）で、ブロックの `text` の各行と
    1 対 1・同じ文字列で並ぶ。食い違えば誤った位置で判定しないようエラーにする。
    段落ごとの語の座標（Token 列）も返す。
    """
    by_block: dict[object, list] = {}
    for line in raw_lines:
        by_block.setdefault(line[5], []).append(line)

    blocks: list[Block] = []
    tokens: list[list[Token]] = []
    for raw in raw_blocks:
        x0, y0, x1, y1, text, block_no, block_type = raw[:7]
        if block_type != 0:
            continue
        lines = by_block.get(block_no, [])
        expected = text.split("\n")
        if expected and expected[-1] == "":
            expected.pop()
        if [line[4] for line in lines] != expected:
            raise ValueError(
                f"p{number}: lines.json の行がブロック {block_no} の text と一致しません"
            )
        boxes = [(line[0], line[1], line[2], line[3]) for line in lines]
        chars = [_chars(line, number) for line in lines]
        for line, box, found in _block_lines(text, y1 - y0, boxes, chars):
            blocks.append(
                Block(type="paragraph", text=line, bbox=box, extra={"block_no": block_no})
            )
            tokens.append(tokens_from_chars(line, found))
    return blocks, tokens


def _table_block(table: dict) -> Block:
    """`find_tables()` の 1 表を table ブロックにする。空セル（None）は空文字にする。"""
    bbox = table.get("bbox")
    # 4 要素でない bbox は IR の (x0, y0, x1, y1) に載せられないので捨てる
    return Block(
        type="table",
        bbox=tuple(bbox) if isinstance(bbox, list) and len(bbox) == 4 else None,
        rows=[[cell or "" for cell in row] for row in table.get("rows") or []],
    )


def normalize(raw_dir: Path) -> Document:
    """blocks.json と tables.json を読み、ページ × （段落 + 表）の Document にする。

    ページ数は blocks.json と tables.json の多いほうに合わせる（テキストが空のページでも
    表を取りこぼさないため）。
    """
    page_blocks = json.loads((raw_dir / "blocks.json").read_text(encoding="utf-8"))
    page_tables = json.loads((raw_dir / "tables.json").read_text(encoding="utf-8"))
    lines_path = raw_dir / "lines.json"
    if not lines_path.is_file():
        raise FileNotFoundError(
            "lines.json がありません（#186 より前の raw）。アダプタを再実行してください"
        )
    page_lines = json.loads(lines_path.read_text(encoding="utf-8"))

    pages = []
    for number in range(1, max(len(page_blocks), len(page_tables)) + 1):
        raw_blocks = page_blocks[number - 1] if number <= len(page_blocks) else []
        tables = page_tables[number - 1] if number <= len(page_tables) else []
        raw_lines = page_lines[number - 1] if number <= len(page_lines) else []
        paragraphs, tokens = _paragraphs(raw_blocks, raw_lines, number)
        table_blocks = [_table_block(table) for table in tables]
        blocks = drop_table_content_lines(
            paragraphs + table_blocks, [*tokens, *([None] * len(table_blocks))]
        )
        pages.append(Page(number=number, blocks=blocks))
    return Document(adapter=NAME, pages=pages)
