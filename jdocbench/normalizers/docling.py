"""Docling の raw 出力（document.json = `export_to_dict()`）を Common Document IR に正規化する。

読み順は `body.children[]` の `$ref`（`#/texts/N` / `#/tables/N` / `#/groups/N` / `#/pictures/N`）を
深さ優先で辿った順とする。`groups` だけでなく `tables` / `pictures` の children も辿る
（表のキャプションや図中のラベルがそこにぶら下がっており、実測では 243 テキスト中 184 件・
約 900 文字がこの下にあった。辿らないと GT にある行を丸ごと落とす）。`body.children` から
1 つも辿れなければ `texts` → `tables` の配列順にフォールバックする。

1 テキスト = 1 paragraph ブロック（Docling の出力粒度が段落なので行には分けない）。
**ROA は `Document.lines()` の規則で行単位に分かれるため、段落で 1 ブロックにする Docling は
GT の行と粒度が違う**。これは Docling の限界ではなく IR の粒度の問題で、CF は `Document.text()`
が行連結なので影響を受けない。
bbox は Docling の `(l, t, r, b)` をそのまま IR の `(x0, y0, x1, y1)` に載せ、座標系は変換せず
`extra["coord_origin"]`（実測では prov が `BOTTOMLEFT`）に記録するだけにする。
表は `data.grid` を IR の規約（`Block.rows`）どおり行 × 列にする（結合セルは範囲の先頭の位置に
文字列を入れ、範囲内の他の位置は空文字にする。grid は結合セルを範囲の全位置に複製して持つ）。
raw は読むだけで変更しない（企画書 §54-2）。
"""

import json
from pathlib import Path

from jdocbench.ir import Block, Document, Page

NAME = "docling"


def _as_int(value: object, default: int) -> int:
    """int（bool は除く）ならそれを、そうでなければ default を返す。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _reading_order(data: dict) -> list[tuple[str, dict]]:
    """`body.children` を辿り、(種別, 要素) を読み順に並べた一覧を返す。

    `groups` / `pictures` は IR に対応するブロックが無いので一覧には入れず children だけ辿る。
    同じ `$ref` は 1 度しか辿らない（壊れた raw で循環しても止まらないようにする）。
    """
    visited: set[str] = set()
    ordered: list[tuple[str, dict]] = []

    def walk(ref: str) -> None:
        parts = ref.split("/")
        if len(parts) != 3 or ref in visited or not parts[2].isdigit():
            return
        visited.add(ref)
        kind, index = parts[1], int(parts[2])
        items = data.get(kind)
        if not isinstance(items, list) or index >= len(items):
            return
        node = items[index]
        if not isinstance(node, dict):
            return
        if kind in ("texts", "tables"):
            ordered.append((kind, node))
        for child in node.get("children") or []:
            if isinstance(child, dict) and isinstance(child.get("$ref"), str):
                walk(child["$ref"])

    body = data.get("body")
    for child in (body.get("children") if isinstance(body, dict) else None) or []:
        if isinstance(child, dict) and isinstance(child.get("$ref"), str):
            walk(child["$ref"])
    if ordered:
        return ordered
    # 読み順が取れない raw では配列順に落とす（表を落とさないよう texts の後ろに tables を置く）
    return [("texts", item) for item in data.get("texts") or []] + [
        ("tables", item) for item in data.get("tables") or []
    ]


def _prov(node: dict) -> dict | None:
    """`prov` の先頭（ページ番号と bbox を持つ）。無ければ None。"""
    prov = node.get("prov")
    first = prov[0] if isinstance(prov, list) and prov else None
    return first if isinstance(first, dict) else None


def _bbox(prov: dict) -> tuple[float, float, float, float] | None:
    """`prov.bbox` の `(l, t, r, b)` を IR の bbox にする。座標系は変換しない。"""
    bbox = prov.get("bbox")
    if not isinstance(bbox, dict):
        return None
    values = [bbox.get(key) for key in ("l", "t", "r", "b")]
    if not all(isinstance(value, int | float) and not isinstance(value, bool) for value in values):
        return None
    return (float(values[0]), float(values[1]), float(values[2]), float(values[3]))


def _extra(node: dict, prov: dict) -> dict[str, object]:
    """Docling 固有情報。bbox の意味を決める座標系は bbox を持つブロックすべてに付ける。"""
    bbox = prov.get("bbox")
    return {
        "label": node.get("label"),
        "coord_origin": bbox.get("coord_origin") if isinstance(bbox, dict) else None,
    }


def _cell_range(row_index: int, col_index: int, cell: dict) -> tuple[int, int, int, int]:
    """セル 1 つが占める範囲 `(r0, r1, c0, c1)`（終端は含まない）。

    `start_*_offset_idx` / `end_*_offset_idx` を優先し、無ければ grid 上の位置と
    `row_span` / `col_span` から求める。
    """
    r0 = _as_int(cell.get("start_row_offset_idx"), row_index)
    c0 = _as_int(cell.get("start_col_offset_idx"), col_index)
    r1 = _as_int(cell.get("end_row_offset_idx"), r0 + _as_int(cell.get("row_span"), 1))
    c1 = _as_int(cell.get("end_col_offset_idx"), c0 + _as_int(cell.get("col_span"), 1))
    return r0, max(r1, r0 + 1), c0, max(c1, c0 + 1)


def _rows(data: dict) -> list[list[str]]:
    """`data.grid` を行 × 列の文字列にする。結合セルは範囲の先頭 `(r0, c0)` にだけ文字列を置く。

    Docling の grid は結合セルを範囲の全位置に同じセル（同じ offset）として複製して持つ。
    複製は同じ先頭位置に同じ文字列を書くだけなので 1 つにまとまり、範囲内の他の位置は
    空文字のまま残る（IR の規約。隣り合う同値の独立セルと区別できる）。空文字は書き込まない
    （展開済みの空セルが、先に置いた文字列を消さないようにする）。
    """
    grid = data.get("grid")
    cells = [
        (_cell_range(row_index, col_index, cell), str(cell.get("text") or ""))
        for row_index, row in enumerate(grid if isinstance(grid, list) else [])
        if isinstance(row, list)
        for col_index, cell in enumerate(row)
        if isinstance(cell, dict)
    ]
    n_rows = max([_as_int(data.get("num_rows"), 0), *(r1 for (_, r1, _, _), _ in cells)], default=0)
    n_cols = max([_as_int(data.get("num_cols"), 0), *(c1 for (_, _, _, c1), _ in cells)], default=0)
    rows = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    for (r0, _, c0, _), text in cells:
        # 負の offset は先頭の行・列に丸める（捨てると文字列が消える）
        r0, c0 = max(r0, 0), max(c0, 0)
        if text and r0 < n_rows and c0 < n_cols:
            rows[r0][c0] = text
    return rows


def _block(kind: str, node: dict, prov: dict) -> Block | None:
    """texts / tables の 1 要素を Block にする。中身が空のテキストは None。"""
    if kind == "texts":
        text = node.get("text")
        if not isinstance(text, str) or not text:
            return None
        return Block(type="paragraph", text=text, bbox=_bbox(prov), extra=_extra(node, prov))
    data = node.get("data")
    return Block(
        type="table",
        rows=_rows(data if isinstance(data, dict) else {}),
        bbox=_bbox(prov),
        extra=_extra(node, prov),
    )


def _page_count(data: dict, numbers: list[int]) -> int:
    """`pages` のページ番号とブロックのページ番号の最大値。

    テキストも表も無いページを空ページとして残し、他アダプタとページ番号がずれないようにする。
    """
    pages = data.get("pages")
    keys = pages.keys() if isinstance(pages, dict) else []
    page_numbers = [int(key) for key in keys if isinstance(key, str) and key.isdigit()]
    return max([*page_numbers, *numbers], default=0)


def normalize(raw_dir: Path) -> Document:
    """document.json を読み、ページ × （段落 + 表）の Document にする。

    ブロックは読み順のまま並べる（表を段落の後ろに寄せない）。`prov` が無い要素は
    ページを決められないので捨てる。
    """
    data = json.loads((raw_dir / "document.json").read_text(encoding="utf-8", errors="replace"))

    placed: list[tuple[int, Block]] = []
    for kind, node in _reading_order(data):
        prov = _prov(node)
        number = _as_int(prov.get("page_no"), 0) if prov else 0
        block = _block(kind, node, prov) if prov and number > 0 else None
        if block is not None:
            placed.append((number, block))

    pages = [
        Page(number=number, blocks=[block for page_no, block in placed if page_no == number])
        for number in range(1, _page_count(data, [number for number, _ in placed]) + 1)
    ]
    return Document(adapter=NAME, pages=pages)
