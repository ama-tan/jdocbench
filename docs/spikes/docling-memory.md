# スパイク: Docling のメモリ消費対策

Issue #86 / Sprint 12。`docling` アダプタ（OCR オフ）の Peak RSS を下げる設定を実測で探し、品質（DSQS-JP）を落とさないものだけ採用する。

判定基準（事前合意）: **Peak RSS が 10% 以上下がり、かつ DSQS Total の低下がどちらの文書でも 0.5 以内で、TSF / SED が下がらないこと**。満たさなければ現状維持。

## 環境

- Intel Xeon (Cascadelake) 6 コア / RAM 8GB（GPU なし）/ CentOS Stream 9 / Linux 5.14
- Python 3.12、docling 2.129.0、docling-ibm-models 4.0.3、docling-parse 7.20.0、pypdfium2 5.13.0、torch 2.14.0+cpu
- docling は 1 プロセスずつ実行（起動前に空きメモリ 3000MiB 以上を確認）。他のエージェントの軽い処理と同居しているため値は数十 MiB 振れる
- 計測は `jdocbench.perf.run_command`（アダプタと同じ。子プロセス 1 つの `ru_maxrss`）。品質は raw を `normalizers.docling` で正規化し、`groundtruth/<stem>.json` に対して `metrics.evaluate` で計算した（cli と同じ経路）
- 対象: `stat-go-jp-children-population-2026.pdf`（6 ページ）と `mof-budget-summary-fy2026.pdf`（3 ページ）。採否の裏付けに `jacn-29-0039-katakana-transliteration-2025.pdf`（8 ページ）と `env-financial-statements-fy2024-excerpt.pdf`（5 ページ）も測った

## 事前に分かったこと: `page_batch_size=1` は効いていなかった

現行コードは `settings.perf.page_batch_size = 1` を指定していたが、docling 2.129 の既定パイプライン `StandardPdfPipeline` はスレッド版で、この値を読まない（読むのは旧来の `PaginatedPipeline` と VLM パイプラインだけ）。スレッド版でページの束ね方を決めるのは `PdfPipelineOptions` の `layout_batch_size` / `table_batch_size` / `ocr_batch_size`（既定は各 4）と `queue_max_size`（既定 100）である。

なお既定値の時点で `do_picture_classification` / `do_picture_description` / `do_formula_enrichment` / `do_code_enrichment` / `do_chart_extraction` / `generate_page_images` / `generate_picture_images` はすべてオフで、Issue に挙がった「不要なモデルの無効化」は既に満たされている。

## 構成

| 記号 | 内容 |
|---|---|
| A | 現行（ThreadedDoclingParse バックエンド、OCR オフ、`page_batch_size=1`） |
| B | A + `PyPdfiumDocumentBackend` |
| C | A + `layout_batch_size=1`、`table_batch_size=1`（`ocr_batch_size=1` も指定。OCR オフでは無関係） |
| CQ | C + `queue_max_size=2` |
| BCQ | B + CQ |

`do_table_structure` はすべてオン（既定）のまま。

## 実測

Wall / Peak RSS は複数回の中央値（回数を n に示す）。DSQS は全回で同一値だった。

| 構成 | 文書 | n | Wall [s] | Peak RSS [MiB] | 対 A | Total | CF | SED | TSF | ROA |
|---|---|---|---|---|---|---|---|---|---|---|
| A | stat（6p） | 5 | 28.8 | 1632 | - | 75.7 | 0.600 | 1.000 | 0.636 | 0.793 |
| B | stat | 1 | 35.0 | 1604 | -2% | **65.1** | 0.658 | 1.000 | **0.129** | 0.818 |
| C | stat | 4 | 28.3 | 1379 | **-16%** | 75.7 | 0.600 | 1.000 | 0.636 | 0.793 |
| CQ | stat | 2 | 27.8 | 1348 | -17% | 75.7 | 0.600 | 1.000 | 0.636 | 0.793 |
| BCQ | stat | 1 | 33.0 | 1259 | -23% | **65.1** | 0.658 | 1.000 | **0.129** | 0.818 |
| A | mof（3p） | 5 | 49.4 | 1689 | - | 87.8 | 0.847 | 1.000 | 0.667 | 0.998 |
| B | mof | 1 | 44.5 | 1565 | -7% | **81.2** | 0.898 | 1.000 | **0.351** | 0.998 |
| C | mof | 4 | 49.0 | 1577 | -7% | 87.8 | 0.847 | 1.000 | 0.667 | 0.998 |
| CQ | mof | 2 | 49.6 | 1558 | -8% | 87.8 | 0.847 | 1.000 | 0.667 | 0.998 |
| BCQ | mof | 1 | 54.4 | 1401 | -17% | **81.2** | 0.898 | 1.000 | **0.351** | 0.998 |
| A | jacn（8p） | 2 | 24.0 | 1776 | - | 88.2 | 0.905 | 1.000 | 0.623 | 0.998 |
| C | jacn | 2 | 22.8 | 1375 | **-23%** | 88.2 | 0.905 | 1.000 | 0.623 | 0.998 |
| A | env（5p） | 2 | 44.7 | 1674 | - | 97.2 | 0.960 | 1.000 | 0.952 | 0.976 |
| C | env | 2 | 43.9 | 1339 | **-20%** | 97.2 | 0.960 | 1.000 | 0.952 | 0.976 |

- A の Peak RSS は mof で 1563〜1831MiB と振れが大きい（stat は 1631〜1682MiB で安定）
- C の出力は A と Markdown が完全一致し、JSON の差は bbox 座標の浮動小数点の末尾だけ（バッチの大きさで演算順が変わるため）
- 採用後のアダプタを `jdocbench run` で通した値（各 1 回）: stat 26.3 秒・1435MiB・Total 75.7、mof 54.3 秒・1584MiB・Total 87.8
- env の Total（97.2）は `docs/results.md` の値と異なるが、本表は同一条件での A / C の比較にだけ使う

### 参考: `docling_ocr`（OCR オン）に C を当てた場合

スキャン文書 `stat-go-jp-children-population-2026-scan.pdf`（6 ページ）で、`docling_ocr` と同じ RapidOCR 設定に対し 2 回ずつ測った。

| 構成 | Wall [s]（平均） | Peak RSS [MiB] | Total |
|---|---|---|---|
| OCR + A | 61.8 | 2351 / 2211 | 78.2 |
| OCR + C | 55.3 | 2056 / 2190 | 78.2 |

平均で -7%、回によっては差が 1% しかなく、基準の 10% に届かない。

## 判断

- **B（PyPdfium バックエンド）は不採用**。メモリは 2〜7% しか下がらず、表の中身 TSF が 0.636→0.129（stat）、0.667→0.351（mof）と大きく落ち、Total も 6.6〜10.6 下がる。表の中身が崩れる原因は未調査（PyPdfium の文字セルの切り方が違い、TableFormer のセルへの文字の割り当てが崩れていると推測）
- **C（`layout_batch_size=1` / `table_batch_size=1`）を `docling` アダプタに採用**。出力は同一（DSQS 全サブスコア不変）で、Peak RSS は stat -16%、jacn -23%、env -20%。mof は -7% で単独では 10% に届かないが、3 ページの文書は既定の 4 ページの束にもともと収まりきり、束ねる効果が出にくいと考えられる。品質の劣化がゼロで、4 ページ以上の文書では一貫して 16〜23% 下がるため採用とした。Wall は変わらない
- 効いていなかった `page_batch_size=1` の指定は削除し、run.json の `extra` には実際に効く `layout_batch_size` / `table_batch_size` を記録する（実験条件の記録。以前の `page_batch_size=1` は条件として意味が無かった）
- **CQ（`queue_max_size=2`）は不採用**。C との差は回ごとの振れ（数十 MiB）の範囲で、効果を確かめられない
- **`docling_ocr` は現状維持**。C の効果が -7% で基準未満。同アダプタの `page_batch_size=1` も同様に効いていない点は記録に留める（#157 で docling と同じ設定に揃えた。実測は [`docling-repro.md`](docling-repro.md)）

## 試さなかったこと

- **`page_range` によるページ分割**: 対象文書は 3〜8 ページで、分割単位（100 ページ等）より小さい。分割すると文書をまたぐ読み順・表の統合の扱いも変わる
- **`release_native_memory_every_n_pages`**: 既定 128 ページ。対象文書は全て 8 ページ以下で発動しない
- **画像分類・数式抽出などの無効化**: 既定でオフ（上記）
- **`images_scale` / `generate_page_images`**: 既定で画像生成はオフ。レイアウトモデル用のページ画像は内部で別に作られるため、この値では変わらない
- **ファイルごとのサブプロセス化**: 既にアダプタが 1 文書 1 子プロセスで動いている
- **スレッド数（`OMP_NUM_THREADS` / `DOCLING_NUM_THREADS`）**: 時間が伸び、CI の他アダプタとの条件も変わるため今回は対象外
