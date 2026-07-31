"""分层记忆。

    WorkingMemory   工作记忆：当前任务的消息序列，超阈值自动压缩
    Store           情景记忆：SQLite 持久化会话与消息，支持 resume
    SemanticMemory  语义记忆：跨会话的长期知识，按相关性召回
    EvidenceStore   证据库：本次调研抓到的原始资料，支撑可追溯引用
"""

from .evidence import EvidenceHit, EvidenceStore, chunk_text
from .semantic import MemoryHit, SemanticMemory
from .store import Store
from .working import CompactionResult, WorkingMemory

__all__ = [
    "CompactionResult",
    "EvidenceHit",
    "EvidenceStore",
    "MemoryHit",
    "SemanticMemory",
    "Store",
    "WorkingMemory",
    "chunk_text",
]
