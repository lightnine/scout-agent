# Scout 核心模块阅读指南

本文档按「先建立全局地图，再深入核心机制」的顺序，帮助快速定位 scout-agent 中值得优先阅读的核心模块。

相关文档：

- [`design.md`](design.md) — 设计取舍与架构说明
- [`flows.md`](flows.md) — 从启动到 Agent 执行的完整流程

---

## 推荐阅读顺序

### 第一层：入口与装配（约 30 分钟）

先看「东西是怎么串起来的」：

| 顺序 | 文件 | 为什么先看 |
| --- | --- | --- |
| 1 | [`flows.md`](flows.md) | 从 `uv run scout` 到 `agent.run()` 的完整链路 |
| 2 | [`../src/scout/runtime.py`](../src/scout/runtime.py) | **唯一知道「谁依赖谁」的地方**，所有模块在这里装配 |
| 3 | [`../src/scout/config.py`](../src/scout/config.py) | 配置项：LLM、搜索后端、权限模式、压缩阈值等 |
| 4 | [`../src/scout/cli.py`](../src/scout/cli.py) | CLI 入口：EventBus、Runtime、Session、Agent 怎么启动 |

`runtime.py` 的注释已经点明设计原则：上层只依赖注入的对象，单测可以换 FakeLLM / 内存库而不改业务代码。

### 第二层：Agent 心脏（约 1 小时，最重要）

这是整个项目的核心，决定 Agent 能不能在真实任务上跑通：

| 文件 | 职责 |
| --- | --- |
| **[`core/agent.py`](../src/scout/core/agent.py)** | Agent Loop：组装上下文 → 调 LLM → 执行工具 → 回灌结果；步数上限、强制收尾、重复调用检测、子 Agent 派生 |
| [`core/session.py`](../src/scout/core/session.py) | 会话对象：工作记忆、证据库、计划、用量统计 |
| [`core/events.py`](../src/scout/core/events.py) | 事件总线：Agent 不 print，只发事件；CLI / Trace / Web 各自订阅 |
| [`core/prompts.py`](../src/scout/core/prompts.py) | System prompt 与 runtime reminder 的组装逻辑 |

[`design.md` 第 4 节](design.md#4-agent-loop)「Agent Loop 里真正重要的四件事」和 `agent.py` 是对着看的——终止条件、工具失败不中断、tool_call 严格配对、重复调用拦截，都在那里。

### 第三层：工具系统（约 45 分钟）

| 文件 | 职责 |
| --- | --- |
| **[`tools/base.py`](../src/scout/tools/base.py)** | `@tool` 装饰器：函数签名 → JSON Schema，写工具 = 写普通函数 |
| **[`tools/registry.py`](../src/scout/tools/registry.py)** | 工具注册表 + 批量执行：全只读并行，有副作用则串行 |
| [`tools/__init__.py`](../src/scout/tools/__init__.py) | `build_registry()`：13 个工具的注册入口 |
| [`permissions.py`](../src/scout/permissions.py) + [`approval.py`](../src/scout/approval.py) | SAFE / CAUTION / DANGEROUS 三级权限 + 人工审批 |

具体工具可以按需挑着看，不必全读：

- [`tools/search.py`](../src/scout/tools/search.py) + [`tools/web.py`](../src/scout/tools/web.py) — 搜索与抓取（调研主路径）
- [`tools/evidence_tools.py`](../src/scout/tools/evidence_tools.py) — 证据入库，支撑 `[S1]` 引用
- [`tools/plan.py`](../src/scout/tools/plan.py) + [`tools/report.py`](../src/scout/tools/report.py) — 计划与报告
- [`tools/delegate.py`](../src/scout/tools/delegate.py) — 子 Agent 委派

### 第四层：记忆体系（约 45 分钟）

| 文件 | 职责 |
| --- | --- |
| [`memory/working.py`](../src/scout/memory/working.py) | 工作记忆 + 上下文压缩（超阈值自动摘要，且不切断 tool 链） |
| [`memory/store.py`](../src/scout/memory/store.py) | SQLite 持久化：会话、消息、计划 |
| [`memory/semantic.py`](../src/scout/memory/semantic.py) | 跨会话长期记忆，按问题召回 Top-K 注入 |
| [`memory/evidence.py`](../src/scout/memory/evidence.py) | 证据库：抓取正文切块，支撑可追溯引用 |
| [`memory/retrieval.py`](../src/scout/memory/retrieval.py) | 检索逻辑（证据 / 语义记忆的召回） |

### 第五层：LLM 抽象（约 20 分钟）

| 文件 | 职责 |
| --- | --- |
| [`llm/base.py`](../src/scout/llm/base.py) | LLM 客户端接口 |
| [`llm/openai_compat.py`](../src/scout/llm/openai_compat.py) | OpenAI 兼容 API 实现（智谱等） |
| [`llm/cache.py`](../src/scout/llm/cache.py) | 响应缓存 |

### 第六层：可观测性（可选）

| 文件 | 职责 |
| --- | --- |
| [`observability/trace.py`](../src/scout/observability/trace.py) | 全过程 JSONL 落盘，可 `jq` 分析 |

---

## 架构一图流

[`design.md`](design.md) 里的架构图就是模块关系：

```
CLI / Web → runtime.py（装配）
              ↓
         core/agent.py（Agent Loop）
         ↙    ↓    ↘
    llm/   tools/   memory/
              ↓
         permissions.py
              ↓
         core/events.py → CLI / Trace / Web
```

---

## 建议暂时跳过的部分

- **`web/`**（Python 后端）+ 前端 SPA：复用同一套 Runtime / Agent，是 UI 层，理解核心后再看
- [`tools/files.py`](../src/scout/tools/files.py)、[`tools/memory_tools.py`](../src/scout/tools/memory_tools.py) — 相对独立的辅助工具
- [`cancellation.py`](../src/scout/cancellation.py)、[`approval_cli.py`](../src/scout/approval_cli.py) — 运行控制细节，需要时再查

---

## 最小核心集（时间紧就看这 5 个）

1. [`runtime.py`](../src/scout/runtime.py) — 全局装配
2. **[`core/agent.py`](../src/scout/core/agent.py)** — Agent Loop（最核心）
3. [`tools/base.py`](../src/scout/tools/base.py) + [`tools/registry.py`](../src/scout/tools/registry.py) — 工具机制
4. [`memory/working.py`](../src/scout/memory/working.py) — 上下文压缩
5. [`permissions.py`](../src/scout/permissions.py) — 权限分级

配合 [`design.md`](design.md)（设计取舍）和 [`flows.md`](flows.md)（执行流程）一起看，基本能覆盖 80% 的核心设计。
