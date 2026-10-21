"""cv2 を提供する配布パッケージが 1 つだけであることの確認（#171）。

opencv-python / opencv-python-headless / opencv-contrib-python(-headless) はどれも同じ cv2/ に
ファイルを置くため、2 つ以上入るとインストール順しだいで壊れた cv2 になる。
"""

import importlib.metadata

import pytest

_CV2_DISTS = (
    "opencv-python",
    "opencv-python-headless",
    "opencv-contrib-python",
    "opencv-contrib-python-headless",
)


def _installed() -> list[str]:
    found = []
    for name in _CV2_DISTS:
        try:
            importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        found.append(name)
    return found


def test_cv2_has_single_provider() -> None:
    found = _installed()
    if not found:
        pytest.skip("cv2 を使う extra（camelot / level2 / level2-paddle）が無い環境ではスキップ")
    # paddlex は配布パッケージ名 opencv-contrib-python の有無で PP-StructureV3 を
    # 起動するか決めるため、揃える先は contrib 版に固定する（pyproject.toml の [tool.uv] を参照）
    assert found == ["opencv-contrib-python"]
