"""证据库：本次调研抓到的原始资料，以及可引用的来源清单。

这是调研型 Agent 与普通聊天机器人的分水岭。所有抓回来的正文都切块入库，
写报告时从库里检索证据、按 [S1] 这样的标签标注出处——
**结论必须可追溯到来源**，否则一份看起来很专业的报告可能整段是幻觉。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .retrieval import cosine, keyword_score
from .store import Store

CHUNK_SIZE = 900
CHUNK_OVERLAP = 120
EMBED_BATCH = 16


@dataclass(slots=True)
class EvidenceHit:
    label: str
    url: str
    title: str
    content: str
    score: float

    def render(self) -> str:
        return f"[{self.label}] {self.title}\n{self.content}\n（来源：{self.url}）"


class EvidenceStore:
    def __init__(self, store: Store, session_id: str, llm: Any = None) -> None:
        self.store = store
        self.session_id = session_id
        self.llm = llm

    @property
    def _embeddings_enabled(self) -> bool:
        return bool(self.llm and getattr(self.llm, "embedding_model", ""))

    def ingest(self, url: str, title: str, text: str) -> tuple[str, int, bool]:
        """把一篇正文切块入库，返回 (引用标签, 入库块数, 是否首次抓取)。"""
        source_id, label, is_new = self.store.add_source(self.session_id, url, title)
        if not is_new:
            return label, 0, False

        chunks = chunk_text(text)
        if not chunks:
            return label, 0, True

        embeddings: list[list[float]] = []
        if self._embeddings_enabled:
            for start in range(0, len(chunks), EMBED_BATCH):
                embeddings.extend(self.llm.embed(chunks[start : start + EMBED_BATCH]))

        self.store.add_evidence(self.session_id, source_id, chunks, embeddings or None)
        return label, len(chunks), True

    def search(self, query: str, limit: int = 6, min_score: float = 0.1) -> list[EvidenceHit]:
        rows = self.store.all_evidence(self.session_id)
        if not rows:
            return []

        query_vector: list[float] = []
        if self._embeddings_enabled:
            vectors = self.llm.embed([query])
            query_vector = vectors[0] if vectors else []

        hits: list[EvidenceHit] = []
        for row in rows:
            if query_vector and row["vector"]:
                score = cosine(query_vector, row["vector"])
            else:
                score = keyword_score(query, row["content"])
            if score >= min_score:
                hits.append(
                    EvidenceHit(row["label"], row["url"], row["title"], row["content"], score)
                )

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def sources(self) -> list[dict[str, Any]]:
        return self.store.list_sources(self.session_id)

    def count(self) -> int:
        return self.store.count_evidence(self.session_id)

    def bibliography(self) -> str:
        """生成 Markdown 参考文献列表，附加在报告末尾。"""
        sources = self.sources()
        if not sources:
            return ""
        lines = ["## 参考来源", ""]
        lines.extend(
            f"- [{s['label']}] [{s['title'] or s['url']}]({s['url']})" for s in sources
        )
        return "\n".join(lines)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """按段落聚合切块，尽量不把一段话拦腰截断。

    段落本身超长时才做硬切，并保留一点重叠，避免关键句正好落在边界上。
    """
    text = text.strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buffer = ""

    for paragraph in paragraphs:
        if len(paragraph) > size:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            step = size - overlap
            for start in range(0, len(paragraph), step):
                piece = paragraph[start : start + size]
                if piece.strip():
                    chunks.append(piece.strip())
            continue

        if len(buffer) + len(paragraph) + 2 <= size:
            buffer = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        else:
            chunks.append(buffer)
            buffer = paragraph

    if buffer:
        chunks.append(buffer)
    return chunks
