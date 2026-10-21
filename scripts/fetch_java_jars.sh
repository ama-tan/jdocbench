#!/usr/bin/env bash
# Java 系アダプタ（tika / tabula）の jar を版とチェックサムを固定して取得する（#192）。
#
#   bash scripts/fetch_java_jars.sh [保存先]   # 既定は ~/.cache/jdocbench（アダプタの既定の置き場所）
#
# 既にあるファイルは取り直さず、チェックサムだけを確認する。CI の bench もこれを使う。
# 版を上げるときは jdocbench/adapters/tika.py の TIKA_VERSION / tabula.py の TABULA_VERSION も合わせる。
#
# チェックサムの出典:
# - tika-app-3.3.3.jar: Maven Central の公式 jar。Maven Central の .sha1
#   （7a23f5b43f6cdf6ec1839b4cc282743e81747dfb）と、Apache の KEYS による .asc の署名
#   （Tim Allison, 184454FAD8697760F3E00D2E4A51A45B944FFD51）を確認したうえで SHA-256 を記録した
# - tabula-1.0.5-jar-with-dependencies.jar: tabula-java の GitHub Release（v1.0.5）。チェックサムの
#   公開が無いため、tabula-py 2.10.0 の wheel に同梱された同名の jar と SHA-256 が一致することを確認して記録した
set -euo pipefail

dest="${1:-$HOME/.cache/jdocbench}"
mkdir -p "$dest"
cd "$dest"

fetch() {
  local name="$1" url="$2"
  if [ ! -f "$name" ]; then
    # 取得元が一時的に 404 を返すことがある（CI の並列ジョブで 1 回発生）ので、404 も再試行する
    curl -fsSL --retry 5 --retry-delay 5 --retry-all-errors -o "$name.part" "$url" \
      || { echo "取得に失敗: $url" >&2; exit 1; }
    mv "$name.part" "$name"
  fi
}

fetch tika-app-3.3.3.jar \
  https://repo1.maven.org/maven2/org/apache/tika/tika-app/3.3.3/tika-app-3.3.3.jar
fetch tabula-1.0.5-jar-with-dependencies.jar \
  https://github.com/tabulapdf/tabula-java/releases/download/v1.0.5/tabula-1.0.5-jar-with-dependencies.jar

sha256sum -c <<'EOF'
f29d4e0d2cff1fc32396f6ac5ce436dfc7b19f9303d3b899e1f56fde85711c96  tika-app-3.3.3.jar
2161e3fff65939d7ce0812479cf9ca36860db5697f7fc1fa01161ed4092a0745  tabula-1.0.5-jar-with-dependencies.jar
EOF
