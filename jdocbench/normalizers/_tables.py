"""表の中身を段落にも持つパーサーの二重計上を解消する（正規化の共通処理）。

pdfplumber / pymupdf はテキスト抽出と表抽出が独立しているため、表の中身が
text.txt / blocks 由来の段落と table ブロックの両方に現れる。`Page.lines()` は段落と表の行を
どちらも平坦化するので、そのままでは表の中身を 2 回数えることになり、表を検出しない
パーサー（pdftotext / pdf2json）や表の中身を table にだけ持つパーサー（docling）と
比べて不公平になる。

**除く条件は「同じページの表の bbox と重なり、かつ重なる表のセルだけで構成できる」段落**
（#186）。位置を見ずに文字列だけで除くと、表の外にある同じ文字列（図中のラベルが表の値と
同じ、など）まで落ち、表を検出したパーサーだけがその行を失う（検出しないパーサーは残す）。
位置を条件にすれば、落ちるのは「そのパーサー自身の table ブロックが同じ場所の文字を
既に持っている」行だけになり、表検出の有無で表の外の文字の扱いが変わらない。

bbox の無い段落・表は判定できないので何も落とさない。表を検出しないパーサーは table
ブロックを持たないので、もともと二重計上が起きない。Ground Truth は作る時点で表の中身を
段落に入れず、表の外の文字（図中ラベルを含む）は残す（`groundtruth/README.md`）。

**行の一部だけが表の中にある段落**（左右に並んだ本文と表の同じ高さの文字が 1 行に連結された
行。例: `100人に対する男子の数（人口性 男女計 12286 12340 -54`）は、段落のトークン（空白区切り）
ごとの座標が渡されたときだけ、表の中のトークンを除いて残りを段落にする（#199）。トークンの
中心が表の bbox の中にあれば「表の中」とし、表の中のトークンを表ごとに並べたものが、その表の
セルだけで構成できるときに限り除く。構成できなければその表の位置のトークンは除かない。トークンの座標は
パーサーを問わず `(文字列, bbox)` の列で渡す（文字座標から作るなら `tokens_from_chars`）。
"""

import re
from dataclasses import replace

from jdocbench.ir import Block, collapse_whitespace

Bbox = tuple[float, float, float, float]
Token = tuple[str, Bbox]
"""段落の空白区切りの 1 語と、その bbox（構成する文字の bbox の和）。"""

_SPACE = re.compile(r"[\s\u3000]+")


def _key(text: str) -> str:
    """比較用のキー。連続空白を潰したうえで空白を全て取り除く。

    表のセルは折り返しや字送りで段落側と空白の入り方が違うため、空白を無視して比べる
    （`collapse_whitespace` の後は空白が半角スペースだけになるのでそれを除けばよい）。
    """
    return collapse_whitespace(text).replace(" ", "")


def _cell_keys(table: Block) -> set[str]:
    """table ブロックの全セルを比較用のキーの集合にする（空セルは除く）。"""
    keys: set[str] = set()
    for row in table.rows or []:
        for cell in row:
            if not isinstance(cell, str):
                continue
            for part in cell.split("\n"):
                if key := _key(part):
                    keys.add(key)
    return keys


def _overlaps(table_bbox: tuple[float, ...], bbox: tuple[float, ...]) -> bool:
    """`bbox` と `table_bbox` が面積を持って重なるか。座標系は同じ前提。

    中心が表の中にあるかでは判定しない。左右に並んだ 2 つの表の同じ高さの行は、
    段落側では 1 行に連結され、その中心が表と表の間の隙間に落ちるため。
    """
    width = min(table_bbox[2], bbox[2]) - max(table_bbox[0], bbox[0])
    height = min(table_bbox[3], bbox[3]) - max(table_bbox[1], bbox[1])
    return width > 0 and height > 0


def _is_made_of_cells(key: str, cells: set[str], max_cell_len: int) -> bool:
    """`key` がセル文字列の連結（1 個以上、順不同・重複可）だけで構成できるか。

    表の 1 行が段落側では 1 行に連結されて現れる（列の区切りが空白になる）ため、
    単一セルとの一致だけでなく複数セルの連結も判定する。DP: `can[i]` = `key[:i]` を
    構成できる。セルの最大長で部分文字列の長さを打ち切る。
    """
    can = [True] + [False] * len(key)
    for end in range(1, len(key) + 1):
        for start in range(max(0, end - max_cell_len), end):
            if can[start] and key[start:end] in cells:
                can[end] = True
                break
    return can[len(key)]


def tokens_from_chars(text: str, chars: list[tuple[str, Bbox]]) -> list[Token]:
    """段落 `text` の空白区切りトークンに、文字座標から bbox を付ける。

    `chars` は `text` の出現順に並ぶ `(文字列, bbox)`。空白だけの文字は飛ばす。文字列は
    複数文字でもよい（pdfplumber の `(cid:NNNN)`）。`text` の空白以外と `chars` の連結が
    食い違えば ValueError にする（誤った位置で判定しないため）。
    """
    pending = [(t, box) for t, box in chars if _SPACE.sub("", t)]
    tokens: list[Token] = []
    index = 0
    for word in _SPACE.split(text.strip()):
        if not word:
            continue
        boxes: list[Bbox] = []
        rest = word
        while rest:
            if index >= len(pending):
                raise ValueError(f"文字座標が段落の文字より少なくなっています: {text!r}")
            piece = _SPACE.sub("", pending[index][0])
            if not rest.startswith(piece):
                raise ValueError(f"文字座標が段落の文字と一致しません: {text!r}")
            boxes.append(pending[index][1])
            rest = rest[len(piece) :]
            index += 1
        tokens.append((word, _union(boxes)))
    if index != len(pending):
        raise ValueError(f"文字座標が段落の文字より多くなっています: {text!r}")
    return tokens


def _union(boxes: list[Bbox]) -> Bbox:
    """bbox の和（すべてを囲む最小の矩形）。"""
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _contains_center(table_bbox: tuple[float, ...], bbox: Bbox) -> bool:
    """`bbox` の中心が `table_bbox` の中にあるか。

    トークンは 1 語なので 2 つの表をまたがない（段落全体の判定で重なりを使う理由が無い）。
    表の端から少しはみ出した数値（誤検出表の切れたセル）を表の中と見なさないため中心で見る。
    """
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return table_bbox[0] <= cx <= table_bbox[2] and table_bbox[1] <= cy <= table_bbox[3]


def _made_of(key: str, cells: set[str]) -> bool:
    return bool(key and cells) and _is_made_of_cells(key, cells, max(map(len, cells)))


def _strip_table_tokens(
    block: Block, tokens: list[Token], tables: list[tuple[tuple[float, ...], set[str]]]
) -> Block | None:
    """段落から表の中の語を除く。全部除けたら None、何も除かなければ `block` のまま。

    表の中の語は、中心を含む表ごとにまとめ、そのまとまりの語の並びが**その表**のセルだけで
    構成できるときにまとまりごと除く。1 行が 2 つの表にまたがるとき、一方の表の位置にある語を
    もう一方の表のセルで除かないため（その位置の表が持っていない文字を落とさない）。
    """
    groups: dict[tuple[int, ...], list[str]] = {}
    where: list[tuple[int, ...]] = []
    for text, bbox in tokens:
        key = tuple(
            i for i, (table_bbox, _) in enumerate(tables) if _contains_center(table_bbox, bbox)
        )
        where.append(key)
        if key:
            groups.setdefault(key, []).append(text)
    removed = {
        key
        for key, words in groups.items()
        if _made_of(_key("".join(words)), set().union(*(tables[i][1] for i in key)))
    }
    if not removed:
        return block
    kept = [token for token, key in zip(tokens, where, strict=True) if key not in removed]
    if not kept:
        return None
    return replace(
        block,
        text=" ".join(text for text, _ in kept),
        bbox=_union([box for _, box in kept]),
    )


def drop_table_content_lines(
    blocks: list[Block], tokens: list[list[Token] | None] | None = None
) -> list[Block]:
    """そのパーサー自身の表が既に持つ文字を paragraph から除く（1 ページ分）。

    `tokens[i]` は `blocks[i]`（paragraph）のトークン列。渡された段落は、表の中のトークン
    だけを除く（#199。全部が表の中なら段落ごと落ちる）。トークンの無い段落（`tokens` 自体が
    None、または `tokens[i]` が None）は、段落の bbox が表の bbox と重なり、重なる表の
    セルだけで構成できるときに段落ごと除く（#186）。重なる表が複数あれば、それらのセルを
    合わせて判定する（左右に並んだ表の行が 1 行に連結されるため）。bbox の無い段落・表は
    判定しない（落とさない）。table ブロックと、条件に当たらない paragraph は順序を保つ。
    """
    if tokens is not None and len(tokens) != len(blocks):
        raise ValueError("tokens は blocks と同じ長さにしてください")
    tables = [
        (block.bbox, cells)
        for block in blocks
        if block.type == "table" and block.bbox and (cells := _cell_keys(block))
    ]
    if not tables:
        return blocks

    kept: list[Block] = []
    for index, block in enumerate(blocks):
        found = tokens[index] if tokens is not None else None
        if block.type == "paragraph" and block.text and found:
            stripped = _strip_table_tokens(block, found, tables)
            if stripped is not None:
                kept.append(stripped)
            continue
        if block.type == "paragraph" and block.text and block.bbox:
            cells = set().union(*(found for bbox, found in tables if _overlaps(bbox, block.bbox)))
            if _made_of(_key(block.text), cells):
                continue
        kept.append(block)
    return kept
