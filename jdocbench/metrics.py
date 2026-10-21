"""DSQS-JP 初版（企画書 §18〜19）。Deterministic 指標のみの純関数。

Ground Truth も正規化結果も IR の `Document` で受け取り、0〜1 のサブスコアと
0〜100 の総合点を返す。企画書 §18 の 10 サブスコアのうち、今のスプリントで
計算できる CF / SED / TSF / ROA だけを実装する（HP / SHA 等は IR に必要な情報が
入っていないため作らない）。TSF も TEDS ではなく「セル内容の再現率」の簡易版
（Sprint 5 計画）。総合点に入れる ROA は `effective_roa` で対応率を加味した値（#134）。
"""

import re
import unicodedata
from collections import Counter

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from jdocbench.ir import Block, Document, Page, collapse_whitespace

_SUBSCORE_DIGITS = 4
_TOTAL_DIGITS = 1
_ROA_FUZZY_THRESHOLD = 0.8
_ROA_FUZZY_MIN_CHARS = 5
_ROA_BOUNDARY_MAX_CHARS = 3
_ROA_USED = "\0"
_WHITESPACE = re.compile(r"[\s\u3000]+")
# 総合点で ROA を数える最低の対応率（GT 行の 15%）。CI の 7 文書 × 9 アダプタでは、
# 0 を除く対応率の下側が 0.089 以下（表専業の camelot が 3 組）と 0.214 以上に分かれ、
# その間に値が無い。docling / ppstructure の最小は 0.48 で十分上にある（#134）
_ROA_MIN_MATCH_RATE = 0.15


def _cell_set(rows: list[list[str]] | None) -> set[str]:
    """表のセル集合。各セルを `\n` で分割し連続空白を圧縮する（空文字と null は捨てる）。

    パーサーは縦に結合されたセルを `"1329\n681"` の 1 セルとして返すことがあるため、
    GT 側も doc 側も同じ規則で分割し、2 つのセルとして数える。
    """
    return {
        collapsed
        for row in rows or []
        for cell in row
        if isinstance(cell, str)
        for part in cell.split("\n")
        if (collapsed := collapse_whitespace(part))
    }


def _tables_by_page(doc: Document) -> dict[int, list[Block]]:
    """ページ番号 → そのページの table ブロック（出現順）。

    **セル集合が空の表は検出とみなさない**（GT 側も doc 側も同じ規則）。OCR オフの
    パーサーはスキャン PDF に対して `rows: []` の表を返すことがあり、中身が無いまま
    表数だけ GT と一致して SED が満点になってしまうのを防ぐ。
    """
    tables: dict[int, list[Block]] = {}
    for page in doc.pages:
        tables.setdefault(page.number, []).extend(
            block for block in page.blocks if block.type == "table" and _cell_set(block.rows)
        )
    return tables


def _jaccard(left: set[str], right: set[str]) -> float:
    """セル集合どうしの Jaccard 係数。どちらかが空なら 0（空の表は対応させない）。"""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def content_fidelity(gt: Document, doc: Document) -> float:
    """CF — Content Fidelity。文書テキストの正規化 Levenshtein 距離の裏返し。

    比較対象は `Document.text()`（段落と表の行を読み順に平坦化した列の連結）。
    両方空なら 1.0。
    """
    gt_text = gt.text()
    doc_text = doc.text()
    if not gt_text and not doc_text:
        return 1.0
    return round(1.0 - Levenshtein.normalized_distance(gt_text, doc_text), _SUBSCORE_DIGITS)


def structural_element_detection(gt: Document, doc: Document) -> float:
    """SED — Structural Element Detection。表検出の F1。

    ページごとに TP = min(GT の表数, doc の表数)、FP = doc の余り、FN = GT の不足を
    集計する（IR に見出し・図が無いため、初版は表だけを数える）。GT に表が 1 つも
    無いときは doc にも無ければ 1.0、あれば 0.0。

    セルが空の表は検出とみなさない（`_tables_by_page`）。
    """
    gt_tables = _tables_by_page(gt)
    doc_tables = _tables_by_page(doc)
    true_positive = false_positive = false_negative = 0
    for number in gt_tables.keys() | doc_tables.keys():
        expected = len(gt_tables.get(number, []))
        found = len(doc_tables.get(number, []))
        matched = min(expected, found)
        true_positive += matched
        false_positive += found - matched
        false_negative += expected - matched

    if true_positive + false_negative == 0:  # GT に表が無い
        return 1.0 if false_positive == 0 else 0.0
    if true_positive == 0:
        return 0.0
    precision = true_positive / (true_positive + false_positive)
    recall = true_positive / (true_positive + false_negative)
    return round(2 * precision * recall / (precision + recall), _SUBSCORE_DIGITS)


def _matching_tables(gt_cells: set[str], doc_cells: list[set[str]]) -> list[int]:
    """GT の 1 表に対応する doc の表の添字（Jaccard 降順、同点は先に出てくる表）。

    Jaccard が 0 の表（共通セルなし・空集合）は対象外。残りを降順に見て、和集合に
    加えるとセル再現率 |GT ∩ ∪doc| / |GT| が上がる間だけ加える（貪欲）。上がらない表が
    現れた時点で止める（それ以降は Jaccard がさらに低く、断片としても寄与しにくい）。
    """
    ranked = sorted(
        (index for index, cells in enumerate(doc_cells) if _jaccard(gt_cells, cells) > 0),
        key=lambda index: -_jaccard(gt_cells, doc_cells[index]),
    )
    chosen: list[int] = []
    union: set[str] = set()
    recalled = 0
    for index in ranked:
        merged = union | doc_cells[index]
        if len(gt_cells & merged) <= recalled:  # 加えても再現率が上がらない
            break
        chosen.append(index)
        union, recalled = merged, len(gt_cells & merged)
    return chosen


def table_matches(gt: Document, doc: Document) -> list[dict]:
    """GT の表（ページ順・出現順）ごとの対応付けとセル再現率。

    **分割・結合に寛容**。GT の 1 表がパーサーでは複数の断片に、または複数の GT 表が
    1 表に、なっていても内容が再現されていれば減点しない。断片化そのものは SED
    （表数の F1）で減点される。

    同一ページの doc の表を Jaccard 降順に見て、再現率が上がる間だけ和集合に加え
    （`_matching_tables`）、セル再現率 = |GT ∩ ∪doc| / |GT| を取る。1 つの doc の表は
    複数の GT の表が共有してよく、共有されていれば `shared` が True。対応する表が
    無ければ `doc_index` は None で 0。戻り値は `metrics/<adapter>.json` の
    `tsf_tables` にそのまま載る。
    """
    doc_tables = _tables_by_page(doc)
    matches: list[dict] = []
    for number, gt_tables in sorted(_tables_by_page(gt).items()):
        doc_cells = [_cell_set(table.rows) for table in doc_tables.get(number, [])]
        page_matches: list[dict] = []
        usage: Counter[int] = Counter()
        for gt_index, gt_table in enumerate(gt_tables):
            gt_cells = _cell_set(gt_table.rows)
            indices = _matching_tables(gt_cells, doc_cells)
            usage.update(indices)
            union = set().union(*(doc_cells[index] for index in indices)) if indices else set()
            # GT のセルが空の表は indices も空になる（0 除算しない）
            score = len(gt_cells & union) / len(gt_cells) if gt_cells else 0.0
            page_matches.append(
                {
                    "page": number,
                    "gt_index": gt_index,
                    "doc_index": indices[0] if indices else None,
                    "doc_indices": indices,
                    "shared": False,
                    "score": round(score, _SUBSCORE_DIGITS),
                }
            )
        for match in page_matches:  # 他の GT 表とも対応した doc 表があれば共有
            match["shared"] = any(usage[index] > 1 for index in match["doc_indices"])
        matches.extend(page_matches)
    return matches


def _mean_table_score(matches: list[dict]) -> float | None:
    """表ごとのセル再現率の平均。GT に表が無ければ None（総合点から除外する）。"""
    if not matches:
        return None
    return round(sum(match["score"] for match in matches) / len(matches), _SUBSCORE_DIGITS)


def table_structural_fidelity(gt: Document, doc: Document) -> float | None:
    """TSF — Table Structural Fidelity（簡易版）。GT の表ごとのセル再現率の平均。

    **内容再現率であり、位置関係・結合セルの構造は測らない**（セルがどの行・列にあるか、
    rowspan / colspan が合っているかは TEDS 導入時に扱う）。表の対応付けは
    `table_matches` を参照。GT に表が無ければ None。
    """
    return _mean_table_score(table_matches(gt, doc))


def _page_lines(doc: Document) -> dict[int, list[str]]:
    """ページ番号 → そのページの行（読み順。段落の行と表の行を含む）。

    行の作り方は `Page.lines()`（= `Document.lines()` をページ単位にしたもの）に任せる。
    ROA はページをまたいだ並べ替えを見ないためページごとに持ち、同じページ番号の Page が
    複数あれば出現順に連結する。
    """
    lines: dict[int, list[str]] = {}
    for page in doc.pages:
        lines.setdefault(page.number, []).extend(page.lines())
    return lines


def _strip_whitespace(text: str) -> str:
    """全角スペースを含む空白類をすべて取り除く（ROA の対応付け専用の正規化）。

    日本語は語の区切りに空白を使わないため、パーサーが行を連結するときに入れる空白
    （`"変 換"`）や字送りの空白（`"（ LLM ）"`）で位置が見つからなくなるのを防ぐ。
    空白の差は CF で測る。
    """
    return _WHITESPACE.sub("", text)


def _mask(text: str, start: int, end: int) -> str:
    """doc 文字列の `[start, end)` を使用済みにする（GT の行に現れない文字で塗る）。"""
    return text[:start] + _ROA_USED * (end - start) + text[end:]


def _find_exact(line: str, text: str, edges: set[int]) -> int:
    """`text` の未使用範囲で `line` が最初に現れる位置（無ければ -1）。

    `_ROA_BOUNDARY_MAX_CHARS`（3）文字以下の行は、境界から始まるか境界で終わる出現だけを
    認める。`edges` は連結文字列の中の境界 = ページの先頭・末尾と、既に対応した区間の
    前端・後端（出力の行の切れ目は使わない。#147）。
    """
    start = text.find(line)
    if len(line) > _ROA_BOUNDARY_MAX_CHARS:
        return start
    while start >= 0 and start not in edges and start + len(line) not in edges:
        start = text.find(line, start + 1)
    return start


def _find_line_aligned(line: str, text: str, starts: set[int]) -> int:
    """`line` の未使用の出現のうち、出力の行頭から始まるものを優先して返す（無ければ最初の出現）。

    出力の行と GT の行が一致しているのに、行の境界をまたいだ偶然の出現を先に取って隣の行を
    塗りつぶすのを防ぐ（GT と同じ行で返したパーサーが満点にならない事態を避ける）。
    `starts` は出力の行頭と、既に対応した区間の後端。出力の行の切れ目を使うのはこの
    優先順位だけで、行頭の出現が無ければ最初の出現を取るので、この行が対応するかどうかは
    行の分け方に依存しない。ただし同じ文字列が複数あるとき、どの出現を取るかは変わり
    うるので、塗った範囲を通じて後の行の対応が変わることはある（受け入れた制約。#147）。
    """
    first = start = text.find(line)
    while start >= 0:
        if start in starts:
            return start
        start = text.find(line, start + 1)
    return first


def _matched_positions(gt_lines: list[str], doc_lines: list[str]) -> list[tuple[int, int, bool]]:
    """対応付いた (GT 行の添字, doc 側の文字オフセット, 完全一致か) を GT の順に並べたもの。

    **出力の行の粒度に依存しない**対応付け。doc の行を空白を除いて 1 本の文字列に連結し
    （区切りは入れない）、GT の各行（同じく空白を除く）がその中のどこに現れるかを探す。
    段落を 1 行で返すパーサーでも、1 行を複数の断片に割るパーサーでも、GT の見た目の
    1 行と同じ行で返すパーサーでも、同じ文字列になるので同じ位置が得られる。
    空白を除くため「1 2 3」と「12 3」は区別しない（受け入れた制約。空白の差は CF で測る）。
    貪欲法（長い行から順に未使用の出現を取る）なので、隣り合う GT 行の文字列が重なり合う
    場合（`aaaab` と `aaabaa` を連結した出力など）は、連結した出力で片方を取り損ねることが
    ある（受け入れた制約。全体最適な割り当ては行わない。実データ 42 組では該当なし）。
    空白を除くと空になる GT 行は対象外（呼び出し側で母数からも除く）。

    3 段階で対応付け、どの段階でも長い GT 行から順に（同じ長さは GT 順に）処理する。
    対応した範囲は GT の行に現れない文字で塗って使用済みにし、以降の探索では一致しない。

    1. `_ROA_FUZZY_MIN_CHARS`（5）文字以上の行の**完全一致**（部分文字列として現れるか）。
       まだ使っていない範囲のうち、出力の行頭から始まる出現を優先し、無ければ最初の
       出現を取る。同じ文字列の GT 行は doc 側の出現順に 1 つずつ消費され、1 か所に
       集まらない
    2. 1 で余った 5 文字以上の行の**部分一致**。`rapidfuzz.fuzz.partial_ratio_alignment`
       で doc 文字列の中の最も似た区間を探し、類似度（Indel 距離による正規化類似度）が
       `_ROA_FUZZY_THRESHOLD`（0.8）以上なら対応とする
    3. 5 文字未満の短い行の**完全一致のみ**（たまたま似てしまうので部分一致はしない）。
       長い行の対応を確定してから探すので、短い行が長い行の中の文字を奪わない。
       さらに `_ROA_BOUNDARY_MAX_CHARS`（3）文字以下の行は、**境界**から始まるか境界で
       終わる出現に限る（`_find_exact`）。境界 = ページ（連結文字列）の先頭・末尾と、
       既に対応した GT 行の区間の前端・後端。どれも連結文字列と GT だけで決まり、出力の
       行の切れ目は使わないので、行の分け方で対応が変わらない（#147。GT `ABC` `DEF` に
       対し出力 `XABCY` `ZDEFQ` も `X` `ABC` `Y` `Z` `DEF` `Q` も 0 行）。「3」が
       「2023年」の中の「3」に当たるような偶然の一致で対応率が水増しされるのを防ぎつつ、
       ページ先頭や対応済みの行に接する短い行（行頭の番号「1」に本文が続く
       「1 インターネット…」、長い行に挟まれた「…GT 行A」「短行」「GT 行B…」）は対応
       させる。対応が付くたびに境界が増えるので、新しい対応が出なくなるまで繰り返す。
       GT に無い文字列（ヘッダー・柱など）に挟まれた短い行は、出力で独立した 1 行でも
       対応しない（受け入れた制約。行の切れ目を使うと粒度依存が戻る）

    位置は対応した区間の開始オフセット。
    """
    # 出力の行頭オフセットと対応した区間の後端（長い行の完全一致で、どの出現を取るかの
    # 優先順位にだけ使う）
    starts: set[int] = set()
    parts: list[str] = []
    offset = 0
    for line in doc_lines:
        part = _strip_whitespace(line)
        if not part:
            continue
        starts.add(offset)
        offset += len(part)
        parts.append(part)
    text = "".join(parts)
    # 3 文字以下の行の境界（ページの先頭・末尾と、対応した区間の前端・後端）
    edges = {0, len(text)}
    stripped = [_strip_whitespace(line) for line in gt_lines]
    order = sorted(
        (index for index, line in enumerate(stripped) if line),
        key=lambda index: (-len(stripped[index]), index),
    )
    long_lines = [index for index in order if len(stripped[index]) >= _ROA_FUZZY_MIN_CHARS]
    short_lines = [index for index in order if len(stripped[index]) < _ROA_FUZZY_MIN_CHARS]

    matched: list[tuple[int, int, bool]] = []
    unmatched: list[int] = []
    for gt_index in long_lines:
        line = stripped[gt_index]
        start = _find_line_aligned(line, text, starts)
        if start < 0:
            unmatched.append(gt_index)
            continue
        matched.append((gt_index, start, True))
        text = _mask(text, start, start + len(line))
        edges.update((start, start + len(line)))
        starts.add(start + len(line))  # 対応した区間の直後も行頭とみなして優先する

    for gt_index in unmatched:
        line = stripped[gt_index]
        # doc 側が GT の行より短いと partial_ratio は doc 側を GT の行の中で探してしまうので、
        # その場合は doc 全体との類似度で判定する（1 文字落ちた短いページも対応させる）
        if len(text) < len(line):
            if fuzz.ratio(line, text) >= _ROA_FUZZY_THRESHOLD * 100:
                matched.append((gt_index, 0, False))
                text = _mask(text, 0, len(text))
            continue
        alignment = fuzz.partial_ratio_alignment(
            line, text, score_cutoff=_ROA_FUZZY_THRESHOLD * 100
        )
        if alignment is None:
            continue
        matched.append((gt_index, alignment.dest_start, False))
        text = _mask(text, alignment.dest_start, alignment.dest_end)
        edges.update((alignment.dest_start, alignment.dest_end))

    # 短い行は、対応が付くたびに境界が増えて隣の短い行も対応できるようになるので、
    # 新しい対応が出なくなるまで繰り返す（連結された短い行の並びを粒度によらず対応させる）
    pending = short_lines
    while pending:
        remaining: list[int] = []
        for gt_index in pending:
            line = stripped[gt_index]
            start = _find_exact(line, text, edges)
            if start < 0:
                remaining.append(gt_index)
                continue
            matched.append((gt_index, start, True))
            text = _mask(text, start, start + len(line))
            edges.update((start, start + len(line)))
        if len(remaining) == len(pending):
            break
        pending = remaining
    return sorted(matched)


def _kendall_tau_a(positions: list[int]) -> float:
    """並び順の一致度 τ-a = (concordant − discordant) / (n(n−1)/2)。−1〜1。

    行数は 1 ページ 100 行程度なので総当たり（O(n²)）で足りる。
    """
    concordant = discordant = 0
    for index, left in enumerate(positions):
        for right in positions[index + 1 :]:
            if left < right:
                concordant += 1
            elif left > right:
                discordant += 1
    total_pairs = len(positions) * (len(positions) - 1) // 2
    return (concordant - discordant) / total_pairs


def _reading_order(
    gt: Document, doc: Document
) -> tuple[float | None, float | None, float | None, float | None]:
    """ROA — Reading Order Accuracy。`(roa, roa_match_rate, roa_exact_rate)` を返す。

    行は `Page.lines()` の列で、**段落の行だけでなく表の行も含む**（CF と同じ平坦化を使い、
    表の中身をどのブロックに入れるかで順序の評価が変わらないようにする）。

    ページごとに GT の各行が doc のどこに現れるかを探し（`_matched_positions`）、対応付いた
    GT 行の doc 側位置（空白を除いて連結した doc 文字列の中の文字オフセット）を GT の順に
    並べた列の Kendall τ-a を `(τ + 1) / 2` で 0〜1 に写す。対応が 1 行以下のページは
    順序対が作れないので τ から除く。ページの値を対応行数で加重平均したものが ROA。

    **対応付けは出力の行の粒度に依存しない**。doc の行を連結した文字列の中で位置を探す
    ので、段落を 1 行で返すパーサー（GT の複数行が出力の 1 行に入る）も、1 行を断片に
    割るパーサー（GT の 1 行が出力の複数行にまたがる）も、GT と同じ行で返すパーサーと
    同じ扱いになる（GT と全パーサーに同じ規則。#143）。完全一致（部分文字列）を先に
    確定し、余った行を類似度 **0.8 以上**で部分一致させる。部分一致は字送り・誤字で
    完全一致しない行を救うためで、5 文字未満の短い行は誤対応しやすいので完全一致のみ
    （長い行の対応を確定した後に探す）。3 文字以下の行はページの先頭・末尾か、対応済みの
    行の区間に接する出現に限る（「3」が「2023年」の中で一致するような偶然の一致で対応率を
    水増ししないため。出力の行の切れ目は使わない）。空白を除くと空になる GT 行は母数から除く。

    `roa_match_rate` は GT の行のうち doc 中に位置が見つかった行の割合（部分一致を含む。
    全ページ合計）、`roa_exact_rate` はそのうち完全一致（空白を除いた部分文字列として
    現れる）だけの割合（`roa_exact_rate <= roa_match_rate`）。両者の差は部分一致で
    どれだけ救ったかを示す。**τ は対応付いた行だけで計算するため、`roa_match_rate` が
    低いときは信頼度が低い**（文字化けや欠落で数行しか対応しなければ、その数行が並んで
    いるだけで τ が 1.0 になりうる）。表の行が列の順に出力されるなど、GT の行が連続した
    文字列として現れない場合も対応しない。

    GT に行が無ければ 3 つとも None。GT に行はあるが τ を計算できるページが 1 つも
    無ければ ROA だけ None（一致率は残る。総合点での扱いは `effective_roa`）。
    """
    # 空白を除くと空になる GT 行は位置を探せないので母数からも除く
    gt_lines = {
        number: [line for line in lines if _strip_whitespace(line)]
        for number, lines in _page_lines(gt).items()
    }
    doc_lines = _page_lines(doc)
    gt_total = sum(len(lines) for lines in gt_lines.values())
    if gt_total == 0:
        return None, None, None, None

    matched_total = 0
    exact_total = 0
    weighted_sum = 0.0
    tau_weight = 0
    for number, lines in gt_lines.items():
        pairs = _matched_positions(lines, doc_lines.get(number, []))
        matched_total += len(pairs)
        exact_total += sum(1 for _gt_index, _position, exact in pairs if exact)
        positions = [position for _gt_index, position, _exact in pairs]
        if len(positions) < 2:  # 順序対が作れないページは τ から除く
            continue
        weighted_sum += (_kendall_tau_a(positions) + 1) / 2 * len(positions)
        tau_weight += len(positions)

    roa = round(weighted_sum / tau_weight, _SUBSCORE_DIGITS) if tau_weight else None
    return (
        roa,
        round(matched_total / gt_total, _SUBSCORE_DIGITS),
        round(exact_total / gt_total, _SUBSCORE_DIGITS),
        matched_total / gt_total,
    )


def reading_order_accuracy(
    gt: Document, doc: Document
) -> tuple[float | None, float | None, float | None]:
    """ROA — Reading Order Accuracy。`(roa, roa_match_rate, roa_exact_rate)` を返す（定義は
    `_reading_order`）。総合点の判定には丸める前の対応率を使うので `evaluate` は
    `_reading_order` を直接呼ぶ。
    """
    roa, match_rate, exact_rate, _raw_match_rate = _reading_order(gt, doc)
    return roa, match_rate, exact_rate


def _gt_has_orderable_page(gt: Document) -> bool:
    """GT に順序対を作れるページ（空白を除いて空でない行が 2 行以上）が 1 つでもあるか。"""
    return any(
        sum(1 for line in lines if _strip_whitespace(line)) >= 2
        for lines in _page_lines(gt).values()
    )


def effective_roa(
    roa: float | None, roa_match_rate: float | None, gt_orderable: bool = True
) -> float | None:
    """総合点に入れる ROA。対応率が `_ROA_MIN_MATCH_RATE` 未満なら 0、それ以外は ROA。

    ROA は対応付いた行だけで計算するので、数行しか対応しなければその数行の並びだけで
    1.0 になりうる。そこで **GT 行の 15% 未満しか対応しない ROA は 0 として数える**。
    対応行が 0 で ROA が None の場合も対応率 0 なので同じく 0 とし、総合点から外さない
    （外すと「対応が 0 行」のほうが「数行対応して ROA 0」より総合点が高くなる）。
    対応率は閾値以上なのに ROA が None のときは 2 通りに分ける（#146）:

    - GT 自体に順序対を作れるページが無い（`gt_orderable` が False。1 行だけの文書など）
      → 順序を測れない文書なので None を返し総合点から外す
    - GT には順序対があるのに、出力側がどのページも 1 行以下しか対応しない → 0。
      外すと「各ページ 1 行だけ対応させる」出力が ROA を除いた平均で得をする

    GT に行が無い（対応率も None）ときも None。
    `roa_match_rate` には丸める前の値を渡す（境界付近で丸めにより判定がずれないように）。
    """
    if roa_match_rate is None:
        return None
    if roa_match_rate < _ROA_MIN_MATCH_RATE:
        return 0.0
    if roa is None:
        return 0.0 if gt_orderable else None
    return roa


# JNF の fold 表の版（docs/jnf.md）。異体表・旧字体表・文字クラスを変えたら上げる（値が動くため）
JNF_VERSION = "1"
JNF_CLASSES = (
    "fullwidth-alnum",
    "fullwidth-symbol",
    "enclosed",
    "halfwidth-kana",
    "kyujitai",
    "negative-mark",
    "ditto",
)
# 記号の異体表。NFKC では畳まれない（`″` は NFKC で `′′` に分解されるので NFKC より先に置く）
_JNF_VARIANTS = {"▲": "△", "∆": "△", "▵": "△", "▴": "△", "″": "〃"}
# 旧字体 → 新字体（estat の GT に出る字から。足すと値が動くので JNF_VERSION を上げる）
_JNF_KYUJITAI = dict(
    zip("國縣體齡關當區觀帶收囘總萬繩兒廣靜內", "国県体齢関当区観帯収回総万縄児広静内", strict=True)
)


def _jnf_fold(text: str) -> str:
    """JNF の比較用の畳み込み: 記号の異体表 → NFKC → 旧字体表。比較だけに使う。"""
    text = "".join(_JNF_VARIANTS.get(char, char) for char in text)
    return "".join(_JNF_KYUJITAI.get(char, char) for char in unicodedata.normalize("NFKC", text))


def _jnf_class(char: str) -> str | None:
    """表記対象文字のクラス（docs/jnf.md「文字クラス」）。対象外なら None。"""
    code = ord(char)
    if 0xFF10 <= code <= 0xFF19 or 0xFF21 <= code <= 0xFF3A or 0xFF41 <= code <= 0xFF5A:
        return "fullwidth-alnum"
    if 0xFF01 <= code <= 0xFF5E or 0xFFE0 <= code <= 0xFFE6:
        return "fullwidth-symbol"
    if (
        0x2460 <= code <= 0x24FF
        or 0x2776 <= code <= 0x2793
        or 0x3251 <= code <= 0x325F
        or 0x32B1 <= code <= 0x32BF
        or 0x2160 <= code <= 0x217F
    ):
        return "enclosed"
    if 0xFF61 <= code <= 0xFF9F:
        return "halfwidth-kana"
    if char in _JNF_KYUJITAI:
        return "kyujitai"
    if char in "△▲∆▵▴":
        return "negative-mark"
    if char in "〃″":
        return "ditto"
    return None


def _is_alnum(char: str) -> bool:
    """縦中横の除外対象（ASCII と全角の英数字）か。"""
    return (char.isascii() and char.isalnum()) or _jnf_class(char) == "fullwidth-alnum"


def _jnf_gt_chars(gt: Document) -> tuple[str, list[bool]]:
    """GT の `text()` から空白類を除いた文字列と、位置ごとの除外フラグ（縦中横）。

    `Document.text()` はブロックごとの行の連結なので、ブロック単位に同じ規則
    （`Page.lines()`）で行を作って空白を除き、つなげれば `text()` から空白を除いたものと一致する。
    GT の block の `extra.writing_mode` が `"vertical"` なら、その英数字の位置を除外する。
    """
    parts: list[str] = []
    excluded: list[bool] = []
    for page in gt.pages:
        for block in page.blocks:
            extra = block.extra if isinstance(block.extra, dict) else {}
            vertical = extra.get("writing_mode") == "vertical"
            for line in Page(number=page.number, blocks=[block]).lines():
                stripped = _WHITESPACE.sub("", line)
                parts.append(stripped)
                excluded.extend(vertical and _is_alnum(char) for char in stripped)
    return "".join(parts), excluded


def japanese_notation_fidelity(gt: Document, doc: Document) -> tuple[float | None, dict]:
    """JNF — Japanese Notation Fidelity（docs/jnf.md が定義の正）。Total には入れない。

    GT と出力の `text()`（CF と同じ比較対象）から空白類を除いて opcodes で整列し、
    表記対象文字を `equal` 区間なら exact、差分区間（連続する equal 以外の区間）で fold が
    一致すれば folded として数える。区間の fold が一致しなければ replace の文字対ごとに見る。
    欠落・fold しても一致しない置換は数えない（CF の領域）。クラスは GT 側の文字、GT 側が
    対象外なら出力側の文字で決める。縦中横の除外位置は gt / exact / folded のどれにも数えない。

    返り値は `(JNF, {class: {"gt", "exact", "folded"}})`。JNF = 1 − folded / (exact + folded)。
    GT のテキストが空、または exact + folded = 0 なら None。
    """
    g, excluded = _jnf_gt_chars(gt)
    d = _WHITESPACE.sub("", doc.text())
    classes = {name: {"gt": 0, "exact": 0, "folded": 0} for name in JNF_CLASSES}
    for char, skip in zip(g, excluded, strict=True):
        if not skip and (name := _jnf_class(char)):
            classes[name]["gt"] += 1
    if not g:
        return None, classes

    ops = list(Levenshtein.opcodes(g, d))
    i = 0
    while i < len(ops):
        op = ops[i]
        if op.tag == "equal":
            for pos in range(op.src_start, op.src_end):
                if not excluded[pos] and (name := _jnf_class(g[pos])):
                    classes[name]["exact"] += 1
            i += 1
            continue
        j = i
        while j < len(ops) and ops[j].tag != "equal":
            j += 1
        region = ops[i:j]
        i = j
        gt_start, gt_end = region[0].src_start, region[-1].src_end
        gt_part = g[gt_start:gt_end]
        doc_part = d[region[0].dest_start : region[-1].dest_end]
        # 区間単位: ⑴ ↔ (1)、ｶﾞ ↔ ガ のように fold で長さが変わる差を拾う。
        # 除外位置を含む区間は区間単位の判定をせず、文字対の判定だけにする
        if (
            gt_part
            and doc_part
            and not any(excluded[gt_start:gt_end])
            and _jnf_fold(gt_part) == _jnf_fold(doc_part)
        ):
            names = [n for c in gt_part if (n := _jnf_class(c))] or [
                n for c in doc_part if (n := _jnf_class(c))
            ]
            for name in names:
                classes[name]["folded"] += 1
            continue
        for sub in region:
            if sub.tag != "replace":
                continue
            for offset, doc_char in enumerate(d[sub.dest_start : sub.dest_end]):
                pos = sub.src_start + offset
                if excluded[pos]:
                    continue
                gt_char = g[pos]
                name = _jnf_class(gt_char) or _jnf_class(doc_char)
                if name and _jnf_fold(gt_char) == _jnf_fold(doc_char):
                    classes[name]["folded"] += 1

    exact = sum(counts["exact"] for counts in classes.values())
    folded = sum(counts["folded"] for counts in classes.values())
    if exact + folded == 0:
        return None, classes
    return round(1 - folded / (exact + folded), _SUBSCORE_DIGITS), classes


def evaluate(gt: Document, doc: Document) -> dict:
    """DSQS-JP 初版のサブスコアと総合点。総合点は存在するサブスコアの平均 × 100。

    総合点に入るのは CF / SED / TSF と `roa_effective`（`effective_roa`）の 4 つ。
    生の `roa` と `roa_match_rate` / `roa_exact_rate`（ROA の信頼度の目安）、
    `tsf_tables`（TSF の内訳）は内訳として残す。
    JNF（`jnf` / 内訳 `jnf_classes` / fold 表の版 `jnf_version`）は別の列として返し、
    総合点には入れない（docs/jnf.md）。
    """
    cf = content_fidelity(gt, doc)
    sed = structural_element_detection(gt, doc)
    matches = table_matches(gt, doc)
    tsf = _mean_table_score(matches)
    roa, roa_match_rate, roa_exact_rate, raw_match_rate = _reading_order(gt, doc)
    roa_effective = effective_roa(roa, raw_match_rate, _gt_has_orderable_page(gt))
    subscores = [score for score in (cf, sed, tsf, roa_effective) if score is not None]
    jnf, jnf_classes = japanese_notation_fidelity(gt, doc)
    return {
        "cf": cf,
        "sed": sed,
        "tsf": tsf,
        "tsf_tables": matches,
        "roa": roa,
        "roa_match_rate": roa_match_rate,
        "roa_exact_rate": roa_exact_rate,
        "roa_effective": roa_effective,
        "total": round(sum(subscores) / len(subscores) * 100, _TOTAL_DIGITS),
        "jnf": jnf,
        "jnf_classes": jnf_classes,
        "jnf_version": JNF_VERSION,
    }
