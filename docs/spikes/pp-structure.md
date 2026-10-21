# スパイク: PP-Structure（PaddleOCR PP-StructureV3）

Issue #92 / Sprint 8。Level 2 の 2 本目候補として、レイアウト + 表の最小構成で CPU のメモリ・時間を実測し導入可否を判定する。
判定基準（事前合意）: **Peak RSS 3GB 以下かつ 6 ページ 10 分以内なら導入可**。

## 環境

- Intel Xeon (Cascadelake) 6 コア / RAM 8GB（GPU なし）/ CentOS Stream 9 / Linux 5.14
- Python 3.12、paddleocr 3.7.0、paddlepaddle 3.3.1（CPU）、paddlex 3.7.2、pypdfium2 5.13.0
- `OMP_NUM_THREADS=1`、`cpu_threads=1`、paddle プロセスは 1 つずつ。一時 venv 1.4GB、モデルキャッシュ 902MB（8 モデル）
- 対象: `samples/stat-go-jp-children-population-2026.pdf`（6 ページ）を pypdfium2 で PNG 化（150dpi = 1241x1755、200dpi = 1654x2339）

## 構成（オフにしたもの・入れ替えたもの）

`PPStructureV3(lang="japan", device="cpu", cpu_threads=1, ...)` に以下を指定。

| 引数 | 値 | 理由 |
|---|---|---|
| `use_doc_orientation_classify` / `use_doc_unwarping` | False | 文書向き分類・歪み補正は不要 |
| `use_formula_recognition` / `use_chart_recognition` / `use_seal_recognition` | False | 数式・図表・印章は対象外。モデル数とメモリを削る |
| `use_region_detection` | False | 領域検出は使わない |
| `use_textline_orientation` | False | 縦書き行の向き分類は今回不要 |
| `use_table_recognition` | True | 主眼（レイアウト + 表） |
| `enable_mkldnn` | **False** | True（既定）だとレイアウト検出で `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support`（oneDNN 実行器が RT-DETR の属性に未対応）で落ちる |
| `text_detection_model_name` / `text_recognition_model_name` | `PP-OCRv5_mobile_det` / `PP-OCRv5_mobile_rec` | 既定の server 版は mkldnn なしのテキスト検出で **11.4GB の確保を試みて MemoryError**（VmHWM 4.97GB）。8GB 機では server 版は使えない |
| `text_det_limit_type` / `text_det_limit_side_len` | `max` / 1280 | 既定（min 側）だと入力を縮小せず巨大テンソルになるため、長辺を 1280 に制限 |

この構成でダウンロードされたモデルは 8 個・902MB（13 個・約 1.8GB の既定構成より小さい）。

## 実測

初回ロードはモデル取得込みで 125.6 秒。以降（キャッシュ済み）は import 3.1〜3.8 秒 + init 4.7〜16.4 秒、初期化直後の VmHWM 1.31〜1.40GB。

| ページ | 150dpi wall | 150dpi VmHWM | 表検出数 | 200dpi wall | 200dpi VmHWM | 表検出数 |
|---|---|---|---|---|---|---|
| p1 | 24.3s | 1.91GB | 0 | 17.7s | 1.86GB | 0 |
| p2 | 54.0s | 1.98GB | 2 | 53.5s | 1.95GB | 2 |
| p3 | 37.7s | 2.06GB | 0 | 38.6s | 2.04GB | 0 |
| p4 | 75.4s | 2.08GB | 2 | 76.5s | 2.04GB | 2 |
| p5 | 19.9s | 2.08GB | 0 | 20.5s | 2.06GB | 0 |
| p6 | 12.4s | 2.08GB | 0 | 12.9s | 2.09GB | 0 |
| **6 ページ計** | **223.7s** | **2.08GB** | 4 | **219.7s** | **2.09GB** | 4 |

150dpi と 200dpi の差はほぼ無い。`text_det_limit_side_len=1280` で OCR 入力が頭打ちになり、レイアウト検出も内部で固定サイズにリサイズされるため。**150dpi で足りる。**

## 出力形式

- `res.save_to_json()` → `<stem>_res.json`。トップレベルキーは `input_path` / `page_index` / `page_count` / `width` / `height` / `model_settings` / `doc_preprocessor_res` / `layout_det_res` / `overall_ocr_res` / `parsing_res_list` / `table_res_list`（表があるページのみ）
- 表は `table_res_list[i].pred_html` に **HTML**（`<table><tr><td colspan=.. rowspan=..>`）。結合セルが取れている
- `layout_det_res.boxes[].label` は `paragraph_title` / `text` / `table` / `chart` / `figure_title` / `number` など
- `res.save_to_markdown()` → `<stem>.md` + `imgs/`。6 ページ分の出力は計 2.0MB
- 日本語の**文字化けは無し**（p1 冒頭「くらしの中に 総務省報道資料 … 令和8年5月4日 統計トピックスNo.148 我が国のこどもの数…」）。ただしグラフ内の凡例や軸の数値を本文テキストとして拾う、段落の読み順が一部前後する、といった OCR 由来の揺れはある

## 判定: **導入可**

6 ページ通しで Peak RSS 2.09GB（< 3GB）、wall 224 秒（< 10 分）。基準を両方満たす。
ただし**既定構成のままでは 8GB 機で落ちる**（mkldnn の未対応エラー、server 版 OCR の 11.4GB 確保）ため、上表の設定は必須。

## アダプタ設計への示唆（#96 向け）

- **子プロセス化は必須**。Docling アダプタ（`jdocbench/adapters/docling.py`）と同じく `python -m jdocbench.adapters.ppstructure` を `perf.run_command` で起動し、paddle を親プロセスに載せない
- 入力は **150dpi の PNG**（pypdfium2）。ページごとに `predict` し、1 ページずつ処理する
- 上表のオプションをアダプタに固定で埋め、`extra` に記録する（`enable_mkldnn=False`、mobile det/rec、`text_det_limit_side_len=1280`、オフにした各 `use_*`）。特に `enable_mkldnn=False` と mobile モデルは動作要件
- raw は `save_to_json()` の JSON をページごとに保存（企画書 §54-2）。表は HTML なので正規化（#97）で HTML → IR の表変換が要る
- キャッシュ: `PADDLE_PDX_CACHE_HOME`（既定 `~/.paddlex`、実測 902MB）と `HF_HOME`（`~/.cache/huggingface`、実測 220KB。モデル実体は paddlex 側）。CI では `~/.paddlex` を cache 対象にする（約 0.9GB。初回取得 126 秒）。`PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True` でホスタへの接続確認を省ける
- CI 時間: 6 ページ 224 秒 + 初期化。CI の 15 分枠では他パーサーと合わせて余裕が少ないので、PP-Structure は対象サンプルを絞るか実行時間を計測して調整する

## 後始末

一時 venv（1.4GB）、モデルキャッシュ（`paddlex` 902MB / `hf` 220KB）、PNG・出力 JSON はスクラッチ領域に作成し、**すべて削除済み**。リポジトリと `~/.paddlex` は汚していない。
