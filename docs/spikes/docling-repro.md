# スパイク: docling の Total がローカルと CI で食い違う原因

Issue #157 / Sprint 13。環境省 財務書類（`env-financial-statements-fy2024-excerpt.pdf`、5 ページ）の docling の DSQS Total が、
ローカル（#86 の実測）で 97.2、CI の bench で 77.5 と食い違っていた。

## 結論

**原因は日本語フォントの有無**。env の p1〜p3 は日本語フォント（ＭＳ ゴシック / ＭＳ 明朝）を埋め込んでいない。
Docling はレイアウト推論の入力にするページ画像を pypdfium2（PDFium）で描くが、CI の Ubuntu ランナーには
日本語フォントが無く、代替できない日本語が**空白で描かれる**。罫線と数字だけになったページ画像では、
レイアウトモデルが p1〜p3 の表を表として検出せず（p1 は 99 個の text、p2 / p3 は 1 個の大きな text）、
TSF / SED / CF が下がった。ローカル機には Noto CJK が入っており、日本語が描かれて表が検出される。

対処: CI に `fonts-noto-cjk` を入れ、run.json に実験条件 `fonts_ja`（`fc-list :lang=ja` のファミリー名）を記録する。
README の導入手順にも日本語フォントを加えた。

## 証拠

### 1. 差は raw にあり、評価コードには無い

#86 のローカルの raw と CI の raw（run 37185926545、Sprint 12 リリース時の `ci-results-full`）を、
現行（#147 後）の評価コードで計算し直した。

| raw | Total | CF | SED | TSF | ROA |
|---|---|---|---|---|---|
| ローカル（#86、構成 C） | 97.2 | 0.960 | 1.000 | 0.952 | 0.976 |
| CI | 77.5 | 0.845 | 0.727 | 0.544 | 0.984 |

#86 当時の値と一致し、GT / 評価コードの版の違いではない。

### 2. CI の raw は p1〜p3 に表が無い

| | 表の数（ページ: 個） | p2 の text |
|---|---|---|
| ローカル | p1:1, p2:1, p3:1, p4:2, p5:2 | 見出し 2 個 + 18×4 の表 |
| CI | p4:2, p5:2 | 見出しと表全体を連結した text 1 個 |

CI の TSF の内訳も p1〜p3 の 3 表が 0.0、p4 / p5 はローカルとほぼ同じ。

### 3. CI のページ画像には日本語が無い

CI のアーティファクトの `pages/p2.png`（view.html 用に pypdfium2 で描いたもの。Docling と同じライブラリ・同じ版）は、
罫線・数字・括弧だけで、見出し「業務費用計算書」や行見出し（人件費 など）が描かれていない。
ローカルで同じページを描くと日本語が出る。

非埋め込みフォントを持つのは 7 文書のうち env の p1〜p3（日本語）と mof-financial の全ページ（ArialMT。PDFium 内蔵の代替で描ける）だけ。

### 4. ローカルで日本語フォントを隠すと CI の値が再現する

`unshare -rm` でマウント名前空間を作り、`/usr/share/fonts/google-noto-cjk` と `/usr/share/fonts/google-droid-sans-fonts`
（`fc-list :lang=ja` が返す 2 ディレクトリ）を空の tmpfs で隠して、現行の docling アダプタを動かした。

| 条件 | Wall [s] | Peak RSS [MiB] | Total | CF | SED | TSF | ROA | 表の数 |
|---|---|---|---|---|---|---|---|---|
| ローカル（日本語フォントあり） | 53.3 | 1376 | 97.2 | 0.960 | 1.000 | 0.952 | 0.976 | 7（p1〜p5） |
| ローカル（日本語フォントを隠す） | 32.9 | 1330 | **77.5** | 0.845 | 0.727 | 0.544 | 0.984 | 4（p4 / p5 のみ） |
| CI（参考） | 70.1 | 1361 | 77.5 | 0.845 | 0.727 | 0.544 | 0.984 | 4（p4 / p5 のみ） |

- フォントを隠すと、CI の値が全サブスコアで小数 4 桁まで一致した。CPU は Xeon のままなので、CPU 依存の差ではない
- フォントありの Markdown は #86 のローカルの raw と完全一致した
- フォントを隠した Markdown は CI の Markdown とセルの中身が一致し、違いは表の列の空白の詰め方（表示幅の計算）だけだった

### 否定した仮説

- **パッケージの版**: CI は `uv sync --frozen --all-extras`、ローカルの venv も uv.lock どおり（docling 2.129.0 / docling-core 2.97.2 /
  docling-parse 7.20.0 / docling-ibm-models 4.0.3 / pypdfium2 5.13.0 / torch 2.14.0+cpu）
- **モデルの重み**: TableFormer は docling が `v2.3.0` に固定。レイアウトモデル（docling-layout-heron）は `main` 参照で固定されていないが、
  `main` は 2026-02-09 の `8f39ad3c` から動いておらず、本リポジトリ（2026-09 開始）の CI キャッシュもローカルも同じ版
- **CPU 依存の数値差**（Xeon Cascadelake / AMD EPYC 7763）: 上の 4 でローカルの CPU のまま CI の値が再現し、差はフォントだけで説明できる
- **#86 の実験が古い評価コードだった**: 上の 1 で否定

## 影響

- env の docling 以外に、PP-Structure（ページ画像を OCR する）も p1〜p3 の TSF が 0.0〜0.02 で、同じ原因で下がっていたと考えられる。
  次の bench から日本語フォント入りの値になり、env の docling / ppstructure の値は上がる見込み。結果ドキュメントの更新は最終 run の値で行う
- テキスト層から文字を取る方式（pdfplumber / PyMuPDF / Camelot など）はページ画像を使わないので影響しない
  （Camelot lattice は罫線検出にページ画像を使うが、文字の有無は罫線検出にほぼ影響しない）
- 再現性（企画書 §49）: フォント環境は OS・パッケージの版と同じく結果を左右する実験条件。`fonts_ja` で記録し、report.md の実行環境表にも出す

## 残るリスク

- レイアウトモデルの `revision=main` は docling 側で固定されていない。HF 側が更新されると、キャッシュの無い環境だけ結果が変わり得る。
  現時点で差は無いため今回は固定しない（固定するなら `LayoutOptions(model_spec=...)` で revision を渡す）

## あわせて: `docling_ocr` のバッチ設定

`docling_ocr` は効いていない `settings.perf.page_batch_size = 1` を指定し、run.json にも記録していた（#86 で判明）。
`docling` アダプタと同じく、スレッド版パイプラインが読む `ocr_batch_size` / `layout_batch_size` / `table_batch_size` を 1 にし、
extra にもこの 3 つだけを記録するよう改めた。

スキャン文書 `stat-go-jp-children-population-2026-scan.pdf`（6 ページ）で 2 回ずつ測った（同じ機械・1 プロセスずつ）。

| 設定 | Wall [s] | Peak RSS [MiB] | Total | CF | SED | TSF | ROA |
|---|---|---|---|---|---|---|---|
| 変更前（`page_batch_size=1`、実質は各バッチ 4） | 60.2 / 54.9 | 2294 / 2247 | 78.4 | 0.610 | 1.000 | 0.639 | 0.887 |
| 変更後（ocr / layout / table = 1） | 55.8 / 52.9 | 2119 / 2014 | 78.4 | 0.610 | 1.000 | 0.639 | 0.887 |

- 出力の Markdown は完全一致し、DSQS は全サブスコアで同じ。Peak RSS は平均 2271 → 2067MiB（-9%）、Wall は平均 57.6 → 54.4 秒
- 採否の基準（DSQS の低下が 0.5 以内）を満たすので採用した。主な目的は、効いていない設定を実験条件に残さないこと
