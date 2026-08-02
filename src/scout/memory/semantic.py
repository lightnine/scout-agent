"""长期语义记忆：跨会话保留的用户偏好、领域知识、既往结论。

和工作记忆的区别：工作记忆随任务结束而丢弃，语义记忆写进数据库，
下次开新会话时按当前问题相关性召回若干条注入到 runtime reminder 里。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .retrieval import cosine, keyword_score
from .store import Store


@dataclass(slots=True)
class MemoryHit:
    id: int
    content: str
    tags: str
    score: float


class SemanticMemory:
    def __init__(self, store: Store, llm: Any = None) -> None:
        self.store = store
        self.llm = llm

    @property
    def _embeddings_enabled(self) -> bool:
        return bool(self.llm and getattr(self.llm, "embedding_model", ""))

    def save(self, content: str, tags: str = "", session_id: str | None = None) -> int:
        content = content.strip()
        if not content:
            raise ValueError("记忆内容不能为空")
        vector: list[float] | None = None
        if self._embeddings_enabled:
            vectors = self.llm.embed([content])
            vector = vectors[0] if vectors else None
        return self.store.upsert_memory(content, tags, vector, session_id)

    def search(self, query: str, limit: int = 5, min_score: float = 0.15) -> list[MemoryHit]:
        rows = self.store.all_memories()
        if not rows:
            return []

        query_vector: list[float] = []
        if self._embeddings_enabled:
            vectors = self.llm.embed([query])
            query_vector = vectors[0] if vectors else []

        hits: list[MemoryHit] = []
        for row in rows:
            if query_vector and row["vector"]:
                score = cosine(query_vector, row["vector"])
            else:
                score = keyword_score(query, f"{row['content']} {row['tags']}")
            if score >= min_score:
                hits.append(MemoryHit(row["id"], row["content"], row["tags"], score))

        hits.sort(key=lambda h: h.score, reverse=True)
        top = hits[:limit]
        self.store.bump_memory_hits([h.id for h in top])
        return top

    def all(self) -> list[dict[str, Any]]:
        return self.store.all_memories()

    def forget(self, memory_id: int) -> bool:
        return self.store.delete_memory(memory_id)
