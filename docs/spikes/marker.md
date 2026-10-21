# スパイク: Marker 2.0.0（Level 3 / Local VLM、CPU + llama.cpp）

Issue #125 / Sprint 10。Level 3（Local VLM）の 1 本目の候補として、marker-pdf 2.0.0 を CPU で動かし、
メモリ・時間・出力形式を実測して導入可否を判定する。
判定基準（事前合意）: **実効 Peak RSS 3GB 超は見送り**。**`llama-server` の導入を CI（ubuntu-latest）で自動化できない場合も見送り**。

## 結論: **見送り（no-go）**

VLM を使う経路では、実効 Peak RSS（marker 本体と、marker が起動する常駐サーバー群の合計）が
**デジタル PDF で 4,116 MiB、スキャン PDF で 5,122 MiB** となり、基準の 3GB を大きく超える。
`llama-server` だけで 2.2〜2.9GB を使うため、並列スロットを 1 に絞っても 4,038 MiB までしか下がらない。
`llama-server` の導入自体は GitHub release から非対話で取得でき、CI の自動化は可能（見送り理由ではない）。

## 環境

- Intel Xeon (Cascadelake) 6 コア / RAM 8GB（GPU なし）/ CentOS Stream 9 / Linux 5.14。他エージェントの軽い処理と同居
- Python 3.12.13、一時 venv（スクラッチ領域。プロジェクトの venv・`pyproject.toml`・`uv.lock` は触っていない）
- `OMP_NUM_THREADS=4`、`MKL_NUM_THREADS=4`、`llama-server --threads 4 --threads-batch 4`。marker プロセスは 1 つずつ実行
- Docling / PaddleOCR は同時に動かしていない

## バージョンとライセンス

| 項目 | 値 | 確認元 |
|---|---|---|
| marker-pdf | 2.0.0 | 導入した `marker_pdf-2.0.0.dist-info/METADATA` |
| surya-ocr | 0.22.1（marker の依存 `surya-ocr<0.23.0,>=0.22.1`） | 同 METADATA |
| torch / torchvision | 2.14.0+cpu / 0.29.0+cpu（PyTorch CPU index） | 同上 |
| transformers / pdftext | 5.17.0 / 0.7.1 | 同上 |
| llama.cpp（llama-server） | b11200（`version: 0.5.0-dev (build 11200, commit 81bc6b83f)`、GNU 11.4.0 でビルド） | `llama-server --version` |
| VLM 重み | `datalab-to/surya-ocr-2-gguf` の `surya-2.gguf`（1,266,400,864 bytes、F16、arch `qwen35`、650M params）+ `surya-2-mmproj.gguf`（204,986,688 bytes） | HF API / GGUF ヘッダ |
| コードのライセンス | **Apache-2.0**（marker-pdf・surya-ocr とも METADATA の `License: Apache-2.0`） | METADATA |
| 重みのライセンス | **AI PUBS OPEN RAIL-M LICENSE (MODIFIED)**（HF モデルカードのメタデータは `license: openrail`）。README の説明は「modified AI Pubs Open Rail-M license (free for research, personal use, and startups under $5M funding/revenue)」 | `surya-ocr-2-gguf` の `LICENSE` 冒頭 / README |

示唆: 重みは研究・個人・年商/調達 $5M 未満のみ無償で、MIT の本体とは条件が異なる。
導入するなら PyMuPDF（AGPL）と同じく **オプトイン extra**（`pyproject.toml` のコメントと `docs/licenses.md` に明記）にし、
既定の `uv sync` では入らない構成にする必要がある。
また marker-pdf は `anthropic` / `openai` / `google-genai` を必須依存に持つ（`--use_llm` 用）。
Parsing 経路で外部 API を呼ばない原則（企画書 §54）のため、`--use_llm` は使わないこと。

## CPU での推論経路

marker 2.0.0 は処理ごとに**別プロセスのサーバー**を起動し、HTTP で推論を依頼する構成になっている。

| プロセス | 役割 | 起動条件 | 終了 |
|---|---|---|---|
| `marker_single`（本体） | PDF 読み込み（pdftext）、ブロック構築、表の再構成、出力 | 常に | 処理後に終了 |
| `python -m surya.fast_layout.server` | 軽量レイアウト検出（rf-detr、`datalab-to/surya_layout2`） | fast モード（CPU の既定）と `--disable_ocr` | **marker 終了後も常駐**（`keep_alive=True` 固定） |
| `python -m surya.ocr_error.server` | テキスト層の文字化け判定 | `--disable_ocr` 以外 | **marker 終了後も常駐** |
| `llama-server`（llama.cpp） | surya VLM（layout / OCR / 表認識） | balanced モード、または fast モードでスキャン・文字化けページがあるとき | marker の atexit で kill |

- VLM は **`llama-server` バイナリ（llama.cpp）+ GGUF 重み**が必要。marker/surya は `PATH` 上の `llama-server`（または `LLAMA_CPP_BINARY`）を探し、GGUF を HF Hub（`SURYA_GGUF_REPO`）から取得し、空きポートで自動起動する（`SURYA_INFERENCE_BACKEND=llamacpp`。NVIDIA GPU があれば既定は vllm の docker）
- 起動引数は `-m surya-2.gguf --mmproj surya-2-mmproj.gguf -ngl 99 --parallel P --ctx-size max(16384, P×12288) --jinja` に `LLAMA_CPP_EXTRA_ARGS` を足したもの。`P` は `SURYA_INFERENCE_PARALLEL`（既定 8。8 だと ctx 98,304 になるため今回は 2 と 1 で計測）
- 既存サーバーに繋ぐ場合は `SURYA_INFERENCE_URL=http://host:port/v1`（自動起動しない）
- **モード**: CPU の既定は `fast`。fast はデジタル PDF では VLM を起動しない（今回の デジタル PDF でも `llama-server` は起動しなかった）。VLM を使わせるには `--mode balanced`（VLM でレイアウト）か、スキャン PDF（全ページ OCR）。`--disable_ocr` は VLM 呼び出しを一切しない
- サーバーのログ・ロック・sentinel（`<backend>_server.json`）は `XDG_CACHE_HOME` に関係なく **`~/.cache/datalab/surya/` に固定**で書かれる。常駐サーバーは sentinel を見て次回の marker が再利用する

## 手順（再現用）

```bash
S=<スクラッチ領域>/marker-spike; mkdir -p $S && cd $S
# 1. marker（CPU 版 torch）
uv venv -p 3.12 .venv
VIRTUAL_ENV=$S/.venv uv pip install marker-pdf==2.0.0 \
  --index-url https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cpu \
  --index-strategy unsafe-best-match                # 約 12 秒、venv 1.3GB
# 2. llama-server（GitHub release の Ubuntu x64 CPU ビルド、17MB、非対話で取得可）
curl -fsSL -o llama.tar.gz \
  https://github.com/ggml-org/llama.cpp/releases/download/b11200/llama-b11200-bin-ubuntu-x64.tar.gz
mkdir -p llama && tar xzf llama.tar.gz -C llama     # llama/llama-b11200/llama-server（展開後 44MB）
# 2'. 開発機（CentOS Stream 9）は libstdc++ が古く GLIBCXX_3.4.30 が無いため、conda-forge の libstdcxx を横に置く
#     （ubuntu-latest では不要）
curl -fsSL -o p.conda https://api.anaconda.org/download/conda-forge/libstdcxx/14.4.0/linux-64/libstdcxx-14.4.0-h934c35e_7.conda
python3 -c "import zipfile; zipfile.ZipFile('p.conda').extractall('libstdcxx')"
(cd libstdcxx && zstd -dc pkg-*.tar.zst | tar x)    # libstdcxx/lib/libstdc++.so.6
# 3. 環境変数
export HF_HOME=$S/hf XDG_CACHE_HOME=$S/cache LD_LIBRARY_PATH=$S/libstdcxx/lib
export PATH=$S/llama/llama-b11200:$S/.venv/bin:$PATH
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 TORCH_DEVICE=cpu
export SURYA_INFERENCE_BACKEND=llamacpp SURYA_INFERENCE_PARALLEL=2
export LLAMA_CPP_EXTRA_ARGS="--threads 4 --threads-batch 4"
# 4. GGUF の事前取得（1.47GB、未認証で 125 秒）
python -c "from surya.inference.backends.llamacpp import _download_gguf_files as f; print(f())"
# 5. 実行（各モード）
marker_single samples/stat-go-jp-children-population-2026.pdf --output_dir out/a --output_format json               # fast（CPU 既定）
marker_single samples/stat-go-jp-children-population-2026.pdf --output_dir out/b --output_format json --mode balanced
marker_single samples/stat-go-jp-children-population-2026.pdf --output_dir out/c --output_format json --disable_ocr
marker_single samples/stat-go-jp-children-population-2026-scan.pdf --output_dir out/d --output_format json          # fast（全ページ VLM OCR）
# 6. 後始末: 常駐する fast_layout / ocr_error サーバーを止める
pkill -f surya.fast_layout.server; pkill -f surya.ocr_error.server
```

計測は `/usr/bin/time` が無いため、自作の小さな監視スクリプト（psutil で 0.5 秒ごとに
marker 本体と上表の 3 サーバーの RSS / PSS を合計し、その最大値を取る）で行った。
常駐サーバーは親子関係が切れる（`start_new_session=True` で起動され、marker 終了後は ppid=1）ため、
プロセスツリーではなくコマンドラインで拾っている。毎回の計測後に常駐サーバーを止め、**全計測がコールドスタート**
（サーバーの起動・モデルのロード込み。モデルのダウンロードは含まない）。

## 実測

対象 PDF はいずれも 6 ページ。wall はコマンド全体、「変換」は marker が出す `Total time`。
「実効」は同時に存在したプロセスの RSS 合計の最大値（PSS は共有ページを按分した値）。

| 条件 | wall | wall/page | 変換 | 実効 Peak RSS | 実効 Peak PSS | 内訳（Peak RSS） |
|---|---|---|---|---|---|---|
| デジタル `--disable_ocr` | 23.3s | 3.9s | 11.7s | **1,617 MiB** | 1,488 MiB | marker 673 / fast_layout 967 |
| デジタル fast（CPU 既定。VLM は起動せず） | 32.4s | 5.4s | 20.9s | **2,645 MiB** | 2,384 MiB | marker 612 / fast_layout 1,019 / ocr_error 1,143 |
| デジタル balanced（VLM、P=2） | 172.6s | **28.8s** | 151.1s | **4,116 MiB** | 3,966 MiB | marker 664 / llama-server 2,363 / ocr_error 1,158 |
| デジタル balanced（VLM、P=1） | 184.2s | 30.7s | 159.8s | **4,038 MiB** | 3,903 MiB | marker 648 / llama-server 2,238 / ocr_error 1,206 |
| スキャン fast（全ページ VLM OCR、P=2） | 804.2s | **134.0s** | 778.6s | **5,122 MiB** | 5,033 MiB | marker 739 / fast_layout 969 / ocr_error 1,005 / llama-server 2,865 |

- `llama-server` の推論速度（P=2、4 スレッド）: balanced のレイアウト 1 ページ = 画像 1,032〜1,036 トークンの prefill に 14〜16 秒（64〜72 tok/s）、生成は 8.6〜21 tok/s。スキャンの全ページ OCR は画像 3,621 トークンの prefill に 88〜92 秒（約 40 tok/s）、1 ページ目の生成は 1,598 トークンで 370 秒（4.3 tok/s。2 スロット同時で遅くなる）
- `--disable_ocr` / fast をウォームで（常駐サーバー再利用）回すと変換 2.7〜3.4 秒・wall 約 14 秒だが、その場合もメモリは常駐サーバーの分が別に要る
- 初回のみ: fast_layout モデル（`datalab-to/surya_layout2`、136MB）と ocr_error モデル（262MB、`$XDG_CACHE_HOME/datalab/models`）を自動取得し、`--disable_ocr` の初回 wall は 43.5 秒だった
- 開発機は計測中も他プロセスで 2〜3GB を使っており、スキャン実行中は available が 2.2GB まで下がった（swap 使用あり）。wall は負荷の影響を含む

## 出力形式（`--output_format json`）

- 出力は `<stem>/<stem>.json`（本体）+ `<stem>_meta.json`（`table_of_contents` とページ統計）。markdown / html モードでは画像が `_page_<n>_<Type>_<i>.jpeg` として別ファイルになる。JSON では画像は `images` に base64 JPEG で埋め込まれる
- 構造: `Document` → `Page`（6 個、`id` は `/page/<0 始まり>/Page/<n>`）→ ブロック。一部はグループ（`TableGroup` / `FigureGroup` / `ListGroup`）の下にさらに子を持つ。すべてのブロックに `id` / `block_type` / `html` / `polygon` / `bbox` / `section_hierarchy` / `images` がある
- 観測した `block_type`: `Page` / `Text` / `SectionHeader` / `Caption` / `Table` / `TableGroup` / `Figure` / `FigureGroup` / `Picture` / `PageHeader` / `PageFooter` / `ListGroup`
- **`polygon` は全ブロックにある**（4 点、左上起点で時計回り）。座標は **PDF ポイント**（ページ `bbox` = `[0, 0, 596, 842]`）、**原点は左上・y 下向き**。スキャン PDF も同じ座標系で返る
- **ページ番号は 0 始まり**（`id` の `/page/0/`）。`Page.html` は子ブロックへの `<content-ref src=...>` の並びで、子の並びが読み順
- **表は `html` に HTML `<table>`**。デジタル PDF はテキスト層からの再構成（`Table processing stats: {'tables_pdftext': 4, 'tables_total': 4}`）で `<thead><th>` はあるが `colspan` / `rowspan` は無く、多段ヘッダは平坦化される。スキャン PDF は VLM の表認識で **`colspan` / `rowspan` 付き**（例: `<td rowspan="3">こどもの<br/>数</td>`）
- `PageHeader` / `PageFooter` は既定で `html` が空（`--keep_pageheader_in_output` / `--keep_pagefooter_in_output` で残る）。テキスト層の無い図中領域などに `html` が空の `Text` ブロックが出る（デジタル fast で 57 ブロック中 14、図以外）

1 ページ目の抜粋（デジタル fast。座標は整数に丸め、`Page.html` と base64 は省略、子 14 個のうち 7 個）:

```json
{
 "block_type": "Document",
 "children": [
  {"id": "/page/0/Page/62", "block_type": "Page",
   "html": "<content-ref src='/page/0/PageHeader/0'></content-ref><content-ref src='/page/0/PageHeader/2'></content-ref>…",
   "polygon": [[0, 0], [596, 0], [596, 842], [0, 842]], "bbox": [0, 0, 596, 842],
   "section_hierarchy": {"1": "/page/0/SectionHeader/6"}, "images": null,
   "children": [
    {"id": "/page/0/PageHeader/0", "block_type": "PageHeader", "html": "",
     "polygon": [[428, 104], [546, 104], [546, 139], [428, 139]], "bbox": [428, 104, 546, 139],
     "section_hierarchy": {}, "images": {}},
    {"id": "/page/0/Picture/1", "block_type": "Picture", "html": "",
     "polygon": [[428, 104], [458, 104], [458, 138], [428, 138]], "bbox": [428, 104, 458, 138],
     "section_hierarchy": {}, "images": {"/page/0/Picture/1": "<base64 jpeg>"}},
    {"id": "/page/0/Text/3", "block_type": "Text", "html": "<p block-type=\"Text\"></p>",
     "polygon": [[427, 148], [545, 148], [545, 165], [427, 165]], "bbox": [427, 148, 545, 165],
     "section_hierarchy": {}, "images": {}},
    {"id": "/page/0/Text/62", "block_type": "Text",
     "html": "<p block-type=\"Text\">1 こどもの数は1329万人、45年連続の減少  2 こどもの割合は10.8%、52年連続の低下</p>",
     "polygon": [[117, 498], [409, 498], [409, 547], [117, 547]], "bbox": [117, 498, 409, 547],
     "section_hierarchy": {}, "images": {}},
    {"id": "/page/0/SectionHeader/9", "block_type": "SectionHeader", "html": "<h1>ポ イ ン ト</h1>",
     "polygon": [[267, 445], [356, 445], [356, 464], [267, 464]], "bbox": [267, 445, 356, 464],
     "section_hierarchy": {"1": "/page/0/SectionHeader/9"}, "images": {}},
    {"id": "/page/0/Text/4", "block_type": "Text", "html": "<p block-type=\"Text\">令和8年5月4日</p>",
     "polygon": [[424, 184], [524, 184], [524, 199], [424, 199]], "bbox": [424, 184, 524, 199],
     "section_hierarchy": {"1": "/page/0/SectionHeader/9"}, "images": {}},
    {"id": "/page/0/SectionHeader/6", "block_type": "SectionHeader",
     "html": "<h1>我が国のこどもの数  -「こどもの日」にちなんで-</h1>",
     "polygon": [[165, 266], [440, 266], [440, 320], [165, 320]], "bbox": [165, 266, 440, 320],
     "section_hierarchy": {"1": "/page/0/SectionHeader/6"}, "images": {}},
    "… 残り 7 ブロック"
   ]},
  "… page 1〜5 も同じ形"
 ]
}
```

## 日本語の品質（目視）

デジタル PDF・fast の markdown 冒頭（`--disable_ocr` と完全に同一の出力だった）:

```markdown
1 こどもの数は1329万人、45年連続の減少 2 こどもの割合は10.8%、52年連続の低下

# ポ イ ン ト

令和8年5月4日

総務省統計局では、5月5日の「こどもの日」にちなんで、2026年4月1日現在に おけるこどもの数(15歳未満人口)を推計しました。

統計トピックス No.148

# 我が国のこどもの数 -「こどもの日」にちなんで-

|           | こどもの 数 |     | ０～２歳 | ３～５歳 | 小学生(６～11歳) | ６～８歳 | ９～11歳 | 中学生 (12～14歳) |
| 男女計 人 口   | 1329   | 456 | 213  | 243  | 564        | 268  | 296   | 309          |
| 女 総人口に占める | 648    | 222 | 104  | 118  | 275        | 131  | 144   | 151          |
```

- **文字化けは無し**。目視した範囲で本文・表の数値に欠落や誤りは無い
- ただし 1 ページ目の読み順が崩れる（「ポイント」の見出しと要点 2 行が、上にある日付・表題より前に出る）。行末の改行が半角スペースとして残る（「現在に おける」）。多段ヘッダの表は平坦化され、行見出しが隣の行と混ざる（「女 総人口に占める」）。これは pdfplumber / Docling と同種の揺れ
- balanced（VLM レイアウト）ではブロックが細かくなり、要点 2 行や副題が別ブロックに分かれて読み順は改善するが、グラフの縦書き軸ラベルを 1 文字ずつ `Text` ブロック（「こ」「ど」「も」「の」…）として拾う
- スキャン PDF（VLM の全ページ OCR）は品質が高い。行末の不要なスペースが無く（「2026年4月1日現在におけるこどもの数（15歳未満人口）を推計しました。」）、読み順も自然で、表は `rowspan` / `colspan` 付き HTML で結合セルまで取れている。docling_ocr / ppstructure（`docs/results.md` の scan で DSQS Total 76.7 / 77.7）と同等以上の可能性があるが、Ground Truth での定量比較は未実施
- 参考（`docs/results.md`、CI 実測）: この文書で docling は wall 40.0 秒・Peak RSS 1,638 MiB、scan で docling_ocr 79.9 秒・2,094 MiB、ppstructure 165.6 秒・2,237 MiB。marker の VLM 経路は時間で 4〜10 倍、メモリで 2〜2.5 倍

## 判定: **見送り（no-go）**

| 基準 | 結果 | 判定 |
|---|---|---|
| 実効 Peak RSS ≤ 3GB | VLM 経路: デジタル balanced 4,116 MiB（P=1 でも 4,038 MiB）、スキャン 5,122 MiB | **不合格** |
| `llama-server` を ubuntu-latest で非対話導入できる | GitHub release の `llama-b<N>-bin-ubuntu-x64.tar.gz`（17MB）を `curl` で取得・展開するだけ。GCC 11.4 ビルドで ubuntu-latest（24.04）の libstdc++ で動く見込み（CentOS 9 の開発機だけ libstdc++ の差し替えが要った）。GGUF も未認証の HF から取得可（1.47GB / 125 秒） | 合格 |

- VLM を使わない fast（デジタル）/ `--disable_ocr` は 1.6〜2.6GB に収まるが、Level 3（Local VLM）ではなく、レイアウト検出モデル（rf-detr）や ocr_error モデルを使う **Level 2（Document AI）相当**であり、Issue の目的（VLM の 1 方式目）を満たさない。Level 2 の候補としては 3GB 基準に収まるので、Epic #116 で検討の余地がある
- **CI 時間の見積り**（全 6 文書 34 ページ。現状の `bench` は約 27 分・上限 60 分）: 開発機（4 スレッド）の実測からの外挿で、balanced 相当で 28 ページ × 28.8 秒 + スキャン 6 ページ × 134 秒 ≈ 27 分。ubuntu-latest の runner（RAM 7.75GiB。vCPU 数は未確認だが開発機の 6 コアより少ない前提）では 1.5〜2 倍と見て **40〜55 分の追加**になり、既存の 27 分と合わせて 60 分枠を超える。CPU 既定の fast にしてもスキャンの 13 分（CI では 20〜27 分）が残り、枠ぎりぎり
- メモリの内訳から見て、3GB に入らない主因は `llama-server`（F16 の重み 1.47GB の mmap + 画像 3,621 トークン分の計算バッファで 2.2〜2.9GB）と、それとは別に常駐する ocr_error / fast_layout サーバー（各 1GB 前後。どちらも torch を別プロセスで読み込む）の二重持ち

### 判定が変わる条件

- surya の **量子化 GGUF（Q8_0 / Q4 等）が公式に配布**され、`llama-server` が 1.5GB 未満に収まる（`surya/scripts/build_gguf.py` に `--outtype q8_0` はあるが自前変換で品質は未検証）。かつ ocr_error / fast_layout サーバーを止められる（または同一プロセスに載る）構成が marker 側で提供される
- 判定基準の見直し（たとえば「VLM 系は Peak RSS 6GB まで、CI では scan 1 文書のみ」）。現行の 8GB 機・7.75GiB の CI runner でも単独実行なら 5.1GB で完走はした
- GPU 付きの CI runner / 開発機が使えるようになる（marker 既定の vllm 経路。コスト面の判断が要る）

見送りの場合、Level 3 の 1 本目は別候補（より小さい VLM、または量子化済み GGUF を公式配布しているもの）を次スパイクで検討する。#126（Marker アダプタ）は着手しない。

## 仮に導入する場合の引き継ぎメモ（#126 向け、参考）

判定は no-go だが、条件が変わって再検討する際のために記録する。

- 設定: `SURYA_INFERENCE_BACKEND=llamacpp`、`SURYA_INFERENCE_PARALLEL=1`（P=2 と比べメモリ差は 125MiB だけだが ctx が 16,384 に下がる）、`LLAMA_CPP_EXTRA_ARGS="--threads <n> --threads-batch <n>"`、`LLAMA_CPP_BINARY=<llama-server の絶対パス>`、`OMP_NUM_THREADS=<n>`、GGUF は `SURYA_GGUF_LOCAL_MODEL_PATH` / `SURYA_GGUF_LOCAL_MMPROJ_PATH` で固定可能（`HF_HOME` 下 1.47GB を CI cache 対象に）
- サーバーの寿命: `llama-server` は marker の atexit で kill されるが、**fast_layout / ocr_error サーバーは常駐し続ける**。アダプタは子プロセス化したうえで、終了時に `~/.cache/datalab/surya/<backend>_server.json` の pid を kill する（または `SURYA_INFERENCE_URL` / `FAST_LAYOUT_SERVER_URL` / `OCR_ERROR_SERVER_URL` で自前起動したサーバーに繋ぐ）。Peak RSS はこれらのサーバーを合算して記録しないと過小評価になる
- `--use_llm` は使わない（外部 API）。モードは `--mode` を明示して `extra` に記録する（CPU の既定 fast はデジタル PDF で VLM を使わず、Level 3 の比較にならない）
- IR マッピング: `Page` の子の並び（= `Page.html` の content-ref 順）を読み順とする。`Text` / `SectionHeader` / `Caption` / `ListGroup` → paragraph（`SectionHeader` は `<h1>`〜 の段で見出しレベル）、`Table` → table（`html` の `<table>` を行・セルに展開。`rowspan` / `colspan` を解釈）、`TableGroup` / `FigureGroup` は子を展開、`PageHeader` / `PageFooter` / `Picture` / `Figure` と `html` が空のブロックは除外。`bbox` は PDF ポイント・左上原点・y 下向きのため、IR の bbox の向きに合わせて変換する。ページ番号は 0 始まり
- raw は `--output_format json` の JSON（画像 base64 込みで数百 KB〜MB）を保存する

## Sprint 16: VLM を使わないモードを Level 2 として採用（#194）

上の判定（Level 3 としては no-go）は変えず、VLM を使わない構成を **Level 2 の `marker` アダプタ**
（`jdocbench/adapters/marker.py`、オプトイン `JDOCBENCH_MARKER=1`、extra `level2-marker`）として入れた。

- **モード**: `--mode fast --disable_ocr --output_format json --disable_image_extraction --disable_multiprocessing`（最後の 2 つは raw の画像の埋め込みと pdftext のワーカープロセスを止めるだけ）。fast の既定は ocr_error
  サーバー（約 1.1GB）で文字化けを判定するが、判定後の OCR は VLM でしか行えないため、VLM を使わない構成では
  判定だけが残る（上の実測でも fast と `--disable_ocr` の出力は同一）。よって `--disable_ocr` を採る
- **サーバー**: レイアウトサーバー（`surya.fast_layout.server`）はアダプタの子プロセスが**自分の子として起動**し、
  `FAST_LAYOUT_SERVER_URL` で marker を繋ぎ、終了後に止める（`PR_SET_PDEATHSIG` で親が落ちても残らない）。
  `SURYA_INFERENCE_AUTOSTART` / `OCR_ERROR_SERVER_AUTOSTART` / `DETECTOR_SERVER_AUTOSTART` /
  `FAST_LAYOUT_SERVER_AUTOSTART` を `false` にし、VLM 等が呼ばれても起動せずエラーになるようにした。
  `~/.cache/datalab/surya/` の sentinel・ログは書かれない
- **Peak RSS**: 子プロセス・marker・レイアウトサーバーそれぞれの `ru_maxrss` の和（同時使用の上限）。
  開発機（`OMP_NUM_THREADS=4`、モデル取得済み）の実測で `stat-go-jp-children-population-2026.pdf` は
  wall 24.1 秒・**1,627 MiB**（marker 647 / サーバー 954 / 子プロセス 26）、上のスパイクの `--disable_ocr`
  （1,617 MiB）とほぼ同じ。スキャン版は 24.5 秒・1,724 MiB で、**OCR しないので文字は 0**（VLM は起動しない）
- **依存の版**: marker の要求（pillow<11、pdftext の pypdfium2==5.10.1）のままだと他アダプタの pillow /
  pypdfium2 / pdfplumber が巻き戻るため、`pyproject.toml` の override で既存の版（pillow 12、pypdfium2 5.13）に
  固定し、marker を要求範囲の外で動かしている。出力（本文・表の抜粋、表 4 つ）は上の記録と一致した
- **既知の差**: marker は ftfy の `fix_text` でテキストを整形し、全角英数字を半角にする（「令和８年」→「令和8年」）。
  GT は全角のまま（NFKC しない方針）なので CF が下がる。設定で外せないため marker の特性として扱う

## 後始末

- 起動した `llama-server` / fast_layout / ocr_error サーバーはすべて停止済み
- 一時 venv・llama.cpp・GGUF・モデルキャッシュ・出力はスクラッチ領域（`marker-spike/`）に残してある（再検討時の再利用のため。リポジトリ外）
- marker が `~/.cache/datalab/surya/` に書いたログ・sentinel は削除済み。リポジトリ・プロジェクト venv・`pyproject.toml`・`uv.lock` は変更していない
