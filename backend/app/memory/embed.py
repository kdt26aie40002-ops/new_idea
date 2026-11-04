"""ローカル埋め込み（外部モデル不要）。

文字 2-gram / 3-gram を特徴量ハッシュで固定次元ベクトルにし、L2 正規化する。
日本語は分かち書きなしでもある程度の類似検索ができる。
意味的な検索精度は Ollama の埋め込みモデルに劣るため、将来差し替えられるよう
関数 1 つに閉じ込めている。
"""

from __future__ import annotations

import hashlib
import math
import re

DIM = 512
_WS = re.compile(r"\s+")


def _bucket(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(), "big") % DIM


def embed(text: str) -> list[float]:
    t = _WS.sub(" ", text.lower()).strip()
    vec = [0.0] * DIM
    for n in (2, 3):
        for i in range(max(len(t) - n + 1, 0)):
            vec[_bucket(t[i:i + n])] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def cosine(a: list[float], b: list[float]) -> float:
    # 両方とも正規化済みなので内積 = コサイン類似度
    return sum(x * y for x, y in zip(a, b))
