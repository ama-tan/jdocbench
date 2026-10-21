"""PP-StructureV3 の raw 出力（ページごとの `json/p001_res.json` …）を IR に正規化する。

読み順は PP-Structure の `parsing_res_list` の順（レイアウト解析が読み順に並べた結果）。
**OCR 由来なので段落の粒度は行〜段落で揺れる**（1 ブロックが 1 行のこともあれば複数行の
段落のこともある）。`Document.lines()` が段落を行に分けるため CF は粒度の影響を受けないが、
ROA は行の対応を取るので揺れの影響を受ける。

ページ番号はファイル名順の連番（`p001_res.json` → 1）。raw の `page_index` は 1 ページずつ
画像を渡す作りのため実測で null であり、使えない。

表は `block_content` の HTML（`<table><tr><td rowspan= colspan=>`）を `_html_table` で分解し、
結合セルは IR の規約（`Block.rows`）どおり範囲の先頭の位置にだけ文字列を置く（範囲内の他の位置は
空文字。`groundtruth/README.md` と同じ）。
図・画像・グラフ・数式はブロックにしない。

bbox は `block_bbox` をそのまま `(x0, y0, x1, y1)` に載せる。**150dpi のページ画像のピクセル
座標**で、他アダプタの PDF ポイント座標とは尺度が違う（正規化では変換しない。raw の値を
そのまま残す）。raw は読むだけで変更しない（企画書 §54-2）。
"""

import json
from pathlib import Path

from jdocbench.ir import Block, Document, Page
from jdocbench.normalizers._html_table import table_rows

NAME = "ppstructure"

# ブロックにしないラベル。数式・印章だけを落とし、図・画像・グラフ（figure / image / chart）の
# 文字（軸ラベル・データラベル等）は段落として残す。GT は図中ラベルを段落に残す規約で、Docling も
# 図の子テキストを拾うため、ここで捨てると PP-Structure だけ CF で不利になる
_DROPPED_LABELS = frozenset({"formula", "formula_number", "seal"})


def _bbox(value: object) -> tuple[float, float, float, float] | None:
    """`block_bbox` の `[x0, y0, x1, y1]` を IR の bbox にする。形が違えば None。"""
    if not isinstance(value, list) or len(value) != 4:
        return None
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in value):
        return None
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def _elements(data: dict) -> list[dict]:
    """読み順のブロック一覧。`parsing_res_list` が無ければ `layout_det_res` に落とす。

    `layout_det_res` はラベルと座標だけでテキストを持たないため、落とした場合の段落は
    空になって捨てられる（ページの枠だけが残る）。
    """
    parsing = data.get("parsing_res_list")
    if isinstance(parsing, list) and parsing:
        return [element for element in parsing if isinstance(element, dict)]
    layout = data.get("layout_det_res")
    boxes = layout.get("boxes") if isinstance(layout, dict) else None
    return [
        {
            "block_label": box.get("label"),
            "block_content": None,
            "block_bbox": box.get("coordinate"),
        }
        for box in (boxes if isinstance(boxes, list) else [])
        if isinstance(box, dict)
    ]


def _pred_html(data: dict, index: int) -> str:
    """同一ページの `table_res_list` の `index` 番目の `pred_html`。無ければ空文字。"""
    tables = data.get("table_res_list")
    table = tables[index] if isinstance(tables, list) and index < len(tables) else None
    html = table.get("pred_html") if isinstance(table, dict) else None
    return html if isinstance(html, str) else ""


def _page(data: dict, number: int) -> Page:
    """1 ページ分の JSON を Page にする。ブロックは `parsing_res_list` の順のまま並べる。"""
    blocks: list[Block] = []
    table_index = 0  # `table_res_list` との対応は表の出現順
    for element in _elements(data):
        label = element.get("block_label")
        if not isinstance(label, str) or label in _DROPPED_LABELS:
            continue
        content = element.get("block_content")
        bbox = _bbox(element.get("block_bbox"))
        if label == "table":
            html = (
                content if isinstance(content, str) and content else _pred_html(data, table_index)
            )
            table_index += 1
            if rows := table_rows(html):
                blocks.append(Block(type="table", rows=rows, bbox=bbox, extra={"label": label}))
        elif isinstance(content, str) and content.strip():
            blocks.append(Block(type="paragraph", text=content, bbox=bbox, extra={"label": label}))
    return Page(number=number, blocks=blocks)


def normalize(raw_dir: Path) -> Document:
    """`json/p*_res.json` をファイル名順に読み、ページ × （段落 + 表）の Document にする。"""
    paths = sorted((raw_dir / "json").glob("p*_res.json"))
    pages = [
        _page(json.loads(path.read_text(encoding="utf-8", errors="replace")), number)
        for number, path in enumerate(paths, start=1)
    ]
    return Document(adapter=NAME, pages=pages)
