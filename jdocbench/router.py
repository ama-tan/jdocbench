"""文書単位の Parser Router 初版（企画書 §26・§27 Strategy E・§39。#162）。

Document Profile（`jdocbench.profile.profile_pdf` の結果。profile.json の dict）を入力に、
推奨パーサーとフォールバック・理由を返すルールベースの関数。外部 API もモデルも使わない。

- `quality`: 品質重視。期待できる DSQS Total が最も高いもの（Document AI を含む）
- `cost`: コスト重視。Level 1（Classical）で足りる特性ならそれ。Level 1 が実測で崩れる特性
  （スキャン・2 段組み）のときだけ重いものへ上げる

分岐に使う特性は結果側リポジトリ（ama-tan/jdoc-bench、非公開）の docs/router.md の
4 特性（`text_layer` / `columns` / `ruled_pages` / `nonembedded_cjk_font_pages`）に限る
（スキャンの判定だけ `chars_per_page` で補う）。n=7 のため、1 文書の癖だけに合う分岐は作らない。
各分岐の根拠（文書と数値）は結果側リポジトリの docs/results.md（CI run 37117313639、
`ci-results-full`）と docs/router.md による。決定木と 7 文書の推奨は結果側リポジトリの
docs/router.md「Router 初版（ルール）」を参照。

推奨は利用可能性を考慮しない（パーサー名を返すだけで、実行は別）。PP-Structure のような
オプトインのアダプタも推奨に出す。

Profile 以外の入力は実行環境の日本語フォント（`fonts_ja`、run.json と同じ
`fc-list :lang=ja` の結果）だけで、cost モードの日本語フォント非埋め込みの分岐（C3）でのみ
使う（#173）。
"""

# v2: 部分スキャンの分岐（#169）、v3: C3 を実行環境の日本語フォントの有無で分ける（#173）、
# v4: Q1-scan を docling_ocr 主・ppstructure 予備にする（#188）
ROUTER_VERSION = "rules-v4"
MODES = ("quality", "cost")

# スキャンとみなす 1 ページあたりの文字数の上限（`text_layer` の補助）。`text_layer` は 1 文字でも
# 取れるページを数えるため、透かしやページ番号だけが文字として入ったスキャンも 0 にならない。
# 7 文書はスキャン 0.0 とテキスト層あり 363.4 以上（geps）に大きく分かれ、この値はどちらにも近くない
_SPARSE_CHARS_PER_PAGE = 20

# Profile が取れない・特性が欠けているときの既定（両モード共通）。
# 根拠: テキスト層のある 6 文書で docling の Total は最小 75.8（stat）で、段組み（jacn 88.2）でも
# 崩れない。Level 1 は jacn で pdfplumber 47.8 まで落ちる。特性が分からない以上、最悪の場合が
# 浅いものを選ぶ。スキャンだった場合に備え docling_ocr（テキスト層が無い文書でだけ動く）を続ける
_SAFE_DEFAULT = {"parser": "docling", "fallbacks": ["docling_ocr", "ppstructure"]}


def _features(profile: dict) -> dict | None:
    """分岐に使う特性を取り出す。欠けている・型が違う場合は None。"""
    try:
        pages = int(profile["pages"])
        features = {
            "pages": pages,
            "text_layer": float(profile["text_layer"]),
            "chars_per_page": float(profile["chars_per_page"]),
            "columns": int(profile["columns"]),
            "ruled_ratio": round(len(profile["ruled_pages"]) / pages, 3) if pages else 0.0,
            "nonembedded_cjk_font_pages": list(profile["nonembedded_cjk_font_pages"]),
            # #169 より前の profile.json には無い。無ければ None（text_layer で代用する）
            "textless_image_pages": (
                list(profile["textless_image_pages"]) if "textless_image_pages" in profile else None
            ),
        }
    except (KeyError, TypeError, ValueError):
        return None
    return features if pages > 0 else None


def _result(mode: str, rule: str, parser: str, fallbacks: list[str], reasons: list[str]) -> dict:
    return {
        "mode": mode,
        "parser": parser,
        "fallbacks": fallbacks,
        "reasons": reasons,
        "rule": rule,
    }


def route(profile: dict, mode: str, fonts_ja: list[str] | None = None) -> dict:
    """Profile とモードから推奨パーサーを返す。

    `fonts_ja` は実行環境の日本語フォント（`fc-list :lang=ja` のファミリー名。
    調べられなければ None）。
    cost モードの C3 でだけ使い、空リスト（無いと確かめた）のときだけ Level 1 に回す。
    戻り値は `{"mode", "parser", "fallbacks", "reasons", "rule"}`。`rule` は通った分岐の ID
    （結果側リポジトリ（ama-tan/jdoc-bench、非公開）の docs/router.md の決定木と対応）。
    Profile に `error` がある・特性が欠けている場合は安全側の既定（rule `default-error`）を返す。
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode!r}（{' / '.join(MODES)}）")

    features = None if "error" in profile else _features(profile)
    if features is None:
        why = (
            f"Profile が取れなかった（{profile['error']}）"
            if "error" in profile
            else "Profile に分岐に必要な特性が無い"
        )
        return _result(
            mode,
            "default-error",
            _SAFE_DEFAULT["parser"],
            list(_SAFE_DEFAULT["fallbacks"]),
            [
                why,
                "特性が分からないため最悪の場合が浅い docling を既定にする"
                "（テキスト層のある 6 文書で Total 75.8〜91.9、2 段組みでも崩れない）",
            ],
        )

    text_layer = features["text_layer"]
    # テキスト層が無い、または透かし程度しか無い文書をスキャンとみなす
    scan = text_layer == 0 or features["chars_per_page"] < _SPARSE_CHARS_PER_PAGE
    # docling_ocr はテキスト層が全体で 20 文字（空白を含む pypdfium2 の文字数）以上あると skipped
    # になる（アダプタの仕様）。Profile の文字数は空白を除くので閾値の比較はできない。
    # 確実に動くと言える文字 0 の文書にだけ推奨し、透かしなどの文字がある文書には推奨しない
    docling_ocr_runs = features["chars_per_page"] == 0
    scan_note = (
        f"テキスト層が無い、または透かし程度（text_layer={text_layer}、"
        f"chars_per_page={features['chars_per_page']}）: スキャンとして扱う"
    )
    reasons: list[str] = []
    # テキスト層が一部のページにだけ無い文書（部分スキャン）。7 文書に該当は無く未計測の仮説の分岐。
    # テキスト層の無いページは OCR しないパーサーでは取れず内容が欠落する。docling_ocr は
    # テキスト層が全体で 20 文字以上あると skipped になるので使えない。ppstructure は全ページを
    # ページ画像から OCR するので、テキスト層の有無が混在しても全ページを扱える（#169）
    # 白紙ページ（文字も画像も無い）は部分スキャンとみなさない。画像だけのページがあるときに限る。
    # 古い Profile で特性が無いときは text_layer < 1 で代用する（安全側: 欠落よりコストを選ぶ）
    textless_image_pages = features["textless_image_pages"]
    partial = not scan and (
        bool(textless_image_pages) if textless_image_pages is not None else text_layer < 1
    )
    partial_reasons = [
        f"テキスト層の無いページがある（text_layer={text_layer}）: そのページは OCR しない"
        "パーサーでは取れず内容が欠落する",
        "docling_ocr はテキスト層があると skipped になるため、全ページを OCR する ppstructure",
        "部分スキャンの文書は 7 文書に無く未計測（仮説の分岐）",
    ]

    if mode == "quality":
        if scan:
            # 根拠: スキャン 2 文書で OCR 系の順位が逆。合成の stat-scan は ppstructure 81.4 >
            # docling_ocr 78.4（3.0 点差）、実物の estat は docling_ocr 82.1 > ppstructure 65.4
            # （16.7 点差）。estat の ppstructure の低さはパーサーの挙動
            # （ページ画像は CropBox どおりに描けている）:
            # 50 行の表の構造認識が 3 行・11 セルで途切れ（その表の TSF 0）、片仮名交じり文の
            # 片仮名（ノ・ヲ・ハ等）の 7 割を落とす・誤読する（#188）。2 文書を分ける Profile の
            # 特性はあるが（1 bit の画像、CropBox ≠ MediaBox）、どちらも原因の機序ではない。
            # 外したときの損失が浅い方
            # （docling_ocr を選んで負けるのは 3.0 点、ppstructure は 16.7 点）を選ぶ。
            # 文字が少しでもある文書は docling_ocr が skipped になりうるので ppstructure
            reasons = [
                scan_note,
                "OCR 系だけが意味を持つ（他のアダプタは 0.0）",
                "スキャン 2 文書で順位が逆: 実物の estat で docling_ocr 82.1 > ppstructure 65.4、"
                "合成の stat-scan で ppstructure 81.4 > docling_ocr 78.4。外したときの損失が浅い"
                " docling_ocr（3.0 点減。ppstructure は 16.7 点減）",
            ]
            if docling_ocr_runs:
                return _result(mode, "Q1-scan", "docling_ocr", ["ppstructure"], reasons)
            reasons.append(
                "テキスト層に文字があり docling_ocr は skipped になりうる"
                "（全体で 20 文字以上で skipped）ため ppstructure"
            )
            return _result(mode, "Q1-scan", "ppstructure", [], reasons)
        if partial:
            return _result(mode, "Q1p-partial-scan", "ppstructure", [], partial_reasons)
        # 根拠: テキスト層のある 6 文書の Total 平均は docling 83.4 > ppstructure 79.9。
        # ppstructure が勝つ 4 文書の差は 0.1〜2.2 点（jacn 88.3/88.2、mof-budget 89.0/87.8、
        # env 78.4/77.5、stat 78.0/75.8）、docling が勝つ 2 文書の差は 7.6〜17.7 点
        # （geps 91.9/74.2、mof-financial 79.1/71.5）。段組み・罫線・フォントのいずれでも
        # 安定して ppstructure が上回る特性は無い。
        # Wall の 6 文書合計も docling 262 秒 < ppstructure 821 秒
        reasons = [
            "テキスト層あり: Document AI の中で docling の期待値が最も高い",
            "6 文書の Total 平均 docling 83.4 > ppstructure 79.9。"
            "ppstructure が勝つ文書の差は 2.2 点以内、docling が勝つ文書の差は 7.6〜17.7 点",
        ]
        return _result(mode, "Q2-text", "docling", ["ppstructure"], reasons)

    # cost モード
    if scan:
        # 根拠: stat-scan では OCR が避けられない（OCR 系以外は 0.0）。docling_ocr は
        # 78.4（Wall 57.7 秒、Peak RSS 2.1 GiB）で、ppstructure 81.4（130.6 秒、2.2 GiB）の
        # 半分以下の時間。文字が少しでもある文書は docling_ocr が skipped になりうるので ppstructure
        reasons = [
            scan_note,
            "OCR は避けられない。stat-scan で docling_ocr 78.4（57.7 秒）は"
            " ppstructure 81.4（130.6 秒）より 3 点低いが半分以下の時間",
        ]
        if docling_ocr_runs:
            return _result(mode, "C1-scan", "docling_ocr", ["ppstructure"], reasons)
        reasons.append(
            "テキスト層に文字があり docling_ocr は skipped になりうる"
            "（全体で 20 文字以上で skipped）ため ppstructure"
        )
        return _result(mode, "C1-scan", "ppstructure", [], reasons)
    if partial:
        return _result(mode, "C1p-partial-scan", "ppstructure", [], partial_reasons)
    if features["columns"] >= 2:
        # 根拠: jacn（全ページ 2 段）で Level 1 は段を解けない。
        # pdfplumber 47.8（CF 0.30・ROA 0.78）、
        # pymupdf 66.3 に対し docling 88.2（Wall 23.6 秒）。22 点の差は Level 1 の範囲で埋まらない。
        # フォールバックは段組みを最もよく解く Level 1 の pymupdf（CF 0.85・ROA 0.97）
        return _result(
            mode,
            "C2-multicolumn",
            "docling",
            ["pymupdf"],
            [
                "2 段組み: Level 1 は段を解けず大差で負ける",
                "jacn で docling 88.2 に対し pymupdf 66.3・pdfplumber 47.8",
                *reasons,
            ],
        )
    if features["nonembedded_cjk_font_pages"]:
        # 根拠: env（p1〜p3 が日本語フォント非埋め込み）。Level 1 はフォントに Unicode の対応が無い
        # 文字を戻せず、pdfplumber は GT 行の対応率 0.56・CF 0.73、pymupdf も 0.96・0.81 に留まる
        # （Total pymupdf 78.9 > pdfplumber 76.8）。ページ画像を描く Document AI は、実行環境に
        # 日本語フォントがあれば代替フォントで描いて読める: CI にフォントを入れた後
        # （#157、run 37876279369）の env で docling は 77.5 → 97.2、pymupdf との差は 18.3 点。
        # フォントが無いと docling も
        # 日本語を空白で描き 77.5（run 37090995609）と pymupdf を 1.4 点下回る。
        # そこで実行環境のフォントで分ける。調べられない（None）ときは docling: 外したときの損失が
        # 浅い側（フォントがあるのに pymupdf なら 18.3 点、無いのに docling なら 1.4 点）
        if fonts_ja == []:
            return _result(
                mode,
                "C3-nonembedded-cjk-nofonts",
                "pymupdf",
                ["docling"],
                [
                    "日本語フォント非埋め込みのページがあり、実行環境に日本語フォントが無い"
                    "（fonts_ja が空）: Document AI もページ画像で日本語を描けない",
                    "フォント無しの env で pymupdf 78.9 > docling 77.5 > pdfplumber 76.8",
                    *reasons,
                ],
            )
        found = "あり" if fonts_ja else "不明（無いと確かめられない）"
        return _result(
            mode,
            "C3-nonembedded-cjk",
            "docling",
            ["pymupdf"],
            [
                "日本語フォント非埋め込みのページがある: Level 1 は日本語を Unicode に戻せず落とす",
                f"実行環境の日本語フォントが{found}: Document AI はページ画像を描いて読める",
                "フォントありの env で docling 97.2 に対し pymupdf 78.9・pdfplumber 76.8",
                *reasons,
            ],
        )
    # 根拠: 1 段組み・テキスト層ありの 5 文書（env・geps・mof-budget・mof-financial・stat）の
    # Total 平均は pdfplumber 75.4 > pymupdf 69.5、Wall は 0.5 秒未満。docling 82.4 との差は
    # 平均 7 点で、時間は 80〜590 倍かかる（docling 16〜78 秒）。camelot_stream（76.4）は表しか
    # 出さないため文書全体の既定にはしない。罫線（ruled_pages）は分岐に使わない:
    # 罫線の無い表（mof-budget で pdfplumber 66.1 / docling 87.8）と表の無い文書を Profile で
    # 区別できず、罫線ありの側も geps の画面写しの枠のように表の証拠にならないため
    return _result(
        mode,
        "C4-classical",
        "pdfplumber",
        ["docling"],
        [
            "1 段組み・テキスト層あり・日本語フォント埋め込み: Level 1 で足りる",
            "1 段組みの 5 文書で pdfplumber 平均 75.4（0.5 秒未満）、docling 82.4（16〜78 秒）",
            *reasons,
        ],
    )


def route_all(
    profile: dict, modes: tuple[str, ...] = MODES, fonts_ja: list[str] | None = None
) -> dict:
    """route.json の中身を作る。

    Profile の識別子・分岐に使った特性と実行環境の日本語フォント・モードごとの推奨。
    """
    features = None if "error" in profile else _features(profile)
    data = {
        "router_version": ROUTER_VERSION,
        "source": profile.get("source"),
        "sha256": profile.get("sha256"),
        "profiler": profile.get("profiler"),
        "features": features,
        # 分岐に使った実行環境の入力（C3 だけが使う。None は調べられなかった）
        "fonts_ja": fonts_ja,
        "routes": {mode: route(profile, mode, fonts_ja) for mode in modes},
    }
    if "error" in profile:
        data["profile_error"] = profile["error"]
    return data
