"""pdf2json の raw 出力（`<入力の basename>.json`）を Common Document IR に正規化する。

pdf2json はページごとに文字の断片（`Texts[]`）を座標付きで返すだけで、段落も表も持たない。
そこで **行単位**でまとめる。`y` が近い断片を 1 行として `x` 昇順に連結し、その 1 行を
1 つの paragraph ブロックにする。行から段落への統合はしない（どこで段落が切れるかは
pdf2json の出力からは決められず、推測を入れると特定パーサーに有利な正規化になるため）。
表は pdf2json が検出しないので table ブロックは作らない。
raw は読むだけで変更しない（企画書 §54-2）。
"""

import json
from pathlib import Path
from urllib.parse import unquote

from jdocbench.ir import Block, Document, Page

NAME = "pdf2json"

_SAME_LINE_TOLERANCE = 0.5
"""同じ行とみなす `y` の許容差（pdf2json 独自単位。1 単位 = 1/4 inch 相当）。"""


def _as_float(value: object) -> float:
    """数値なら float に、そうでなければ 0.0。"""
    return float(value) if isinstance(value, int | float) else 0.0


def _number(item: dict, key: str) -> float:
    """座標値（`x` / `y` / `w`）を float で取る。欠けていれば 0.0。"""
    return _as_float(item.get(key))


def _text(item: dict) -> str:
    """`R[].T` を連結する。`T` は URL エンコードされていることがあるため unquote する。"""
    return "".join(unquote(run.get("T") or "") for run in item.get("R") or [])


def _font_size(item: dict) -> float:
    """`R[0].TS[1]`（フォントサイズ）。取れなければ 0.0 = 高さの無い bbox。"""
    runs = item.get("R") or []
    text_style = runs[0].get("TS") if runs else None
    if isinstance(text_style, list) and len(text_style) > 1:
        return _as_float(text_style[1])
    return 0.0


def _line_block(line: list[dict]) -> Block:
    """`x` 昇順に並んだ 1 行分の断片を 1 つの paragraph ブロックにする。

    bbox は行全体を囲む `(x0, y0, x1, y1)`。高さは行頭の断片のフォントサイズを使う
    （座標の `x` / `y` / `w` は pdf2json 独自単位なので、高さだけ由来が違う。他アダプタの
    bbox とそのまま比較はできない）。
    """
    y0 = min(_number(item, "y") for item in line)
    bbox = (
        _number(line[0], "x"),
        y0,
        max(_number(item, "x") + _number(item, "w") for item in line),
        y0 + _font_size(line[0]),
    )
    return Block(type="paragraph", text=" ".join(_text(item) for item in line).strip(), bbox=bbox)


def _lines(texts: list[dict]) -> list[Block]:
    """`Texts` を `y` 昇順 → `x` 昇順に並べ、`y` が近いものを 1 行にまとめる。"""
    ordered = sorted(texts, key=lambda item: (_number(item, "y"), _number(item, "x")))
    lines: list[list[dict]] = []
    for item in ordered:
        # 行頭の `y` との差で判定する。行内で `y` がわずかにずれても同じ行に入る
        if lines and _number(item, "y") - _number(lines[-1][0], "y") <= _SAME_LINE_TOLERANCE:
            lines[-1].append(item)
        else:
            lines.append([item])
    # 行内の並びは `y` の微差ではなく `x` で決める。空白だけの行は pdftotext の正規化
    # （空段落を捨てる）と揃えて落とす。残さないと chars が空白分だけ多く出て比較が歪む
    blocks = [_line_block(sorted(line, key=lambda item: _number(item, "x"))) for line in lines]
    return [block for block in blocks if block.text]


def normalize(raw_dir: Path) -> Document:
    """`raw_dir` 直下の JSON を読み、ページ × 行の Document にする。

    JSON が 1 つも無ければ FileNotFoundError。`Texts` が空のページは blocks を空にして
    残す（ページ番号がずれると他アダプタと比較できなくなるため）。
    """
    paths = sorted(raw_dir.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"pdf2json の JSON が見つかりません: {raw_dir}")
    data = json.loads(paths[0].read_text(encoding="utf-8", errors="replace"))
    pages = [
        Page(number=number, blocks=_lines(page.get("Texts") or []))
        for number, page in enumerate(data.get("Pages") or [], start=1)
    ]
    return Document(adapter=NAME, pages=pages)
