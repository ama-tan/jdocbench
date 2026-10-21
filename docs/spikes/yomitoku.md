# スパイク: YomiToku の導入可否

日本語特化の Document AI である YomiToku を、OCR の比較に加えられるかを判断した記録。
Sprint 16（#196）でライセンスと依存の衝突を理由に見送り、Sprint 17（#213）でユーザー判断を受けて
別 venv の CLI として実測した。

## 結論（Sprint 17 / #213）: **導入する（go）。モードは通常モード。1 ページ 1 プロセスで呼ぶ**

判定基準（Issue と事前合意）: **Peak RSS 3GB（3,072 MiB）未満**、**1 ページ 60 秒未満**。

- **通常モードを 1 ページずつ別プロセスで呼ぶ**（`--pages N`）と、3 文書 13 ページの最大 Peak RSS は
  **2,440 MiB**、ページあたり **約 24.5 秒**。基準を両方満たす
- 同じ通常モードでも、**文書をまとめて 1 プロセスで処理すると Peak RSS がページ数とともに増え**、
  4 ページで 3,114 MiB、6 ページで 3,273 MiB と基準を超える（`MALLOC_ARENA_MAX=2` でも 2,892 MiB で、増加は残る）
- `--lite` は文書まとめで 2,819〜3,042 MiB・ページあたり 7〜11 秒。6 ページで 3,042 MiB と基準ぎりぎりで、
  ページ数が多い文書では超える見込み。1 ページ 1 プロセスなら下がる（1 ページで 2,001 MiB）が、
  通常モードでも基準を満たせるので、Issue の取り決め（両方満たせば通常モード）に従い**通常モードを採る**
- 表はセル構造（行・列・結合）つきで取れる。デジタル PDF・現代のスキャンでは表の値がセルに入る。
  1920 年の実物スキャン（estat）の大きな表は、行・列は切れるがセルの 7 割が空で、数字は段落として出る
- 依存の衝突は `uv tool` の別環境で避けられた。本体の `uv lock --check` は差分なし

アダプタ（#214）での呼び方（ページ数 N は pypdfium2 等で事前に数え、1 から N まで繰り返す）:

```bash
HF_HOME=<モデルキャッシュ> yomitoku <pdf> -f json -o <outdir> -d cpu --pages <N>
```

## 導入手順（本体の venv と uv.lock を変えない）

```bash
S=~/.cache/jdocbench-spikes/yomitoku
UV_TOOL_DIR=$S/tool UV_TOOL_BIN_DIR=$S/bin \
  uv tool install yomitoku==0.15.0 \
  --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match   # 約 40 秒
$S/bin/yomitoku --help
```

- 導入されたのは torch 2.14.1+cpu / torchvision 0.29.1+cpu / onnxruntime 1.23.2 / opencv-python 5.0.0.93 /
  pypdfium2 4.30.0 ほか 52 パッケージ。**nvidia-* / triton は入らない**（CUDA 版を引いていない。
  `torch.cuda.is_available()` は False）
- uv が選んだ Python は **3.10.20**（tool 環境のみ）。tool 環境のサイズは約 1.4GB
- `--index-strategy unsafe-best-match` は、CPU 版 index にない依存を PyPI から取るために要る（marker のスパイクと同じ）
- 本体: `uv lock --check` は `Resolved 271 packages` で差分なし。pyproject.toml / uv.lock は変更していない

## モデル（CI キャッシュ設計 #215 用）

取得先はすべて HF Hub の `KotaroKinoshita/*`（各リポジトリ `model.safetensors` 1 ファイル）。
初回実行時に `huggingface_hub` が `$HF_HOME/hub` に落とす（未指定なら `~/.cache/huggingface/hub`）。
本スパイクでは `HF_HOME=~/.cache/jdocbench-spikes/yomitoku/hf` に寄せた。

| モード | リポジトリ | サイズ |
|---|---|---|
| 共通 | `KotaroKinoshita/yomitoku-text-detector-dbnet-v2_1` | 98MB |
| 共通 | `KotaroKinoshita/yomitoku-layout-parser-rtdtrv2-v2` | 164MB |
| 共通 | `KotaroKinoshita/yomitoku-table-structure-recognizer-rtdtrv2-open-beta` | 164MB |
| 通常 | `KotaroKinoshita/yomitoku-text-recognizer-parseq-middle-dynw-v5` | 192MB |
| lite | `KotaroKinoshita/yomitoku-text-recognizer-parseq-tiny-dynw-v5` | 35MB |

- 合計: **通常モード 617MB**、lite を足すと 652MB
- 初回ダウンロード: 通常モードの 4 モデルで**約 70 秒**（未認証。モデル初期化を含む）。初回の 1 ページ実行は
  計 90.6 秒、2 回目以降の同じ 1 ページは約 25 秒
- `--lite` は初回に検出モデルを ONNX へ変換し、**tool 環境の site-packages 内**
  （`yomitoku/onnx/yomitoku-text-detector-dbnet-v2_1.onnx{,.data}`、約 97MB）に書く。通常モードでは作らない。
  通常モードを採るので CI では気にしなくてよい
- 未認証だと HF Hub のレート制限の警告が出る。CI では `HF_HOME` をキャッシュすれば 2 回目以降は通信しない

### リビジョンの固定（#227）

上の「初回実行時に落とす」は #215 時点の挙動。#227 で、取得は `scripts/install_yomitoku.sh --models` に寄せ、
各モデルをコミット SHA で固定した（2026-10-20 時点の main）。

| リポジトリ | コミット SHA |
|---|---|
| `KotaroKinoshita/yomitoku-text-detector-dbnet-v2_1` | `3ac8375d1fd124e8074b013a32f2085d1199654c` |
| `KotaroKinoshita/yomitoku-text-recognizer-parseq-middle-dynw-v5` | `cfe941a992ba3eedbdd3d54ef22ef34d39a88fa7` |
| `KotaroKinoshita/yomitoku-layout-parser-rtdtrv2-v2` | `6772f1b6ea5b694da2e8fe19cf6d38f453822616` |
| `KotaroKinoshita/yomitoku-table-structure-recognizer-rtdtrv2-open-beta` | `91103cbcefc7f9d0ba8234fd3caba28b6d564a46` |

- **取得**: `hf_hub_download(repo, "model.safetensors", revision=<SHA>)`（取得済みなら通信しない）。SHA を渡すと
  huggingface_hub は `refs/main` を書かないため、スクリプトが `refs/main` をその SHA に書く
- **実行時に固定の重みを読ませる方法**: yomitoku は `Net.from_pretrained(<リポジトリ名>)` を revision なしで呼ぶ
  （`yomitoku/base.py`）。オンラインだと main を問い合わせ、上流が進んでいれば新しい重みを取って `refs/main` も
  進める。`HF_HUB_OFFLINE=1` だけでは、各リポジトリに無い `config.json` の探索が `LocalEntryNotFoundError` に
  なり、`PyTorchModelHubMixin.from_pretrained`（tool 環境の huggingface_hub 2.2.0）が捕まえるのは
  `HfHubHTTPError` だけなので落ちる（実機で確認）。そこでアダプタは `refs/main` が指す
  `snapshots/<SHA>/` を、`--td_cfg` / `--tr_cfg` / `--lp_cfg` / `--tsr_cfg` の YAML の `hf_hub_repo` に渡す。
  ディレクトリを渡された `from_pretrained` は中の `model.safetensors` を直接読み、Hub に問い合わせない。
  仕掛け線として `HF_HUB_OFFLINE=1` も付ける（上書きが効かなければ、黙って取得せず失敗する）
- **記録**: run.json の `extra.models` に `{リポジトリ: SHA}`。SHA は実際に渡したスナップショットのディレクトリ名
- **未取得のとき**: アダプタは YomiToku を起動する前に、`install_yomitoku.sh --models` を案内して失敗する
- **CI**: HF のキャッシュキーにこのスクリプトのハッシュが入るので、SHA を変えるとキーも変わる。restore-keys で
  古いキャッシュが戻っても、スクリプトが固定の SHA を取得し直して `refs/main` を向け直す
- **照合（#245）**: 同じ `HF_HOME` で yomitoku をオンラインで直接動かすと `refs/main` が上流の main に進む。
  アダプタは期待する 4 SHA（`jdocbench/adapters/yomitoku.py` の `_REVISIONS`）を持ち、`refs/main` がそれと
  違えば YomiToku を起動する前に `install_yomitoku.sh --models` を案内して失敗する（違う重みの値を結果に混ぜない）。
  スクリプトを再実行すれば戻る。SHA はスクリプトと `_REVISIONS` の 2 箇所に書き、テスト
  （`test_revisions_match_install_script`）で一致を確かめる。スクリプト側に残すのは CI の HF キャッシュのキーが
  スクリプトのハッシュだから
- 確認（2026-10-20、mof-budget 3 ページ、`HF_ENDPOINT=http://127.0.0.1:9` で Hub に届かない状態）: 成功し、
  `extra.models` は上の 4 SHA。stderr に各スナップショットのディレクトリで `config.json not found in ...` の
  警告が出る（ディレクトリから読んだ証拠）。正規化結果は直近の CI（固定前）の結果と同一。Wall 106 秒、Peak RSS 2.3GB。
  空の `HF_HOME` では 0.2 秒で案内つきの RuntimeError

## 実測（2026-10-19）

環境: CentOS Stream 9、Intel Xeon（Cascade Lake）6 vCPU、RAM 7.5GiB、GPU なし。torch のスレッド数は既定（6）。
yomitoku 0.15.0、`-f json -d cpu`、既定の DPI 200。モデルはキャッシュ済み（初回ダウンロードは上の節）。
Peak RSS は `os.wait4()` の `ru_maxrss`（`perf.run_command` と同じ。子プロセス込み）。
重い実測は `flock` で 1 つずつ、開始前の available は 4.8〜5.7GB。

### 文書をまとめて 1 プロセス（`yomitoku <pdf> ...`）

| 文書 | ページ | 通常: Wall | 通常: Peak RSS | 通常: 秒/ページ | lite: Wall | lite: Peak RSS | lite: 秒/ページ |
|---|---:|---:|---:|---:|---:|---:|---:|
| estat-census-1920-scan-excerpt（実物スキャン） | 4 | 87.8 秒 | **3,114 MiB** | 22.0 | 34.0 秒 | 2,819 MiB | 8.5 |
| stat-go-jp-children-population-2026-scan | 6 | 140.8 秒 | **3,273 MiB** | 23.5 | 43.5 秒 | 3,042 MiB | 7.3 |
| mof-budget-summary-fy2026（デジタル） | 3 | 84.0 秒 | 2,758 MiB | 28.0 | 31.5 秒 | 2,914 MiB | 10.5 |

- 20 分の打ち切りに達した実行はない。全実行が終了コード 0
- Wall にはモデルの読み込み（約 10 秒）を含む。ログの `Total Processing time` は通常 / stat-scan で 129 秒
- lite の Peak RSS が通常と大差ないのは、レイアウト・表構造・検出のモデルが共通で、軽くなるのは認識モデルだけだから
- 参考: 通常 / stat-scan を `MALLOC_ARENA_MAX=2` で動かすと 123.6 秒・2,892 MiB

### 1 ページ 1 プロセス（`--pages N` を N = 1..ページ数で繰り返す）: 採用する呼び方

| 文書 | ページ | 通常: Wall 合計 | 通常: Peak RSS 最大 | 通常: 秒/ページ | 各ページの Wall（秒） |
|---|---:|---:|---:|---:|---|
| estat-census-1920-scan-excerpt | 4 | 98.8 秒 | 2,440 MiB | 24.7 | 14.1 / 17.5 / 22.3 / 44.9 |
| stat-go-jp-children-population-2026-scan | 6 | 146.5 秒 | 2,395 MiB | 24.4 | 22.1 / 30.4 / 23.2 / 33.9 / 19.6 / 17.3 |
| mof-budget-summary-fy2026 | 3 | 73.4 秒 | 2,302 MiB | 24.5 | 24.8 / 24.9 / 23.7 |

- 1 ページの最大は estat の p4（55 行 × 10 列の表）で 44.9 秒・2,440 MiB。基準（60 秒）に対し余裕は 15 秒
- まとめて処理するより Wall は 1 割前後増える（ページごとのモデル読み込み）が、Peak RSS はページ数に依らず 2.2〜2.4GB
- 参考: lite を 1 ページ 1 プロセスで stat-scan の p1 に当てると 17.2 秒・2,001 MiB
- CI（ubuntu-latest）は開発機より遅い前提で、1.5〜2 倍と見ると 1 ページ 35〜50 秒（estat p4 は 70〜90 秒の恐れ）。
  #215 では文書単位のジョブ時間を確認する

## 出力の形（アダプタ #214 の入力）

### ファイル名

`-o <outdir>` に **1 ページ 1 ファイル**で `<親ディレクトリ名>_<PDF の stem>_p<ページ番号>.json` を書く。
例: `samples/mof-budget-summary-fy2026.pdf` → `samples_mof-budget-summary-fy2026_p1.json`。
ページ番号は 1 始まりで、`--pages 3` なら `_p3.json` だけが出る。`--combine` で 1 ファイルにまとめられる（未検証）。
接頭辞が入力 PDF の親ディレクトリ名に依存するので、アダプタは `*_p<N>.json` を glob で拾うのが安全。

### トップレベル（1 ページ分）

```json
{"paragraphs": [...], "tables": [...], "figures": [...], "words": [...]}
```

- 座標はすべて **DPI 200 で描画したページ画像のピクセル**（`box` は `[x1, y1, x2, y2]`、`words` は 4 点の `points`）。
  PDF のポイントに戻すには × 72 / 200
- **読み順**: `paragraphs` / `tables` / `figures` の各要素が `order`（ページ内で共通の連番、0 始まり）を持つ。
  ページ内の要素を `order` で並べれば読み順になる（`--reading_order auto` が既定）。`words` は `order` を持たない
- ヘッダ・フッタは段落の `role`（`page_header` / `page_footer`）で分かる。`--ignore_meta` で出力から落とせる

### 段落（`paragraphs[]`）

```json
{"box": [1176, 29, 1384, 62], "contents": "総務省統計局", "direction": "horizontal", "order": 0, "role": "page_header"}
```

- `role`: `null`（本文）/ `section_headings` / `page_header` / `page_footer` / `list_item` / `caption` /
  `inline_formula` / `display_formula` / `index`（スキーマの説明より）
- `contents` は行ごとに `\n` を含む（`--ignore_line_break` で除ける）。`direction` は `horizontal` / `vertical`

### 表（`tables[]`）

```json
{"box": [171, 367, 1563, 1845], "n_row": 13, "n_col": 5, "order": 3,
 "rows": [{"box": [...], "score": 0.87}, ...], "cols": [...], "spans": [...],
 "cells": [
   {"row": 1, "col": 1, "row_span": 1, "col_span": 1, "box": [...], "contents": "区\n分"},
   {"row": 3, "col": 2, "row_span": 1, "col_span": 1, "box": [...], "contents": "778,190"}
 ]}
```

- `row` / `col` は **1 始まり**。結合は `row_span` / `col_span`（estat p4 では `(2, 3)` などの結合を検出）
- `rows` / `cols` / `spans` は検出した行・列・結合領域の box とスコアで、セルの再構成には不要
- セルの `contents` も行ごとに `\n` を含む

### 図（`figures[]`）

```json
{"box": [1192, 295, 1274, 384], "direction": "horizontal", "figure_path": null, "order": 4, "paragraphs": []}
```

- 既定では図の画像は書き出さない（`--figure` で書き出す）。図の中の文字は `--figure_letter` で `paragraphs` に入る

### 単語（`words[]`）

```json
{"content": "1 ", "det_score": 0.66, "direction": "horizontal", "points": [[199, 1587], [292, 1587], [292, 1614], [199, 1614]], "rec_score": 0.25}
```

段落・セルに入った文字の元になる行単位の認識結果。アダプタは段落と表だけで足りる（二重計上を避けるため words は使わない）。#214 の正規化では、表の中身の二重計上を除く判定（#199）に渡す語の座標を作るのにだけ使う（本文には使わない）。

## 出力の目視

| 文書 | 表の検出（通常モード） | 一言 |
|---|---|---|
| mof-budget（デジタル） | 3 ページ各 1 表（13×5 / 28×5 / 17×5）、空セル 4〜9 | 行・列・値とも正しく取れている。「歳入」の見出しセルに罫線由来の `グーーーー` が混ざる、「公債金」が「公債債金」になるなど、OCR の誤りが少しある。テキスト層は使わず、デジタル PDF も描画して OCR する |
| stat-scan（現代のスキャン） | p2 に 2 表（10×5 / 7×10）、p4 に 2 表（19×8 / 14×6）、空セル 0〜4 | 表の構造・値とも良好。グラフは `figures` として出る |
| estat（1920 年の実物スキャン） | p3 に 14×10、p4 に 55×10 | 行・列の区切りは取れるが、p4 は 539 セル中 398 が空で、数字 226 個が表の領域内の段落として出る。`1` を `I` / `Ⅰ` と読む誤りも多い。アダプタでは表の領域内の段落の扱いを決める必要がある |

- 通常と lite でレイアウト・表の検出結果（表の数・行列数・空セル数）は全ページで同じ。違うのは認識した文字だけで、
  文字列の一致率（difflib の ratio）は estat 0.945 / stat-scan 0.992 / mof 0.947

## ライセンス

コードとモデルの重みの両方が **CC BY-NC-SA 4.0**（非営利）。Sprint 16 の確認内容は下の表のとおり。

**扱い（Sprint 17 でユーザーが判断）**: ユーザー判断で**非商用のオプトイン**として導入する。
既定では動かさず（`JDOCBENCH_YOMITOKU=1` のときだけ）、本体の extras と uv.lock には入れず、別環境の CLI を
外部コマンドとして呼ぶ。CI の bench では回すが、**README・記事などの公開表への掲載は Sprint 19 の公開判断まで保留**する。

| 項目 | 内容 | 確認元 |
|---|---|---|
| 版 | yomitoku 0.15.0（PyPI の最新） | https://pypi.org/project/yomitoku/ |
| PyPI の License 欄 | `CC BY-NC-SA 4.0` | PyPI の JSON API |
| 対象 | 「本リポジトリ内のソースコードおよび本プロジェクトに関連する HuggingFace Hub 上のモデルの重みファイル」 | GitHub README の LICENSE 節 |
| 非営利で許される利用 | 「非商用での個人利用・研究目的での利用は自由」。ガイドラインの PoC の例: 自社内で完結する精度検証・アルゴリズム / モデル比較、学術研究・教育（ただし研究成果物を商用利用する場合は別途相談） | README / `docs/commercial_use_guideline.ja.md` |
| 商用とされる利用 | 有償 PoC、業務への組み込み、SaaS・再販、受託、自社業務の効率化、**出力結果（Outputs）の外部提供** | 同ガイドライン §2 |
| その他の禁止 | 競合するモデルの開発・学習への利用は形態を問わず不可 | 同ガイドライン §3 |
| 商用ライセンス | 製品版（オンプレ / AWS Marketplace）を MLism 社が提供 | README |

本体（MIT）への影響: YomiToku を vendoring せず、別環境のコマンドとして呼ぶだけなので、jdocbench の
コードの配布条件は変わらない。ShareAlike が及ぶのは YomiToku の改変物で、呼び出すアダプタは含まない
と考えられる。公開表に載せるか（出力結果の外部提供に当たるか）は Sprint 19 で判断する。

## 経緯: Sprint 16（#196）の見送り

Sprint 16 では次の 2 点で見送った（2026-10-17）。

1. **ライセンス**: 企業が運営する公開ベンチマークで結果を公開する使い方が非営利の範囲に入るかを、
   こちらでは判断できなかった → Sprint 17 でユーザーが上記の扱いを決めた
2. **依存関係**: `yomitoku` は `pypdfium2==4.30.0` を固定で要求し、`--all-extras` の lock に入れると
   全アダプタの pypdfium2 が 5.13.0 → 4.30.0、pdfplumber が 0.11.10 → 0.11.9、pdfminer.six が
   20260107 → 20251230 に下がる → Sprint 17 で `uv tool` の別環境にして解消した

当時の技術メモ: 依存は torch >= 2.6 / torchvision >= 0.21 / onnxruntime / `opencv-python` / `pypdfium2==4.30.0` /
timm / onnx / onnxscript。CPU 向けの軽量モード（`--lite`、認識に `parseq-tiny`、検出を ONNX 化）がある。
表はセル構造つきで返る（JSON / HTML）。

## 再現用の資材

`~/.cache/jdocbench-spikes/yomitoku/` に置いた（#214 / #215 で再利用するので消さない）。

- `tool/`（uv tool の環境）、`bin/yomitoku`（コマンド）、`hf/`（`HF_HOME`。モデル 652MB）
- `install.sh`（導入）、`run.sh`（available 4GB 待ち + `flock` + 計測）、`perpage.sh`（1 ページ 1 プロセス）、
  `measure.py`（`os.wait4` で Wall / Peak RSS、20 分で打ち切り）
- `out/results.jsonl`（全実行の Wall / Peak RSS）、`out/<label>/`（JSON 出力）、`out/<label>.log`
