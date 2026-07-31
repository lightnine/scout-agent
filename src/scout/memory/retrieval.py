"""检索打分工具。

语义检索用 embedding 余弦相似度；没有配置 embedding 模型时退化为关键词打分。
关键词分词对中文做了 bigram 处理——中文没有空格，只按空白切词等于没切。
"""

from __future__ import annotations

import math
import re
from collections import Counter

_WORD = re.compile(r"[a-zA-Z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def tokenize(text: str) -> list[str]:
    """英文按单词、中文按二元组切分。

    "向量数据库" -> ["向量", "量数", "数据", "据库"]，
    这样"数据库"这个查询词能命中，而不需要引入 jieba 之类的分词依赖。
    """
    lowered = text.lower()
    tokens = _WORD.findall(lowered)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", lowered)
    for run in cjk_runs:
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def keyword_score(query: str, text: str) -> float:
    """词频重合度打分，结果归一化到 0~1。"""
    q_tokens = tokenize(query)
    if not q_tokens:
        return 0.0
    t_counter = Counter(tokenize(text))
    if not t_counter:
        return 0.0
    hit = sum(1 for token in set(q_tokens) if t_counter[token] > 0)
    coverage = hit / len(set(q_tokens))
    # 长文本天然更容易命中，用长度做轻微惩罚
    density = min(1.0, 200 / max(len(text), 1) + 0.5)
    return coverage * density


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))
