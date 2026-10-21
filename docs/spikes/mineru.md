# スパイク: MinerU 4.0.12（basic tier = pipeline 相当、CPU / ONNX Runtime）

Issue #195 / Sprint 16。Sprint 8 で「新版が VLM 中心・独自ライセンス」として保留した MinerU を、
VLM を使わない経路（レイアウト・OCR・表の個別モデル）で CPU 8GB に載るか、ライセンスを本プロジェクトの方針
（MIT の本体と異なるものはオプトイン extra として記録する）で扱えるかを確かめる。
判定基準（Issue と事前合意）: **実効 Peak RSS 3GB 以内**、**外部 API を呼ばない**、**ライセンスを
docs/licenses.md に記録できる**、CPU 時間が CI の bench（文書ごとのジョブ、上限 30 分）に収まる、
依存が既存の lock と共存できる（他アダプタの版を変えない）。

## 結論: **導入する（go）**

- ライセンス: コードは **MinerU Open Source License**（Apache-2.0 に追加条項。一定規模以上の商用利用は別ライセンス、
  オンラインサービスでの利用表示義務）。非商用限定ではなく条件を記録できるため、PyMuPDF（AGPL）と同じく
  **オプトイン**（extra `level2-mineru` + 環境変数 `JDOCBENCH_MINERU=1`）とする。basic tier が使うモデルの
  取得元はすべて **Apache-2.0**
- メモリ: プロセスツリー全体の Peak RSS は全 9 文書で **1.05〜2.54GB**（最大は 2 段組み論文の jsda）。3GB 以内
- 時間: 1 文書 **7.5〜21.6 秒**（1 ページ 1.9〜5.4 秒）。docling（1 文書 40 秒前後）より速く、CI の枠に十分収まる
- 外部 API: 呼ばない。ネットワーク名前空間を切った状態（`unshare -rn`）でも同じ出力で完走した
- 依存: torch を引かない（ONNX Runtime のみ）。`uv lock` で既存の 215 パッケージの版は 1 つも変わらず、44 パッケージの追加だけ。
  OpenCV（#171）も要求しない

## 環境

- Intel Xeon (Cascadelake) 6 コア / RAM 8GB（GPU なし）/ CentOS Stream 9 / Linux 5.14。他エージェントの処理と同居
  （計測開始時の available は 5.4〜5.7GB）。重い処理は 1 つずつ（ロックで直列化）
- Python 3.12.13、一時 venv（`~/.cache/jdocbench-spikes/mineru/`。プロジェクトの venv・`pyproject.toml`・`uv.lock` は
  スパイク中は触っていない）
- 計測は自作の監視（psutil で 0.2 秒ごとにコマンドとその子孫全部の RSS / PSS を合計し、最大値を取る）。
  MinerU は表認識を multiprocessing の spawn した子プロセスで動かし、resource_tracker も立つため、
  親子合わせて 3〜5 プロセスになる

## バージョンとライセンス

| 項目 | 値 | 確認元 |
|---|---|---|
| mineru | 4.0.12（2026-10-11 リリース） | PyPI / `mineru-4.0.12.dist-info/METADATA` |
| docvortex（middle JSON の型・PDF 処理） | 0.5.16 | 同上 |
| onnxruntime | 1.31.0（スパイク venv）/ 1.30.0（プロジェクトの lock） | 同上 |
| magic-pdf（旧名） | 1.3.12 で更新停止（AGPL-3.0）。現行は `mineru` | PyPI |
| コードのライセンス | **MinerU Open Source License**（`License-Expression: LicenseRef-MinerU-Open-Source-License`） | https://github.com/opendatalab/MinerU/blob/master/LICENSE.md |
| docvortex | MIT AND Apache-2.0 AND BSD-3-Clause | METADATA |
| mineru-vl-utils | Apache-2.0（VLM 用。basic tier では使わない） | METADATA |
| mineru-llama-cpp | PyPI・METADATA にライセンス表記なし（同梱は GCC Runtime Library Exception の文書のみ）。VLM サーバー（`mineru-kit vlm-server`）だけが import し、basic tier では使わない | METADATA / `mineru/kit/vlm_server/` |
| モデル（basic / ONNX） | `opendatalab/MinerU-4_models_onnx`（HF のメタデータにライセンス無し。README: "Original model licenses and attribution remain applicable"）。`manifest.json` の取得元はすべて **Apache-2.0**: `PaddlePaddle/PP-DocLayoutV2_onnx`、`PaddlePaddle/PP-OCRv6_small_rec_onnx`、`PaddlePaddle/PP-OCRv6_tiny_det_onnx`、`jinzhenj/PP-FormulaNet_plus-M_onnx`、`opendatalab/MinerU-4_models_torch`（数式・印章・表の PP-LCNet / SLANet+ / UNet の変換元） | HF API の `cardData.license` |

LICENSE.md の要点（英語版の原文から）:

> MinerU is licensed under Apache License 2.0 and is subject to the additional terms below.
>
> **1. Commercial License and Thresholds** — MinerU may be used for commercial purposes without a separate commercial license.
> However, if you and your Affiliates, on a consolidated basis, meet either of the following thresholds, you must obtain a separate
> commercial license from [MinerU Team] before continuing such use: a. monthly active users (MAU) exceed 100 million; or
> b. total monthly revenue exceeds USD 20 million.
>
> **2. Online Service Attribution Obligation** — If you provide online services to third parties based on MinerU, you must clearly
> and prominently indicate, in the relevant product or service interface or in publicly available documentation, that MinerU is used.
>
> **3. Termination** — (1 の商用ライセンスを取らずに使い続けた場合、2 の表示義務を守らない場合はライセンスが自動終了する)

判断: 非商用限定・用途制限ではなく、**規模の閾値と表示義務を足した Apache-2.0** である。本ベンチマークの利用
（ローカルでの比較実験、結果の公開）は閾値に当たらないが、jdocbench を MinerU 込みでオンライン提供する場合は
表示義務が生じ、MIT の本体と条件が異なる。そのため既定の `uv sync` には入れず、PyMuPDF と同じく
**明示的に選んだときだけ入る extra**（`level2-mineru`）にし、アダプタも `JDOCBENCH_MINERU=1` のときだけ動かす。
条件は docs/licenses.md に記録する。

VLM backend（`standard` / `advanced` tier）の重み（`MinerU2.5` 系の GGUF 等）は今回入れていないため確認対象外。
basic tier は VLM の重みを取得しない（`mineru-kit models download --tier basic` の取得物は上表の ONNX 13 ファイルのみ）。

## MinerU 4 の構成（tier）

MinerU 4 では旧来の backend 名（pipeline / vlm / hybrid）ではなく **tier** を選ぶ。

| tier | 中身 | 本件 |
|---|---|---|
| flash | 非 PDF（Office / HTML 等）向けの軽量変換 | 対象外 |
| **basic** | 小モデルだけ（旧 pipeline 相当）: Layout = PP-DocLayoutV2、OCR = PP-OCRv6 tiny det + small rec、表 = PP-LCNet（分類）+ SLANet+ / UNet、数式 = PP-FormulaNet plus-M、印章 = PP-OCRv4 seal det | **採用** |
| standard / advanced | 小モデル + VLM（Linux の既定は llama.cpp の in-process エンジン。vLLM / LMDeploy も可） | 対象外（Level 3。marker のスパイクと同じく 3GB 基準を超える見込み） |

- 小モデルの推論は **ONNX Runtime（CPU）** か torch。Linux の基本パッケージ（`pip install mineru`）の既定は ONNX で、
  torch（`mineru[torch]`）は入れていない。プロジェクトの `--all-extras` では torch（level2）が同居するため、
  アダプタは `MINERU_MODEL_SMALL_BACKEND=onnx` を固定する
- 解析は in-process（`mineru.parser.parse`）。`mineru-kit parse` も同じ関数を呼ぶ。サーバーは立たない
- `ocr_mode=auto` は**文書単位**で `txt` / `ocr` を決める（`extensions.mineru.parse_mode`）。`txt` でもテキスト層の無いページは OCR される:
  stat-go-jp の p1〜p3（テキスト層）と stat-go-jp-scan の p4〜p6（画像のみ）を混ぜた PDF は `parse_mode: txt` と判定されたが、
  p4〜p6 の文字数は 1,490 / 167 / 343 で、scan 単体を OCR した結果（1,490 / 169 / 342）とほぼ同じだった（basic tier は
  レイアウトの文字ブロックごとに OCR の検出をかけ、テキスト層の無いブロックは認識結果で埋める）

## 外部通信・利用状況の送信

- 公式リモート解析（mineru.net）は `--remote` / `--remote-url` を指定したときだけ。ローカル解析（`mineru.parser.parse`）は使わない
- LLM 補助の後処理（`llm_aided`: 見出しレベル推定・ページをまたぐ表のセル結合。既定の接続先は DashScope）は
  `features.title_leveling` / `cross_page_table_cell_merge` が既定で **False**。ただし `~/.mineru/config.yaml`（`MINERU_CONFIG`）や
  環境変数で有効にできるため、アダプタは `MINERU_LLM_AIDED_FEATURES_TITLE_LEVELING=false` /
  `MINERU_LLM_AIDED_FEATURES_CROSS_PAGE_TABLE_CELL_MERGE=false` を渡し（環境変数は設定ファイルより優先される）、子プロセスでも
  `config.llm_aided.features` が無効であることを確かめてから解析する（有効なら例外で止める）。設定ファイルで両方を有効にした状態で、
  環境変数ありなら無効になること、環境変数なしなら解析前に止まることを確認した（Codex レビューの指摘）
- 利用状況の送信（telemetry）は `mineru` CLI の文書ライブラリ（`mineru.doclib`）の機能で、初回に対話で同意を聞く。
  `mineru.parser` / `mineru.backend` / `mineru.model` は doclib を import しない（grep で確認）。アダプタは doclib を通らない
- モデル取得元の `auto` は Hugging Face に疎通確認し、結果を `~/.mineru` の設定ファイルに書き込む。アダプタは
  `MINERU_MODEL_SOURCE=huggingface` を明示し（利用者が設定済みならそれを使う）、モデルが揃っていれば取得しない
- **ネットワークを切った状態で完走することを確認**: `unshare -rn`（ネットワーク名前空間を新規に作り loopback 以外を持たない）の中で
  `MINERU_MODEL_SOURCE=local` で stat-go-jp を解析し、ネットワークありの実行と middle JSON の本文が同一（差分は保存時に付く
  `image_path` だけ）

## 手順（再現用）

```bash
S=~/.cache/jdocbench-spikes/mineru; mkdir -p $S && cd $S
uv venv -p 3.12 .venv
VIRTUAL_ENV=$S/.venv uv pip install mineru==4.0.12 psutil   # venv 634MB。torch・OpenCV は入らない
export MINERU_HOME=$S/home MINERU_MODEL_SMALL_BACKEND=onnx
.venv/bin/mineru-kit models download --tier basic --small-backend onnx --source huggingface
#   → 13 ファイル・約 820MiB（du 819M）、未認証で 72 秒。保存先は $MINERU_HOME/models/MinerU-4_models_onnx
MINERU_MODEL_SOURCE=local .venv/bin/mineru-kit parse samples/stat-go-jp-children-population-2026.pdf \
  -o out/children.zip -f zip --tier basic
#   → markdown.md / middle_json.json / structured_content.json / model_output.json / images/*.jpg
```

モデルは**使うものだけが解析中に自動取得**される（モデル未取得の状態から stat-go-jp を解析すると 237MB だけ取得し、
wall は 41 秒。数式モデル 591MB は数式のある文書で初めて取得される）。CI では先に `mineru-kit models download` で
全部を揃え、取得時間が MinerU の Wall time に入らないようにする。

## 実測

### mineru-kit 単体（スパイク venv、`MINERU_INTRA_OP_NUM_THREADS=4`）

| 文書 | ページ | 経路 | wall | wall/page | Peak RSS（ツリー合計） | Peak PSS |
|---|---|---|---|---|---|---|
| stat-go-jp-children-population-2026（デジタル） | 6 | txt | 16.2s | 2.7s | **1,508 MiB** | 1,457 MiB |
| estat-census-1920-scan-excerpt（実物スキャン） | 4 | ocr | 22.1s | 5.5s | **1,891 MiB** | 1,840 MiB |
| stat-go-jp-children-population-2026-scan（合成スキャン） | 6 | ocr | 23.3s | 3.9s | 2,131 MiB | 2,070 MiB |

### jdocbench のアダプタ経由（全 9 文書、スレッド数は既定）

`jdocbench run` を 1 文書ずつ実行。「Wall / RSS（run.json）」はアダプタが `perf.run_command` で記録する値
（`ru_maxrss` は**最大の 1 プロセス**の値で、spawn した表認識の子プロセスとの合計ではない）。
「ツリー合計」は `jdocbench run` 全体（jdocbench・アダプタの子プロセス・MinerU の子プロセス）の RSS 合計の最大値。
DSQS は現行の Ground Truth でローカル計算した値（CI の値ではない。参考に docs/results.md の docling / ppstructure を並べる）。

| 文書 | Wall（run.json） | RSS（run.json） | ツリー合計 | 表 | CF | SED | TSF | ROA | Total | docling | ppstructure |
|---|---|---|---|---|---|---|---|---|---|---|---|
| env-financial-statements-fy2024-excerpt | 13.7s | 1,663 MiB | 1,722 MiB | 7 | 0.924 | 1.000 | 0.871 | 0.972 | **94.2** | 97.2 | 85.7 |
| estat-census-1920-scan-excerpt | 18.5s | 1,739 MiB | 1,868 MiB | 2 | 0.529 | 1.000 | 0.455 | 0.994 | **74.4** | 0.0（docling_ocr 82.1） | 65.4 |
| geps-manual-internet-excerpt | 15.4s | 2,008 MiB | 2,163 MiB | 2 | 0.613 | 1.000 | 0.909 | 1.000 | **88.1** | 91.9 | 74.2 |
| jacn-29-0039-katakana-transliteration-2025 | 19.9s | 2,201 MiB | 2,453 MiB | 3 | 0.929 | 1.000 | 0.623 | 1.000 | **88.8** | 88.2 | 88.3 |
| jsda-9-3-e15-oais-information-package-2025-excerpt | 21.6s | 2,377 MiB | **2,527 MiB** | 1 | 0.945 | 1.000 | 0.591 | 0.981 | **87.9** | 86.1 | 82.2 |
| mof-budget-summary-fy2026 | 7.5s | 944 MiB | 1,089 MiB | 3 | 0.300 | 1.000 | 0.026 | 1.000 | **58.2** | 87.8 | 89.0 |
| mof-financial-statements-fy2024-excerpt | 9.9s | 1,248 MiB | 1,394 MiB | 10 | 0.509 | 0.947 | 0.452 | 0.801 | **67.7** | 79.1 | 71.5 |
| stat-go-jp-children-population-2026-scan | 20.5s | 1,943 MiB | 2,078 MiB | 4 | 0.587 | 1.000 | 0.694 | 0.913 | **79.9** | 0.0（docling_ocr 78.4） | 81.4 |
| stat-go-jp-children-population-2026 | 11.6s | 1,335 MiB | 1,479 MiB | 4 | 0.561 | 1.000 | 0.582 | 0.910 | **76.3** | 75.8 | 78.0 |

- 9 文書の Total 平均は **79.5**（ppstructure 79.5 と同じ、docling 67.3。スキャンも 1 本で通る）。Wall は合計 139 秒で、
  ppstructure（CI で 1 文書 103〜450 秒）の 1/10 以下
- プロジェクトの lock と同じ版（numpy 2.3.5、onnxruntime 1.30.0、torch 2.14.0+cpu と docling も同居）の venv でも
  stat-go-jp と estat を回し、**middle JSON がスパイク venv の出力とバイト単位で同一**、表認識の ONNX セッションは
  `CPUExecutionProvider`、stderr に torch の痕跡なし（`MINERU_MODEL_SMALL_BACKEND=onnx` が効いている）
- CI の ubuntu-latest（4 vCPU 程度）では開発機（6 コア）の 1.5〜2 倍と見ても 1 文書 40 秒前後で、bench の 30 分枠に影響しない

### 品質の所見（目視）

- **読み順は良い**。stat-go-jp の 1 ページ目は日付 → 表題 → 副題 → 本文 → ポイントの順で、marker・docling で崩れた箇所も自然。
  2 段組み（jacn / jsda）も ROA 0.98〜1.00
- **全角の数字・記号を半角にする**（`２` → `2`、`％` → `%`、`１` → `1`）。DSQS-JP は NFKC 正規化をしないため（企画書 §16）
  CF が下がる（stat-go-jp の CF 0.56 の主因）。パーサーの出力のまま評価し、正規化で補正はしない
- **図（chart）の中の文字は出さない**（図は画像として `images/` に切り出され、本文には入らない）。GT は図中ラベルを段落に持つため CF が下がる
- **罫線の無い表で行が潰れる**: mof-budget の 3 表は列は 5 列で取れるが、行がすべて 1 行に連結される
  （`778,19087,318286,471…`。セル内の行を区切りなしで連結）。TSF 0.03。stat-go-jp の表 1 も多段の行ラベルの行が連結される
- 実物スキャン（estat）は OCR で旧字体を一部取り違える（`ハ` → `八`、`ヲ` → `尹`）が、表は `rowspan` / `colspan` 付きで構造が取れる
- geps のスクリーンショットの中の文字を `code` ブロックとして OCR する（GT は画像の中の文字を含まない規約）

## 出力形式（IR マッピングの根拠）

`ParseResult.save()` が書くもの: `middle_json.json`（構造。正規化の入力）、`markdown.md`、`structured_content.json`
（middle JSON から index 等を落とした簡約版）、`model_output.json`（レイアウト・OCR の生出力。約 1.6MB/6 ページ）、`images/`（表・図の切り出し JPEG）。

- `middle_json.json` の schema は `docvortex.middle` 2.0。`pages[]`（`page_idx` は **0 始まり**）→ `blocks[]`。**ブロックの並びが読み順**
- ブロックの `type`（9 文書で観測）: `text` / `paragraph_title`（`level` 2〜6）/ `doc_title`（`level` 1）/ `header` / `footer` /
  `page_number` / `ref_text` / `table` / `image` / `chart` / `code`。スキーマ上はほかに `list` / `index` / `equation` /
  `aside_text` / `page_footnote` がある
- `content` は 3 通り: 行内要素の配列（`{"type": "text", "content": "…"}`、`equation_inline`、`code_inline`、`hyperlink`（中に行内要素））、
  子ブロックの配列（`table` = `table_caption` → `table_body` → `table_footnote`、`image` / `chart` / `code` も同形）、
  文字列（`table_body` は HTML の `<table>`（`rowspan` / `colspan` あり）、`code_body` はテキスト、`image_body` / `chart_body` は空）
- **bbox はページの幅・高さで割った 0〜1**（左上原点・y 下向き）。ページの大きさは `extensions.docvortex_layout.pages[].width_pt / height_pt`
- 経路は `extensions.mineru`（`{"tier": "basic", "parse_mode": "txt"}`。スキャンは `ocr`）

抜粋（stat-go-jp の 1 ページ目と 2 ページ目の表。一部省略）:

```json
{
 "metadata": {"file_suffix": "pdf", "producer": {"name": "mineru", "version": "4.0.12"},
              "document": {"languages": ["ja"], "page_count": 6, "page_count_kind": "physical", "…": "…"}},
 "extensions": {"mineru": {"tier": "basic", "parse_mode": "txt"},
                "docvortex_layout": {"version": 1, "pages": [{"page_idx": 0, "width_pt": 595.22, "height_pt": 842.0}, "…"]}},
 "pages": [
  {"page_idx": 0, "blocks": [
   {"type": "header", "index": 0, "bbox": [0.105, 0.143, 0.226, 0.167], "content": [{"type": "text", "content": "報道資料"}]},
   {"type": "text", "index": 3, "bbox": [0.71, 0.221, 0.871, 0.238], "content": [{"type": "text", "content": "令和8年5月4日"}]},
   {"type": "doc_title", "index": 5, "bbox": [0.278, 0.318, 0.719, 0.349], "level": 1,
    "content": [{"type": "text", "content": "我 が 国 の こ ど も の 数"}]},
   "…"]},
  {"page_idx": 1, "blocks": ["…",
   {"type": "table", "index": 8, "bbox": [0.467, 0.174, 0.902, 0.371], "content": [
    {"type": "table_caption", "index": 7, "bbox": [0.589, 0.154, 0.794, 0.17], "content": [{"type": "text", "content": "表1 男女別こどもの数"}]},
    {"type": "table_body", "index": 8, "bbox": [0.467, 0.174, 0.902, 0.371], "image_path": "images/page_1_table_8.jpg",
     "content": "<table><tbody><tr><td colspan=\"2\"></td><td>2026年4月1日現在</td><td>2025年4月1日現在</td><td>対前年増減数</td></tr><tr><td rowspan=\"2\">こどもの数(万人)</td><td>男女計男女</td><td>1329681648</td>…</tbody></table>"},
    {"type": "table_footnote", "index": 9, "bbox": [0.478, 0.383, 0.891, 0.43], "content": [{"type": "text", "content": "注）表中の数値は、単位未満を四捨五入しているため、…"}]}]},
   "…"]}
 ]
}
```

IR への対応（`jdocbench/normalizers/mineru.py`）: ブロックの並び = 読み順、行内要素の連結 = 1 paragraph（見出しは `extra.level`）、
子ブロックの配列は順に展開、`table_body` の HTML → table（PP-Structure の正規化と同じ HTML 分解。結合セルは範囲の先頭だけ）、
`image_body` / `chart_body` / `equation` は落とす、ヘッダー・フッター・ページ番号は段落に残す（GT の規約と同じ）。
bbox は PDF ポイント（左上原点）に直す。表の中身は段落に重ならないので `drop_table_content_lines` は使わない。

## 依存の共存（#171・#194 との関係）

- `mineru` の必須依存は onnxruntime・pypdfium2・pillow・shapely・pyclipper・tokenizers・openai・gradio・fastapi など。
  **torch・transformers・OpenCV は要求しない**（torch は `mineru[torch]`、vLLM は `mineru[full]` の extra）。openai / gradio /
  fastapi は Web UI・API サーバー・リモート VLM 用で、アダプタの経路では使わない
- プロジェクトの `pyproject.toml` に `level2-mineru = ["mineru>=4.0.12"]` を足して `uv lock` した結果: **既存パッケージの版の変更 0、
  追加 44**（mineru・docvortex・gradio・fastapi・openai など）。override も不要（#194 で要った pillow / pypdfium2 の override に当たる衝突は無い。
  mineru の `pillow>=11.0.0` / `pypdfium2<6,>=5.10.1` は lock の 12.3.0 / 5.13.0 で満たされる）
- `--all-extras` の `.venv` は約 600MB 増える（スパイク venv の mineru 単体が 634MB）

## 判定: **導入する（go）**

| 基準 | 結果 | 判定 |
|---|---|---|
| ライセンスをオプトインとして記録できる | コード: Apache-2.0 + 追加条項（MAU 1 億 / 月商 2,000 万ドル超は商用ライセンス、オンライン提供時の表示義務）。モデル: 取得元はすべて Apache-2.0 | 合格（extra `level2-mineru` + `JDOCBENCH_MINERU=1`） |
| 実効 Peak RSS ≤ 3GB | ツリー合計で最大 2,527 MiB（jsda）、run.json の ru_maxrss で最大 2,377 MiB | 合格 |
| CPU 時間（CI の 1 文書ジョブ 30 分枠） | 1 文書 7.5〜21.6 秒（開発機 6 コア） | 合格 |
| 外部 API を呼ばない | ネットワーク名前空間を切って完走。LLM 補助・リモート解析・telemetry は経路に無い | 合格 |
| 依存が lock と共存 | 既存の版の変更 0、override 不要 | 合格 |

### 判定が変わる条件（再検討のきっかけ）

- MinerU のライセンスが変わる（非商用化、閾値の引き下げ等）。版を上げるときは LICENSE.md を確認する
- basic tier のモデルの取得元のライセンスが変わる（`manifest.json` の `source.repo`）
- `--all-extras` で torch が同居していても ONNX が選ばれる前提（`MINERU_MODEL_SMALL_BACKEND=onnx`）が崩れる変更
- standard tier（VLM）を Level 3 として評価する場合は別スパイク（llama.cpp の in-process エンジン + MinerU2.5 系の重み。3GB 基準の見直しが前提）

## 後始末

- スパイク venv（`.venv` 634MB、lock と同じ版の確認用 `.venv-lock` 2.0GB）・モデル（820MiB）・出力は
  `~/.cache/jdocbench-spikes/mineru/` に残してある（再計測用。リポジトリ外）。常駐プロセスは無い（MinerU はサーバーを立てない）
- `~/.mineru` には何も書いていない（`MINERU_HOME` をスパイク領域に向けた）
