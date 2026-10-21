"""Document Profile: Parser Router の入力にする文書の構造特性（企画書 §15・§26）。

PyMuPDF だけで軽量に取れる特性に絞る（Document AI・外部 API は使わない。1 文書 1 秒未満）。
PyMuPDF はオプトインの extra（AGPL、`docs/licenses.md`）なので、未導入なら `profile_pdf` は
ImportError を出す（呼び出し側がエラーとして記録する）。

文書単位のフィールド:

- `pages`: ページ数
- `text_layer`: テキスト層のあるページ（空白以外の文字が 1 文字以上取れるページ）の割合。
  スキャン PDF は 0
- `chars_per_page`: 1 ページあたりの文字数（空白を除く）の平均
- `cjk_ratio`: 取れた文字のうち CJK（ひらがな・カタカナ・漢字）の割合
- `columns`: 段組み数の推定の最大値（0 / 1 / 2。3 段以上は判定しない）。`_columns` を参照
- `multi_column_pages`: 2 段組みと推定したページ番号
- `ruled_pages`: 罫線の多いページ（格子状の罫線 = 罫線つきの表の目安）。`_rules` を参照
- `embedded_fonts`: 文書内のフォントのうち埋め込まれているものの割合（フォントが無ければ None）
- `nonembedded_cjk_font_pages`: 埋め込まれていない Type0（CID）フォントを使うページ番号。
  日本語フォントは Type0 で使われるため、日本語フォント非埋め込みの目安になる
  （pdfminer 系のパーサーがそのページの日本語を取り出せないことがある）
- `textless_image_pages`: テキスト層がほぼ無く（空白以外の文字が 20 未満。ページ番号や透かし程度）、
  画像を 1 つ以上含むページ番号。テキスト PDF の中のスキャンページ（部分スキャン）の目安。
  白紙ページ（画像が無い）は含めない（#169）。
- `vertical_text_pages`: 縦書きが主なページ番号。ページの文字数（空白を除く）のうち縦書きの行
  （PyMuPDF の行の `dir` が (0, ±1) に近い行）の文字の割合が 50% 以上のページ。`_vertical_chars`
  を参照。**測るだけで Router の分岐には使わない**（#234。縦書き文書が 1 件だけのため）。
  閾値の根拠: 既存 9 文書の最大は mof-financial p3 の縦書きセル 1 個（12 文字、1.1%）と
  jsda p2 の表頭（6 文字、0.3%）で、「縦書きが主」とは桁が違う

`page_details` にページごとの値（文字数・段組み数・横罫 / 縦罫の位置の数・非埋め込みの有無・
縦書きの文字数）を残す。
"""

import importlib.metadata
from pathlib import Path

# 段組み判定: 「長い行」とみなす幅（ページ幅に対する割合）と、各段に要る長い行の数
_WIDE_LINE_RATIO = 0.25
_MIN_LINES_PER_COLUMN = 5
# 罫線の判定: 線とみなす最小の長さと、水平・垂直とみなす太さ / ずれ（pt）
_MIN_RULE_LENGTH = 5.0
_RULE_THICKNESS = 2.0
# 格子状とみなす横罫・縦罫の位置の数の下限
_MIN_H_RULES = 10
_MIN_V_RULES = 3
# 行の向きの判定: PyMuPDF の行の dir（進行方向の単位ベクトル）の直交成分がこれ未満なら横書き /
# 縦書きとみなす（`_columns` の横書きの判定と同じ許容）
_DIR_TOLERANCE = 0.1
# 縦書きのページとみなす、ページの文字数のうち縦書きの行の文字の割合の下限（根拠は docstring）
_VERTICAL_TEXT_RATIO = 0.5

_CJK_RANGES = (
    (0x3040, 0x30FF),  # ひらがな・カタカナ
    (0x3400, 0x4DBF),  # CJK 統合漢字拡張 A
    (0x4E00, 0x9FFF),  # CJK 統合漢字
    (0xF900, 0xFAFF),  # CJK 互換漢字
)


_SPARSE_PAGE_CHARS = 20
"""テキスト層が「ほぼ無い」とみなす 1 ページの文字数（空白を除く）の上限。スキャンページに
ページ番号や透かしだけがテキスト層として載っている場合も部分スキャンとして拾う。"""


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def _columns(page, text_dict: dict) -> int:
    """段組み数を推定する（0: テキスト無し / 1 / 2）。

    横書きの行のうちページ幅の 25% 以上ある「長い行」を、テキスト領域の中央線より左に収まる行・
    右に収まる行・中央線をまたぐ行に分ける。左右それぞれに 5 行以上あり、どちらも中央線を
    またぐ行より多ければ 2 段組みとする。表のセルは短いので数えず、左右に並べた表を
    2 段組みと誤らないようにしている。
    """
    lines = [
        line["bbox"]
        for block in text_dict["blocks"]
        for line in block.get("lines", [])
        if abs(line["dir"][1]) < _DIR_TOLERANCE
        and "".join(s["text"] for s in line["spans"]).strip()
    ]
    if not lines:
        return 0
    center = (min(b[0] for b in lines) + max(b[2] for b in lines)) / 2
    wide = [b for b in lines if b[2] - b[0] >= _WIDE_LINE_RATIO * page.rect.width]
    left = sum(1 for b in wide if b[2] <= center)
    right = sum(1 for b in wide if b[0] >= center)
    spanning = len(wide) - left - right
    if min(left, right) >= _MIN_LINES_PER_COLUMN and min(left, right) > spanning:
        return 2
    return 1


def _vertical_chars(text_dict: dict) -> int:
    """縦書きの行（dir が (0, ±1) に近い行）の文字数（空白を除く）を返す。

    PyMuPDF の `dir` はフォントの書字方向（WMode）やテキスト行列から決まり、文字の形からは
    推定しないので、1 文字だけの行でも向きは信頼できる。縦書きフォント（Identity-V 等）の
    文字は PyMuPDF が 1 文字ずつ別の行にする（mof-financial p3 の縦書きセルも同じ）ため、
    1 文字の行を除くと本物の縦書きが数えられなくなる。行数ではなく文字数で数え、
    少数の縦書きセルや回転したラベルは `_VERTICAL_TEXT_RATIO` の割合で除く。
    斜めの行（どちらの軸にも近くない）は横書きにも縦書きにも数えない。
    """
    return sum(
        1
        for block in text_dict["blocks"]
        for line in block.get("lines", [])
        if abs(line["dir"][0]) < _DIR_TOLERANCE
        for span in line["spans"]
        for ch in span["text"]
        if not ch.isspace()
    )


def _rules(page) -> tuple[int, int]:
    """ベクター描画から横罫・縦罫の**位置の数**（y 座標・x 座標を 1pt に丸めた異なり数）を返す。

    線分（`l`）と細い矩形（`re`）を罫線とみなし、太い矩形は 4 辺を罫線として数える。
    同じ罫線が短い線分に分けて描かれていても数が膨らまないよう、本数ではなく位置の数にする。
    横罫 10 位置以上かつ縦罫 3 位置以上のページを `ruled_pages`（格子状 = 罫線つきの表の目安）
    とする。
    外枠と列の縦罫だけの表（行の罫線が無い）は横罫が少ないため入らない。
    画面写しの枠や図形の線も数えるので、表以外の線が多いページも入りうる
    （目安であり表の検出ではない）。
    """
    h: set[int] = set()
    v: set[int] = set()
    for path in page.get_drawings():
        for item in path["items"]:
            if item[0] == "l":
                a, b = item[1], item[2]
                if abs(a.y - b.y) <= 1 and abs(a.x - b.x) >= _MIN_RULE_LENGTH:
                    h.add(round((a.y + b.y) / 2))
                elif abs(a.x - b.x) <= 1 and abs(a.y - b.y) >= _MIN_RULE_LENGTH:
                    v.add(round((a.x + b.x) / 2))
            elif item[0] == "re":
                r = item[1]
                if r.height <= _RULE_THICKNESS and r.width >= _MIN_RULE_LENGTH:
                    h.add(round((r.y0 + r.y1) / 2))
                elif r.width <= _RULE_THICKNESS and r.height >= _MIN_RULE_LENGTH:
                    v.add(round((r.x0 + r.x1) / 2))
                elif r.width >= _MIN_RULE_LENGTH and r.height >= _MIN_RULE_LENGTH:
                    h.update((round(r.y0), round(r.y1)))
                    v.update((round(r.x0), round(r.x1)))
    return len(h), len(v)


def profile_pdf(pdf: Path) -> dict:
    """PDF の Document Profile を返す（定義はモジュールの docstring）。"""
    import pymupdf  # オプトイン extra。未導入なら ImportError

    details: list[dict] = []
    fonts: dict[int, bool] = {}  # xref → 埋め込みの有無（ページをまたいで同じフォントを数えない）
    total_chars = 0
    cjk_chars = 0
    doc = pymupdf.open(pdf)
    try:
        for page in doc:
            text_dict = page.get_text("dict")
            chars = [
                ch
                for block in text_dict["blocks"]
                for line in block.get("lines", [])
                for span in line["spans"]
                for ch in span["text"]
                if not ch.isspace()
            ]
            total_chars += len(chars)
            cjk_chars += sum(1 for ch in chars if _is_cjk(ch))
            nonembedded_cjk = False
            # get_fonts の要素は (xref, ext, type, basefont, name, encoding)。
            # ext が "n/a" なら非埋め込み
            for xref, ext, font_type, *_ in page.get_fonts():
                embedded = ext != "n/a"
                fonts[xref] = embedded
                if not embedded and font_type == "Type0":
                    nonembedded_cjk = True
            h_rules, v_rules = _rules(page)
            details.append(
                {
                    "page": page.number + 1,
                    "chars": len(chars),
                    # 画像 XObject（get_images）とインライン画像（text_dict の画像ブロック）の多い方
                    "images": max(
                        len(page.get_images()),
                        sum(1 for block in text_dict["blocks"] if block.get("type") == 1),
                    ),
                    "columns": _columns(page, text_dict),
                    "vertical_chars": _vertical_chars(text_dict),
                    "h_rules": h_rules,
                    "v_rules": v_rules,
                    "nonembedded_cjk_font": nonembedded_cjk,
                }
            )
    finally:
        doc.close()

    pages = len(details)
    return {
        "profiler": f"pymupdf {importlib.metadata.version('pymupdf')}",
        "pages": pages,
        "text_layer": round(sum(1 for d in details if d["chars"]) / pages, 3) if pages else 0.0,
        "chars_per_page": round(total_chars / pages, 1) if pages else 0.0,
        "cjk_ratio": round(cjk_chars / total_chars, 3) if total_chars else 0.0,
        "columns": max((d["columns"] for d in details), default=0),
        "multi_column_pages": [d["page"] for d in details if d["columns"] >= 2],
        "ruled_pages": [
            d["page"]
            for d in details
            if d["h_rules"] >= _MIN_H_RULES and d["v_rules"] >= _MIN_V_RULES
        ],
        "embedded_fonts": round(sum(fonts.values()) / len(fonts), 3) if fonts else None,
        "nonembedded_cjk_font_pages": [d["page"] for d in details if d["nonembedded_cjk_font"]],
        "textless_image_pages": [
            d["page"] for d in details if d["chars"] < _SPARSE_PAGE_CHARS and d["images"]
        ],
        "vertical_text_pages": [
            d["page"]
            for d in details
            if d["chars"] and d["vertical_chars"] / d["chars"] >= _VERTICAL_TEXT_RATIO
        ],
        "page_details": details,
    }
