"""アダプタが満たす最小のインタフェース。"""

from pathlib import Path
from typing import Protocol


class Adapter(Protocol):
    """パーサを 1 つ呼び出すアダプタ。メソッドはこの 3 つだけに保つ。"""

    name: str

    def version(self) -> str:
        """ツールのバージョン文字列。取れなければ "unknown"。"""
        ...

    def available(self) -> bool:
        """この環境で実行できるか。"""
        ...

    def run(self, pdf: Path, out_dir: Path) -> dict[str, object]:
        """raw 出力を out_dir 配下に書き、計測値を返す。失敗は例外で伝える。

        戻り値は `wall_time_sec` / `peak_rss_kb` を含む dict。計測できないアダプタは
        空の dict を返してよい。

        戻り値の `extra`（dict）があれば run.json の `adapters[<name>]["extra"]` に
        そのまま記録される。実験条件の記録（企画書 §49）に使う。
        """
        ...
