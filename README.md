# jdocbench

日本語の実務文書（PDF）を複数のパーサーにかけ、同じ Ground Truth・同じ指標で
**構造化品質 × 計算コスト**を実測比較するベンチマークツールです。
目的は「一番強いパーサー」を決めることではなく、「この文書のこの部分には、どの方式が合理的か」を
実測で示すことです。AI を使わない方式（Level 0 / 1）を Baseline として常に残し、
特定のパーサーに有利な正規化はしません。

このリポジトリは**ツール（コードとテスト）だけ**を公開しています。評価に使うサンプル文書・
Ground Truth・実測結果は開発リポジトリ（非公開）で別に管理しており、ここには含みません。
自分の PDF と Ground Truth を用意すれば、同じ手順で計測できます。

## 方針

- **Parsing 経路で外部の LLM / VLM API（OpenAI / Claude / Gemini / LlamaParse など）を呼びません**。すべて手元の CPU で動きます（GPU は使いません）
- **raw 出力を捨てません**。各パーサーの出力をそのまま保存し、共通形式（Common Document IR）への正規化は別に行います
- **実験条件を記録します**。パーサー・モデルのバージョン、OS / CPU / RAM、設定、使われた日本語フォントを結果（`run.json`）とともに保存します

## 指標

**DSQS-JP**（Ground Truth のある文書だけで計算）。Total は 4 つのサブスコアの単純平均 × 100 です。

- **CF**（Content Fidelity）: 文書の全テキスト（表の行を含む）の再現率
- **SED**（Structural Element Detection）: 表を検出できたか（ページごとの表数の F1）
- **TSF**（Table Structural Fidelity）: 検出できた表の中身の再現率
- **ROA**（Reading Order Accuracy）: 行の並び順。GT 行との対応率が 0.15 未満のときは Total に 0 で入れます

**JNF**（Japanese Notation Fidelity）は、読めた文字のうち表記（全角/半角・丸数字・旧字体・△ など）まで
原本どおりかを測ります。Total には入れず、別の列で出します。定義は [docs/jnf.md](docs/jnf.md)、
各指標の実装は `jdocbench/metrics.py` の docstring にあります。

## アダプタ

| Level | 名前 | 方式 | ライセンス | 有効化 |
|---|---|---|---|---|
| 0 | `pdftotext` | Poppler（外部コマンド） | GPL-2.0-or-later | 既定（コマンドがあれば） |
| 0 | `pdf2json` | pdf2json（Node.js の外部コマンド） | Apache-2.0 | 既定（コマンドがあれば） |
| 0 | `pypdfium2` | PDFium のテキスト抽出 | Apache-2.0 / BSD-3-Clause | 既定（extra `level1`） |
| 0 | `mutool` | MuPDF の `mutool draw -F stext`（外部コマンド） | AGPL-3.0 / 商用 | 既定（コマンドがあれば） |
| 0 | `tika` | Apache Tika 3.3.3（Java） | Apache-2.0 | 既定（Java と jar があれば） |
| 1 | `pdfplumber` | 罫線ベースの表抽出 | MIT | 既定（extra `level1`） |
| 1 | `pymupdf` | PyMuPDF のテキスト・表抽出 | AGPL-3.0 / 商用 | **オプトイン**（extra `pymupdf`） |
| 1 | `camelot` / `camelot_stream` | Camelot の表抽出（`lattice` / `stream` 固定） | MIT | 既定（extra `camelot`） |
| 1 | `tabula` / `tabula_stream` | tabula-java 1.0.5（`lattice` / `stream` 固定） | MIT | 既定（JDK と jar があれば） |
| 1 | `tesseract` / `tesseract_psm6` | Tesseract OCR（`jpn`。テキスト層の無い PDF だけ） | Apache-2.0 | 既定（コマンドがあれば） |
| 1 | `markitdown` | MarkItDown の PDF → Markdown | MIT | 既定（extra `markitdown`） |
| 1 | `unstructured` | Unstructured `partition_pdf(strategy="fast")` | Apache-2.0 | 既定（extra `unstructured`） |
| 2 | `docling` | Docling（レイアウト + TableFormer、OCR なし） | MIT | 既定（extra `level2`） |
| 2 | `docling_ocr` | Docling + RapidOCR（テキスト層の無い PDF だけ） | MIT / Apache-2.0 | 既定（extra `level2`） |
| 2 | `ppstructure` | PaddleOCR PP-StructureV3 | Apache-2.0 | **オプトイン**（extra `level2-paddle` + `JDOCBENCH_PPSTRUCTURE=1`） |
| 2 | `marker` | Marker 2.0.0（`--mode fast --disable_ocr`、VLM なし） | Apache-2.0 / 重み modified OpenRAIL-M | **オプトイン**（extra `level2-marker` + `JDOCBENCH_MARKER=1`） |
| 2 | `mineru` | MinerU の `basic` tier（ONNX Runtime、VLM なし） | MinerU Open Source License | **オプトイン**（extra `level2-mineru` + `JDOCBENCH_MINERU=1`） |
| 2 | `yomitoku` | YomiToku 0.15.0（別環境の CLI を呼ぶ） | **CC BY-NC-SA 4.0（非商用のみ）** | **オプトイン**（`JDOCBENCH_YOMITOKU=1`）。下の「ライセンス」を参照 |

「既定」は、依存（extra・外部コマンド）が入っていれば `jdocbench run` で動くものです。入っていないアダプタは
スキップされます。各アダプタの制約（表を出さない、ページ境界が残らない など）は `jdocbench/adapters/` の
各ファイルの docstring にあります。

## セットアップ

Python 3.12 以上と [uv](https://docs.astral.sh/uv/) が必要です。

```bash
uv sync --all-extras                    # すべての extra（オプトインの pymupdf / level2-* を含む）
uv sync --extra level1 --extra level2   # 必要なものだけ選ぶ場合
```

- extra: `level1`（pdfplumber / pypdfium2）、`pymupdf`、`camelot`、`markitdown`、`unstructured`、`level2`（Docling）、
  `level2-paddle`（PP-StructureV3）、`level2-marker`、`level2-mineru`。torch は CPU 版を `pyproject.toml` で固定しています
- `--all-extras` を選ぶと PyMuPDF・PP-StructureV3・Marker・MinerU などオプトインの extra もまとめて入りますが、
  Marker・MinerU・YomiToku はさらに `JDOCBENCH_MARKER=1` 等の環境変数を立てないと動きません（上の「アダプタ」表を参照）
- 導入は uv だけを前提にしています（cv2 の提供元を揃える override などが uv 専用のため）
- Level 2 のモデルは初回実行時に取得します（Docling 約 500MB は `HF_HOME`、PP-StructureV3 約 770MB は
  `PADDLE_PDX_CACHE_HOME`、MinerU 約 820MB は `MINERU_MODEL_BASE_DIR`）
- 外部コマンド（無ければ該当アダプタはスキップ）
  - poppler-utils（pdftotext / pdfinfo）: `sudo apt install poppler-utils` / `sudo dnf install poppler-utils`
  - pdf2json: `npm install -g pdf2json`（Node.js 22 以上。CI は `4.1.0` 固定）
  - mutool: `sudo apt install mupdf-tools`
  - Tesseract: `sudo apt install tesseract-ocr tesseract-ocr-jpn` / `sudo dnf install tesseract tesseract-langpack-jpn`
- Tika / Tabula: JDK 11 以上を入れ、`bash scripts/fetch_java_jars.sh` で版とチェックサムを固定した jar を
  `~/.cache/jdocbench/` に取得します（置き場所は `JDOCBENCH_TIKA_JAR` / `JDOCBENCH_TABULA_JAR` で変更可）
- **日本語フォント**（Noto CJK など）を入れてください: `sudo apt install fonts-noto-cjk` /
  `sudo dnf install google-noto-sans-cjk-ttc-fonts`。フォントを埋め込んでいない PDF は、ページを画像にする段階
  （レイアウト推論・OCR・`view.html`）で日本語が空白になり、結果が大きく変わります
  （[docs/spikes/docling-repro.md](docs/spikes/docling-repro.md)）
- YomiToku は本体の venv に入れず、別環境に CLI を入れます（`bash scripts/install_yomitoku.sh --models <TOOL_DIR> <BIN_DIR>`
  の後、`JDOCBENCH_YOMITOKU=1 JDOCBENCH_YOMITOKU_BIN=<BIN_DIR>/yomitoku`）。**非商用の用途に限ります**

## 使い方

```bash
uv run jdocbench run path/to/*.pdf
```

`results/<stem>/` に `run.json`（実験条件）・`profile.json`・`raw/`・`normalized/`・`metrics/`・`report.md`・`view.html` を、
`results/summary.md` に文書横断の比較表を書きます。出力先は `--out` で変えられます。

- `uv run jdocbench summary results path/to/*.pdf`: 別々に `run` した結果から `summary.md` / `summary.json` を作り直す
- `uv run jdocbench profile path/to/*.pdf`: 文書の構造特性（テキスト層・段組み・罫線・フォント埋め込み）だけを書く（PyMuPDF が必要）
- `uv run jdocbench route path/to/*.pdf`: Profile から推奨パーサー（品質重視 `quality` / コスト重視 `cost`）と理由を `route.json` に書く

### 公開サイト用のデータ（site-data）

```bash
uv run jdocbench site-data results --out site/data.json --exclude-adapter yomitoku
```

結果ディレクトリから、公開サイト用の**数値だけ**の `data.json` を作ります。形は `summary.json` と互換
（`documents` / `missing` / `failures`）で、各アダプタに `version` を、各文書に実行環境のメタ `run` を足し、
トップの `run` に全文書で同じ値（違えば `null`。アダプタの版数は `adapter_versions`）を置きます。
`--exclude-adapter` に挙げたアダプタは列ごと落とします（繰り返し可）。

- 読むもの: `<results>/summary.json` の数値（pages・ground_truth_sha256・各アダプタの status と指標・Wall・Peak RSS）、
  `missing` のファイル名、`failures` の stem / adapter / kind（message は入れません）。
  `<results>/<stem>/run.json` の `pages` / `ground_truth_sha256` / `jdocbench_version` / `python` / `platform` /
  `cpu` / `ram_total_bytes` / `started_at` / `fonts_ja` と、各アダプタの `version` / `status`
- 読まないもの: run.json の `input` / `ground_truth`（パス）/ `reason` / `extra` などそれ以外のキー、
  `raw/` / `normalized/` / `metrics/` / `profile.json` / `report.md` / `view.html`、PDF、Ground Truth
- 文字列の値は形を検査し、想定外のもの（未知の status・kind、パス区切りを含む名前、長い文字列）は落とします

### 公開サイト（site）

```bash
uv run jdocbench site site/data.json --meta site/documents.json --out site
```

`site-data` で作った `data.json` と、文書の公開情報を書いた `documents.json` の **2 つだけ**から、
静的 HTML の `<out>/index.html`（説明・計測条件・文書一覧・DSQS Total・Parser 平均・注記）と
`<out>/documents/<stem>.html`（出典・ライセンス・構造特性・パーサーごとの数値・計測環境）を作ります。
JS・CDN・画像などの外部読み込みは使わず（CSS は `<style>` にインライン）、生成時刻を埋めないので、
同じ入力からは同じ HTML になります。数値の桁と Parser 平均の式は `summary.md` と同じです。
ページ画像・抽出テキスト・Ground Truth は載せません。

`documents.json` の形は次のとおりです（`run_url` / `article_url` は `null` なら出しません）。
`documents[].stem` の集合が `data.json` と一致しないときは何も書かずに 2 を返します。

```json
{
  "source": {"run_id": "...", "run_url": null, "commit": "...", "tool_sha": "...",
             "measured_at": "YYYY-MM-DD", "repo_url": "https://...", "article_url": null, "notes": ["..."]},
  "documents": [
    {"stem": "...", "title": "...", "publisher": "...", "domain": "...", "pages": 1,
     "source_url": "https://...", "license": {"name": "...", "url": "https://..."},
     "attribution": "...", "features": ["..."], "excerpt_note": "...", "notes": ["..."]}
  ]
}
```

自分の結果で公開サイトを作る手順:

1. `uv run jdocbench run path/to/*.pdf` で `results/`（`summary.json` を含む）を作る（別々に `run` したときは `uv run jdocbench summary results path/to/*.pdf` で作り直す）
2. `uv run jdocbench site-data results --out site/data.json`（載せられないアダプタは `--exclude-adapter` で落とす）
3. 各文書の出典・ライセンスを確かめて `site/documents.json` を書く
4. `uv run jdocbench site site/data.json --meta site/documents.json --out site` で HTML を作り、
   `site/` を GitHub Pages などで公開する

### Ground Truth

```bash
uv run jdocbench run doc.pdf                         # まず run して正規化結果を作る
uv run jdocbench gt init doc.pdf --from pdfplumber   # groundtruth/doc.json に seed を写す
```

seed を PDF と照合して人手で直したものを正とします。`groundtruth/<stem>.json` があれば `run` が DSQS-JP を計算し、
`report.md` と `summary.md` に載せます（別の場所の GT は、1 文書のとき `--gt` で指定）。

形式は IR の Document JSON（`jdocbench/ir.py`）です。要点は次のとおりです。

- `{"adapter": "ground_truth", "pages": [{"number": 1, "blocks": [...]}]}`。ページ番号は PDF の物理ページ（1 始まり）
- block は `type` が `paragraph`（`text`）か `table`（`rows`: 行 × セルの文字列）。読み順に並べる
- 段落は表の中身を含まない（表の中身は table だけが持つ）。表のタイトル・単位・注記は段落に残す
- 結合セルは範囲の先頭（左上）のセルにだけ文字列を入れ、他のセルは空文字にする。別のセルなら同じ文字列でも両方に書く

## スクリプト

`uv run python scripts/compare_summaries.py <a.json> <b.json>`: 2 つの `summary.json`
（`jdocbench summary` の出力。別の場所で同じスキーマの `data.json` を作る場合も使えます）を、
文書 stem × アダプタの全組で照合します。既定で比べるのは status / total / cf / sed / tsf /
roa_effective / jnf、トップレベルの missing（結果の無い文書）・failures（失敗したアダプタ）の
集合も比べます。アダプタの結果自体が片方にしか無い組も差として出します。差があれば Markdown の
表を標準出力に出し、終了コード 1 で終わります（無ければ 0）。既知の揺れを許す組は
`--allow STEM:ADAPTER`（繰り返し指定可）で除いて表に残しつつ終了コードには数えず、
Wall / Peak RSS は `--include-perf` を付けたときだけ比べます。

## テスト

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

実 PDF を使うテストは、`JDOCBENCH_SAMPLES` が未設定ならリポジトリ直下の `samples/` を探します。
見つからない場合や、対象の PDF・外部コマンドが無い場合は skip します。別の場所を使うときだけ
環境変数で渡してください。

```bash
JDOCBENCH_SAMPLES=/path/to/samples uv run pytest
```

## ライセンス

本体は MIT（[LICENSE](LICENSE)）です。依存ライブラリ・外部ツール・モデルはそれぞれのライセンスに従い、
一覧を [docs/licenses.md](docs/licenses.md) に記録しています。外部ツールの実行形式・jar・モデルの重みは同梱しません。

オプトインのアダプタは、本体と条件が異なるため既定では動かしません。有効にする前に条件を確認してください。

- **PyMuPDF / mutool**: AGPL-3.0（または Artifex の商用ライセンス）。mutool は外部コマンドとして呼ぶだけです。
  AGPL 以外で配布・ネットワーク提供する場合は、PyMuPDF を含めない構成（既定の `uv sync`）を使ってください
- **Marker**: コードは Apache-2.0 ですが、自動取得されるモデルの重みは modified AI Pubs Open RAIL-M
  （研究・個人利用と、調達額・売上 $5M 未満の組織のみ無償）です
- **MinerU**: Apache-2.0 に追加条項（大規模利用者の商用ライセンス、オンライン提供時の表示義務）を付けた独自ライセンスです
- **YomiToku**: コードとモデルの重みの両方が CC BY-NC-SA 4.0 で、**非商用の用途に限ります。商用案件では有効にしないでください**。
  出力結果の外部提供も商用に当たりうるため、**YomiToku の結果は本ベンチマークの公開表には載せません**。
  アダプタは別環境の CLI を呼ぶだけで、YomiToku のコードもモデルも同梱しません

---

コード中のコメントにある `#NNN` は、開発リポジトリ（非公開）の Issue 番号です。同様に、コード・docs 中の
「企画書 §NN」や `docs/results.md` 等への言及も、開発リポジトリ（非公開）の文書を指します。
