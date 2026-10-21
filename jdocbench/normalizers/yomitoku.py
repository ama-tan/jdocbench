"""YomiToku の raw 出力（1 ページ 1 ファイルの `*_p<N>.json`）を IR に正規化する。

各ファイルは `{"paragraphs": [...], "tables": [...], "figures": [...], "words": [...]}`。

- **読み順**: `paragraphs` / `tables` / `figures` の各要素がページ内で共通の連番 `order` を持つ
  ので、`order` で並べる（YomiToku の `--reading_order auto` の結果）
- **段落**: `contents`（行ごとに `\\n` を含む OCR 文字列）を 1 paragraph にする。`role` が
  `page_header` / `page_footer` / `section_headings` / `caption` などでも段落に残す（GT は
  ノンブル・柱を段落に残す規約。`groundtruth/README.md`）。`role` と `direction`（縦書き）は
  `extra` に残す
- **図**: 図に 7 割以上含まれる段落は YomiToku が `paragraphs` から除いて `figures[].paragraphs`
  に移す（`--figure_letter` に関係なく JSON には入る）。図の `order` の位置に、図内の `order`
  （図ごとに 0 始まりの別系列）の順で段落として展開する。GT は図中ラベルを段落に残す規約で、
  ppstructure / docling も図中の文字を残すため、ここで捨てると YomiToku だけ不利になる
- **表**: `cells` は結合範囲の先頭（`row` / `col` は 1 始まり、`row_span` / `col_span`）だけを
  持つので、`n_row × n_col` の空文字のグリッドの先頭位置に `contents` を置く。これで IR の規約
  （結合セルは範囲の先頭にだけ文字列。`Block.rows`）になる。`rows` / `cols` / `spans` は使わない

**座標**: すべて DPI 200 で描画したページ画像のピクセル（左上原点・y 下向き）。アダプタが
`--dpi 200` を明示して呼ぶので、× 72 / 200 で PDF ポイントに直す（pdfplumber と同じ左上原点）。

**表の中身の二重計上**（#186 / #199）: 表の領域の文字は通常セルにだけ入るが、セルに入らずに
表の領域内の段落として出る文字もある（1920 年の実物スキャンで数字 226 個。セルの 7 割が空）。
規則は他のパーサーと共通の `drop_table_content_lines` に任せ、段落の語の座標を渡す。
`words`（行単位の認識結果。段落とセルの文字の元）は本文には使わず、語の座標を作るのにだけ使う:
段落の各行を、段落の box に中心が入る同じ文字列の word に対応付け、その行の box を空白以外の
文字数で等分して文字の座標とする（YomiToku は文字ごとの座標を出さないので近似。横書きは x、
縦書きは y 方向に分ける）。1 行でも対応が取れない段落は語の座標を渡さず、段落単位の規則
（#186）で判定する。表が持っていない文字（空セルの位置の数字など）は落とさない。

raw は読むだけで変更しない（企画書 §54-2）。
"""

import json
from pathlib import Path

from jdocbench.adapters.yomitoku import DPI, page_files
from jdocbench.ir import Block, Document, Page
from jdocbench.normalizers._tables import Bbox, Token, drop_table_content_lines, tokens_from_chars

NAME = "yomitoku"

# DPI 200 のピクセル → PDF ポイント（1 pt = 1/72 inch）
_SCALE = 72 / DPI

Entry = tuple[Block, Bbox | None]
"""ブロックと、その元の box（ピクセル。語の対応付けに使う。表は None）。"""

# order を持たない要素を並べる位置（ページの末尾）
_NO_ORDER = 10**9


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _pixel_box(value: object) -> Bbox | None:
    """`box` の `[x1, y1, x2, y2]`（ピクセル）。形が違えば None。"""
    if not isinstance(value, list) or len(value) != 4 or not all(map(_is_number, value)):
        return None
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def _to_pt(box: Bbox | None) -> Bbox | None:
    return (
        None
        if box is None
        else (box[0] * _SCALE, box[1] * _SCALE, box[2] * _SCALE, box[3] * _SCALE)
    )


def _points_box(points: object) -> Bbox | None:
    """words の 4 点 `[[x, y], ...]`（ピクセル）を囲む矩形。形が違えば None。"""
    if not isinstance(points, list) or not points:
        return None
    if not all(isinstance(p, list) and len(p) == 2 and all(map(_is_number, p)) for p in points):
        return None
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _order(item: dict) -> int:
    order = item.get("order")
    return order if isinstance(order, int) and not isinstance(order, bool) else _NO_ORDER


def _paragraph(item: dict, *, figure: bool = False) -> Block | None:
    """段落 1 つを paragraph にする。文字列が空なら None。"""
    contents = item.get("contents")
    if not isinstance(contents, str) or not contents.strip():
        return None
    extra: dict[str, object] = {
        "role": item.get("role"),
        "direction": item.get("direction"),
        "order": item.get("order"),
        "bbox_unit": "pt",
    }
    if figure:
        extra["figure"] = True
    return Block(
        type="paragraph", text=contents, bbox=_to_pt(_pixel_box(item.get("box"))), extra=extra
    )


def _table(item: dict) -> Block | None:
    """表 1 つを table にする。行数・列数が正の整数でなければ None。"""
    n_row, n_col = item.get("n_row"), item.get("n_col")
    if not (isinstance(n_row, int) and isinstance(n_col, int) and n_row > 0 and n_col > 0):
        return None
    rows = [[""] * n_col for _ in range(n_row)]
    for cell in item.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        row, col, contents = cell.get("row"), cell.get("col"), cell.get("contents")
        if not (isinstance(row, int) and isinstance(col, int) and isinstance(contents, str)):
            continue
        if 1 <= row <= n_row and 1 <= col <= n_col and not rows[row - 1][col - 1]:
            rows[row - 1][col - 1] = contents.strip()
    extra = {"order": item.get("order"), "n_row": n_row, "n_col": n_col, "bbox_unit": "pt"}
    return Block(type="table", rows=rows, bbox=_to_pt(_pixel_box(item.get("box"))), extra=extra)


def _elements(data: dict) -> list[Entry]:
    """ページの段落・表・図中段落を `order` の順に並べ、元の box（ピクセル）を添える。"""
    keyed: list[tuple[int, int, list[Entry]]] = []
    for item in data.get("paragraphs") or []:
        if isinstance(item, dict) and (block := _paragraph(item)):
            keyed.append((_order(item), len(keyed), [(block, _pixel_box(item.get("box")))]))
    for item in data.get("tables") or []:
        if isinstance(item, dict) and (block := _table(item)):
            keyed.append((_order(item), len(keyed), [(block, None)]))
    for item in data.get("figures") or []:
        if not isinstance(item, dict):
            continue
        inner = [p for p in item.get("paragraphs") or [] if isinstance(p, dict)]
        inner.sort(key=_order)
        blocks = [
            (block, _pixel_box(p.get("box")))
            for p in inner
            if (block := _paragraph(p, figure=True))
        ]
        if blocks:
            keyed.append((_order(item), len(keyed), blocks))
    keyed.sort(key=lambda entry: (entry[0], entry[1]))
    return [block for _, _, blocks in keyed for block in blocks]


def _line_chars(line: str, box: Bbox, vertical: bool) -> list[tuple[str, Bbox]]:
    """行の box（pt）を空白以外の文字数で等分し、各文字に座標を付ける。"""
    chars = [c for c in line if not c.isspace()]
    if not chars:
        return []
    x0, y0, x1, y1 = box
    step = ((y1 - y0) if vertical else (x1 - x0)) / len(chars)
    located = []
    for i, char in enumerate(chars):
        if vertical:
            located.append((char, (x0, y0 + step * i, x1, y0 + step * (i + 1))))
        else:
            located.append((char, (x0 + step * i, y0, x0 + step * (i + 1), y1)))
    return located


def _words(data: dict) -> list[tuple[str, Bbox, bool]]:
    """words を (文字列, box（ピクセル）, 縦書きか) の列にする。形の悪いものは捨てる。"""
    words = []
    for word in data.get("words") or []:
        if not isinstance(word, dict) or not isinstance(word.get("content"), str):
            continue
        box = _points_box(word.get("points"))
        if box is not None:
            words.append((word["content"].strip(), box, word.get("direction") == "vertical"))
    return words


def _center_in(box: Bbox, area: Bbox) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return area[0] <= cx <= area[2] and area[1] <= cy <= area[3]


def _paragraph_tokens(
    block: Block, area: Bbox | None, words: list[tuple[str, Bbox, bool]], used: set[int]
) -> list[Token] | None:
    """段落の語の座標（pt）。行と word の対応が取れなければ None（段落単位の規則に落とす）。

    候補は段落の box（ピクセル）に中心が入る word。読み順（横書きは上 → 左、縦書きは右 → 上）に
    並べ、各行に同じ文字列の未使用の word を先頭から割り当てる。
    """
    if area is None or block.type != "paragraph" or not block.text:
        return None
    candidates = [
        i for i, (_, box, _) in enumerate(words) if i not in used and _center_in(box, area)
    ]
    candidates.sort(
        key=lambda i: (
            (-words[i][1][0], words[i][1][1]) if words[i][2] else (words[i][1][1], words[i][1][0])
        )
    )
    chosen: list[int] = []
    chars: list[tuple[str, Bbox]] = []
    for line in block.text.split("\n"):
        if not line.strip():
            continue
        match = next(
            (i for i in candidates if i not in chosen and words[i][0] == line.strip()), None
        )
        if match is None:
            return None
        chosen.append(match)
        _, box, vertical = words[match]
        chars.extend(_line_chars(line, _to_pt(box), vertical))
    try:
        tokens = tokens_from_chars(block.text, chars)
    except ValueError:
        return None
    used.update(chosen)
    return tokens


def _page(data: dict, number: int) -> Page:
    """1 ページ分の JSON を Page にする。"""
    entries = _elements(data)
    words = _words(data)
    used: set[int] = set()
    blocks = [block for block, _ in entries]
    tokens = [_paragraph_tokens(block, area, words, used) for block, area in entries]
    return Page(number=number, blocks=drop_table_content_lines(blocks, tokens))


def normalize(raw_dir: Path) -> Document:
    """`*_p<N>.json` を読み、ページ × （段落 + 表）の Document にする。

    ページ番号はファイル名の `p<N>`。抜けた番号は空ページとして残す（番号がずれると
    他アダプタと比較できないため）。
    """
    pages_by_number: dict[int, Page] = {}
    for number, path in page_files(raw_dir):
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        pages_by_number[number] = _page(data if isinstance(data, dict) else {}, number)
    count = max(pages_by_number, default=0)
    pages = [pages_by_number.get(n, Page(number=n)) for n in range(1, count + 1)]
    return Document(adapter=NAME, pages=pages)
