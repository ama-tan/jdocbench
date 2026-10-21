import json
from pathlib import Path

import pytest

from jdocbench.ir import Block, Page
from jdocbench.normalizers import NORMALIZERS
from jdocbench.normalizers._tables import drop_table_content_lines, tokens_from_chars
from jdocbench.normalizers.camelot import normalize as normalize_camelot
from jdocbench.normalizers.docling import normalize as normalize_docling
from jdocbench.normalizers.pdf2json import normalize as normalize_pdf2json
from jdocbench.normalizers.pdfplumber import normalize as normalize_pdfplumber
from jdocbench.normalizers.pdftotext import normalize
from jdocbench.normalizers.ppstructure import normalize as normalize_ppstructure
from jdocbench.normalizers.pymupdf import normalize as normalize_pymupdf

# pdftotext の出力を模した固定入力。2 ページ、各ページ 3 行（空行を挟む）、
# 最終ページ末尾にも \f が付く
_TEXT = "一行目\n二行目\n\n三行目\n\f四行目\n\n  五行目  \n六行目\n\f"


def _raw_dir(tmp_path: Path, text: str) -> Path:
    raw = tmp_path / "raw" / "pdftotext"
    raw.mkdir(parents=True)
    (raw / "text.txt").write_text(text, encoding="utf-8")
    return raw


def test_normalize_splits_pages_into_lines(tmp_path: Path) -> None:
    """段落 = 非空行。空行は区切りではなく単に捨てる。"""
    doc = normalize(_raw_dir(tmp_path, _TEXT))

    assert doc.adapter == "pdftotext"
    assert len(doc.pages) == 2
    assert [page.number for page in doc.pages] == [1, 2]
    assert [len(page.blocks) for page in doc.pages] == [3, 3]

    texts = [[block.text for block in page.blocks] for page in doc.pages]
    assert texts == [
        ["一行目", "二行目", "三行目"],
        ["四行目", "五行目", "六行目"],  # 前後の空白は strip される
    ]
    assert all(block.type == "paragraph" for page in doc.pages for block in page.blocks)
    assert all("\n" not in block.text for page in doc.pages for block in page.blocks)
    assert all(block.bbox is None for page in doc.pages for block in page.blocks)
    assert all(block.rows is None for page in doc.pages for block in page.blocks)


def test_normalize_counts(tmp_path: Path) -> None:
    doc = normalize(_raw_dir(tmp_path, _TEXT))

    # 3 文字 × 6 行
    assert doc.char_count() == 18
    assert doc.table_count() == 0


def test_empty_page_is_kept_with_no_blocks(tmp_path: Path) -> None:
    """空ページはページとして残し blocks を 0 にする（ページ番号をずらさないため）。"""
    doc = normalize(_raw_dir(tmp_path, "一ページ目\n\f\f三ページ目\n\f"))

    assert [page.number for page in doc.pages] == [1, 2, 3]
    assert [len(page.blocks) for page in doc.pages] == [1, 0, 1]


def test_blank_only_page_has_no_blocks(tmp_path: Path) -> None:
    """空行だけのページも blocks 0（行に分けても空行は捨てる）。"""
    doc = normalize(_raw_dir(tmp_path, "一ページ目\n\f  \n\n\u3000\n\f"))

    assert [len(page.blocks) for page in doc.pages] == [1, 0]


def test_trailing_form_feed_does_not_add_a_page(tmp_path: Path) -> None:
    """末尾の \\f は改ページ記号なので、その後ろに空ページを作らない。"""
    with_feed = normalize(_raw_dir(tmp_path / "a", "一ページ目\n\f"))
    without_feed = normalize(_raw_dir(tmp_path / "b", "一ページ目\n"))

    assert len(with_feed.pages) == 1
    assert len(without_feed.pages) == 1


def test_empty_file_yields_no_pages(tmp_path: Path) -> None:
    assert normalize(_raw_dir(tmp_path, "")).pages == []


def test_registry_contains_pdftotext() -> None:
    assert NORMALIZERS["pdftotext"] is normalize


# --- pdf2json ---------------------------------------------------------------

# pdf2json の出力を模した固定入力。1 ページ目は「y がわずかに違う同じ行の 2 要素」と
# 「別の行の 1 要素」を、わざと読み順と逆に並べてある。T は URL エンコード
_PDF2JSON_RAW = {
    "Transcoder": "pdf2json@4.1.0",
    "Meta": {},
    "Pages": [
        {
            "Width": 37.201,
            "Height": 52.625,
            "Texts": [
                # 2 行目（y が大きい）を先頭に置く
                {
                    "x": 7.0,
                    "y": 33.3,
                    "w": 4.5,
                    "R": [{"T": "%E4%BA%8C%E8%A1%8C%E7%9B%AE", "S": -1, "TS": [2, 16.0, 0, 0]}],
                },
                # 1 行目の右側（x が大きい）を左側より先に置く
                {
                    "x": 13.5,
                    "y": 31.4,
                    "w": 3.0,
                    "R": [{"T": "1329", "S": -1, "TS": [2, 12.0, 0, 0]}],
                },
                {
                    "x": 7.0,
                    "y": 31.41,
                    "w": 6.0,
                    "R": [{"T": "%E3%81%82%E3%81%84", "S": -1, "TS": [2, 12.0, 0, 0]}],
                },
            ],
        },
        {"Width": 37.201, "Height": 52.625, "Texts": []},
    ],
}


def _pdf2json_raw_dir(tmp_path: Path, data: dict) -> Path:
    raw = tmp_path / "raw" / "pdf2json"
    raw.mkdir(parents=True)
    (raw / "sample.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return raw


def test_pdf2json_groups_texts_into_lines(tmp_path: Path) -> None:
    doc = normalize_pdf2json(_pdf2json_raw_dir(tmp_path, _PDF2JSON_RAW))

    assert doc.adapter == "pdf2json"
    assert [page.number for page in doc.pages] == [1, 2]
    # y が 0.01 しか違わない 2 要素は 1 行、y が 1.9 違う要素は別の行。Texts が空のページは blocks 0
    assert [len(page.blocks) for page in doc.pages] == [2, 0]

    blocks = doc.pages[0].blocks
    assert all(block.type == "paragraph" for block in blocks)
    # URL エンコードが解け、行内は x 昇順で " " 区切りに連結される
    assert [block.text for block in blocks] == ["あい 1329", "二行目"]
    assert doc.char_count() == 10
    assert doc.table_count() == 0  # pdf2json は表を検出しない


def test_pdf2json_bbox_spans_the_line(tmp_path: Path) -> None:
    doc = normalize_pdf2json(_pdf2json_raw_dir(tmp_path, _PDF2JSON_RAW))

    # (行頭の x, 行の y, 行内の max(x + w), y + 行頭のフォントサイズ)
    assert doc.pages[0].blocks[0].bbox == pytest.approx((7.0, 31.4, 16.5, 43.4))
    assert doc.pages[0].blocks[1].bbox == pytest.approx((7.0, 33.3, 11.5, 49.3))


def test_pdf2json_bbox_height_is_zero_without_font_size(tmp_path: Path) -> None:
    """TS が無い要素は高さを取れないので y1 = y0 にする。"""
    raw = {"Pages": [{"Texts": [{"x": 1.0, "y": 2.0, "w": 3.0, "R": [{"T": "abc"}]}]}]}
    doc = normalize_pdf2json(_pdf2json_raw_dir(tmp_path, raw))

    assert doc.pages[0].blocks[0].bbox == pytest.approx((1.0, 2.0, 4.0, 2.0))


def test_pdf2json_without_json_raises(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "pdf2json"
    raw.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        normalize_pdf2json(raw)


# --- pdfplumber -------------------------------------------------------------

# pdfplumber の text.txt を模した固定入力。2 ページ、1 ページ目は 3 行
_PDFPLUMBER_TEXT = "一行目\n二行目\n\n三行目\f二ページ目の行"
# extract_tables() の結果を模した固定入力。1 ページ目に 1 表（None セルを含む）、2 ページ目は表なし
_PDFPLUMBER_TABLES = [[[["見出し", None], ["値", "1329"]]], []]


_AUTO = "auto"


def _auto_pdfplumber_lines(text: str) -> list:
    """text.txt の各行に、どの表とも重ならない座標（y 1000 以降）を付けた lines.json。"""
    return [
        [
            {"text": line, "x0": 0, "top": 1000 + 10 * i, "x1": 10, "bottom": 1008 + 10 * i}
            for i, line in enumerate(page.split("\n"))
        ]
        for page in text.split("\f")
    ]


def _spread_chars(text: str, x0: float, top: float, x1: float, bottom: float) -> list:
    """行の bbox を文字数（空白を含む）で等分し、空白以外の各文字に `[文字, x0, top, x1, bottom]`
    を付ける（#199 の文字座標を模す）。"""
    width = (x1 - x0) / max(len(text), 1)
    return [
        [char, x0 + width * i, top, x0 + width * (i + 1), bottom]
        for i, char in enumerate(text)
        if not char.isspace()
    ]


def _with_pdfplumber_chars(lines: list) -> list:
    """lines.json の各行に、`chars` が無ければ行の bbox を等分した文字座標を付ける。"""
    return [
        [
            line
            if "chars" in line
            else {
                **line,
                "chars": _spread_chars(
                    line["text"], line["x0"], line["top"], line["x1"], line["bottom"]
                ),
            }
            for line in page
        ]
        for page in lines
    ]


def _pdfplumber_raw_dir(
    tmp_path: Path,
    text: str = _PDFPLUMBER_TEXT,
    tables: object = _PDFPLUMBER_TABLES,
    lines: object = _AUTO,
    table_bboxes: object = _AUTO,
    chars: bool = True,
) -> Path:
    """pdfplumber の raw を作る。

    lines / table_bboxes は既定で自動生成する（段落は表と重ならない位置、表は (0, 0, 1, 1)）。
    None ならそのファイルを作らない（#186 より前の raw）。`chars` が真なら、lines の各行に
    `chars` が無ければ行の bbox を等分した文字座標を付ける（偽なら #199 より前の raw）。
    """
    if lines == _AUTO:
        lines = _auto_pdfplumber_lines(text)
    if chars and isinstance(lines, list):
        lines = _with_pdfplumber_chars(lines)
    if table_bboxes == _AUTO:
        table_bboxes = [[[0, 0, 1, 1] for _ in page] for page in tables]
    raw = tmp_path / "raw" / "pdfplumber"
    raw.mkdir(parents=True)
    (raw / "text.txt").write_text(text, encoding="utf-8")
    (raw / "tables.json").write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")
    for name, data in (("lines.json", lines), ("table_bboxes.json", table_bboxes)):
        if data is not None:
            (raw / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return raw


def test_pdfplumber_appends_tables_after_paragraphs(tmp_path: Path) -> None:
    doc = normalize_pdfplumber(_pdfplumber_raw_dir(tmp_path))

    assert doc.adapter == "pdfplumber"
    assert [page.number for page in doc.pages] == [1, 2]
    # 1 ページ目は行 3 つ + 表 1 つ。表は段落の後ろに来る
    assert [[block.type for block in page.blocks] for page in doc.pages] == [
        ["paragraph", "paragraph", "paragraph", "table"],
        ["paragraph"],
    ]
    assert [block.text for block in doc.pages[0].blocks[:3]] == ["一行目", "二行目", "三行目"]
    # 段落の bbox は lines.json の行（空行も 1 行として数える）、表の bbox は table_bboxes.json
    assert [block.bbox for block in doc.pages[0].blocks] == [
        (0.0, 1000.0, 10.0, 1008.0),
        (0.0, 1010.0, 10.0, 1018.0),
        (0.0, 1030.0, 10.0, 1038.0),
        (0.0, 0.0, 1.0, 1.0),
    ]


def test_pdfplumber_table_rows_replace_none_with_empty_string(tmp_path: Path) -> None:
    doc = normalize_pdfplumber(_pdfplumber_raw_dir(tmp_path))

    table = doc.pages[0].blocks[3]
    assert table.rows == [["見出し", ""], ["値", "1329"]]
    assert table.text is None
    assert doc.table_count() == 1


# 表の中身の行を位置で判定する固定入力（#186）。1 ページ目の表は y 25〜60。
# 最後の「値」は表の外（y 90。図中のラベルが表のセルと同じ文字列のケース）
_PDFPLUMBER_DUP_TEXT = "表１ 見出しと値\n見出し 1329\n値\n表の外の本文\n値\f二ページ目の行"
_PDFPLUMBER_DUP_TABLES = [[[["見出し", None], ["値", "1329"]]], []]
_PDFPLUMBER_DUP_LINES = [
    [
        {"text": "表１ 見出しと値", "x0": 10, "top": 10, "x1": 100, "bottom": 18},
        {"text": "見出し 1329", "x0": 10, "top": 30, "x1": 100, "bottom": 38},
        {"text": "値", "x0": 10, "top": 45, "x1": 20, "bottom": 53},
        {"text": "表の外の本文", "x0": 10, "top": 70, "x1": 100, "bottom": 78},
        {"text": "値", "x0": 10, "top": 90, "x1": 20, "bottom": 98},
    ],
    [{"text": "二ページ目の行", "x0": 10, "top": 10, "x1": 100, "bottom": 18}],
]
_PDFPLUMBER_DUP_BBOXES = [[[0, 25, 200, 60]], []]


def test_pdfplumber_drops_paragraphs_that_are_table_content(tmp_path: Path) -> None:
    """表の bbox の中にある表の中身の行だけ段落から落ちる。表の外の同じ文字列は残る（#186）。"""
    doc = normalize_pdfplumber(
        _pdfplumber_raw_dir(
            tmp_path,
            _PDFPLUMBER_DUP_TEXT,
            _PDFPLUMBER_DUP_TABLES,
            _PDFPLUMBER_DUP_LINES,
            _PDFPLUMBER_DUP_BBOXES,
        )
    )

    # 表の中の「見出し 1329」（セル 2 つの連結）と「値」（単一セル）が落ち、表の外の「値」は残る
    assert [block.text for block in doc.pages[0].blocks if block.type == "paragraph"] == [
        "表１ 見出しと値",
        "表の外の本文",
        "値",
    ]
    assert doc.pages[0].blocks[0].bbox == (10.0, 10.0, 100.0, 18.0)
    assert doc.pages[0].blocks[-1].bbox == (0.0, 25.0, 200.0, 60.0)
    assert doc.table_count() == 1
    # 表の中身は table ブロック由来の行としてだけ現れる（二重に数えない）
    assert doc.lines() == [
        "表１ 見出しと値",
        "表の外の本文",
        "値",
        "見出し",
        "値 1329",
        "二ページ目の行",
    ]


@pytest.mark.parametrize("missing", ["lines.json", "table_bboxes.json"])
def test_pdfplumber_without_positions_is_an_error(tmp_path: Path, missing: str) -> None:
    """座標ファイルの無い raw（#186 より前）はエラー。二重に数えた結果を黙って出さない。"""
    positions = {"lines": _PDFPLUMBER_DUP_LINES, "table_bboxes": _PDFPLUMBER_DUP_BBOXES}
    positions[missing.removesuffix(".json")] = None
    raw = _pdfplumber_raw_dir(tmp_path, _PDFPLUMBER_DUP_TEXT, _PDFPLUMBER_DUP_TABLES, **positions)

    with pytest.raises(FileNotFoundError, match=missing):
        normalize_pdfplumber(raw)


def test_pdfplumber_lines_that_do_not_match_text_are_an_error(tmp_path: Path) -> None:
    """lines.json の行が text.txt と食い違う raw はエラー（誤った位置で判定しない）。"""
    lines = [
        [{**line, "text": "別の行"} if index == 2 else line for index, line in enumerate(page)]
        for page in _PDFPLUMBER_DUP_LINES
    ]
    raw = _pdfplumber_raw_dir(
        tmp_path, _PDFPLUMBER_DUP_TEXT, _PDFPLUMBER_DUP_TABLES, lines, _PDFPLUMBER_DUP_BBOXES
    )

    with pytest.raises(ValueError, match="p1: lines.json"):
        normalize_pdfplumber(raw)


def test_pdfplumber_table_bbox_count_mismatch_is_an_error(tmp_path: Path) -> None:
    """table_bboxes.json の表の数が tables.json と違う raw はエラー。"""
    raw = _pdfplumber_raw_dir(
        tmp_path, _PDFPLUMBER_DUP_TEXT, _PDFPLUMBER_DUP_TABLES, _PDFPLUMBER_DUP_LINES, [[], []]
    )

    with pytest.raises(ValueError, match="p1: table_bboxes.json"):
        normalize_pdfplumber(raw)


def test_registry_contains_pdf2json_and_pdfplumber() -> None:
    assert NORMALIZERS["pdf2json"] is normalize_pdf2json
    assert NORMALIZERS["pdfplumber"] is normalize_pdfplumber


def test_pdf2json_drops_whitespace_only_lines(tmp_path: Path) -> None:
    """空白だけの行は pdftotext の正規化（空段落を捨てる）と揃えて落とす。chars を歪めない。"""
    data = {
        "Pages": [
            {
                "Texts": [
                    {"x": 1.0, "y": 10.0, "w": 2.0, "R": [{"T": "%20", "TS": [0, 12.0, 0, 0]}]},
                    {
                        "x": 1.0,
                        "y": 20.0,
                        "w": 2.0,
                        "R": [{"T": " %E3%81%82 ", "TS": [0, 12.0, 0, 0]}],
                    },
                ]
            }
        ]
    }
    doc = normalize_pdf2json(_pdf2json_raw_dir(tmp_path, data))

    assert [block.text for block in doc.pages[0].blocks] == ["あ"]
    assert doc.char_count() == 1


# --- pymupdf ----------------------------------------------------------------

# get_text("blocks") を模した固定入力（[x0, y0, x1, y1, text, block_no, block_type]）。
# 2 ページ。1 ページ目はテキストブロック 2 つ（各 2 行、末尾 \n）と画像ブロック（type 1）
_PYMUPDF_BLOCKS = [
    [
        [10.0, 20.0, 110.0, 44.0, "一行目\n二行目\n", 0, 0],
        [10.0, 50.0, 110.0, 74.0, "  三行目  \n\n四行目\n", 1, 0],
        [10.0, 80.0, 110.0, 180.0, "<image: DeviceRGB, ...>", 2, 1],
    ],
    [[10.0, 20.0, 110.0, 32.0, "二ページ目の行\n", 0, 0]],
]
# find_tables() の結果を模した固定入力。1 ページ目に bbox 付きの 1 表、2 ページ目は表なし
_PYMUPDF_TABLES = [[{"bbox": [1, 2, 3, 4], "rows": [["a", None]]}], []]


def _auto_pymupdf_lines(blocks: list) -> list:
    """各テキストブロックの行を、ブロックの bbox の中に上から等分に積んだ lines.json。"""
    pages = []
    for page in blocks:
        lines = []
        for x0, y0, x1, y1, text, block_no, block_type in page:
            if block_type != 0:
                continue
            parts = text.split("\n")
            if parts and parts[-1] == "":
                parts.pop()
            step = (y1 - y0) / len(parts)
            lines.extend(
                [x0, y0 + step * i, x1, y0 + step * (i + 1), part, block_no]
                for i, part in enumerate(parts)
            )
        pages.append(lines)
    return pages


def _with_pymupdf_chars(lines: list) -> list:
    """lines.json の各行に、7 要素目が無ければ行の bbox を等分した文字座標を付ける。"""
    return [
        [line if len(line) >= 7 else [*line, _spread_chars(line[4], *line[:4])] for line in page]
        for page in lines
    ]


def _pymupdf_raw_dir(
    tmp_path: Path,
    tables: object = _PYMUPDF_TABLES,
    blocks: object = _PYMUPDF_BLOCKS,
    lines: object = _AUTO,
    chars: bool = True,
) -> Path:
    """pymupdf の raw を作る。lines は既定で自動生成し、None なら作らない（#186 より前の raw）。

    `chars` が真なら、7 要素目（文字座標）の無い行に行の bbox を等分した文字座標を付ける
    （偽なら #199 より前の raw）。
    """
    if lines == _AUTO:
        lines = _auto_pymupdf_lines(blocks)
    if chars and isinstance(lines, list):
        lines = _with_pymupdf_chars(lines)
    raw = tmp_path / "raw" / "pymupdf"
    raw.mkdir(parents=True)
    # text.txt は raw に残るが正規化では読まない（読んでいたら中身の違いで気づけるようにする）
    (raw / "text.txt").write_text("正規化では読まれない", encoding="utf-8")
    (raw / "blocks.json").write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")
    (raw / "tables.json").write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")
    if lines is not None:
        (raw / "lines.json").write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    return raw


def test_pymupdf_appends_tables_after_paragraphs(tmp_path: Path) -> None:
    doc = normalize_pymupdf(_pymupdf_raw_dir(tmp_path))

    assert doc.adapter == "pymupdf"
    assert [page.number for page in doc.pages] == [1, 2]
    # 1 ページ目はブロック 2 つ × 2 行 = 段落 4 つ + 表 1 つ。表は段落の後ろに来る
    assert [[block.type for block in page.blocks] for page in doc.pages] == [
        ["paragraph", "paragraph", "paragraph", "paragraph", "table"],
        ["paragraph"],
    ]
    # ブロックの配列順（= pymupdf の読み順）のまま。前後の空白は strip され、空行は捨てる
    assert [block.text for block in doc.pages[0].blocks[:4]] == [
        "一行目",
        "二行目",
        "三行目",
        "四行目",
    ]


def test_pymupdf_paragraph_has_line_bbox_and_block_no(tmp_path: Path) -> None:
    """段落の bbox は lines.json の行の bbox（#186）。同じブロックの行は block_no を共有する。"""
    doc = normalize_pymupdf(_pymupdf_raw_dir(tmp_path))

    paragraphs = doc.pages[0].blocks[:4]
    assert [block.bbox for block in paragraphs] == [
        (10.0, 20.0, 110.0, 32.0),
        (10.0, 32.0, 110.0, 44.0),
        (10.0, 50.0, 110.0, 58.0),
        (10.0, 66.0, 110.0, 74.0),
    ]
    assert [block.extra["block_no"] for block in paragraphs] == [0, 0, 1, 1]


def test_pymupdf_without_lines_is_an_error(tmp_path: Path) -> None:
    """lines.json の無い raw（#186 より前）はエラー。表の中身を二重に数えた結果を黙って出さない。"""
    with pytest.raises(FileNotFoundError, match="lines.json"):
        normalize_pymupdf(_pymupdf_raw_dir(tmp_path, lines=None))


def test_pymupdf_lines_that_do_not_match_blocks_are_an_error(tmp_path: Path) -> None:
    """lines.json の行がブロックの text と食い違う raw はエラー（誤った位置で判定しない）。"""
    lines = _auto_pymupdf_lines(_PYMUPDF_BLOCKS)
    lines[0][1][4] = "別の行"

    with pytest.raises(ValueError, match="p1: lines.json"):
        normalize_pymupdf(_pymupdf_raw_dir(tmp_path, lines=lines))


def test_pymupdf_drops_image_blocks(tmp_path: Path) -> None:
    """block_type == 1（画像）はテキストを持たないので段落にしない。"""
    doc = normalize_pymupdf(_pymupdf_raw_dir(tmp_path))

    assert all("image" not in (block.text or "") for page in doc.pages for block in page.blocks)


def test_pymupdf_joins_lines_of_a_one_line_high_block(tmp_path: Path) -> None:
    """1 行分の高さしか無いブロックの `\\n` は行の区切りではないので 1 行に連結する。

    `get_text("text")` が表の 1 行を断片に割ってしまう文書（予算のポイント）への対処。
    """
    blocks = [[[10.0, 20.0, 500.0, 29.8, "１．\n778,190\n837,350\n   \n59,160\n", 0, 0]]]
    lines = [
        [
            [10.0 + 100 * i, 20.0, 60.0 + 100 * i, 29.8, text, 0]
            for i, text in enumerate(["１．", "778,190", "837,350", "   ", "59,160"])
        ]
    ]
    doc = normalize_pymupdf(_pymupdf_raw_dir(tmp_path, tables=[[]], blocks=blocks, lines=lines))

    assert [block.text for block in doc.pages[0].blocks] == ["１． 778,190 837,350 59,160"]
    # 連結した行の bbox は元の行（空白だけの行を除く）の bbox を合わせたもの
    assert doc.pages[0].blocks[0].bbox == (10.0, 20.0, 460.0, 29.8)


def test_pymupdf_table_keeps_bbox_and_replaces_none_cells(tmp_path: Path) -> None:
    doc = normalize_pymupdf(_pymupdf_raw_dir(tmp_path))

    table = doc.pages[0].blocks[4]
    assert table.rows == [["a", ""]]
    assert table.text is None
    # bbox は JSON の配列から IR の tuple になる
    assert table.bbox == (1, 2, 3, 4)
    assert doc.table_count() == 1


def test_pymupdf_drops_bbox_that_is_not_four_numbers(tmp_path: Path) -> None:
    """bbox を取れなかった表も、表としては残す。"""
    raw_dir = _pymupdf_raw_dir(tmp_path, [[{"bbox": None, "rows": [["a"]]}], []])
    doc = normalize_pymupdf(raw_dir)

    table = doc.pages[0].blocks[4]
    assert table.bbox is None
    assert table.rows == [["a"]]


def test_pymupdf_keeps_pages_that_only_have_tables(tmp_path: Path) -> None:
    """ページ数は blocks と tables の多いほう。"""
    blocks = [[[10.0, 20.0, 110.0, 32.0, "一ページ目\n", 0, 0]]]
    tables = [[], [{"bbox": [1, 2, 3, 4], "rows": [["a"]]}]]
    doc = normalize_pymupdf(_pymupdf_raw_dir(tmp_path, tables, blocks))

    assert [page.number for page in doc.pages] == [1, 2]
    assert [[block.type for block in page.blocks] for page in doc.pages] == [
        ["paragraph"],
        ["table"],
    ]


def test_pymupdf_keeps_outside_line_of_a_block_that_straddles_a_table(tmp_path: Path) -> None:
    """表をまたぐブロックでも、表の外の行は残る（行の bbox で判定する。#186）。

    ブロックの bbox を各行に付けると、表の外の「値」（図中のラベルなど）まで表と重なって
    見えて落ちてしまう。
    """
    blocks = [[[10.0, 40.0, 110.0, 95.0, "値\n値\n", 0, 0]]]
    lines = [[[10.0, 40.0, 30.0, 50.0, "値", 0], [10.0, 85.0, 30.0, 95.0, "値", 0]]]
    tables = [[{"bbox": [5, 35, 200, 55], "rows": [["見出し", None], ["値", "1329"]]}]]
    doc = normalize_pymupdf(_pymupdf_raw_dir(tmp_path, tables, blocks, lines))

    paragraphs = [block for block in doc.pages[0].blocks if block.type == "paragraph"]
    assert [(block.text, block.bbox) for block in paragraphs] == [("値", (10.0, 85.0, 30.0, 95.0))]


def test_pymupdf_drops_paragraphs_that_are_table_content(tmp_path: Path) -> None:
    """pdfplumber と同じく、表の bbox と重なる表の中身の行だけ段落から落ちる（#186）。"""
    blocks = [
        [
            [10.0, 10.0, 110.0, 20.0, "表１ 見出しと値\n", 0, 0],
            [10.0, 40.0, 110.0, 60.0, "見出し 1329\n値\n", 1, 0],
            # 表の外のブロック。「値」は表のセルと同じ文字列（図中のラベルなど）
            [10.0, 70.0, 110.0, 94.0, "表の外の本文\n値\n", 2, 0],
        ],
        [[10.0, 20.0, 110.0, 32.0, "二ページ目の行\n", 0, 0]],
    ]
    tables = [[{"bbox": [5, 35, 200, 65], "rows": [["見出し", None], ["値", "1329"]]}], []]
    doc = normalize_pymupdf(_pymupdf_raw_dir(tmp_path, tables, blocks))

    assert [block.text for block in doc.pages[0].blocks if block.type == "paragraph"] == [
        "表１ 見出しと値",
        "表の外の本文",
        "値",
    ]
    assert doc.table_count() == 1
    assert doc.lines() == [
        "表１ 見出しと値",
        "表の外の本文",
        "値",
        "見出し",
        "値 1329",
        "二ページ目の行",
    ]


def test_registry_contains_pymupdf() -> None:
    assert NORMALIZERS["pymupdf"] is normalize_pymupdf


# --- camelot ----------------------------------------------------------------

# Camelot の tables.json を模した固定入力。3 ページ。1 ページ目に 2 表（2 つ目は None セル）、
# 2 ページ目は表なし、3 ページ目に 1 表。bbox は PDF 座標系（原点は左下）
_CAMELOT_RAW = {
    "flavor": "lattice",
    "page_count": 3,
    "tables": [
        {
            "page": 1,
            "flavor": "lattice",
            "bbox": [80.0, 450.0, 540.0, 660.0],
            "page_height": 842.0,
            "accuracy": 100.0,
            "whitespace": 0.0,
            "cells": [
                ["書類", "法人", "個人"],
                ["登記事項証明書 \n（履歴事項全部証明書）", "○", "-"],
            ],
        },
        {
            "page": 1,
            "flavor": "lattice",
            "bbox": [80.0, 100.0, 540.0, 200.0],
            "page_height": 842.0,
            "accuracy": 90.5,
            "whitespace": 25.0,
            "cells": [["見出し", None]],
        },
        {
            "page": 3,
            "flavor": "lattice",
            "bbox": [10.0, 20.0, 30.0, 40.0],
            "page_height": 100.0,
            "accuracy": 99.0,
            "whitespace": 0.0,
            "cells": [["a", "b"]],
        },
    ],
}


def _camelot_raw_dir(tmp_path: Path, raw: dict = _CAMELOT_RAW) -> Path:
    raw_dir = tmp_path / "raw" / "camelot"
    raw_dir.mkdir(parents=True)
    (raw_dir / "tables.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return raw_dir


def test_camelot_produces_only_table_blocks(tmp_path: Path) -> None:
    """段落は作らない（他パーサーのテキストで補わない）。表の無いページも空で残す。"""
    doc = normalize_camelot(_camelot_raw_dir(tmp_path))

    assert doc.adapter == "camelot"
    assert [page.number for page in doc.pages] == [1, 2, 3]
    assert [[block.type for block in page.blocks] for page in doc.pages] == [
        ["table", "table"],
        [],
        ["table"],
    ]
    assert doc.table_count() == 3


def test_camelot_keeps_cells_and_replaces_none(tmp_path: Path) -> None:
    doc = normalize_camelot(_camelot_raw_dir(tmp_path))

    first, second = doc.pages[0].blocks
    # セル内の改行・空白は加工しない
    assert first.rows == [
        ["書類", "法人", "個人"],
        ["登記事項証明書 \n（履歴事項全部証明書）", "○", "-"],
    ]
    assert second.rows == [["見出し", ""]]
    assert first.text is None
    assert first.extra == {"flavor": "lattice", "accuracy": 100.0, "whitespace": 0.0}


def test_camelot_flips_bbox_to_top_left_origin(tmp_path: Path) -> None:
    """左下原点の (x0, y0, x1, y1) を、ページ高さで上原点の (x0, top, x1, bottom) にする。"""
    doc = normalize_camelot(_camelot_raw_dir(tmp_path))

    assert doc.pages[0].blocks[0].bbox == (80.0, 182.0, 540.0, 392.0)
    assert doc.pages[0].blocks[1].bbox == (80.0, 642.0, 540.0, 742.0)
    assert doc.pages[2].blocks[0].bbox == (10.0, 60.0, 30.0, 80.0)


def test_camelot_without_tables_yields_empty_pages(tmp_path: Path) -> None:
    """スキャン PDF など 0 表の文書でも、ページ数分の空 Page を返す（正規化失敗にしない）。"""
    raw = {"flavor": "lattice", "page_count": 2, "tables": []}
    doc = normalize_camelot(_camelot_raw_dir(tmp_path, raw))

    assert [page.number for page in doc.pages] == [1, 2]
    assert doc.table_count() == 0
    assert doc.lines() == []


def test_camelot_skips_tables_without_valid_page(tmp_path: Path) -> None:
    """page が 1 未満・整数でない表は置けないので捨てる（最終ページに紛れ込ませない）。"""
    raw = json.loads(json.dumps(_CAMELOT_RAW))
    raw["tables"][2]["page"] = 0
    raw["tables"][1]["page"] = None
    doc = normalize_camelot(_camelot_raw_dir(tmp_path, raw))

    assert [page.number for page in doc.pages] == [1, 2, 3]
    assert [len(page.blocks) for page in doc.pages] == [1, 0, 0]
    assert doc.table_count() == 1


def test_camelot_extends_pages_for_table_beyond_page_count(tmp_path: Path) -> None:
    """page_count を超える表は、ページを増やしてそのページに置く。"""
    raw = json.loads(json.dumps(_CAMELOT_RAW))
    raw["tables"][2]["page"] = 5
    doc = normalize_camelot(_camelot_raw_dir(tmp_path, raw))

    assert [page.number for page in doc.pages] == [1, 2, 3, 4, 5]
    assert [len(page.blocks) for page in doc.pages] == [2, 0, 0, 0, 1]


def test_registry_contains_camelot() -> None:
    assert NORMALIZERS["camelot"] is normalize_camelot


def test_camelot_stream_reuses_camelot_normalizer(tmp_path: Path) -> None:
    """camelot_stream は raw の形式が同じなので正規化を流用し、アダプタ名だけ変える。"""
    raw = json.loads(json.dumps(_CAMELOT_RAW))
    raw["flavor"] = "stream"
    doc = NORMALIZERS["camelot_stream"](_camelot_raw_dir(tmp_path, raw))

    assert doc.adapter == "camelot_stream"
    assert doc.table_count() == 3


def _docling_text(index: int, text: str, page_no: int | None, label: str = "text") -> dict:
    """Docling の texts[] 1 件。page_no が None なら prov 無し（ページを決められない要素）。"""
    item: dict = {"self_ref": f"#/texts/{index}", "children": [], "label": label, "text": text}
    if page_no is not None:
        item["prov"] = [
            {
                "page_no": page_no,
                "bbox": {"l": 10.0, "t": 20.0, "r": 30.0, "b": 40.0, "coord_origin": "BOTTOMLEFT"},
            }
        ]
    return item


def _docling_cell(text: str, r0: int, r1: int, c0: int, c1: int) -> dict:
    """Docling の grid セル 1 つ。span は offset から求まるので併せて持たせる。"""
    return {
        "text": text,
        "row_span": r1 - r0,
        "col_span": c1 - c0,
        "start_row_offset_idx": r0,
        "end_row_offset_idx": r1,
        "start_col_offset_idx": c0,
        "end_col_offset_idx": c1,
        "column_header": r0 == 0,
        "row_header": False,
    }


# 2 ページ。body.children は texts 2 件 → tables 1 件（children にキャプション）→ 2 ページ目の
# texts 1 件の順。空テキストと prov 無しのテキストも混ぜる（どちらも捨てられる）。
# 表の grid は展開前の形（行ごとにセル数が違い、row_span 2 / col_span 2 を含む）
_DOCLING_DOCUMENT = {
    "body": {
        "self_ref": "#/body",
        "children": [
            {"$ref": "#/texts/0"},
            {"$ref": "#/texts/1"},
            {"$ref": "#/tables/0"},
            {"$ref": "#/texts/3"},
            {"$ref": "#/texts/4"},
            {"$ref": "#/texts/5"},
        ],
    },
    "groups": [],
    "texts": [
        _docling_text(0, "見出し", 1, label="section_header"),
        _docling_text(1, "本文", 1),
        _docling_text(2, "表１ キャプション", 1, label="caption"),
        _docling_text(3, "二ページ目", 2),
        _docling_text(4, "", 1),
        _docling_text(5, "prov が無い", None),
    ],
    "tables": [
        {
            "self_ref": "#/tables/0",
            "children": [{"$ref": "#/texts/2"}],
            "label": "table",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "BOTTOMLEFT"},
                }
            ],
            "data": {
                "num_rows": 3,
                "num_cols": 2,
                "grid": [
                    [_docling_cell("年 次", 0, 2, 0, 1), _docling_cell("値", 0, 1, 1, 2)],
                    [_docling_cell("2025", 1, 2, 1, 2)],
                    [_docling_cell("合計", 2, 3, 0, 2)],
                ],
            },
        }
    ],
    "pictures": [],
    "pages": {"1": {"page_no": 1}, "2": {"page_no": 2}},
}


def _docling_raw_dir(tmp_path: Path, document: dict | None = None) -> Path:
    raw = tmp_path / "raw" / "docling"
    raw.mkdir(parents=True)
    (raw / "document.json").write_text(
        json.dumps(document if document is not None else _DOCLING_DOCUMENT, ensure_ascii=False),
        encoding="utf-8",
    )
    return raw


def test_docling_keeps_body_reading_order(tmp_path: Path) -> None:
    """ブロックは body.children の順のまま（表を段落の後ろに寄せない）。"""
    doc = normalize_docling(_docling_raw_dir(tmp_path))

    assert doc.adapter == "docling"
    assert [page.number for page in doc.pages] == [1, 2]
    assert [[block.type for block in page.blocks] for page in doc.pages] == [
        # 表の children にあるキャプションも読み順どおり表の直後に入る
        ["paragraph", "paragraph", "table", "paragraph"],
        ["paragraph"],
    ]
    assert [block.text for block in doc.pages[0].blocks] == [
        "見出し",
        "本文",
        None,
        "表１ キャプション",
    ]
    assert [block.text for block in doc.pages[1].blocks] == ["二ページ目"]


def test_docling_paragraph_keeps_bbox_and_label(tmp_path: Path) -> None:
    """bbox は (l, t, r, b) を tuple にしたもの。座標系は変換せず extra に記録するだけ。"""
    block = normalize_docling(_docling_raw_dir(tmp_path)).pages[0].blocks[0]

    assert block.bbox == (10.0, 20.0, 30.0, 40.0)
    assert block.extra == {"label": "section_header", "coord_origin": "BOTTOMLEFT"}
    assert block.rows is None


def test_docling_puts_merged_cell_text_at_the_top_left(tmp_path: Path) -> None:
    """結合セルは範囲の先頭にだけ文字列を置き、範囲内の他の位置は空文字にする（IR の規約）。"""
    table = normalize_docling(_docling_raw_dir(tmp_path)).pages[0].blocks[2]

    assert table.rows == [
        ["年 次", "値"],
        ["", "2025"],  # row_span 2 の 2 行目は空
        ["合計", ""],  # col_span 2 の 2 列目は空
    ]
    assert table.bbox == (1.0, 2.0, 3.0, 4.0)
    assert table.extra == {"label": "table", "coord_origin": "BOTTOMLEFT"}
    assert table.text is None


def test_docling_drops_empty_text_and_text_without_prov(tmp_path: Path) -> None:
    """text が空の要素と prov が無い要素は捨てる。"""
    doc = normalize_docling(_docling_raw_dir(tmp_path))

    # 表の行も読み順で入る（縦結合の「年 次」・横結合の「合計」は 1 回だけ）
    assert doc.lines() == [
        "見出し",
        "本文",
        "年 次 値",
        "2025",
        "合計",
        "表１ キャプション",
        "二ページ目",
    ]
    assert doc.table_count() == 1


def test_docling_keeps_adjacent_equal_cells_and_folds_expanded_span(tmp_path: Path) -> None:
    """独立セルの `○ | ○` は両方残し、展開済み grid の結合セルの複製は 1 つにまとめる（#128）。

    Docling の grid は結合セルを範囲の全位置に同じ offset のセルとして複製して持つ（実測）。
    offset が別のセルは同じ文字列でも別のセル。
    """
    document = json.loads(json.dumps(_DOCLING_DOCUMENT))
    merged = _docling_cell("提出書類", 0, 1, 0, 3)
    document["tables"][0]["data"] = {
        "num_rows": 2,
        "num_cols": 3,
        "grid": [
            [merged, merged, merged],
            [
                _docling_cell("項目", 1, 2, 0, 1),
                _docling_cell("○", 1, 2, 1, 2),
                _docling_cell("○", 1, 2, 2, 3),
            ],
        ],
    }
    doc = normalize_docling(_docling_raw_dir(tmp_path, document))

    assert doc.pages[0].blocks[2].rows == [["提出書類", "", ""], ["項目", "○", "○"]]
    assert doc.pages[0].lines()[2:4] == ["提出書類", "項目 ○ ○"]


def test_docling_clamps_negative_offsets_to_first_row_and_col(tmp_path: Path) -> None:
    """負の start offset は捨てずに先頭の行・列へ丸める（#128 以前の挙動）。"""
    document = json.loads(json.dumps(_DOCLING_DOCUMENT))
    document["tables"][0]["data"] = {
        "num_rows": 2,
        "num_cols": 2,
        "grid": [
            [_docling_cell("左上", -1, 1, -1, 1), _docling_cell("右上", -2, 1, 1, 2)],
            [_docling_cell("左下", 1, 2, -3, 1), _docling_cell("右下", 1, 2, 1, 2)],
        ],
    }
    table = normalize_docling(_docling_raw_dir(tmp_path, document)).pages[0].blocks[2]

    assert table.rows == [["左上", "右上"], ["左下", "右下"]]


def test_docling_keeps_page_without_blocks(tmp_path: Path) -> None:
    """テキストも表も無いページは pages の範囲で空ページとして残す。"""
    document = json.loads(json.dumps(_DOCLING_DOCUMENT))
    document["pages"]["3"] = {"page_no": 3}
    doc = normalize_docling(_docling_raw_dir(tmp_path, document))

    assert [page.number for page in doc.pages] == [1, 2, 3]
    assert [len(page.blocks) for page in doc.pages] == [4, 1, 0]


def test_docling_falls_back_to_array_order(tmp_path: Path) -> None:
    """body.children から 1 つも辿れなければ texts → tables の配列順にする。"""
    document = json.loads(json.dumps(_DOCLING_DOCUMENT))
    document["body"]["children"] = []
    doc = normalize_docling(_docling_raw_dir(tmp_path, document))

    assert [[block.type for block in page.blocks] for page in doc.pages] == [
        ["paragraph", "paragraph", "paragraph", "table"],
        ["paragraph"],
    ]
    assert [block.text for block in doc.pages[0].blocks][:3] == [
        "見出し",
        "本文",
        "表１ キャプション",
    ]


def test_docling_grid_already_expanded_is_kept_as_is(tmp_path: Path) -> None:
    """展開済みの grid（各 (row, col) に 1 セル）はそのまま並べたのと同じになる。"""
    document = json.loads(json.dumps(_DOCLING_DOCUMENT))
    document["tables"][0]["data"] = {
        "num_rows": 2,
        "num_cols": 2,
        "grid": [
            [_docling_cell("a", 0, 1, 0, 1), _docling_cell("a", 0, 1, 1, 2)],
            [_docling_cell("b", 1, 2, 0, 1), {"text": None}],
        ],
    }
    table = normalize_docling(_docling_raw_dir(tmp_path, document)).pages[0].blocks[2]

    assert table.rows == [["a", "a"], ["b", ""]]


def test_registry_contains_docling() -> None:
    assert NORMALIZERS["docling"] is normalize_docling


# --- ppstructure -----------------------------------------------------------

# PP-StructureV3 の `p*_res.json` を模した固定入力。1 ページ目は読み順に
# 段落 2 件 → 表 1 件 → 図 1 件（図はブロックにしない）。表の HTML は rowspan=2 と colspan=2 を含む
_PPSTRUCTURE_TABLE_HTML = (
    "<html><body><table>"
    '<tr><td rowspan="2">年 次</td><td>値</td><td>割合</td></tr>'
    "<tr><td>2025</td><td>11.1</td></tr>"
    '<tr><td colspan="2">合計</td><td>100.0</td></tr>'
    "</table></body></html>"
)

_PPSTRUCTURE_PAGE1 = {
    "width": 1241,
    "height": 1755,
    "parsing_res_list": [
        {
            "block_label": "paragraph_title",
            "block_content": "見出し",
            "block_bbox": [10, 20, 30, 40],
        },
        {"block_label": "text", "block_content": "本文\n", "block_bbox": [10, 50, 300, 90]},
        {
            "block_label": "table",
            "block_content": _PPSTRUCTURE_TABLE_HTML,
            "block_bbox": [1, 2, 3, 4],
        },
        {"block_label": "chart", "block_content": "（万人）\n400", "block_bbox": [5, 6, 7, 8]},
    ],
    "layout_det_res": {"boxes": []},
}

_PPSTRUCTURE_PAGE2 = {
    "parsing_res_list": [
        {"block_label": "text", "block_content": "二ページ目", "block_bbox": [10, 20, 30, 40]},
        {"block_label": "number", "block_content": "  ", "block_bbox": [10, 90, 30, 100]},
    ]
}


def _ppstructure_raw_dir(tmp_path: Path, *pages: dict) -> Path:
    raw = tmp_path / "raw" / "ppstructure"
    (raw / "json").mkdir(parents=True)
    for number, page in enumerate(pages or (_PPSTRUCTURE_PAGE1, _PPSTRUCTURE_PAGE2), start=1):
        (raw / "json" / f"p{number:03d}_res.json").write_text(
            json.dumps(page, ensure_ascii=False), encoding="utf-8"
        )
    return raw


def test_ppstructure_keeps_parsing_res_list_order(tmp_path: Path) -> None:
    """ページはファイル名順の連番、ブロックは parsing_res_list の順のまま。"""
    doc = normalize_ppstructure(_ppstructure_raw_dir(tmp_path))

    assert doc.adapter == "ppstructure"
    assert [page.number for page in doc.pages] == [1, 2]
    assert [[block.type for block in page.blocks] for page in doc.pages] == [
        ["paragraph", "paragraph", "table", "paragraph"],  # chart の文字（軸ラベル等）も段落に残す
        ["paragraph"],  # 空文字だけの number は捨てる
    ]
    assert [block.text for block in doc.pages[0].blocks] == [
        "見出し",
        "本文\n",
        None,
        "（万人）\n400",
    ]
    assert doc.pages[0].blocks[3].extra == {"label": "chart"}
    assert [block.text for block in doc.pages[1].blocks] == ["二ページ目"]


def test_ppstructure_paragraph_keeps_bbox_and_label(tmp_path: Path) -> None:
    """bbox は block_bbox の (x0, y0, x1, y1)（150dpi の画像ピクセル）をそのまま載せる。"""
    block = normalize_ppstructure(_ppstructure_raw_dir(tmp_path)).pages[0].blocks[0]

    assert block.bbox == (10.0, 20.0, 30.0, 40.0)
    assert block.extra == {"label": "paragraph_title"}
    assert block.rows is None


def test_ppstructure_puts_merged_cell_text_at_the_top_left(tmp_path: Path) -> None:
    """HTML の rowspan / colspan は範囲の先頭にだけ文字列を置き、他の位置は空文字にする。"""
    table = normalize_ppstructure(_ppstructure_raw_dir(tmp_path)).pages[0].blocks[2]

    assert table.rows == [
        ["年 次", "値", "割合"],
        ["", "2025", "11.1"],  # rowspan=2 の 2 行目は空
        ["合計", "", "100.0"],  # colspan=2 の 2 列目は空
    ]
    assert table.bbox == (1.0, 2.0, 3.0, 4.0)
    assert table.extra == {"label": "table"}
    assert table.text is None


def test_ppstructure_keeps_adjacent_equal_cells(tmp_path: Path) -> None:
    """独立した `<td>○</td><td>○</td>` は両方残し、colspan は 1 回だけ出す（#128）。"""
    html = (
        "<table>"
        '<tr><td colspan="3">提出書類</td></tr>'
        "<tr><td>項目</td><td>○</td><td>○</td></tr>"
        "</table>"
    )
    page = {"parsing_res_list": [{"block_label": "table", "block_content": html}]}
    doc = normalize_ppstructure(_ppstructure_raw_dir(tmp_path, page))

    assert doc.pages[0].blocks[0].rows == [["提出書類", "", ""], ["項目", "○", "○"]]
    assert doc.lines() == ["提出書類", "項目 ○ ○"]


def test_ppstructure_lines_flatten_paragraphs_and_tables(tmp_path: Path) -> None:
    doc = normalize_ppstructure(_ppstructure_raw_dir(tmp_path))

    # 縦結合の「年 次」・横結合の「合計」は 1 回だけ
    assert doc.lines() == [
        "見出し",
        "本文",
        "年 次 値 割合",
        "2025 11.1",
        "合計 100.0",
        "（万人）",  # chart の文字も段落として残す（2 行）
        "400",
        "二ページ目",
    ]
    assert doc.table_count() == 1


def test_ppstructure_falls_back_to_table_res_list(tmp_path: Path) -> None:
    """block_content に HTML が無ければ table_res_list の pred_html を出現順に対応させる。"""
    page = json.loads(json.dumps(_PPSTRUCTURE_PAGE1))
    page["parsing_res_list"][2]["block_content"] = None
    page["table_res_list"] = [{"pred_html": "<table><tr><td>1</td><td>2</td></tr></table>"}]
    doc = normalize_ppstructure(_ppstructure_raw_dir(tmp_path, page))

    assert doc.pages[0].blocks[2].rows == [["1", "2"]]


def test_ppstructure_falls_back_to_layout_det_res(tmp_path: Path) -> None:
    """parsing_res_list が無ければ layout_det_res を使う（テキストが無いので表だけ残る）。"""
    page = {
        "layout_det_res": {
            "boxes": [
                {"label": "text", "coordinate": [10, 20, 30, 40]},
                {"label": "table", "coordinate": [1, 2, 3, 4]},
            ]
        },
        "table_res_list": [{"pred_html": "<table><tr><td>1</td></tr></table>"}],
    }
    doc = normalize_ppstructure(_ppstructure_raw_dir(tmp_path, page))

    assert [block.type for block in doc.pages[0].blocks] == ["table"]
    assert doc.pages[0].blocks[0].rows == [["1"]]
    assert doc.pages[0].blocks[0].bbox == (1.0, 2.0, 3.0, 4.0)


def test_ppstructure_empty_table_html_is_not_a_block(tmp_path: Path) -> None:
    """表の HTML が取れなかった table ブロックは（行が 1 つも無いので）作らない。"""
    page = json.loads(json.dumps(_PPSTRUCTURE_PAGE1))
    page["parsing_res_list"][2]["block_content"] = "<html><body><table></table></body></html>"
    doc = normalize_ppstructure(_ppstructure_raw_dir(tmp_path, page))

    assert [block.type for block in doc.pages[0].blocks] == ["paragraph", "paragraph", "paragraph"]


def test_ppstructure_no_json_yields_no_pages(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "ppstructure"
    (raw / "json").mkdir(parents=True)

    assert normalize_ppstructure(raw).pages == []


def test_registry_contains_ppstructure() -> None:
    assert NORMALIZERS["ppstructure"] is normalize_ppstructure


# --- _tables（表の中身の行を段落から除く） -----------------------------------

_TABLE_BBOX = (0.0, 100.0, 200.0, 200.0)
_INSIDE = (10.0, 110.0, 190.0, 120.0)
_OUTSIDE = (10.0, 300.0, 190.0, 310.0)


def _paragraph(text: str, bbox: tuple[float, float, float, float] | None = _INSIDE) -> Block:
    """段落。既定では表（`_TABLE`）の bbox の中に置く。"""
    return Block(type="paragraph", text=text, bbox=bbox)


_TABLE = Block(
    type="table",
    bbox=_TABLE_BBOX,
    rows=[["年 次", "総人口", "割合"], ["2026年", "1,329", "11.1"]],
)


def test_drop_table_content_lines_keeps_only_real_paragraphs() -> None:
    """表の中でセルの連結だけで構成できる段落（複数セル・単一セル）を除き、本文は残す。"""
    blocks = [
        _paragraph("表１ 年齢別の人口"),  # 表題 → 残る
        _paragraph("年次 総人口 割合"),  # セル 3 つの連結 → 除く
        _paragraph("2026年"),  # 単一セルと一致 → 除く
        _paragraph("2026年の推計値である"),  # セルで始まるが全体は構成できない → 残る
        _TABLE,
    ]

    kept = drop_table_content_lines(blocks)

    assert [block.type for block in kept] == ["paragraph", "paragraph", "table"]
    assert [block.text for block in kept if block.type == "paragraph"] == [
        "表１ 年齢別の人口",
        "2026年の推計値である",
    ]


def test_drop_table_content_lines_keeps_cell_text_outside_the_table() -> None:
    """表のセルと同じ文字列でも、表の bbox の外にある段落（図中のラベルなど）は残す（#186）。"""
    blocks = [
        _paragraph("11.1", _OUTSIDE),  # 表の外（図のデータラベル） → 残る
        _paragraph("総人口", _OUTSIDE),  # 表の外（図の凡例） → 残る
        _paragraph("11.1"),  # 表の中 → 除く
        _paragraph("総人口"),  # 表の中 → 除く
        _TABLE,
    ]

    kept = drop_table_content_lines(blocks)

    assert kept == [blocks[0], blocks[1], _TABLE]


def test_drop_table_content_lines_keeps_paragraphs_without_bbox() -> None:
    """bbox の無い段落・表は位置を判定できないので落とさない（座標を返さないパーサー）。"""
    no_bbox_table = Block(type="table", rows=_TABLE.rows)
    blocks = [_paragraph("年次 総人口 割合", None), _paragraph("2026年", None), _TABLE]
    assert drop_table_content_lines(blocks) == blocks

    blocks = [_paragraph("年次 総人口 割合"), _paragraph("2026年"), no_bbox_table]
    assert drop_table_content_lines(blocks) == blocks


def test_drop_table_content_lines_keeps_page_without_tables() -> None:
    """表の無いページは何も落とさない（表を検出しないパーサーの出力は変わらない）。"""
    blocks = [_paragraph("年次 総人口 割合"), _paragraph("2026年", None)]

    assert drop_table_content_lines(blocks) == blocks


def test_drop_table_content_lines_uses_cells_of_overlapping_tables_only() -> None:
    """段落が重なる表のセルだけで判定する。別の表のセルと同じ文字列の段落は残す。

    jsda の図 3 のように、図が表として誤検出され（図の範囲の表）、図中のラベルが
    別の表（表 1）のセルと同じ文字列になる場合、ラベルは図の範囲の表とは重なるが
    表 1 とは重ならないので残る。
    """
    figure_table = Block(type="table", bbox=(0.0, 0.0, 200.0, 50.0), rows=[["パッケージ1"]])
    blocks = [_paragraph("総人口", (10.0, 10.0, 60.0, 20.0)), figure_table, _TABLE]

    assert drop_table_content_lines(blocks) == blocks


def test_drop_table_content_lines_joins_cells_of_side_by_side_tables() -> None:
    """左右に並んだ 2 表の同じ高さの行が 1 行に連結された段落も、両方の表のセルで判定して除く。

    行の中心は表と表の間の隙間に落ちるので、中心ではなく重なりで判定する。
    """
    left = Block(type="table", bbox=(0.0, 100.0, 95.0, 200.0), rows=[["人件費", "5.2"]])
    right = Block(type="table", bbox=(105.0, 100.0, 200.0, 200.0), rows=[["財源合計", "151.3"]])
    blocks = [_paragraph("人件費 5.2 財源合計 151.3", (10.0, 110.0, 190.0, 120.0)), left, right]

    assert drop_table_content_lines(blocks) == [left, right]


def test_drop_table_content_lines_ignores_whitespace_differences() -> None:
    """セルと段落で空白の入り方が違っても（折り返し・字送り）一致とみなす。"""
    blocks = [_paragraph("　年 次  総人口　割合 "), _TABLE]

    assert drop_table_content_lines(blocks) == [_TABLE]


def test_drop_table_content_lines_keeps_all_when_cells_are_empty() -> None:
    """空セルだけの表はセル集合が空になるので、段落は 1 つも落ちない。"""
    empty_table = Block(type="table", bbox=_TABLE_BBOX, rows=[["", "  "], ["　", ""]])
    blocks = [_paragraph("本文"), _paragraph(""), empty_table]

    assert drop_table_content_lines(blocks) == blocks


def test_drop_table_content_lines_preserves_order() -> None:
    """table が段落の間にあっても順序は変えない。"""
    blocks = [_TABLE, _paragraph("2026年"), _paragraph("本文"), _paragraph("割合"), _TABLE]

    kept = drop_table_content_lines(blocks)

    assert [block.type for block in kept] == ["table", "paragraph", "table"]
    assert kept[1].text == "本文"


def test_drop_table_content_lines_splits_cells_on_newline() -> None:
    """セル内の改行は分割して扱う（折り返したセルの断片も段落に出てくるため）。"""
    table = Block(type="table", bbox=_TABLE_BBOX, rows=[["年 次\n（和暦）", "総人口"]])
    blocks = [_paragraph("（和暦）"), _paragraph("年次（和暦）総人口"), _paragraph("本文"), table]

    kept = drop_table_content_lines(blocks)

    assert [block.text for block in kept if block.type == "paragraph"] == ["本文"]


# 行の一部だけが表の中にある段落（#199）。表は x 0〜200 なので、x 0 より左は表の外
def _tokens(*words: tuple[str, float]) -> list[tuple[str, tuple[float, float, float, float]]]:
    """(語, 左端 x) から Token 列を作る。各語は幅 20、高さは `_INSIDE` と同じ。"""
    return [(word, (x, _INSIDE[1], x + 20, _INSIDE[3])) for word, x in words]


def test_drop_table_content_lines_strips_table_tokens_from_a_mixed_line() -> None:
    """本文と表の行が 1 行に連結された段落は、表の中の語だけを除き本文を残す（#199）。"""
    mixed = Block(type="paragraph", text="本文の続き 2026年 1,329 11.1", bbox=(-300, 110, 190, 120))
    tokens = _tokens(("本文の続き", -300), ("2026年", 20), ("1,329", 60), ("11.1", 100))

    kept = drop_table_content_lines([mixed, _TABLE], [tokens, None])

    assert [block.text for block in kept] == ["本文の続き", None]
    # bbox は残った語の bbox の和にする
    assert kept[0].bbox == (-300, 110, -280, 120)
    # 表の値は table ブロック由来の行としてだけ現れる
    assert Page(number=1, blocks=kept).lines() == [
        "本文の続き",
        "年 次 総人口 割合",
        "2026年 1,329 11.1",
    ]


def test_drop_table_content_lines_keeps_mixed_line_when_table_part_is_not_cells() -> None:
    """表の中の語がセルの連結で構成できなければ何も除かない（表が持っていない文字を落とさない）。"""
    mixed = Block(type="paragraph", text="本文 2026年 未知の値", bbox=(-300, 110, 190, 120))
    tokens = _tokens(("本文", -300), ("2026年", 20), ("未知の値", 60))

    assert drop_table_content_lines([mixed, _TABLE], [tokens, None]) == [mixed, _TABLE]


def test_drop_table_content_lines_judges_tokens_by_center() -> None:
    """表の端から語の半分以上はみ出した語は表の外とみなす（誤検出表の切れたセルなど）。"""
    edge = Block(type="paragraph", text="本文 11.1", bbox=(-300, 110, 215, 120))
    tokens = _tokens(("本文", -300), ("11.1", 185))  # 中心 x 195 は表の中
    kept = drop_table_content_lines([edge, _TABLE], [tokens, None])
    assert [block.text for block in kept] == ["本文", None]

    tokens = _tokens(("本文", -300), ("11.1", 195))  # 中心 x 205 は表の外
    assert drop_table_content_lines([edge, _TABLE], [tokens, None]) == [edge, _TABLE]


def test_drop_table_content_lines_drops_line_whose_tokens_are_all_inside() -> None:
    """全ての語が表の中でセルだけで構成できる段落は、従来どおり段落ごと落ちる。"""
    row = _paragraph("2026年 1,329 11.1")
    tokens = _tokens(("2026年", 20), ("1,329", 60), ("11.1", 100))

    assert drop_table_content_lines([row, _TABLE], [tokens, None]) == [_TABLE]


def test_drop_table_content_lines_keeps_cell_text_of_tokens_outside_the_table() -> None:
    """段落の bbox が表と重なっても、表の外の語は表のセルと同じ文字列でも残す。

    語の座標が無い場合（#186 の規則）は、行全体がセルだけで構成できれば表の外の部分ごと
    落ちる（左右に並んだ表の片方だけを検出した行の、検出していない側の見出しなど）。
    """
    line = Block(type="paragraph", text="総人口 割合 総人口 割合", bbox=(-300, 110, 190, 120))
    tokens = _tokens(("総人口", -300), ("割合", -260), ("総人口", 20), ("割合", 60))

    kept = drop_table_content_lines([line, _TABLE], [tokens, None])
    assert [block.text for block in kept] == ["総人口 割合", None]
    assert drop_table_content_lines([line, _TABLE]) == [_TABLE]


def test_drop_table_content_lines_matches_tokens_against_the_table_at_their_position() -> None:
    """1 行が 2 つの表にまたがるとき、語はその位置の表のセルだけで判定する（#199）。

    左の表の位置にある `財源合計` は右の表のセルにしか無いので、左の表の中身ではない。
    両方の表のセルを合わせて判定すると、左の表が持っていない文字まで落ちる。
    """
    left = Block(type="table", bbox=(0.0, 100.0, 95.0, 200.0), rows=[["人件費", "5.2"]])
    right = Block(type="table", bbox=(105.0, 100.0, 200.0, 200.0), rows=[["財源合計", "151.3"]])
    line = Block(type="paragraph", text="本文 人件費 財源合計 151.3", bbox=(-300, 110, 190, 120))
    tokens = _tokens(("本文", -300), ("人件費", 10), ("財源合計", 50), ("151.3", 150))

    kept = drop_table_content_lines([line, left, right], [tokens, None, None])

    # 左の表の位置の語（`人件費 財源合計`）は左の表のセルで構成できないので残し、
    # 右の表の位置の `151.3` だけを除く
    assert [block.text for block in kept] == ["本文 人件費 財源合計", None, None]


def test_drop_table_content_lines_rejects_tokens_of_wrong_length() -> None:
    with pytest.raises(ValueError, match="同じ長さ"):
        drop_table_content_lines([_paragraph("本文"), _TABLE], [None])


def test_tokens_from_chars_skips_spaces_and_unions_char_boxes() -> None:
    """空白で区切った語ごとに、構成する文字の bbox の和を付ける。空白の文字は飛ばす。"""
    chars = [
        ("本", (0, 0, 10, 10)),
        ("文", (10, 1, 20, 9)),
        (" ", (20, 0, 25, 10)),
        ("1", (30, 0, 35, 12)),
        ("2", (35, 0, 40, 10)),
    ]

    assert tokens_from_chars("本文  12", chars) == [
        ("本文", (0, 0, 20, 10)),
        ("12", (30, 0, 40, 12)),
    ]


def test_tokens_from_chars_accepts_multi_character_glyphs() -> None:
    """1 つの文字オブジェクトが複数文字を持つ場合（pdfplumber の `(cid:NNNN)`）も対応付ける。"""
    chars = [("ま", (0, 0, 10, 10)), ("(cid:1)", (10, 0, 20, 10)), ("提", (30, 0, 40, 10))]

    assert tokens_from_chars("ま (cid:1) 提", chars) == [
        ("ま", (0, 0, 10, 10)),
        ("(cid:1)", (10, 0, 20, 10)),
        ("提", (30, 0, 40, 10)),
    ]


@pytest.mark.parametrize(
    "chars",
    [
        [("本", (0, 0, 10, 10))],  # 足りない
        [("本", (0, 0, 10, 10)), ("体", (10, 0, 20, 10))],  # 違う
        [("本", (0, 0, 10, 10)), ("文", (10, 0, 20, 10)), ("末", (20, 0, 30, 10))],  # 余る
    ],
)
def test_tokens_from_chars_mismatch_is_an_error(chars: list) -> None:
    """段落の文字と文字座標が食い違えばエラー（誤った位置で判定しない）。"""
    with pytest.raises(ValueError, match="文字座標"):
        tokens_from_chars("本文", chars)


# stat-go-jp-children-population-2026 p2 の混在行（#199）を模した固定入力。本文は x 103〜257、
# 表は x 279〜536。pdfplumber は同じ高さの本文と表の文字を 1 行に連結する
_MIXED_TEXT = "100人に対する男子の数（人口性 男女計 12286 12340 -54"
_MIXED_TABLES = [[[["", "2026年", "2025年", "増減"], ["男女計\n男\n女", "12286", "12340", "-54"]]]]
_MIXED_TABLE_BBOX = [279.7, 148.8, 535.9, 312.5]


def _mixed_chars(top: float, bottom: float) -> list:
    """`_MIXED_TEXT` の文字座標。本文の 16 文字は x 103 から、表の語は表の列の位置に置く。"""
    body, *cells = _MIXED_TEXT.split(" ")
    chars = [[char, 103.6 + 9.6 * i, top, 113.2 + 9.6 * i, bottom] for i, char in enumerate(body)]
    for cell, x in zip(cells, (335.8, 380.0, 430.0, 480.0), strict=True):
        chars += [[char, x + 5 * i, top, x + 5 * (i + 1), bottom] for i, char in enumerate(cell)]
    return chars


def test_pdfplumber_strips_table_part_of_a_line_joined_with_body_text(tmp_path: Path) -> None:
    """本文と表の行が 1 行に連結された段落は、表の中の語だけを除き本文を残す（#199）。"""
    lines = [
        [
            {
                "text": _MIXED_TEXT,
                "x0": 103.6,
                "top": 200.0,
                "x1": 500.0,
                "bottom": 210.0,
                "chars": _mixed_chars(200.0, 210.0),
            }
        ]
    ]
    doc = normalize_pdfplumber(
        _pdfplumber_raw_dir(tmp_path, _MIXED_TEXT, _MIXED_TABLES, lines, [[_MIXED_TABLE_BBOX]])
    )

    paragraphs = [block for block in doc.pages[0].blocks if block.type == "paragraph"]
    assert [block.text for block in paragraphs] == ["100人に対する男子の数（人口性"]
    assert paragraphs[0].bbox == (103.6, 200.0, 257.2, 210.0)
    # 表の値は table ブロック由来の行としてだけ現れる（二重に数えない）
    assert sum("12286" in line for line in doc.lines()) == 1


def test_pdfplumber_without_chars_is_an_error(tmp_path: Path) -> None:
    """文字座標（lines.json の chars）の無い raw（#199 より前）はエラー。"""
    raw = _pdfplumber_raw_dir(
        tmp_path,
        _PDFPLUMBER_DUP_TEXT,
        _PDFPLUMBER_DUP_TABLES,
        _PDFPLUMBER_DUP_LINES,
        _PDFPLUMBER_DUP_BBOXES,
        chars=False,
    )

    with pytest.raises(ValueError, match="chars"):
        normalize_pdfplumber(raw)


def test_pymupdf_strips_table_part_of_a_line_joined_with_body_text(tmp_path: Path) -> None:
    """本文と表の行が 1 行に連結された段落は、表の中の語だけを除き本文を残す（#199）。"""
    blocks = [[[103.6, 200.0, 500.0, 210.0, _MIXED_TEXT + "\n", 0, 0]]]
    lines = [[[103.6, 200.0, 500.0, 210.0, _MIXED_TEXT, 0, _mixed_chars(200.0, 210.0)]]]
    tables = [[{"bbox": _MIXED_TABLE_BBOX, "rows": _MIXED_TABLES[0][0]}]]
    doc = normalize_pymupdf(_pymupdf_raw_dir(tmp_path, tables, blocks, lines))

    paragraphs = [block for block in doc.pages[0].blocks if block.type == "paragraph"]
    assert [block.text for block in paragraphs] == ["100人に対する男子の数（人口性"]
    assert paragraphs[0].extra == {"block_no": 0}
    assert sum("12286" in line for line in doc.lines()) == 1


def test_pymupdf_without_chars_is_an_error(tmp_path: Path) -> None:
    """文字座標（lines.json の 7 要素目）の無い raw（#199 より前）はエラー。"""
    with pytest.raises(ValueError, match="文字座標"):
        normalize_pymupdf(_pymupdf_raw_dir(tmp_path, chars=False))
