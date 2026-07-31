# Scout · 调研型 AI Agent

从零手写的 AI Agent 框架，不依赖 LangChain / LlamaIndex 之类的封装——Agent Loop、工具调用、上下文压缩、记忆检索、子 Agent 编排全部是可读的 Python 代码。

给它一个问题，它会自己拆解子问题、联网搜索、抓取正文、建立证据库，最后产出一份**每条结论都能追溯到来源**的调研报告。

```
› 调研一下 2026 年主流的开源向量数据库，对比它们的适用场景

🧠 召回 2 条长期记忆
⚙ update_plan(steps=['梳理候选项目', '对比架构与性能', '总结选型建议'], current=1)
  ✓ 计划 1/3 步 (0ms)
⚙ web_search(query=开源向量数据库 对比 2026)
  ✓ 搜索「开源向量数据库 对比 2026」→ 6 条 (842ms)
🚀 派出子调研员 worker-0：Milvus 的架构特点与适用规模
🚀 派出子调研员 worker-1：Qdrant / Weaviate 的差异
  ⚙ (worker-0) fetch_url(url=https://milvus.io/docs/overview.md)
  ⚙ (worker-1) fetch_url(url=https://qdrant.tech/documentation/)
🏁 worker-0 完成（5 步，18420 tokens）
📦 上下文压缩：17832 → 4106 tokens（合并 14 条消息）
⚙ write_report(title=开源向量数据库选型对比)
  ✓ 生成报告 20260731-1042-开源向量数据库选型对比.md (12ms)
```

## 为什么写这个

市面上的 Agent 框架把关键决策都藏在抽象层后面：上下文超了怎么办、工具报错要不要中断、多个工具调用能不能并行、子 Agent 的上下文怎么隔离。这些恰恰是决定 Agent 能不能在真实任务上跑通的地方。这个项目把它们全部显式实现出来，每个决策都有注释说明取舍。

## 能力一览

| 能力 | 实现位置 | 说明 |
| --- | --- | --- |
| Agent Loop | `core/agent.py` | 组装上下文 → 请求模型 → 执行工具 → 回灌结果，含步数上限与强制收尾 |
| 工具系统 | `tools/base.py` | 函数签名自动生成 JSON Schema，写工具 = 写带类型注解的普通函数 |
| 并行工具调用 | `tools/registry.py` | 全只读则并行，含副作用则退化串行 |
| 权限分级 | `permissions.py` | SAFE / CAUTION / DANGEROUS 三级，支持 ask / auto / readonly |
| 工作记忆 + 压缩 | `memory/working.py` | 超阈值自动摘要历史，且保证不切断工具调用链 |
| 情景记忆 | `memory/store.py` | SQLite 持久化会话，支持 `/resume` 恢复 |
| 语义记忆 | `memory/semantic.py` | 跨会话长期记忆，按相关性自动召回注入 |
| 证据库 | `memory/evidence.py` | 抓取正文切块入库，支撑 `[S1]` 形式的可追溯引用 |
| 子 Agent | `core/agent.py` | 独立上下文、共享证据库，可并行调研多个子课题 |
| 事件流 | `core/events.py` | Agent 不 print，只发事件；CLI / Trace 各自订阅 |
| 可观测 | `observability/trace.py` | 全过程 JSONL 落盘，可 `jq` 分析 |

## 快速开始

```bash
git clone <repo> && cd scout-agent

uv venv --python 3.12
uv pip install -e ".[dev]"

cp .env.example .env
# 填入 LLM_API_KEY（智谱开放平台可免费领：https://bigmodel.cn）

uv run scout                              # 交互模式
uv run scout "GraphRAG 和传统 RAG 的区别"   # 单次执行
uv run scout --auto --readonly "查点资料"   # 无人值守 + 禁止落盘
```

### 搜索后端

三个可插拔后端，改 `.env` 里的 `SEARCH_PROVIDER` 即可切换：

| 后端 | 是否需要 Key | 特点 |
| --- | --- | --- |
| `tavily` | 需要（有免费额度） | 专为 LLM 设计，返回的正文摘要质量最好 |
| `serper` | 需要（有免费额度） | Google 搜索结果，中文长尾覆盖更全 |
| `duckduckgo` | 不需要 | 解析网页版搜索页，零配置开箱即用，但会被限流 |

选了 `tavily`/`serper` 却没填 Key 时会退回 DuckDuckGo，并在启动和搜索结果里明确提示——不会静默降级。

### 交互命令

```
/new              开新会话        /sessions    历史会话列表
/resume <id>      恢复会话        /plan        当前调研计划
/sources          已收录来源      /memory      查看长期记忆
/tools            工具与风险等级  /trace [n]   最近的执行事件
/cost             token 消耗      /quit        退出
```

## 项目结构

```
src/scout/
├── config.py            环境变量 → Settings
├── runtime.py           依赖装配（唯一知道"谁依赖谁"的地方）
├── permissions.py       权限策略与人工审批
├── cli.py               终端界面：事件流 → 彩色输出
├── llm/
│   ├── base.py          Message / ToolCall / Usage 统一模型
│   └── openai_compat.py OpenAI 兼容端点，含流式 tool_call 分片重组与重试
├── tools/
│   ├── base.py          @tool 装饰器：类型注解 → JSON Schema
│   ├── registry.py      注册、鉴权、并发执行、结果截断
│   ├── search.py        DuckDuckGo / Tavily / Serper 三后端
│   ├── web.py           抓取正文并自动入证据库
│   ├── evidence_tools.py  证据检索、来源清单
│   ├── memory_tools.py  remember / recall
│   ├── plan.py          调研计划
│   ├── report.py        产出带引用的 Markdown 报告
│   ├── files.py         本地文件读写
│   └── delegate.py      派生子调研员
├── memory/
│   ├── store.py         SQLite：会话、消息、记忆、来源、证据
│   ├── working.py       工作记忆与上下文压缩
│   ├── semantic.py      长期语义记忆
│   ├── evidence.py      证据切块、检索、参考文献生成
│   └── retrieval.py     余弦相似度 / 中文 bigram 关键词打分
├── core/
│   ├── agent.py         Agent Loop 与子 Agent
│   ├── prompts.py       静态准则 + 运行时状态的提示词组装
│   ├── session.py       会话状态与持久化
│   └── events.py        事件总线
└── observability/
    └── trace.py         JSONL Trace
```

## 换模型 / 换搜索后端

任何 OpenAI 兼容端点都能直接用，改 `.env` 即可：

```bash
# DeepSeek
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# 本地 vLLM
LLM_BASE_URL=http://localhost:8000/v1
LLM_MODEL=qwen3-8b
LLM_API_KEY=EMPTY
```

子 Agent 调用量最大，可以单独指定便宜的小模型：`LLM_FAST_MODEL=glm-4.7-flash`。

## 开发

```bash
uv run pytest -q          # 全部单测，不联网、不消耗 token
uv run ruff check src tests
```

测试用 `FakeLLM` 按剧本驱动整个 Loop，覆盖终止条件、工具回灌、重复调用保护、子 Agent 隔离、上下文压缩不切断工具链等关键行为。

设计取舍的详细说明见 [docs/design.md](docs/design.md)。

## License

MIT
