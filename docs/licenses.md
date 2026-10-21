# 外部ツール・モデル・データセットのライセンス

jdocbench が呼び出す外部ツール・モデルのライセンスをここに記録する。追加時は行を足す。jdocbench 本体は MIT（[LICENSE](../LICENSE)）で、以下のツール・モデルは同梱しない（Java の jar・モデルの重みは利用者の環境に取得する）。

| ツール | 種別 | ライセンス | 利用形態 | 出典 URL |
|---|---|---|---|---|
| pdftotext（Poppler） | Software | GPL-2.0-or-later | 実行形式を subprocess で呼ぶのみ（コードは取り込まない） | https://poppler.freedesktop.org/ |
| pdf2json | Software | Apache-2.0 | npm パッケージを実行形式として呼ぶのみ | https://github.com/modesty/pdf2json |
| pypdfium2 | Software | Apache-2.0 または BSD-3-Clause（バインディング）。同梱の PDFium バイナリは BSD-3-Clause / Apache-2.0（PDFium が取り込む依存はそれぞれのライセンス） | Python パッケージを import（extra `level1`。pdfplumber の依存でもある） | https://github.com/pypdfium2-team/pypdfium2 |
| mutool（MuPDF、Debian / Ubuntu の `mupdf-tools`） | Software | AGPL-3.0-or-later または Artifex Commercial License（デュアル） | 実行形式を subprocess で呼ぶのみ（リンクせず、コードは取り込まない。未導入ならスキップ） | https://mupdf.com/ |
| Apache Tika（tika-app 3.3.3。PDFBox などの依存を同梱した jar） | Software | Apache-2.0（同梱の依存は jar 内の LICENSE / NOTICE を参照） | 公式 jar を `java -jar` で子プロセスとして呼ぶのみ（`scripts/fetch_java_jars.sh` で取得。リポジトリには含めない） | https://tika.apache.org/ |
| Tabula（tabula-java 1.0.5、jar-with-dependencies） | Software | MIT（同梱の依存: PDFBox / FontBox / jbig2-imageio / Commons CLI・CSV・Logging / Gson は Apache-2.0、JTS は EPL-2.0 または EDL-1.0、SLF4J は MIT、jai-imageio は BSD-3-Clause（jpeg2000 は JJ2000 ライセンス）） | 公式 jar をクラスパスに置き、`jdocbench/adapters/TabulaPages.java` から API を呼ぶ（子プロセス。取得は Tika と同じ） | https://github.com/tabulapdf/tabula-java |
| YomiToku（yomitoku 0.15.0） | Software | **CC BY-NC-SA 4.0（非商用のみ）**。商用利用（有償 PoC・業務への組み込み・SaaS・受託・自社業務の効率化・出力結果の外部提供など）は別途の商用ライセンスが必要 | **非商用のみ。商用案件では有効にしない**。本体の venv・extras・uv.lock に入れず、別環境（`uv tool install`）の CLI を subprocess で呼ぶのみ（コードは取り込まない）。オプトイン `JDOCBENCH_YOMITOKU=1` | https://github.com/kotaro-kinoshita/yomitoku |
| YomiToku のモデル（`KotaroKinoshita/yomitoku-text-detector-dbnet-v2_1`、`-text-recognizer-parseq-middle-dynw-v5`、`-layout-parser-rtdtrv2-v2`、`-table-structure-recognizer-rtdtrv2-open-beta`） | Model | **CC BY-NC-SA 4.0（非商用のみ）** | `scripts/install_yomitoku.sh --models` がコミット SHA を固定して Hugging Face から取得（4 ファイル・約 617MB、`$HF_HOME/hub`。#227） | https://huggingface.co/KotaroKinoshita |
| PyMuPDF（pymupdf） | Software | AGPL-3.0-only または Artifex Commercial License（デュアル） | Python パッケージを import して利用（オプトイン extra `pymupdf`。既定では導入しない） | https://pymupdf.readthedocs.io/ |
| MarkItDown（markitdown） | Software | MIT | Python パッケージを import（extra `markitdown`。PDF は pdfminer.six / pdfplumber を使う。LLM・Azure 連携は使わない） | https://github.com/microsoft/markitdown |
| Magika（magika） | Software / Model | Apache-2.0 | markitdown の依存（ファイル種別の判定）。判定モデル（約 3MB）は wheel に同梱。実行時のダウンロードは無し | https://github.com/google/magika |
| Unstructured（unstructured） | Software | Apache-2.0 | Python パッケージを import（extra `unstructured`。`partition_pdf(strategy="fast")` のみ。Unstructured API・利用状況の送信は使わない） | https://github.com/Unstructured-IO/unstructured |
| unstructured-inference | Software | Apache-2.0 | unstructured の依存（`partition_pdf` がモジュールの先頭で import する）。fast ではモデルを使わず、モデルの取得も無し | https://github.com/Unstructured-IO/unstructured-inference |
| Docling（docling / docling-core / docling-ibm-models / docling-parse） | Software | MIT | Python パッケージを import（extra `level2`） | https://github.com/docling-project/docling |
| Docling models（ds4sd/docling-models: layout / TableFormer） | Model | CDLA-Permissive-2.0 / Apache-2.0 | 初回実行時に Hugging Face から自動取得（約 500MB、`HF_HOME`） | https://huggingface.co/ds4sd/docling-models |
| RapidOCR（rapidocr） | Software | Apache-2.0 | Python パッケージを import（docling の依存。`docling_ocr` の OCR エンジン） | https://github.com/RapidAI/RapidOCR |
| ONNX Runtime（onnxruntime） | Software | MIT | Python パッケージを import（extra `level2`。RapidOCR の推論バックエンド） | https://github.com/microsoft/onnxruntime |
| PP-OCR models（PP-OCRv6 det / rec small、PP-OCRv4 cls mobile） | Model | Apache-2.0 | `rapidocr` の wheel に同梱（約 31MB）。実行時のダウンロードは無し | https://www.modelscope.cn/models/RapidAI/RapidOCR |
| PaddleOCR（paddleocr） | Software | Apache-2.0 | Python パッケージを import（extra `level2-paddle`。`ppstructure` アダプタ） | https://github.com/PaddlePaddle/PaddleOCR |
| PaddleX（paddlex） | Software | Apache-2.0 | Python パッケージを import（paddleocr の依存。モデルの取得・推論パイプライン） | https://github.com/PaddlePaddle/PaddleX |
| PaddlePaddle（paddlepaddle、CPU 版） | Software | Apache-2.0 | Python パッケージを import（推論ランタイム） | https://github.com/PaddlePaddle/Paddle |
| PP-StructureV3 models（PP-DocLayout_plus-L、PP-OCRv5_mobile_det / _rec、SLANeXt_wired、SLANet_plus、RT-DETR-L wired / wireless table cell det、PP-LCNet_x1_0 doc_ori / table_cls / textline_ori） | Model | Apache-2.0 | 初回実行時に自動取得（10 モデル・約 770MB、`PADDLE_PDX_CACHE_HOME`（既定 `~/.paddlex`）。取得元は PaddlePaddle の BOS / Hugging Face） | https://huggingface.co/PaddlePaddle |
| Tesseract OCR（tesseract） | Software | Apache-2.0 | 実行形式を subprocess で呼ぶのみ（OS のパッケージで導入。`tesseract` / `tesseract_psm6` アダプタ） | https://github.com/tesseract-ocr/tesseract |
| Leptonica | Software | BSD-2-Clause | Tesseract の依存ライブラリ（OS のパッケージとして入る。jdocbench からは直接使わない） | http://www.leptonica.org/ |
| Tesseract 学習データ（`jpn.traineddata`） | Model | Apache-2.0（tessdata / tessdata_fast とも） | OS のパッケージ（Ubuntu `tesseract-ocr-jpn`）で導入。jdocbench には同梱しない | https://github.com/tesseract-ocr/tessdata_fast |
| Marker（marker-pdf 2.0.0） | Software | Apache-2.0 | Python パッケージを import / CLI（`marker_single`）を子プロセスで実行（オプトイン extra `level2-marker`。`marker` アダプタ、`JDOCBENCH_MARKER=1`） | https://github.com/datalab-to/marker |
| Surya（surya-ocr 0.22.1） | Software | Apache-2.0 | Python パッケージ（marker の依存。レイアウト検出サーバー `surya.fast_layout.server` を子プロセスで起動） | https://github.com/datalab-to/surya |
| pdftext（0.7.1） | Software | Apache-2.0 | Python パッケージ（marker の依存。PDF のテキスト層の抽出） | https://github.com/datalab-to/pdftext |
| Surya Layout (fast)（datalab-to/surya_layout2: rf-detr レイアウト検出 + 読み順ヘッド） | Model | **AI PUBS OPEN RAIL-M LICENSE (MODIFIED)**（HF のメタデータは `license: openrail`） | 初回実行時に Hugging Face から自動取得（約 140MB、`HF_HOME`）。VLM（surya-ocr-2）の重みは使わない・取得しない | https://huggingface.co/datalab-to/surya_layout2 |
| MinerU（mineru） | Software | MinerU Open Source License（Apache-2.0 + 追加条項: 利用者と関連会社の合算で月間アクティブユーザー 1 億超または月間売上 2,000 万ドル超なら別途商用ライセンスが必要。第三者にオンラインサービスとして提供する場合は MinerU の利用を画面または公開文書に明示。違反時はライセンスが自動終了） | Python パッケージを import（オプトイン extra `level2-mineru` + 環境変数 `JDOCBENCH_MINERU=1`。`basic` tier のみ。リモート解析・LLM 補助・VLM・telemetry は使わない） | https://github.com/opendatalab/MinerU/blob/master/LICENSE.md |
| DocVortex（docvortex） | Software | MIT AND Apache-2.0 AND BSD-3-Clause | mineru の依存（middle JSON の型・PDF の読み込み） | https://github.com/myhloli/DocVortex |
| mineru-llama-cpp / mineru-vl-utils | Software | ライセンス表記なし（PyPI・METADATA に無い）/ Apache-2.0 | mineru の必須依存として導入されるが、VLM 用で `basic` tier では import しない | https://pypi.org/project/mineru-llama-cpp/ |
| MinerU 4 ONNX models（PP-DocLayoutV2、PP-OCRv6 tiny det / small rec、PP-OCRv4 seal det、PP-FormulaNet plus-M、PP-LCNet_x1_0 table_cls、SLANet+、UNet） | Model | Apache-2.0（リポジトリ自体にライセンス表記は無く、README で元モデルのライセンスに従うとする。`manifest.json` の取得元 `PaddlePaddle/*_onnx`・`jinzhenj/PP-FormulaNet_plus-M_onnx`・`opendatalab/MinerU-4_models_torch` はすべて Apache-2.0） | 使うモデルだけを初回実行時に自動取得（全部で 13 ファイル・約 820MB、`MINERU_MODEL_BASE_DIR`（既定 `~/.mineru/models`））。取得元は Hugging Face | https://huggingface.co/opendatalab/MinerU-4_models_onnx |

YomiToku は**コードとモデルの重みの両方が CC BY-NC-SA 4.0（非商用のみ）**。**商用案件では有効にしない**。非商用のオプトインとして扱い、`JDOCBENCH_YOMITOKU=1` を設定したときだけ動かす。依存（`pypdfium2==4.30.0` の固定）が本体と衝突するため、本体の venv・extras・uv.lock には入れず、別環境の CLI を外部コマンドとして呼ぶだけにしている（vendoring しないので jdocbench のコードの配布条件は変わらない）。導入手順:

```bash
S=~/.cache/jdocbench-spikes/yomitoku
UV_TOOL_DIR=$S/tool UV_TOOL_BIN_DIR=$S/bin \
  uv tool install yomitoku==0.15.0 \
  --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match
export JDOCBENCH_YOMITOKU=1 JDOCBENCH_YOMITOKU_BIN=$S/bin/yomitoku
```

YomiToku の商用利用ガイドラインでは出力結果（Outputs）の外部提供も商用に当たる。そのため YomiToku の結果は**本ベンチマークの公開表（README・記事・公開サイトなど）には載せない**。アダプタのコードは含めるが、既定は無効で、YomiToku のコードもモデルも同梱しない。導入時の調査の記録は [`docs/spikes/yomitoku.md`](spikes/yomitoku.md)。

mutool も MuPDF（AGPL）だが、jdocbench は外部コマンドとして起動するだけで同梱・リンクしない（pdftotext の GPL と同じ扱い）。CI では apt の `mupdf-tools` を入れる。

PyMuPDF は AGPL のため、jdocbench を AGPL 以外で配布・ネットワーク提供する場合は PyMuPDF を含めない構成（既定の `uv sync`）を使うこと。`uv sync --extra pymupdf` / `--all-extras` を選んだ環境でのみ導入される。

Marker は**コードが Apache-2.0、モデルの重みが modified AI Pubs Open RAIL-M** で条件が異なる。重みの LICENSE は冒頭で「AI PUBS OPEN RAIL-M LICENSE (MODIFIED) Version 0.1, March 2, 2023 (Modified)」と名乗り、同じライセンスの重みについて Datalab は README（surya-ocr-2-gguf。スパイクで確認）で「modified AI Pubs Open Rail-M license (free for research, personal use, and startups under $5M funding/revenue)」（研究・個人利用と、調達額・売上 $5M 未満のスタートアップのみ無償）。MIT の本体とは条件が違うため、PyMuPDF と同じくオプトイン extra `level2-marker` に分け、さらに実行も `JDOCBENCH_MARKER=1` のときだけにしている（`--all-extras` では導入される）。marker-pdf は `anthropic` / `openai` / `google-genai` を必須依存として引き込むが、それらは `--use_llm`（外部 LLM による補正）用で、`marker` アダプタは `--use_llm` を渡さず**外部 API を呼ばない**（Parsing 経路で外部 LLM / VLM API を呼ばない方針）。VLM の推論サーバー（llama-server）も起動しない（`docs/spikes/marker.md`）。

MinerU はコードのライセンスに規模の閾値とオンライン提供時の表示義務が付くため、既定の `uv sync` には含めず、`uv sync --extra level2-mineru` / `--all-extras` を選び、かつ `JDOCBENCH_MINERU=1` を設定したときだけ動かす。判断の根拠は [`docs/spikes/mineru.md`](spikes/mineru.md)。

評価に使うサンプル文書と Ground Truth は本リポジトリに含めない（開発リポジトリで別に管理し、出典とライセンスもそちらに記録する）。
