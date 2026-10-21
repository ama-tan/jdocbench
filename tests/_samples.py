"""実 PDF（samples/）の置き場所。

公開リポジトリには samples/ を含めないため、環境変数 JDOCBENCH_SAMPLES で場所を受け取る。
未設定なら従来どおりリポジトリ直下の samples/ を使う。PDF が無いテストは skip する。
"""

import os
from pathlib import Path

SAMPLES = Path(
    os.environ.get("JDOCBENCH_SAMPLES") or Path(__file__).resolve().parents[1] / "samples"
)
