# Scout 执行流程

本文档总结两个核心流程：

1. **`uv run scout` 启动时做了什么**
2. **用户输入 query 后，Agent 如何执行**

对应源码入口：`pyproject.toml` → `scout.cli:main` → `cli.py` → `runtime.py` → `core/agent.py`。

---

## 1. `uv run scout` 启动流程

### 1.1 命令入口

```bash
uv run scout
# 等价于
# pyproject.toml 中 [project.scripts] scout = "scout.cli:main"
# → 调用 scout/cli.py 的 main()
```

### 1.2 总览流程图

```mermaid
flowchart TD
    A["uv run scout"] --> B["pyproject.toml<br/>scout = scout.cli:main"]
    B --> C["cli.main()"]

    C --> D["解析命令行参数<br/>argparse"]
    D --> E["load_settings()<br/>读取 .env → Settings"]
    E --> F{"settings.validate()<br/>LLM_API_KEY 是否存在?"}
    F -->|否| G["打印错误，退出 code=1"]
    F -->|是| H["resolve_provider()<br/>检查搜索后端配置"]
    H --> I{"Tavily/Serper<br/>缺 Key?"}
    I -->|是| J["打印黄色降级提示"]
    I -->|否| K["继续"]
    J --> K

    K --> L["创建 EventBus"]
    L --> M["订阅 Renderer<br/>终端彩色输出"]
    L --> N["创建 Runtime"]
    N --> O["Runtime 内部初始化"]
    O --> P["Store → SQLite<br/>.scout/scout.db"]
    O --> Q["OpenAICompatClient<br/>LLM 客户端"]
    O --> R["SemanticMemory<br/>长期记忆"]
    O --> S["PolicyApprover<br/>权限审批"]
    O --> T["TraceRecorder<br/>订阅 EventBus<br/>写入 traces.jsonl"]

    N --> U{"--resume ID?"}
    U -->|是| V["resume_session()<br/>从 SQLite 恢复历史消息"]
    U -->|否| W["new_session()<br/>创建新会话"]
    V --> X["build_agent(session)"]
    W --> X

    X --> Y["组装 ToolContext<br/>workspace / memory / evidence / session"]
    Y --> Z["build_registry()<br/>注册 13 个工具"]
    Z --> AA["创建 Agent<br/>回填 ctx.spawn 子 Agent 派生函数"]

    AA --> AB{"命令行带了 question?"}
    AB -->|是| AC["_ask() 单次执行<br/>agent.run(question)"]
    AB -->|否| AD["_repl() 交互模式<br/>等待用户输入"]
    AC --> AE["runtime.close()<br/>关闭 SQLite"]
    AD --> AE

    AD --> AF{"用户输入"}
    AF -->|普通文本| AG["_ask() → agent.run()"]
    AF -->|/help /sessions 等| AH["_handle_command()"]
    AF -->|/quit| AE
    AG --> AF
    AH --> AF
```

### 1.3 启动阶段各模块职责

| 阶段 | 模块 | 做了什么 |
| --- | --- | --- |
| 入口 | `pyproject.toml` | 注册 `scout` 命令，指向 `scout.cli:main` |
| 配置 | `config.py` | 从 `.env` 加载 LLM、搜索、Agent 行为等参数 |
| 装配 | `runtime.py` | 创建 SQLite、LLM 客户端、长期记忆、Trace、权限器 |
| 会话 | `core/session.py` | 新建或恢复会话，挂载工作记忆和证据库 |
| 工具 | `tools/registry.py` | 注册搜索、抓取、证据、计划、报告等 13 个工具 |
| Agent | `core/agent.py` | 组装 Agent Loop，注入子 Agent 派生能力 |
| 展示 | `cli.py Renderer` | 订阅事件总线，把工具调用、压缩、子 Agent 等渲染到终端 |
| 持久化 | `observability/trace.py` | 同步订阅事件，写入 `.scout/traces.jsonl` |

### 1.4 Runtime 装配细节

```mermaid
flowchart LR
    subgraph Runtime["Runtime.__init__()"]
        S[Settings]
        B[EventBus]
        DB[(Store / SQLite)]
        LLM[OpenAICompatClient]
        MEM[SemanticMemory]
        PERM[PolicyApprover]
        TR[TraceRecorder]
    end

    S --> DB
    S --> LLM
    DB --> MEM
    LLM --> MEM
    B --> TR

    subgraph build_agent["build_agent(session)"]
        CTX[ToolContext]
        REG[ToolRegistry<br/>13 tools]
        AG[Agent]
    end

    S --> CTX
    LLM --> CTX
    MEM --> CTX
    session --> CTX
    CTX --> REG
    REG --> AG
    AG -->|make_spawner| CTX
```

---

## 2. 用户输入 Query 后的 Agent 执行流程

用户输入可以是：

- 交互模式：`› 调研一下 Qdrant 和 Milvus 的区别`
- 单次模式：`uv run scout "调研一下 Qdrant 和 Milvus 的区别"`

两者最终都调用 `_ask()` → `agent.run(question)`。

### 2.1 总览流程图

```mermaid
flowchart TD
    START["用户输入 query"] --> ASK["cli._ask()"]
    ASK --> RUN["agent.run(user_input)"]

    RUN --> E1["emit RUN_START"]
    RUN --> E2["set_title_from()<br/>用首行文本设会话标题"]
    RUN --> E2B["format_run_timestamp()<br/>固定本 run 开始时间"]
    RUN --> E3["_recall()<br/>从长期记忆检索 Top-5（仅一次）"]
    E3 --> E4["working.add(user_message)<br/>写入工作记忆"]

    E4 --> LOOP{"Agent Loop<br/>step = 1 .. max_steps"}

    LOOP --> S1["emit STEP_START"]
    S1 --> S2["_maybe_compact()<br/>token 超阈值则压缩历史"]
    S2 --> S3["_assemble()<br/>static system + 历史 + runtime reminder"]
    S3 --> S4["emit LLM_START"]
    S4 --> S5["llm.chat(messages, cached_schemas())<br/>最后一步 tools=None"]
    S5 --> S6["emit LLM_END<br/>含 cached_tokens"]
    S6 --> S7["working.add(assistant_message)"]

    S7 --> DEC{"模型返回了 tool_calls?"}

    DEC -->|否| DONE["final_text = content<br/>stop_reason = completed"]
    DEC -->|是| T1["_run_tools()"]

    T1 --> T2{"重复调用检测<br/>同参数 ≥ 3 次?"}
    T2 -->|拦截| T3["构造 blocked 错误消息"]
    T2 -->|放行| T4["PolicyApprover 权限检查"]
    T4 --> T5{"需要用户确认?"}
    T5 -->|拒绝| T6["返回拒绝原因"]
    T5 -->|允许| T7["ToolRegistry.execute_batch()"]
    T7 --> T8{"全部只读?"}
    T8 -->|是| T9["ThreadPool 并行执行"]
    T8 -->|否| T10["串行执行"]
    T9 --> T11["工具执行<br/>web_search / fetch_url / ..."]
    T10 --> T11
    T3 --> T12["构造 tool 消息<br/>写回 working memory"]
    T6 --> T12
    T11 --> T12

    T12 --> T13{"tool = research_subtopic?"}
    T13 -->|是| SUB["ctx.spawn(brief)<br/>同步派生子 Agent<br/>结论经 tool 消息回传"]
    SUB --> T12
    T13 -->|否| LOOP

    DONE --> P1["session.persist()<br/>消息写入 SQLite"]
    P1 --> P2["累加 token 统计"]
    P2 --> P3["emit RUN_END"]
    P3 --> OUT["返回 AgentResult<br/>text / steps / usage / stop_reason"]

    OUT --> CLI["Renderer 展示结果<br/>步数 · 工具次数 · tokens"]
```

### 2.2 单步 Loop 详细流程

每一步 Loop 内部按以下顺序执行：

```mermaid
sequenceDiagram
    participant U as 用户
    participant CLI as cli._ask
    participant A as Agent
    participant WM as WorkingMemory
    participant M as SemanticMemory
    participant L as LLM
    participant R as ToolRegistry
    participant T as Tools
    participant DB as SQLite
    participant BUS as EventBus

    U->>CLI: 输入 query
    CLI->>A: agent.run(query)

    A->>M: search(query) 召回长期记忆
    M-->>A: Top-5 记忆
    A->>WM: add(user_message)

    Note over A: run_started_at 在本 run 开始时固定

    loop 每一步 (最多 max_steps 次)
        A->>WM: needs_compaction()?
        alt token 超阈值
            A->>L: 摘要早期消息
            L-->>A: 压缩摘要
            A->>WM: 替换早期消息
        end

        A->>A: _assemble()<br/>static system + working.messages<br/>+ runtime reminder
        A->>L: chat(messages, cached_schemas())
        Note over L: 最后一步 tools=None<br/>+ FORCE_FINAL
        L-->>A: assistant_message (+ tool_calls?)

        alt 无 tool_calls
            A-->>CLI: 最终答复
        else 有 tool_calls
            A->>R: execute_batch(tool_calls)
            R->>R: 权限检查 / 重复检测
            par 只读工具可并行
                R->>T: web_search / fetch_url / ...
            end
            T-->>R: ToolResult
            R-->>A: tool 消息列表
            A->>WM: extend(tool_messages)
        end
    end

    A->>DB: persist(本轮新消息)
    A->>BUS: RUN_END
    A-->>CLI: AgentResult
    CLI-->>U: 流式/最终输出 + 统计信息
```
注：在执行过程中，事件都会调用EventBus进行

### 2.3 `_assemble()`：每步组装的上下文

`WorkingMemory` 只存 user / assistant / tool 对话。**system 和 runtime reminder 不在 working memory 里**，每步调用 LLM 前由 `_assemble()` 临时拼进请求。

对应源码：`core/agent.py::_assemble()` + `core/prompts.py`。

#### 完整请求结构

```mermaid
flowchart TB
    subgraph API["一次 llm.chat() 调用"]
        subgraph Messages["messages 数组（稳定 → 易变）"]
            SYS["[1] role: system<br/>build_static_system_prompt()<br/>整轮 run 字节级不变"]
            HIST["[2] working.messages<br/>append-only 对话历史"]
            REM["[3] role: user<br/>build_runtime_reminder()<br/>每步重新生成"]
            FIN["[4] role: user FORCE_FINAL<br/>仅最后一步"]
        end
        TOOLS["tools: registry.cached_schemas()<br/>memoize + 按名称排序<br/>最后一步为 None"]
    end

    SYS --> HIST --> REM
    REM -.->|max_steps| FIN
    TOOLS -.->|独立字段| API
```

#### static system（`build_static_system_prompt`）

| 块 | 来源 | 是否变化 |
| --- | --- | --- |
| 行为准则 | `prompts.LEAD_SYSTEM` | 否 |
| 工作区 / 搜索后端 / 步数上限 | `_static_runtime_block()` | 否 |
| 可用工具名称 | `registry.tools` 名称列表 | 否（同一 Agent 整 run 不变） |

子调研员（`role=worker`）用 `build_worker_static_prompt()`，只含 `WORKER_SYSTEM`。

#### runtime reminder（`build_runtime_reminder`）

追加在 messages **末尾** 的 `role=user` 消息，以 `【当前运行时状态】` 开头：

| 块 | 来源 | 是否变化 |
| --- | --- | --- |
| 任务开始时间 / 子 Agent 配额 | `_volatile_runtime_block()` | 配额会变，时间整 run 固定 |
| 长期记忆 Top-5 | `run()` 入口 `_recall()`，整轮复用 | 否 |
| 调研计划 | `session.plan.render()` | 是（update_plan 后） |
| 已收录来源 | `session.evidence.sources()` | 是（fetch_url 后） |

reminder **不写入 working memory**，不 persist，不参与压缩。

#### tools cache block

工具 schema 通过 API 的 `tools` 字段单独发送（不在 messages 里）。`ToolRegistry.cached_schemas()` 对 schema memoize 并按工具名排序，避免顺序抖动导致 prefix cache miss。

#### prefix cache 验证

智谱 GLM 等平台支持隐式上下文缓存。trace 的 `llm_end` 事件含 `cached_tokens` 字段：

```bash
jq -r 'select(.type=="llm_end") | "\(.step) cached=\(.cached_tokens)"' .scout/traces.jsonl
```

详见 `design.md` §7。

### 2.4 工具执行分支

模型一次可能请求多个工具，执行路径如下：

```mermaid
flowchart TD
    TC["assistant.tool_calls"] --> REP{"同参数重复 ≥ 3 次?"}
    REP -->|是| BLK["blocked: 提示换思路"]
    REP -->|否| PERM{"PolicyApprover.check()"}

    PERM -->|readonly 模式 + 有副作用| DENY["拒绝: 只读模式"]
    PERM -->|ask 模式 + 需确认| ASK["CLI 弹窗询问用户<br/>y / n / a"]
    PERM -->|auto 或 SAFE| EXEC["允许执行"]

    ASK -->|拒绝| DENY
    ASK -->|同意| EXEC

    EXEC --> PAR{"全部 concurrency_safe?"}
    PAR -->|是| POOL["ThreadPoolExecutor 并行"]
    PAR -->|否| SEQ["串行执行"]

    POOL --> TOOL["Tool.run(ctx, **args)"]
    SEQ --> TOOL

    TOOL --> RES{"执行结果"}
    RES -->|成功| OK["ToolResult(ok=True, content=...)"]
    RES -->|失败| FAIL["ToolResult(ok=False, content=错误信息)"]
    RES -->|异常| EX["捕获异常 → 错误文本<br/>不中断 Loop"]

    BLK --> MSG["Message(role=tool)"]
    DENY --> MSG
    OK --> MSG
    FAIL --> MSG
    EX --> MSG

    MSG --> WM["写回 working memory<br/>进入下一步 Loop"]
```

### 2.5 子 Agent 派生与通信

#### 什么时候会派生

**无自动触发**——仅当主 Agent LLM 在某步调用 `research_subtopic` 工具时派生。典型场景：子课题搜索/阅读量大，但主 Agent 只需一段结论（见 `prompts.LEAD_SYSTEM`）。

硬约束：

- `subagents_used < max_subagents`（默认 3），否则工具返回失败
- 子 Agent 无 `research_subtopic`，不可递归
- 同一轮多个 `research_subtopic` 可并行（`concurrency_safe=True`）

#### 主从如何通信

```mermaid
sequenceDiagram
    participant M as 主 Agent
    participant Tool as research_subtopic
    participant W as worker-N
    participant EV as 共享 EvidenceStore

    M->>Tool: tool_call(topic, questions?)
    Tool->>W: spawn(brief) → worker.run(brief)
    Note over W: 独立 WorkingMemory<br/>不见主 Agent 历史
    W->>EV: fetch_url 写入证据
    W-->>Tool: result.text（≤800 字）
    Tool-->>M: ToolResult → role=tool 消息
    M->>EV: 后续可 search_evidence / 引用 [S1]
```

| 方向 | 载体 | 内容 |
| --- | --- | --- |
| 主 → 子 | `brief` 字符串 | `topic` + 可选 `questions`；**须自包含背景** |
| 子 → 主 | `role=tool` 消息 | 子 Agent 最终 `result.text`，无中间步骤 |
| 双向共享 | `Session.evidence` | 子 Agent 抓取的正文，主 Agent 可引用 `[S1]` |
| 隔离 | `WorkingMemory` | 各自独立；子 Agent `persist=False` 不入 SQLite |

代码路径：`tools/delegate.py` → `ctx.spawn`（`runtime.py` 注入）→ `agent.make_spawner()`。

### 2.6 典型调研任务的执行路径

以一个调研问题为例，Agent 通常会按以下顺序调用工具：

```mermaid
flowchart LR
    Q["用户 query"] --> PLAN["update_plan<br/>拆解 3~6 步计划"]
    PLAN --> SEARCH["web_search × N<br/>Tavily 搜索线索"]
    SEARCH --> FETCH["fetch_url × N<br/>抓取正文 → 证据库 S1/S2/..."]
    FETCH --> SUB{"子课题工作量大?"}
    SUB -->|是| WORKER["research_subtopic<br/>子 Agent 并行调研"]
    SUB -->|否| EVID
    WORKER --> EVID["search_evidence<br/>从证据库检索细节"]
    EVID --> REPORT["write_report<br/>产出 Markdown 报告"]
    REPORT --> ANSWER["直接回答用户<br/>或指向 reports/ 文件"]
```

### 2.7 终止条件

Agent Loop 有三种结束方式：

| stop_reason | 触发条件 | 行为 |
| --- | --- | --- |
| `completed` | 模型返回纯文本，不再请求工具 | 正常结束，输出最终答复 |
| `max_steps` | 达到 `AGENT_MAX_STEPS` 上限 | 最后一步不提供 tools，追加 FORCE_FINAL 指令，强制基于现有信息收尾 |
| `error` | Loop 内抛出未捕获异常 | 返回错误信息，已产生的消息仍会 persist |

### 2.8 数据持久化

一轮 query 执行完后，以下数据会被写入：

```mermaid
flowchart LR
    RUN["agent.run() 结束"] --> M1["SQLite messages 表<br/>user / assistant / tool 消息"]
    RUN --> M2["SQLite sources + evidence 表<br/>fetch_url 抓取的来源和正文块"]
    RUN --> M3["SQLite memories 表<br/>remember 工具写入的长期记忆"]
    RUN --> M4[".scout/traces.jsonl<br/>每步 LLM/工具事件"]
    RUN --> M5["reports/*.md<br/>write_report 产出的报告"]
    RUN --> M6["session.usage<br/>累计 token 统计"]
```

---

## 3. 两个流程的关系

```mermaid
flowchart TB
    subgraph 启动["流程 1: uv run scout（一次性）"]
        INIT["配置加载 → Runtime 装配 → 创建 Agent"]
    end

    subgraph 运行["流程 2: 每次用户输入 query（可重复）"]
        QUERY["agent.run(query)"]
        LOOP["Agent Loop"]
        TOOLS["工具调用"]
        OUT["输出结果"]
    end

    INIT --> QUERY
    QUERY --> LOOP --> TOOLS --> OUT
    OUT -->|交互模式继续等待| QUERY
    OUT -->|/quit 或单次模式| CLOSE["runtime.close()"]
```

**一句话总结**：

- **流程 1** 是"把机器开起来"——读配置、连数据库、注册工具、创建 Agent，只做一次。
- **流程 2** 是"开始干活"——每输入一个问题，Agent 就在 Loop 里反复「想 → 调工具 → 看结果 → 再想」，直到给出最终答案。
