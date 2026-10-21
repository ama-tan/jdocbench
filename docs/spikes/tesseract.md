# スパイク: Tesseract（jpn）の設定

Issue #196 / Sprint 16。日本語 OCR の Baseline として Tesseract をアダプタにするときの設定（テキスト層の扱い・
描画・psm）を決めた記録。

## 結論

- **2 アダプタに分ける**: `tesseract`（`--psm 3`。Tesseract の既定）と `tesseract_psm6`（`--psm 6`）。
  psm は文書によって切り替えない（camelot の `lattice` / `stream` と同じ扱い。#135）
- **テキスト層のある PDF は skipped**（`docling_ocr` と同じ判定。全ページ合計 20 文字以上で
  `text layer present (<n> chars)`）
- 描画は pypdfium2・300dpi・CropBox。`--oem 1`（LSTM のみ）、`--dpi 300`、`OMP_THREAD_LIMIT=1`
- 正規化は txt（空行区切りの段落）。**表は作らない**（Tesseract は表構造を出さない）

## テキスト層の扱い

`docling_ocr` と同じく、テキスト層のある文書は OCR せず skipped にした。

- 比べたいのは「テキスト層が無いので OCR が要る」文書での OCR 同士（企画書 Q5）。テキスト層がある文書に
  OCR をかけた値は、Level 0 / 1 の抽出と比べる別の問いになる
- 判定と閾値を `docling_ocr` と共有するので、OCR 系の列（docling_ocr / tesseract / tesseract_psm6）は
  同じ文書の集合で比べられる。ppstructure はテキスト層の有無で分けない Document AI として全ページを OCR する
- 一部のページだけがスキャンの文書（Router の C1p）では skipped になる。これは `docling_ocr` と同じ制約

## 描画

- pypdfium2 は CropBox を描画する。実物スキャン `estat-census-1920-scan-excerpt.pdf` は MediaBox が見開き
  （1029.12 × 725.76 pt）で CropBox が片ページなので、描画結果は 2163〜2340 × 3025 px（300dpi）になり、
  見開きの 4288 px にはならない（`tests/test_tesseract.py` の実 PDF テストで tsv のページ幅を確かめている）
- 300dpi は実物スキャンの元画像の解像度（4288 px ÷ 1029.12 pt × 72）。合成スキャン
  （`stat-go-jp-children-population-2026-scan.pdf`、200dpi で画像化）では拡大になる
- PNG に解像度を埋めないため `--dpi 300` を渡す。渡さないと Tesseract が文字の大きさから推定し
  （実測で 307〜516）、ページごとに値が揺れる
- ページ画像は一時ディレクトリに描画して捨てる（PDF と dpi から再現できる入力で、raw 出力ではない）

## psm の比較（CI の実測。実測値の正）

CI run 39032637753（ubuntu-latest、Tesseract 5.3.4 + `tesseract-ocr-jpn`、PR #203）。

| 文書 | psm | CF | ROA | ROA 対応率 | Total | Wall [s] | Peak RSS [MiB] |
|---|---|---|---|---|---|---|---|
| estat-census-1920-scan-excerpt（実物、4 ページ） | 3 | 0.701 | 1.000 | 0.272 | 42.5 | 5.2 | 130 |
| 同上 | 6 | 0.644 | 0.969 | 0.294 | 40.3 | 4.9 | 136 |
| stat-go-jp-children-population-2026-scan（合成、6 ページ） | 3 | 0.559 | 0.877 | 0.544 | 35.9 | 13.4 | 147 |
| 同上 | 6 | 0.528 | 0.897 | 0.597 | 35.6 | 12.3 | 148 |

- CI では psm 3 と psm 6 の差は小さく（2.2 点・0.3 点）、どちらも psm 3 が上
- 同じ文書で docling_ocr は 82.1 / 78.4、ppstructure は 65.4 / 81.4。Tesseract は表構造を出さない
  （SED / TSF = 0）ので Total は CF と ROA だけで決まり、構造を返す OCR より低く出る
- psm 3 は実物スキャンの表紙の大きな題字を落とす（p1 の txt は psm 3 が 45 文字、psm 6 が 226 文字）。
  表のページでは psm 3 の方が多く読めていて、合計では psm 3 が上回る

## psm の比較（開発機の実測。参考）

Tesseract 5.5.3（conda-forge、`jpn.traineddata` は同梱のもの）、CentOS Stream 9 / Xeon 6 コア。
CI（Ubuntu の `tesseract-ocr` 5.3.x + `tesseract-ocr-jpn`）とは版と学習データが違うので、CI の値が正。

| 文書 | psm | CF | ROA | ROA 対応率 | Total | Wall [s] | Peak RSS [MiB] |
|---|---|---|---|---|---|---|---|
| estat-census-1920-scan-excerpt（実物、4 ページ） | 3 | 0.395 | 1.000 | 0.120 | 9.9 | 10.9 | 128 |
| 同上 | 6 | 0.655 | 1.000 | 0.217 | 41.4 | 10.9 | 133 |
| stat-go-jp-children-population-2026-scan（合成、6 ページ） | 3 | 0.556 | 0.880 | 0.553 | 35.9 | 18.0 | 147 |
| 同上 | 6 | 0.521 | 0.875 | 0.605 | 34.9 | 16.8 | 146 |

- **開発機では実物スキャンの psm 3 が 9.9 まで落ち、CI（42.5）と大きく食い違った**。Tesseract の版と学習データ
  （5.5.3 / conda-forge 同梱 と 5.3.4 / Ubuntu）の違いで、psm の効き方は環境に依存する。以下は開発機での観察
- 実物スキャンでは psm 3 のページ分割が、表紙の大きな題字（「大正九年」「全國の部第一巻」）などを文字の無い
  領域として落とす（p1 の txt は psm 3 が 46 文字、psm 6 が 168 文字）。表のページはどちらも行ごとに読むが、
  GT の行と対応が取れる行は psm 3 の方が少ない（ROA 対応率 0.120 は Total の足切り 0.15 未満で、ROA が 0 として
  入る）。psm 6 はページを分割せず 1 ブロックとして上から行ごとに読む
- 合成スキャンでは差が小さい（psm 3 は見出しの番号「1」「2」を落とし、psm 6 は罫線を文字にしやすい）
- 開発機の 2 文書の値を見て psm 6 だけを選んでいたら、Tesseract だけを評価用の文書（と開発機）に合わせて
  調整したうえ、CI では逆の結果になっていた。既定（psm 3）を `tesseract` として残し、psm 6 を別アダプタとして
  並べる方針はこの食い違いでも妥当だった
- SED / TSF は両方とも 0（表を表として返さない）。Total は CF と ROA だけで決まる
- 開発機では 1 ページあたり約 3 秒、Peak RSS は 150 MiB 未満（描画と tesseract を含む子プロセス全体）

## 正規化

txt（Tesseract 自身が単語を連結したテキスト）を空行で段落に分ける。tsv は単語ごとの bbox と確信度を持つが、
日本語の単語の間に空白を入れるかどうか（Tesseract 内部の空白判定）を持たないため、tsv から文字列を組み立てると
連結の規則をこちらで決めることになる。特定パーサーに手を加えないため txt をそのまま使い、tsv は raw に残すだけにした。
