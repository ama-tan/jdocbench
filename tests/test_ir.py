import json

import pytest

from jdocbench.ir import Block, Document, Page, collapse_whitespace


def _sample_document() -> Document:
    """段落 3 つ・表 1 つを持つ 2 ページの固定入力。"""
    return Document(
        adapter="fake",
        pages=[
            Page(
                number=1,
                blocks=[
                    Block(type="paragraph", text="あいうえお"),
                    Block(
                        type="paragraph",
                        text="かきくけこ さしすせそ",
                        bbox=(1.0, 2.0, 3.0, 4.0),
                    ),
                ],
            ),
            Page(
                number=2,
                blocks=[
                    Block(type="paragraph", text="たちつてと"),
                    Block(type="table", rows=[["年", "人口"], ["2026", "1,234"]]),
                ],
            ),
        ],
    )


def test_to_dict_shape() -> None:
    doc = _sample_document().to_dict()

    assert doc["adapter"] == "fake"
    assert [page["number"] for page in doc["pages"]] == [1, 2]
    assert doc["pages"][0]["blocks"][0]["type"] == "paragraph"
    assert doc["pages"][0]["blocks"][0]["text"] == "あいうえお"
    assert doc["pages"][0]["blocks"][0]["bbox"] is None
    assert doc["pages"][0]["blocks"][0]["rows"] is None
    assert doc["pages"][0]["blocks"][0]["extra"] == {}
    assert doc["pages"][0]["blocks"][1]["bbox"] == (1.0, 2.0, 3.0, 4.0)
    assert doc["pages"][1]["blocks"][1]["type"] == "table"
    assert doc["pages"][1]["blocks"][1]["rows"] == [["年", "人口"], ["2026", "1,234"]]
    assert doc["pages"][1]["blocks"][1]["text"] is None


def test_to_json_is_utf8_and_ends_with_newline() -> None:
    text = _sample_document().to_json()

    assert text.endswith("\n")
    assert "あいうえお" in text  # ensure_ascii=False
    # bbox の tuple は JSON では配列になる
    assert json.loads(text)["pages"][0]["blocks"][1]["bbox"] == [1.0, 2.0, 3.0, 4.0]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a\u3000\u3000b  \n c ", "a b c"),  # 全角スペース・連続空白・改行が 1 つの半角スペースに
        ("  \u3000\n\t", ""),  # 空白類だけなら空文字
        ("\uff21\u3000\uff22", "\uff21 \uff22"),  # 全角英字は NFKC しないのでそのまま
    ],
)
def test_collapse_whitespace(raw: str, expected: str) -> None:
    assert collapse_whitespace(raw) == expected


# 段落内に改行を含む固定入力。1 ページ目は 2 段落 5 行、2 ページ目は 1 段落 2 行
_PAGE_PARAGRAPHS = [
    ["見出し\n本文の一行目\n本文の二行目", "次の段落  \n  その続き"],
    ["二ページ目の段落\n二ページ目の二行目"],
]


def _paragraph_granularity_document() -> Document:
    """段落内に改行を持つ Document（従来の pdfplumber / pymupdf の粒度）。"""
    return Document(
        adapter="paragraphs",
        pages=[
            Page(number=number, blocks=[Block(type="paragraph", text=text) for text in texts])
            for number, texts in enumerate(_PAGE_PARAGRAPHS, start=1)
        ],
    )


def _line_granularity_document() -> Document:
    """同じ内容を 1 行 = 1 段落に割り直した Document（統一後の粒度）。"""
    return Document(
        adapter="lines",
        pages=[
            Page(
                number=number,
                blocks=[
                    Block(type="paragraph", text=line)
                    for text in texts
                    for line in text.split("\n")
                ],
            )
            for number, texts in enumerate(_PAGE_PARAGRAPHS, start=1)
        ],
    )


def test_lines_splits_paragraphs_into_collapsed_lines() -> None:
    # 段落の行に続けて表の行も読み順で入り、前後の空白は落ちる
    assert _sample_document().lines() == [
        "あいうえお",
        "かきくけこ さしすせそ",
        "たちつてと",
        "年 人口",
        "2026 1,234",
    ]
    assert _paragraph_granularity_document().lines() == [
        "見出し",
        "本文の一行目",
        "本文の二行目",
        "次の段落",
        "その続き",
        "二ページ目の段落",
        "二ページ目の二行目",
    ]


def test_lines_drops_blank_lines() -> None:
    doc = Document(
        adapter="fake",
        pages=[Page(number=1, blocks=[Block(type="paragraph", text="あ\n  \u3000\n\nい")])],
    )

    assert doc.lines() == ["あ", "い"]


# 縦結合（1 列目のラベルが 2 行分。IR の規約で先頭だけが文字列を持つ）と空セルを含む固定入力
_TABLE_ROWS = [
    ["区分", "A", ""],
    ["こどもの数", "男女計", "  "],
    ["", "男", "　"],
]
_TABLE_LINES = ["区分 A", "こどもの数 男女計", "男"]


def _table_document() -> Document:
    """段落 2 行のあとに表を持つ Document（Docling / pdfplumber の粒度）。"""
    return Document(
        adapter="tables",
        pages=[
            Page(
                number=1,
                blocks=[
                    Block(type="paragraph", text="段落1\n段落2"),
                    Block(type="table", rows=_TABLE_ROWS),
                ],
            )
        ],
    )


def _flat_paragraph_document() -> Document:
    """同じ内容を段落の行だけで持つ Document（pdftotext のような粒度）。"""
    return Document(
        adapter="paragraphs",
        pages=[
            Page(
                number=1,
                blocks=[
                    Block(type="paragraph", text="段落1\n段落2"),
                    *(Block(type="paragraph", text=line) for line in _TABLE_LINES),
                ],
            )
        ],
    )


def test_lines_flattens_table_rows_in_reading_order() -> None:
    """表の行は空セルを除き、残りを半角スペースで連結した 1 行になる。"""
    assert _table_document().lines() == ["段落1", "段落2", "区分 A", "こどもの数 男女計", "男"]


def test_values_do_not_depend_on_where_the_table_content_lives() -> None:
    """同じ内容を「表」で持っても「段落」で持っても text() / char_count() が一致する。"""
    by_table = _table_document()
    by_paragraph = _flat_paragraph_document()

    # ブロックの持ち方は実際に違う（表 1 つ対 段落 4 つ）
    assert by_table.table_count() == 1
    assert by_paragraph.table_count() == 0

    assert by_table.lines() == by_paragraph.lines()
    assert by_table.text() == by_paragraph.text()
    assert by_table.char_count() == by_paragraph.char_count()
    # char_count は lines() の総文字数と整合する
    assert by_table.char_count() == sum(len(line) for line in by_table.lines())


def test_table_cell_newline_becomes_a_space() -> None:
    """セル内の改行は連続空白として圧縮される（縦結合を 1 セルに畳むパーサー向け）。"""
    doc = Document(
        adapter="fake",
        pages=[Page(number=1, blocks=[Block(type="table", rows=[["1329\n681", "合計\n計"]])])],
    )

    assert doc.lines() == ["1329 681 合計 計"]


def test_table_row_keeps_cells_equal_to_the_row_above() -> None:
    """1 つ上の行と同じ列・同じ文字列のセルも独立したセルとして残す（#128）。

    縦結合は先頭だけが文字列を持つ規約なので、上と同じ文字列は結合の写しではない
    （同じ数値が縦に並ぶ列や、`－` が続く列）。空セルだけの行は捨てる。
    """
    doc = Document(
        adapter="fake",
        pages=[
            Page(
                number=1,
                blocks=[Block(type="table", rows=[["A", "－"], ["B", "－"], ["", " "], ["C"]])],
            )
        ],
    )

    assert doc.lines() == ["A －", "B －", "C"]


def test_values_do_not_depend_on_paragraph_granularity() -> None:
    """同じ内容なら「段落内に改行」でも「行ごとに段落」でも値が変わらない。"""
    by_paragraph = _paragraph_granularity_document()
    by_line = _line_granularity_document()

    # 粒度は実際に違う（1 ページ目が 2 ブロック対 5 ブロック）
    assert [len(page.blocks) for page in by_paragraph.pages] == [2, 1]
    assert [len(page.blocks) for page in by_line.pages] == [5, 2]

    assert by_paragraph.lines() == by_line.lines()
    assert by_paragraph.text() == by_line.text()
    assert by_paragraph.char_count() == by_line.char_count()


def test_char_count_counts_collapsed_paragraph_text() -> None:
    # 段落 5 + 11（「かきくけこ さしすせそ」は圧縮しても 11）+ 5、表 4（年 人口）+ 10
    assert _sample_document().char_count() == 35


def test_char_count_collapses_runs_of_whitespace() -> None:
    """`pdftotext -layout` の桁揃え空白で chars が膨らまないこと。"""
    doc = Document(
        adapter="fake", pages=[Page(number=1, blocks=[Block(type="paragraph", text="a   b")])]
    )

    assert doc.char_count() == 3


def test_text_joins_collapsed_paragraphs_with_newline() -> None:
    doc = _sample_document()

    # table ブロックの行も 1 行ずつ連結される
    assert doc.text() == "あいうえお\nかきくけこ さしすせそ\nたちつてと\n年 人口\n2026 1,234"


def test_text_drops_paragraphs_that_collapse_to_empty() -> None:
    doc = Document(
        adapter="fake",
        pages=[
            Page(
                number=1,
                blocks=[
                    Block(type="paragraph", text="あ"),
                    Block(type="paragraph", text="  \u3000 "),
                    Block(type="paragraph", text="い"),
                ],
            )
        ],
    )

    assert doc.text() == "あ\nい"


def test_table_count() -> None:
    assert _sample_document().table_count() == 1


def test_empty_document() -> None:
    doc = Document(adapter="fake")

    assert doc.pages == []
    assert doc.lines() == []
    assert doc.char_count() == 0
    assert doc.table_count() == 0
    assert doc.text() == ""
    assert doc.to_dict() == {"adapter": "fake", "pages": []}


def test_table_row_keeps_adjacent_equal_cells() -> None:
    """隣り合う同値の独立セル（`○ | ○`）は両方残す（#128。GEPS マニュアルの提出書類表）。"""
    doc = Document(
        adapter="fake",
        pages=[
            Page(
                number=1,
                blocks=[
                    Block(
                        type="table",
                        rows=[
                            ["書類", "法人", "個人"],
                            ["項目", "○", "○"],
                            ["資料", "（○）", "（○）"],
                        ],
                    )
                ],
            )
        ],
    )

    assert doc.lines() == ["書類 法人 個人", "項目 ○ ○", "資料 （○） （○）"]


def test_table_row_prints_horizontally_merged_cell_once() -> None:
    """横結合は先頭だけが文字列を持つ規約なので 1 回だけ出る（テキスト系パーサーと揃う）。"""
    doc = Document(
        adapter="fake",
        pages=[
            Page(
                number=1,
                blocks=[
                    Block(
                        type="table",
                        rows=[
                            ["", "未就学", "", "小学生"],
                            ["男女計", "456", "213", "564"],
                        ],
                    )
                ],
            )
        ],
    )

    assert doc.lines() == ["未就学 小学生", "男女計 456 213 564"]
