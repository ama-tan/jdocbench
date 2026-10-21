"""表の HTML（`<table><tr><td rowspan= colspan=>`）を IR の行 × 列にする（正規化の共通処理）。

PP-Structure（`block_content` / `pred_html`）・Marker（`Table` ブロックの `html`）・Unstructured
（`text_as_html`）が使う。
標準ライブラリの `html.parser` で分解し、結合セルは IR の規約（`Block.rows`）どおり範囲の先頭の
位置にだけ文字列を置く（範囲内の他の位置は空文字。`groundtruth/README.md` と同じ）。
"""

from html.parser import HTMLParser

# rowspan / colspan の上限。壊れた HTML で巨大なグリッドを作らないための歯止め
_MAX_SPAN = 1000


def _span(attrs: list[tuple[str, str | None]], name: str) -> int:
    """`rowspan` / `colspan` の値。数値でなければ 1。"""
    for key, value in attrs:
        if key == name and value is not None and value.strip().isdigit():
            return min(max(int(value), 1), _MAX_SPAN)
    return 1


class _TableHtmlParser(HTMLParser):
    """`<table>` の HTML を `<tr>` / `<td>` / `<th>` に分解して行 × 列にする。

    セル内の `<br>` は改行（`\\n`）にする（Marker はセル内の複数行を `<br>` で区切る。
    pdfplumber 等がセル内の改行を `\\n` で返すのと同じ扱い。区切りを捨てて連結すると別々の行の
    文字列が 1 語につながる）。他のタグ（`<b>` 等）は無視して文字列だけを拾う。閉じタグが無い
    セルも次のセル・行の開始で閉じる（PP-Structure / Marker の出力は整形式だが、壊れても
    止まらないようにする）。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # 行ごとの (rowspan, colspan, セル文字列)。配置は rows() でまとめて行う
        self._rows: list[list[tuple[int, int, str]]] = []
        self._text: list[str] | None = None
        self._span: tuple[int, int] = (1, 1)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._close_cell()
            self._rows.append([])
        elif tag in ("td", "th"):
            self._close_cell()
            if not self._rows:
                self._rows.append([])  # `<tr>` の無い表
            self._span = (_span(attrs, "rowspan"), _span(attrs, "colspan"))
            self._text = []
        elif tag == "br" and self._text is not None:
            self._text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th", "tr", "table"):
            self._close_cell()

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)

    def _close_cell(self) -> None:
        if self._text is None:
            return
        row_span, col_span = self._span
        self._rows[-1].append((row_span, col_span, "".join(self._text).strip()))
        self._text = None

    def rows(self) -> list[list[str]]:
        """行 × 列。結合セルは範囲の先頭にだけ文字列を置き、範囲内の他の位置は空文字で占める。

        空セル・結合範囲も位置を占める（後続セルの列がずれないように）。
        """
        self._close_cell()
        placed: dict[tuple[int, int], str] = {}
        for row_index, cells in enumerate(self._rows):
            column = 0
            for row_span, col_span, text in cells:
                while (row_index, column) in placed:
                    column += 1  # 上の行の rowspan が占めている列を飛ばす
                for r in range(row_index, row_index + row_span):
                    for c in range(column, column + col_span):
                        placed[(r, c)] = ""
                placed[(row_index, column)] = text
                column += col_span
        if not placed:
            return []
        n_rows = max(row for row, _ in placed) + 1
        n_cols = max(column for _, column in placed) + 1
        return [
            [placed.get((row, column), "") for column in range(n_cols)] for row in range(n_rows)
        ]


def table_rows(html: str) -> list[list[str]]:
    """表の HTML を行 × 列にする。"""
    parser = _TableHtmlParser()
    parser.feed(html)
    parser.close()
    return parser.rows()
