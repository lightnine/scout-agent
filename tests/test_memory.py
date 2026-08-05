"""记忆层：切块、检索、长期记忆、上下文压缩。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from scout.llm.base import Message, ToolCall
from scout.memory.evidence import EvidenceStore, chunk_text
from scout.memory.retrieval import cosine, keyword_score, tokenize
from scout.memory.semantic import SemanticMemory
from scout.memory.working import WorkingMemory


# ------------------------------------------------------------------ 检索打分
def test_tokenize_handles_chinese_bigrams():
    tokens = tokenize("向量数据库 Milvus")
    assert "milvus" in tokens
    assert "数据" in tokens and "据库" in tokens


def test_keyword_score_prefers_relevant_text():
    query = "向量数据库怎么选型"
    relevant = keyword_score(query, "选型时向量数据库主要看召回率和运维成本")
    irrelevant = keyword_score(query, "今天天气不错适合散步")
    assert relevant > irrelevant


def test_cosine_edge_cases():
    assert cosine([1, 0], [1, 0]) == 1.0
    assert cosine([], [1, 0]) == 0.0
    assert cosine([0, 0], [1, 0]) == 0.0


# ---------------------------------------------------------------------- 切块
def test_chunk_text_keeps_paragraphs_together():
    text = "\n\n".join(["段落一" * 50, "段落二" * 50, "段落三" * 50])
    chunks = chunk_text(text, size=400, overlap=50)
    assert len(chunks) >= 2
    assert all(len(c) <= 400 for c in chunks)


def test_chunk_text_splits_oversized_paragraph():
    chunks = chunk_text("啊" * 3000, size=900, overlap=100)
    assert len(chunks) >= 4
    assert all(len(c) <= 900 for c in chunks)


def test_chunk_text_empty():
    assert chunk_text("   ") == []


# ------------------------------------------------------------------ 证据入库
def test_evidence_ingest_and_search(store):
    session_id = store.create_session("t")
    evidence = EvidenceStore(store, session_id)

    label, chunks, is_new = evidence.ingest(
        "https://example.com/a",
        "Milvus 简介",
        "Milvus 是一个开源向量数据库。\n\n它支持十亿级向量检索，常用于 RAG 场景。",
    )
    assert (label, is_new) == ("S1", True)
    assert chunks >= 1

    label2, chunks2, is_new2 = evidence.ingest("https://example.com/a", "Milvus 简介", "重复内容")
    assert (label2, chunks2, is_new2) == ("S1", 0, False), "同一 URL 不应重复入库"

    hits = evidence.search("向量数据库")
    assert hits and hits[0].label == "S1"
    assert "[S1]" in hits[0].render()

    assert "参考来源" in evidence.bibliography()


def test_evidence_labels_increment_per_source(store):
    session_id = store.create_session("t")
    evidence = EvidenceStore(store, session_id)
    evidence.ingest("https://a.com", "A", "内容 A" * 30)
    label, _, _ = evidence.ingest("https://b.com", "B", "内容 B" * 30)
    assert label == "S2"
    assert [s["label"] for s in evidence.sources()] == ["S1", "S2"]


def test_evidence_is_isolated_per_session(store):
    first, second = store.create_session("a"), store.create_session("b")
    EvidenceStore(store, first).ingest("https://a.com", "A", "只属于第一个会话的内容" * 10)
    assert EvidenceStore(store, second).search("会话") == []


def test_add_source_concurrent_distinct_urls(store):
    session_id = store.create_session("t")
    urls = [f"https://example.com/{index}" for index in range(24)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda url: store.add_source(session_id, url, url), urls))

    labels = [label for _, label, is_new in results if is_new]
    assert len(labels) == len(urls)
    assert len(set(labels)) == len(labels)
    assert sorted(labels, key=lambda label: int(label[1:])) == [
        f"S{index}" for index in range(1, len(urls) + 1)
    ]
    assert [source["label"] for source in store.list_sources(session_id)] == [
        f"S{index}" for index in range(1, len(urls) + 1)
    ]


def test_add_source_concurrent_same_url(store):
    session_id = store.create_session("t")
    url = "https://example.com/same"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.add_source(session_id, url, "same"), range(24)))

    assert sum(is_new for _, _, is_new in results) == 1
    assert {label for _, label, _ in results} == {"S1"}
    assert len(store.list_sources(session_id)) == 1


# ------------------------------------------------------------------ 长期记忆
def test_semantic_memory_save_and_recall(store):
    memory = SemanticMemory(store)
    memory.save("用户是数据平台方向的后端工程师，关注 Agent 落地", tags="用户画像")
    memory.save("用户偏好中文输出，不喜欢冗长的开场白", tags="偏好")

    hits = memory.search("这个用户是做什么的")
    assert hits and "后端工程师" in hits[0].content


def test_semantic_memory_dedupes_same_content(store):
    memory = SemanticMemory(store)
    memory.save("同一句话")
    memory.save("同一句话", tags="更新后的标签")
    assert len(memory.all()) == 1


# ---------------------------------------------------------------- 上下文压缩
def _conversation() -> list[Message]:
    """构造一段"助手调工具 → 工具返回"交替出现的历史。"""
    messages: list[Message] = [Message(role="user", content="调研一下向量数据库" * 20)]
    for i in range(6):
        messages.append(
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id=f"c{i}", name="web_search", arguments={"query": "x"})],
            )
        )
        messages.append(
            Message(role="tool", content="搜索结果内容" * 200, tool_call_id=f"c{i}", name="web_search")
        )
    return messages


def test_compaction_reduces_tokens(fake_llm):
    fake_llm.script = [Message(role="assistant", content="摘要：已经查过 6 次搜索。")]
    working = WorkingMemory(threshold=500, keep_recent=4)
    working.extend(_conversation())
    assert working.needs_compaction()

    before = working.tokens()
    result = working.compact(fake_llm)

    assert result is not None
    assert result.tokens_after < before
    assert working.tokens() < before


def test_compaction_never_orphans_tool_messages(fake_llm):
    """压缩后，第一条被保留的消息不能是 tool —— 它的 tool_call_id 会悬空。"""
    fake_llm.script = [Message(role="assistant", content="摘要")]
    working = WorkingMemory(threshold=100, keep_recent=3)
    working.extend(_conversation())
    working.compact(fake_llm)

    for index, message in enumerate(working.messages):
        if message.role != "tool":
            continue
        previous = working.messages[index - 1]
        referenced = any(
            tc.id == message.tool_call_id
            for m in working.messages[:index]
            for tc in m.tool_calls
        )
        assert referenced, f"第 {index} 条 tool 消息 {message.tool_call_id} 找不到对应的调用（前一条是 {previous.role}）"


def test_compaction_keeps_original_task_verbatim(fake_llm):
    fake_llm.script = [Message(role="assistant", content="摘要")]
    working = WorkingMemory(threshold=100, keep_recent=2)
    working.extend(_conversation())
    original = working.messages[0].content
    working.compact(fake_llm)
    assert working.messages[0].content == original


def test_no_compaction_below_threshold(fake_llm):
    working = WorkingMemory(threshold=100000)
    working.extend(_conversation())
    assert working.needs_compaction() is False
    assert working.compact(fake_llm) is None
