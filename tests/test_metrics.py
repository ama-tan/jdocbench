from jdocbench.ir import Block, Document, Page
from jdocbench.metrics import (
    JNF_CLASSES,
    JNF_VERSION,
    _jnf_gt_chars,
    _matched_positions,
    content_fidelity,
    effective_roa,
    evaluate,
    japanese_notation_fidelity,
    reading_order_accuracy,
    structural_element_detection,
    table_matches,
    table_structural_fidelity,
)


def _document(*blocks: Block, adapter: str = "fake", number: int = 1) -> Document:
    """1 ページに指定のブロックだけを持つ Document。"""
    return Document(adapter=adapter, pages=[Page(number=number, blocks=list(blocks))])


def _paragraph(text: str) -> Block:
    return Block(type="paragraph", text=text)


def _table(rows: list[list[str]]) -> Block:
    return Block(type="table", rows=rows)


_GT = _document(
    _paragraph("あいうえお\nかきくけこ"),
    _table([["A", "B"], ["1", "2"]]),
    adapter="ground_truth",
)


def test_identical_document_scores_full_marks() -> None:
    scores = evaluate(_GT, _document(*_GT.pages[0].blocks))

    assert scores == {
        "cf": 1.0,
        "sed": 1.0,
        "tsf": 1.0,
        "tsf_tables": [
            {
                "page": 1,
                "gt_index": 0,
                "doc_index": 0,
                "doc_indices": [0],
                "shared": False,
                "score": 1.0,
            }
        ],
        "roa": 1.0,
        "roa_match_rate": 1.0,
        "roa_exact_rate": 1.0,
        "roa_effective": 1.0,
        "total": 100.0,
        # 表記対象文字が無いので JNF は None（Total には入らない）
        "jnf": None,
        "jnf_classes": {name: {"gt": 0, "exact": 0, "folded": 0} for name in JNF_CLASSES},
        "jnf_version": JNF_VERSION,
    }


def test_content_fidelity_matches_known_edit_distance() -> None:
    # "あいう" → "あいえ" は置換 1 回。正規化距離は 1/3
    gt = _document(_paragraph("あいう"))
    doc = _document(_paragraph("あいえ"))

    assert content_fidelity(gt, doc) == round(1 - 1 / 3, 4)


def test_content_fidelity_of_two_empty_documents_is_one() -> None:
    assert content_fidelity(_document(), _document()) == 1.0


def test_vertically_merged_cells_still_score_full_marks() -> None:
    # パーサーが縦に結合したセル "1329\n681" は 2 セルに分けて数える
    gt = _document(_table([["1329"], ["681"]]), adapter="ground_truth")
    doc = _document(_table([["1329\n681"]]))

    assert table_structural_fidelity(gt, doc) == 1.0


def test_missing_half_of_the_gt_cells_scores_half() -> None:
    doc = _document(_paragraph("あいうえお"), _table([["A", "B"]]))

    # GT の 4 セルのうち 2 セルしか再現できていない
    assert table_structural_fidelity(_GT, doc) == 0.5


def test_missing_table_scores_zero_for_sed_and_tsf() -> None:
    doc = _document(_paragraph("あいうえお"))

    assert structural_element_detection(_GT, doc) == 0.0
    assert table_structural_fidelity(_GT, doc) == 0.0


def test_table_without_common_cells_is_not_matched() -> None:
    doc = _document(_paragraph("あいうえお"), _table([["X", "Y"]]))

    # Jaccard 0 の表には対応付けない
    assert table_matches(_GT, doc) == [
        {
            "page": 1,
            "gt_index": 0,
            "doc_index": None,
            "doc_indices": [],
            "shared": False,
            "score": 0.0,
        }
    ]
    assert table_structural_fidelity(_GT, doc) == 0.0


def test_empty_tables_are_not_counted_as_detection() -> None:
    """セルが空の表は検出とみなさない（OCR オフのパーサーがスキャン PDF に返す `rows: []`）。"""
    doc = _document(_table([]), _table([["", "  "]]))

    # 表の数だけ GT と一致していても中身が無いので TP 0
    assert structural_element_detection(_GT, doc) == 0.0
    assert table_structural_fidelity(_GT, doc) == 0.0
    assert table_matches(_GT, doc) == [
        {
            "page": 1,
            "gt_index": 0,
            "doc_index": None,
            "doc_indices": [],
            "shared": False,
            "score": 0.0,
        }
    ]


def test_empty_gt_table_is_not_in_the_denominator() -> None:
    """GT 側の空の表も数えない（分母に入れると再現できない表として減点されてしまう）。"""
    gt = _document(_table([["A", "B"], ["1", "2"]]), _table([]), adapter="ground_truth")
    doc = _document(_table([["A", "B"], ["1", "2"]]))

    assert structural_element_detection(gt, doc) == 1.0
    assert table_structural_fidelity(gt, doc) == 1.0
    assert len(table_matches(gt, doc)) == 1


def test_extra_table_lowers_sed_f1() -> None:
    doc = _document(_paragraph("あいうえお"), _table([["A", "B"], ["1", "2"]]), _table([["X"]]))

    # TP=1, FP=1, FN=0 → precision 0.5 / recall 1.0
    assert structural_element_detection(_GT, doc) == round(2 * 0.5 * 1.0 / 1.5, 4)


def test_tsf_is_none_and_excluded_from_total_when_gt_has_no_table() -> None:
    gt = _document(_paragraph("あいうえお"), adapter="ground_truth")
    doc = _document(_paragraph("あいうえ"))

    scores = evaluate(gt, doc)
    assert scores["tsf"] is None
    assert scores["tsf_tables"] == []
    # GT の 1 行は類似度で対応する（対応率 1.0）が、1 行だけで順序対が作れないので ROA は
    # None。順序を測れないだけなので総合点からも外す（CF / SED の平均）
    assert scores["roa"] is None
    assert scores["roa_match_rate"] == 1.0
    assert scores["roa_effective"] is None
    assert scores["total"] == round((scores["cf"] + scores["sed"]) / 2 * 100, 1)


def test_cell_set_ignores_collapsible_whitespace() -> None:
    gt = _document(_table([["a  b", "c　d"]]), adapter="ground_truth")
    doc = _document(_table([["a b", "c d"]]))

    assert table_structural_fidelity(gt, doc) == 1.0


def test_table_with_the_largest_jaccard_on_the_page_is_matched() -> None:
    gt = _document(_table([["A", "B"], ["1", "2"]]), adapter="ground_truth")
    # 1 つ目の表とは 1 セルしか共通しない。2 つ目が正解
    doc = _document(_table([["A", "X"], ["9", "8"]]), _table([["A", "B"], ["1", "2"]]))

    assert table_matches(gt, doc) == [
        {
            "page": 1,
            "gt_index": 0,
            "doc_index": 1,
            "doc_indices": [1],
            "shared": False,
            "score": 1.0,
        }
    ]
    assert table_structural_fidelity(gt, doc) == 1.0


def test_gt_table_split_into_two_doc_tables_scores_full_marks() -> None:
    # GT の 1 表がパーサーでは 2 つの断片になっている（分割に寛容）
    gt = _document(_table([["A", "B"], ["1", "2"]]), adapter="ground_truth")
    doc = _document(_table([["A", "B"]]), _table([["1", "2"]]))

    matches = table_matches(gt, doc)
    assert matches == [
        {
            "page": 1,
            "gt_index": 0,
            "doc_index": 0,
            "doc_indices": [0, 1],
            "shared": False,
            "score": 1.0,
        }
    ]
    assert table_structural_fidelity(gt, doc) == 1.0


def test_one_doc_table_is_shared_by_two_gt_tables() -> None:
    # 複数の GT 表がパーサーでは 1 表に結合されている（結合に寛容）
    gt = _document(_table([["A", "B"]]), _table([["C", "D"]]), adapter="ground_truth")
    doc = _document(_table([["A", "B"], ["C", "D"]]))

    assert table_matches(gt, doc) == [
        {
            "page": 1,
            "gt_index": 0,
            "doc_index": 0,
            "doc_indices": [0],
            "shared": True,
            "score": 1.0,
        },
        {
            "page": 1,
            "gt_index": 1,
            "doc_index": 0,
            "doc_indices": [0],
            "shared": True,
            "score": 1.0,
        },
    ]
    assert table_structural_fidelity(gt, doc) == 1.0


def test_unrelated_table_is_not_added_to_the_union() -> None:
    gt = _document(_table([["A", "B"], ["1", "2"]]), adapter="ground_truth")
    # 3 つ目は共通セルが無い（Jaccard 0）ので和集合に入らない
    doc = _document(_table([["A", "B"]]), _table([["1", "2"]]), _table([["X", "Y"]]))

    assert table_matches(gt, doc)[0]["doc_indices"] == [0, 1]


def test_table_that_does_not_improve_recall_is_not_added() -> None:
    gt = _document(_table([["A", "B"]]), adapter="ground_truth")
    # 2 つ目は GT と共通セルがあるが、1 つ目で再現済みなので加えても再現率が上がらない
    doc = _document(_table([["A", "B"]]), _table([["A", "Z"]]))

    assert table_matches(gt, doc)[0]["doc_indices"] == [0]
    assert table_structural_fidelity(gt, doc) == 1.0


def test_tables_are_matched_within_the_same_page() -> None:
    gt = Document(
        adapter="ground_truth",
        pages=[
            Page(number=1, blocks=[_table([["A"]])]),
            Page(number=2, blocks=[_table([["B"]])]),
        ],
    )
    # 同じ表でもページが違えば対応しない（2 ページ目の GT は対応する表が無く 0）
    doc = Document(
        adapter="fake", pages=[Page(number=1, blocks=[_table([["A"]]), _table([["B"]])])]
    )

    assert table_matches(gt, doc) == [
        {
            "page": 1,
            "gt_index": 0,
            "doc_index": 0,
            "doc_indices": [0],
            "shared": False,
            "score": 1.0,
        },
        {
            "page": 2,
            "gt_index": 0,
            "doc_index": None,
            "doc_indices": [],
            "shared": False,
            "score": 0.0,
        },
    ]
    assert table_structural_fidelity(gt, doc) == 0.5
    assert structural_element_detection(gt, doc) == round(2 * 0.5 * 0.5 / 1.0, 4)


def test_reading_order_of_the_same_lines_is_full_marks() -> None:
    gt = _document(_paragraph("一行目\n二行目\n三行目"), adapter="ground_truth")

    assert reading_order_accuracy(gt, _document(*gt.pages[0].blocks)) == (1.0, 1.0, 1.0)


def test_completely_reversed_reading_order_scores_zero() -> None:
    gt = _document(_paragraph("一行目\n二行目\n三行目"), adapter="ground_truth")
    doc = _document(_paragraph("三行目\n二行目\n一行目"))

    # 全ての順序対が逆転 → τ = −1 → (τ + 1) / 2 = 0
    assert reading_order_accuracy(gt, doc) == (0.0, 1.0, 1.0)


def test_swapped_middle_lines_score_the_known_tau() -> None:
    gt = _document(_paragraph("1 行目\n2 行目\n3 行目\n4 行目"), adapter="ground_truth")
    doc = _document(_paragraph("1 行目\n3 行目\n2 行目\n4 行目"))

    # doc 側の位置は [0, 2, 1, 3]。6 対のうち 5 対が順方向 → τ = (5 − 1) / 6
    assert reading_order_accuracy(gt, doc) == (round((4 / 6 + 1) / 2, 4), 1.0, 1.0)


def test_lines_missing_from_doc_lower_the_match_rate_only() -> None:
    gt = _document(_paragraph("あ\nい\nう\nえ"), adapter="ground_truth")
    doc = _document(_paragraph("い\nあ"))

    # 一致するのは「あ」「い」の 2 行だけ。その 2 行は逆順なので τ = −1
    assert reading_order_accuracy(gt, doc) == (0.0, 0.5, 0.5)


def test_duplicated_lines_are_consumed_one_by_one_in_order() -> None:
    gt = _document(_paragraph("同\n別\n同"), adapter="ground_truth")
    doc = _document(_paragraph("同\n同\n別"))

    # 2 つ目の「同」は doc の 2 つ目（位置 1）を取る → [0, 2, 1]、τ = (2 − 1) / 3
    assert reading_order_accuracy(gt, doc) == (round((1 / 3 + 1) / 2, 4), 1.0, 1.0)


def test_page_with_one_matched_line_is_excluded_from_tau() -> None:
    gt = Document(
        adapter="ground_truth",
        pages=[
            Page(number=1, blocks=[_paragraph("単独行")]),
            Page(number=2, blocks=[_paragraph("あ\nい\nう")]),
        ],
    )
    doc = Document(
        adapter="fake",
        pages=[
            Page(number=1, blocks=[_paragraph("単独行")]),
            Page(number=2, blocks=[_paragraph("う\nい\nあ")]),
        ],
    )

    # 1 ページ目は順序対が作れないので τ から除き、2 ページ目の 0.0 だけが残る
    assert reading_order_accuracy(gt, doc) == (0.0, 1.0, 1.0)


def test_reading_order_is_none_when_gt_has_no_line() -> None:
    # 段落も、行になる表のセルも無い GT（空セルだけの表）
    gt = _document(_table([["", "  "]]), adapter="ground_truth")

    assert reading_order_accuracy(gt, _document(_paragraph("あ\nい"))) == (None, None, None)


def test_reading_order_includes_table_rows() -> None:
    """表の行も ROA の行列に入る（表の行順が逆なら下がる）。"""
    gt = _document(_paragraph("見出し"), _table([["A", "B"], ["1", "2"]]), adapter="ground_truth")

    # 表の行が同じ順なら満点
    same = _document(_paragraph("見出し"), _table([["A", "B"], ["1", "2"]]))
    assert reading_order_accuracy(gt, same) == (1.0, 1.0, 1.0)

    # 表の行だけを入れ替えると位置は [0, 2, 1]。3 対のうち 1 対が逆転 → τ = (2 − 1) / 3
    swapped = _document(_paragraph("見出し"), _table([["1", "2"], ["A", "B"]]))
    assert reading_order_accuracy(gt, swapped) == (round((1 / 3 + 1) / 2, 4), 1.0, 1.0)


def test_line_with_one_character_difference_is_matched_by_fuzzy() -> None:
    """行末 1 文字違いの行が部分一致で対応付き、τ の対象に入る。"""
    gt = _document(_paragraph("あいうえお\nかきくけこ"), adapter="ground_truth")
    doc = _document(_paragraph("あいうえお\nかきくけさ"))

    # 「かきくけこ」対「かきくけさ」は類似度 1 − 1/5 = 0.8 でちょうど閾値。2 行とも
    # 対応するので順序対が作れ、完全一致だけなら None だった ROA が出る
    roa, match_rate, exact_rate = reading_order_accuracy(gt, doc)
    assert (roa, match_rate, exact_rate) == (1.0, 1.0, 0.5)
    assert exact_rate <= match_rate


def test_completely_different_line_is_not_matched_by_fuzzy() -> None:
    gt = _document(_paragraph("あいうえお\nかきくけこ"), adapter="ground_truth")
    doc = _document(_paragraph("あいうえお\nたちつてと"))

    # 類似度 0 の行は対応しない。対応は 1 行だけで順序対が作れず ROA は None
    assert reading_order_accuracy(gt, doc) == (None, 0.5, 0.5)


def test_short_line_is_matched_by_exact_only() -> None:
    gt = _document(_paragraph("あいうえ\nかきくけこ"), adapter="ground_truth")
    doc = _document(_paragraph("あいうお\nかきくけこ"))

    # 「あいうえ」は 4 文字（_ROA_FUZZY_MIN_CHARS 未満）なので類似度 0.75 でも対応しない
    assert reading_order_accuracy(gt, doc) == (None, 0.5, 0.5)


def test_exact_match_wins_over_a_more_similar_position() -> None:
    # 完全一致を先に確定するので、先に出てくる似た区間（オフセット 0）ではなく、
    # 完全一致する「あいうえお」の開始オフセット 5 を取る
    assert _matched_positions(["あいうえお"], ["あいうえか", "あいうえお"]) == [(0, 5, True)]


# 3 つの段落（各 3 行）。ROA の粒度テストで使う GT の見た目の行
_BLOCKS = [
    ["日本語の実務文書は段組みが", "多く、読み順の推定が難しい", "ことが知られている。"],
    ["本研究では八種類のパーサー", "を同じ規則で比較し、構造化", "品質と計算コストを測る。"],
    ["結果は表と段落に分けて示し", "文書の部分ごとに適した方式", "を明らかにする。"],
]


def _as_gt_lines(blocks: list[list[str]]) -> Document:
    """(a) GT と同じ見た目の 1 行 = 1 行。"""
    return _document(*(_paragraph("\n".join(lines)) for lines in blocks))


def _as_paragraph_lines(blocks: list[list[str]]) -> Document:
    """(b) 段落を 1 行で返す（docling / ppstructure 型。行の連結に空白が入る）。"""
    return _document(*(_paragraph(" ".join(lines)) for lines in blocks))


def _as_fragments(blocks: list[list[str]]) -> Document:
    """(c) GT の 1 行を 2〜3 個の断片に割って返す（字送りで行が割れるパーサー型）。"""
    pieces = []
    for lines in blocks:
        for index, line in enumerate(lines):
            size = len(line) // (2 + index % 2)
            pieces.extend(line[start : start + size] for start in range(0, len(line), size))
    return _document(_paragraph("\n".join(pieces)))


def test_reading_order_does_not_depend_on_output_line_granularity() -> None:
    """同じ内容を行単位・段落 1 行・断片で与えても、対応率と ROA がほぼ同じ（差 0.01 以内）。"""
    gt = _as_gt_lines(_BLOCKS)
    reordered = [_BLOCKS[0], _BLOCKS[2], _BLOCKS[1]]  # 2 段落目と 3 段落目が逆
    for blocks in (_BLOCKS, reordered):
        results = [
            reading_order_accuracy(gt, make(blocks))
            for make in (_as_gt_lines, _as_paragraph_lines, _as_fragments)
        ]
        roas = [roa for roa, _match_rate, _exact_rate in results]
        match_rates = [match_rate for _roa, match_rate, _exact_rate in results]
        assert match_rates == [1.0, 1.0, 1.0], results
        assert max(roas) - min(roas) <= 0.01, results
    # 並べ替えた入力では 3 形式とも満点から下がる
    assert all(roa < 1.0 for roa, _m, _e in results), results


def test_swapping_halves_of_a_paragraph_line_lowers_reading_order() -> None:
    gt = _as_gt_lines(_BLOCKS)
    lines = [line for block in _BLOCKS for line in block]
    half = len(lines) // 2
    same = _document(_paragraph(" ".join(lines)))
    swapped = _document(_paragraph(" ".join(lines[half:] + lines[:half])))

    assert reading_order_accuracy(gt, same) == (1.0, 1.0, 1.0)
    roa, match_rate, _exact_rate = reading_order_accuracy(gt, swapped)
    assert match_rate == 1.0
    assert roa < 0.5


def test_repeated_short_lines_take_successive_occurrences() -> None:
    """繰り返す短い行（「－」）は 1 か所目に集まらず、doc 側の出現順に 1 つずつ対応する。"""
    gt_lines = ["－", "あいうえおかきく", "－", "さしすせそたちつ", "－"]
    doc_lines = ["－ あいうえおかきく", "－ さしすせそたちつ", "－"]

    # 空白を除いた doc 文字列は「－あいうえおかきく－さしすせそたちつ－」。3 つの「－」は
    # どれも出力の行頭にある
    assert _matched_positions(gt_lines, doc_lines) == [
        (0, 0, True),
        (1, 1, True),
        (2, 9, True),
        (3, 10, True),
        (4, 18, True),
    ]


def test_short_line_does_not_take_characters_inside_a_longer_line() -> None:
    # 長い行を先に確定するので、GT の 1 行目「い」は「あいうえお」の中の「い」ではなく
    # 後ろにある単独の「い」を取る
    assert _matched_positions(["い", "あいうえお"], ["あいうえお", "い"]) == [
        (0, 5, True),
        (1, 0, True),
    ]


def test_total_is_the_mean_of_the_four_subscores() -> None:
    doc = _document(_paragraph("かきくけこ\nあいうえお"), _table([["A", "B"]]))

    scores = evaluate(_GT, doc)
    # GT の 4 行（段落 2 + 表 2）のうち 3 行が対応し、doc 側の位置は [1, 0, 2]
    assert scores["roa"] == round((1 / 3 + 1) / 2, 4)
    assert scores["roa_match_rate"] == 0.75
    assert scores["roa_exact_rate"] == 0.75
    # 対応率 0.75 は閾値以上なので、総合点に入る ROA は生の ROA のまま
    assert scores["roa_effective"] == scores["roa"]
    assert scores["total"] == round(
        (scores["cf"] + scores["sed"] + scores["tsf"] + scores["roa"]) / 4 * 100, 1
    )


def test_long_line_fuzzy_match_is_settled_before_short_lines() -> None:
    # 短い行「い」は長い行の部分一致を確定してから探すので、「あいうえおき」の中の「い」を
    # 奪わず、長い行「あいうえおか」は部分一致（1 文字違い）で対応する
    assert _matched_positions(["い", "あいうえおか"], ["あいうえおき", "い"]) == [
        (0, 6, True),
        (1, 0, False),
    ]


def test_very_short_line_does_not_match_in_the_middle_of_unrelated_text() -> None:
    # 3 文字以下の行はページの先頭・末尾か対応済みの区間に接する出現だけを認める。
    # 「3」は GT に無い「2023年の人口は増えた」の途中にしか無いので、出力をどう行に
    # 分けても対応しない
    gt_lines = ["3", "あいうえおかきく"]
    for doc_lines in (
        ["2023年の人口は増えた", "あいうえおかきく"],
        ["202", "3", "年の人口は増えた", "あいうえおかきく"],
    ):
        assert _matched_positions(gt_lines, doc_lines) == [(1, 12, True)], doc_lines


def test_very_short_lines_do_not_depend_on_output_line_splitting() -> None:
    """同じ連結文字列なら、出力の行の分け方によらず 3 文字以下の行の対応は同じ（#147）。"""
    gt_lines = ["ABC", "DEF"]
    merged = _matched_positions(gt_lines, ["XABCY", "ZDEFQ"])
    split = _matched_positions(gt_lines, ["X", "ABC", "Y", "Z", "DEF", "Q"])

    # GT に無い文字に挟まれた短い行は、独立した行で出力されていても対応しない
    assert merged == split == []


def test_very_short_line_matches_at_the_start_of_the_page() -> None:
    # 行頭の番号「1」に本文が続く出力（段落 1 行型）では、「1」はページ先頭、「2」は
    # 対応済みの長い行の直後に接するので対応する
    gt_lines = ["1", "インターネットに接続する", "2", "メールを設定する"]
    doc_lines = ["1 インターネットに接続する", "2 メールを設定する"]

    assert _matched_positions(gt_lines, doc_lines) == [
        (0, 0, True),
        (1, 1, True),
        (2, 13, True),
        (3, 14, True),
    ]


class _PageWithBlankLine(Page):
    """空白を除くと空になる行を返す Page（正規化を通らない入力に対する防御の確認用）。"""

    def lines(self) -> list[str]:
        return ["\u3000", *super().lines()]


def test_gt_line_that_is_empty_after_removing_whitespace_is_excluded() -> None:
    gt = Document(
        adapter="ground_truth",
        pages=[_PageWithBlankLine(number=1, blocks=[_paragraph("あいうえお\nかきくけこ")])],
    )
    doc = _document(_paragraph("あいうえお かきくけこ"))

    # 空行は母数に入らず、残る 2 行がどちらも対応する
    assert reading_order_accuracy(gt, doc) == (1.0, 1.0, 1.0)


def test_very_short_line_between_matched_lines_in_a_merged_paragraph() -> None:
    # 段落 1 行に連結されても、対応済みの長い行に挟まれた短い行は対応する
    # （行境界だけで判定すると、別々の行なら 1.0・連結すると 0.67 と粒度で対応率が変わる）
    gt_lines = ["甲乙丙丁戊己", "短行", "庚辛壬癸子丑"]
    separate = _matched_positions(gt_lines, gt_lines)
    merged = _matched_positions(gt_lines, ["甲乙丙丁戊己 短行 庚辛壬癸子丑"])

    assert len(separate) == len(merged) == 3
    assert [position for _, position, _ in merged] == [0, 6, 8]


def test_chain_of_very_short_lines_does_not_depend_on_granularity() -> None:
    # 3 文字以下の行が並んで連結されても、別々の行と同じ対応になる（対応のたびに境界が増える）
    gt_lines = ["甲乙丙", "丁戊己", "庚辛壬"]
    reordered = ["丁戊己", "甲乙丙", "庚辛壬"]
    gt = _document(_paragraph("\n".join(gt_lines)))
    separate = reading_order_accuracy(gt, _document(_paragraph("\n".join(reordered))))
    merged = reading_order_accuracy(gt, _document(_paragraph("".join(reordered))))

    assert separate == merged
    assert separate[1] == 1.0


def test_fuzzy_match_when_the_page_is_shorter_than_the_gt_line() -> None:
    # 出力が 1 文字落ちて GT の行より短くても、類似度が閾値以上なら対応する
    assert _matched_positions(["あいうえおか"], ["あいうえお"]) == [(0, 0, False)]


def test_identical_lines_get_full_marks_even_with_cross_boundary_occurrences() -> None:
    # 長い行が前の行との境界をまたいでも現れる場合でも、GT と同じ行の出力は満点になる
    lines = ["acaab", "aabaab"]
    doc = _document(_paragraph("\n".join(lines)))

    assert reading_order_accuracy(doc, doc) == (1.0, 1.0, 1.0)


# 20 行の GT。行は互いに似ていない（部分一致で対応しない）
_TWENTY_LINES = [
    f"第{index}行目の文章です" * 2 for index in "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"
]
_GT_TWENTY = _document(_paragraph("\n".join(_TWENTY_LINES)), adapter="ground_truth")


def test_low_match_rate_with_perfect_order_does_not_inflate_total() -> None:
    """対応率が低い ROA は総合点に効かない（数行が並んでいるだけで満点にしない）。"""
    # 2 行だけ正しい順で出す → ROA 1.0 / 対応率 0.1
    few = _document(_paragraph("\n".join(_TWENTY_LINES[:2])))
    scores = evaluate(_GT_TWENTY, few)
    assert (scores["roa"], scores["roa_match_rate"]) == (1.0, 0.1)
    assert scores["total"] == round((scores["cf"] + scores["sed"] + 0.0) / 3 * 100, 1)
    assert scores["roa_effective"] == 0.0


def test_zero_matches_is_not_better_than_few_matches() -> None:
    """対応行 0（ROA None）と数行だけ対応（ROA 0）は同じ規則で 0 として数える。"""
    # 対応 0 行: GT と無関係な行だけ
    none = evaluate(_GT_TWENTY, _document(_paragraph("まったく別の内容の行です")))
    # 対応 2 行: 逆順で出す → ROA 0.0 / 対応率 0.1
    reversed_two = evaluate(_GT_TWENTY, _document(_paragraph("\n".join(_TWENTY_LINES[1::-1]))))
    assert (none["roa"], none["roa_match_rate"]) == (None, 0.0)
    assert (reversed_two["roa"], reversed_two["roa_match_rate"]) == (0.0, 0.1)
    # 内容を多く再現した側（対応 2 行）の総合点が、対応 0 行より低くならない
    assert none["cf"] < reversed_two["cf"]
    assert none["total"] < reversed_two["total"]
    assert none["roa_effective"] == reversed_two["roa_effective"] == 0.0


def test_match_rate_at_threshold_keeps_raw_roa() -> None:
    """対応率がちょうど閾値（0.15）なら生の ROA をそのまま総合点に入れる。"""
    three = evaluate(_GT_TWENTY, _document(_paragraph("\n".join(_TWENTY_LINES[:3]))))
    assert (three["roa"], three["roa_match_rate"]) == (1.0, 0.15)
    assert three["roa_effective"] == 1.0


def test_effective_roa_is_none_only_when_gt_has_no_lines() -> None:
    assert effective_roa(None, None) is None
    assert effective_roa(None, 0.0) == 0.0
    assert effective_roa(0.9, 0.149) == 0.0
    assert effective_roa(0.9, 0.15) == 0.9


def test_effective_roa_excludes_unmeasurable_order_instead_of_zero() -> None:
    # 全行対応していても順序対が作れない（1 行だけの文書）なら、0 ではなく総合点から外す
    doc = _document(_paragraph("あいうえおかきく"))
    result = evaluate(doc, doc)

    assert result["roa"] is None
    assert result["roa_effective"] is None
    assert result["total"] == 100.0


def test_effective_roa_uses_the_unrounded_match_rate() -> None:
    # 151 / 1007 = 0.14995… は 4 桁に丸めると 0.15 だが、閾値未満として 0 にする
    from jdocbench.metrics import effective_roa

    assert effective_roa(1.0, 151 / 1007) == 0.0
    assert effective_roa(1.0, 0.15) == 1.0


def test_roa_counts_as_zero_when_gt_is_orderable_but_output_matches_one_line_per_page() -> None:
    # 5 ページ × GT 各 2 行、出力は各ページ 1 行だけ対応 → 対応率 0.5 でも ROA は出ない。
    # GT には順序対があるので、ROA を外さず 0 として数える（外すと平均が押し上げられる）
    gt = Document(
        adapter="ground_truth",
        pages=[
            Page(number=n, blocks=[_paragraph(f"第{n}ページの一行目です\n第{n}ページの二行目です")])
            for n in range(1, 6)
        ],
    )
    doc = Document(
        adapter="fake",
        pages=[
            Page(number=n, blocks=[_paragraph(f"第{n}ページの一行目です")]) for n in range(1, 6)
        ],
    )
    result = evaluate(gt, doc)

    assert result["roa"] is None
    assert result["roa_match_rate"] == 0.5
    assert result["roa_effective"] == 0.0
    assert result["total"] == round((result["cf"] + result["sed"] + 0.0) / 3 * 100, 1)


# --- JNF（docs/jnf.md） ---


def _jnf(gt_text: str, doc_text: str, *, vertical: bool = False) -> tuple[float | None, dict]:
    extra = {"writing_mode": "vertical"} if vertical else {}
    gt = _document(Block(type="paragraph", text=gt_text, extra=extra), adapter="ground_truth")
    return japanese_notation_fidelity(gt, _document(_paragraph(doc_text)))


def _counts(classes: dict, name: str) -> tuple[int, int, int]:
    return classes[name]["gt"], classes[name]["exact"], classes[name]["folded"]


def test_jnf_is_one_for_identical_text() -> None:
    text = "令和７年度（予算）は１２３億円、前年比△５％。⑴ 國の補助"
    value, classes = _jnf(text, text)

    assert value == 1.0
    assert _counts(classes, "fullwidth-alnum") == (5, 5, 0)
    assert _counts(classes, "kyujitai") == (1, 1, 0)
    assert all(counts["folded"] == 0 for counts in classes.values())


def test_jnf_drops_on_halfwidth_output_while_cf_stays_high() -> None:
    # 全角を半角にしただけ: CF は 1 文字ずつの置換で高いまま、JNF は表記の差として下がる
    gt_text = "本年度の歳出総額は前年度に比べ増加した（１２３億円）。内訳は別表のとおりである。"
    doc_text = (
        gt_text.replace("（", "(").replace("）", ")").translate(str.maketrans("１２３", "123"))
    )
    gt = _document(_paragraph(gt_text), adapter="ground_truth")
    doc = _document(_paragraph(doc_text))
    scores = evaluate(gt, doc)

    assert scores["cf"] > 0.85
    assert scores["jnf"] == 0.0
    assert _counts(scores["jnf_classes"], "fullwidth-symbol") == (2, 0, 2)
    assert _counts(scores["jnf_classes"], "fullwidth-alnum") == (3, 0, 3)


def test_jnf_is_none_without_notation_characters() -> None:
    assert _jnf("あいうえお ABC 123", "あいうえお ABC 123")[0] is None
    # GT が空でも None
    assert _jnf("", "１２３")[0] is None


def test_jnf_is_none_when_notation_characters_are_only_missing() -> None:
    # 欠落は分母に入れない（CF の領域）。整列した表記対象文字が無ければ None
    value, classes = _jnf("（１２３）億円", "億円")

    assert value is None
    assert _counts(classes, "fullwidth-alnum") == (3, 0, 0)


def test_jnf_folds_kyujitai() -> None:
    value, classes = _jnf("國立廣島大學の縣立", "国立広島大學の県立")

    # 國→国・廣→広・縣→県 は表の字なので folded。學は表に無い（v0.1）ので数えない
    assert _counts(classes, "kyujitai") == (3, 0, 3)
    assert value == 0.0


def test_jnf_folds_differences_that_change_length() -> None:
    # ⑴ ↔ (1) は 1 文字 ↔ 3 文字。差分区間の fold が一致すれば folded として 1 件数える
    value, classes = _jnf("第⑴項と第⑵項", "第(1)項と第⑵項")

    assert _counts(classes, "enclosed") == (2, 1, 1)
    assert value == 0.5


def test_jnf_folds_halfwidth_kana_with_voiced_mark() -> None:
    # ｶﾞ（2 文字）↔ ガ（1 文字）。クラスは GT 側の文字（半角カナ 2 文字）で数える
    value, classes = _jnf("ｶﾞｲﾄﾞ", "ガイド")

    assert _counts(classes, "halfwidth-kana") == (5, 0, 5)
    assert value == 0.0


def test_jnf_counts_notation_differences_only_on_output_side() -> None:
    # GT が半角・新字体で、出力だけが全角・旧字体: 出力側の文字のクラスで folded として数える
    value, classes = _jnf("売上(前年比)は県全体で増加した", "売上（前年比）は縣全体で増加した")

    assert _counts(classes, "fullwidth-symbol") == (0, 0, 2)
    assert _counts(classes, "kyujitai") == (0, 0, 1)
    assert value == 0.0


def test_jnf_folds_negative_mark_variants() -> None:
    # ▲ / ∆ は NFKC で畳まれないため異体表で △ にそろえる
    value, classes = _jnf("差引 △100 と ▲200", "差引 ∆100 と △200")

    assert _counts(classes, "negative-mark") == (2, 0, 2)
    assert value == 0.0


def test_jnf_ignores_replacements_that_do_not_fold() -> None:
    # `，` → `、` は NFKC でも表でも畳まれない（CF の領域）。数えない
    value, classes = _jnf("Ａ，Ｂ", "Ａ、Ｂ")

    assert _counts(classes, "fullwidth-alnum") == (2, 2, 0)
    assert _counts(classes, "fullwidth-symbol") == (1, 0, 0)
    assert value == 1.0


def test_jnf_excludes_alnum_in_vertical_gt_blocks() -> None:
    # 縦書きのブロックの英数字（全角の正立と縦中横を印字で区別できない）は
    # gt / exact / folded から除く
    value, classes = _jnf("令和７年１２月（官報）", "令和7年12月(官報)", vertical=True)

    assert _counts(classes, "fullwidth-alnum") == (0, 0, 0)
    # 英数字以外の表記差は縦書きでも数える
    assert _counts(classes, "fullwidth-symbol") == (2, 0, 2)
    assert value == 0.0

    # 同じテキストでも横書きなら英数字の表記差を数える
    _, horizontal = _jnf("令和７年１２月（官報）", "令和7年12月(官報)")
    assert _counts(horizontal, "fullwidth-alnum") == (3, 0, 3)


def test_jnf_skips_region_fold_when_region_contains_excluded_positions() -> None:
    # 横書きなら ⑴１２ ↔ (1)12 は区間単位の fold で一致し、3 文字とも folded
    _, horizontal = _jnf("第⑴１２条", "第(1)12条")
    assert _counts(horizontal, "enclosed") == (1, 0, 1)
    assert _counts(horizontal, "fullwidth-alnum") == (2, 0, 2)

    # 縦書きで差分区間に除外位置（１２）を含むときは区間単位の判定をせず、文字対だけで見る。
    # 文字対 ⑴ ↔ ( は fold で一致しないので数えない
    value, vertical = _jnf("第⑴１２条", "第(1)12条", vertical=True)
    assert _counts(vertical, "enclosed") == (1, 0, 0)
    assert _counts(vertical, "fullwidth-alnum") == (0, 0, 0)
    assert value is None


def test_jnf_vertical_exclusion_follows_gt_block_positions() -> None:
    # 縦書きブロックの位置だけを除く。横書きブロックの英数字は数える（位置の対応がずれない）
    gt = _document(
        _paragraph("第１条"),
        Block(type="paragraph", text="第２条", extra={"writing_mode": "vertical"}),
        _table([["第３条", "（４）"]]),
        adapter="ground_truth",
    )
    doc = _document(_paragraph("第1条\n第2条\n第3条 (4)"))
    value, classes = japanese_notation_fidelity(gt, doc)

    assert _counts(classes, "fullwidth-alnum") == (3, 0, 3)
    assert _counts(classes, "fullwidth-symbol") == (2, 0, 2)
    assert value == 0.0
    # 除外フラグの文字列は text() から空白類を除いたものと一致する
    chars, excluded = _jnf_gt_chars(gt)
    assert chars == "".join(gt.text().split())
    assert [c for c, skip in zip(chars, excluded, strict=True) if skip] == ["２"]


def test_jnf_does_not_change_total() -> None:
    gt = _document(_paragraph("（１２３）億円の増加\n内訳は別表"), adapter="ground_truth")
    doc = _document(_paragraph("(123)億円の増加\n内訳は別表"))
    scores = evaluate(gt, doc)

    assert scores["jnf"] == 0.0
    subscores = [scores[k] for k in ("cf", "sed", "tsf", "roa_effective") if scores[k] is not None]
    assert scores["total"] == round(sum(subscores) / len(subscores) * 100, 1)
