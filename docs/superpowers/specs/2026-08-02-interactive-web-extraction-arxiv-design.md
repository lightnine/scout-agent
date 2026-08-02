# Scout 人在环、Web、正文提取与 arXiv 支持设计

日期：2026-08-02  
状态：待评审

## 1. 背景

Scout 当前是一个同步运行的 Python CLI 调研 Agent。它已经具备 Agent Loop、工具风险审批、EventBus、SQLite 会话与证据库、网页抓取和引用能力，但存在四个缺口：

1. 调研计划生成后不能暂停等待用户确认；
2. 只有 Rich CLI，没有 Web 交互界面；
3. 网页正文依赖正则剥标签，导航和广告噪音较多，也不支持动态渲染页；
4. arXiv URL 没有专门处理，PDF 链接和摘要元数据不能稳定进入证据库。

本设计一次定义四项能力的共同边界，实施时按可独立验证的阶段逐步交付。

## 2. 已确认的产品决策

- 人在环包括：首个调研计划确认，以及 CAUTION/DANGEROUS 工具审批。
- Web 面向本机单用户，不做登录、公网部署或多租户隔离。
- Web 使用 FastAPI + React/Vite，采用“调研工作台”布局。
- 正文提取使用 trafilatura；静态提取为空或过短时，使用 Playwright 渲染后重试。
- arXiv 本版只识别 arxiv.org URL、标准化到摘要页并提取元数据/摘要；不增加论文搜索，不下载或解析 PDF。
- CLI 继续保留，并与 Web 复用同一个 Agent 核心和审批抽象。

## 3. 目标与非目标

### 3.1 目标

- Agent 首次制定非空计划后暂停，用户确认后才执行后续调研工具。
- 用户可以要求修改计划；模型根据反馈重拟后再次请求确认。
- 高风险工具支持“允许一次、拒绝、本会话总是允许”。
- Web 能创建/恢复会话、发起调研、流式展示回答和事件、查看计划与来源、处理审批、取消运行。
- 静态网页提取质量明显优于当前正则实现，动态页在安装浏览器运行时后可自动回退。
- arXiv 摘要页能以稳定、结构化、可引用的文本进入 EvidenceStore。
- 现有 CLI、证据标签、会话持久化和 Trace 行为保持兼容。

### 3.2 非目标

- 公网部署、用户账号、权限系统、多租户和横向扩容。
- 同一进程同时运行多个主调研任务。
- arXiv 关键词搜索、推荐、PDF 下载、PDF 全文解析或公式提取。
- 通用浏览器自动化、登录态抓取、验证码绕过。
- 将 EventBus 改造成分布式消息队列。

## 4. 总体架构

系统分为四层：

1. **交互层**
   - 现有 Rich CLI；
   - React/Vite 调研工作台。
2. **Web 适配层**
   - FastAPI REST API；
   - SSE 事件流；
   - `RunManager` 管理后台 Agent 线程、取消和单运行约束；
   - `WebApprovalGateway` 保存待处理审批并等待 HTTP 决定。
3. **核心层**
   - Agent、Session、ToolRegistry、EventBus；
   - 新增 UI 无关的审批协议和运行取消协议。
4. **内容与存储层**
   - `PageFetcher`、trafilatura、Playwright 回退、arXiv 适配器；
   - 现有 SQLite Store、EvidenceStore、SemanticMemory 和 JSONL Trace。

依赖方向始终从交互层指向核心层。核心层不能导入 FastAPI、React 或 Rich。

## 5. 人在环设计

### 5.1 统一审批模型

新增 UI 无关的数据结构：

- `ApprovalKind`：`plan` 或 `tool`；
- `ApprovalRequest`：`id`、`run_id`、`session_id`、`kind`、展示标题、结构化 payload、创建时间；
- `ApprovalAction`：`approve`、`revise`、`reject`、`allow_session`、`cancel`；
- `ApprovalDecision`：action 和可选 feedback；
- `ApprovalGateway.request(request) -> ApprovalDecision`：同步阻塞协议。

CLI gateway 直接调用终端输入并返回决定。Web gateway 发出 `approval_required` 事件，然后阻塞 Agent 后台线程，直到 HTTP 接口提交决定或运行被取消。

`PolicyApprover` 继续负责风险策略：

- SAFE 直接放行；
- readonly 拒绝非 SAFE；
- auto 直接放行；
- ask 通过 `ApprovalGateway` 请求决定；
- `allow_session` 按 `(session_id, tool_name)` 记录，不能泄漏到其他会话；恢复会话时该临时授权不恢复。

### 5.2 计划确认语义

- 每轮 `Agent.run()` 只强制确认首个成功写入的非空计划。
- 后续仅更新 `current` 或调整执行进度时不重复打断用户。
- 用户选择 `revise` 时，feedback 以工具结果返回模型，计划保持未确认；模型重拟计划后再次确认。
- 用户选择 `cancel` 时结束本轮运行，`stop_reason` 为 `cancelled`。
- 简单任务如果模型没有调用 `update_plan`，不会人为插入空计划。

### 5.3 同一批工具的执行边界

模型可能在一次响应中同时调用 `update_plan` 和搜索工具。首次计划尚未确认时，Agent 必须：

1. 只执行 `update_plan`；
2. 对同批其他工具生成“计划尚未确认，请在确认后重新调用”的合成工具结果；
3. 请求计划审批；
4. 只有确认后的下一步才允许执行调研工具。

这避免并行工具在计划审批前提前产生网络请求或副作用，同时保持每个 tool call 都有对应 tool message。

### 5.4 取消

`RunCancellation` 提供线程安全的取消标记。Agent 在以下边界检查：

- 每个 Loop step 开始前；
- 工具批次执行前；
- 等待审批时；
- Playwright 回退前。

取消不强杀正在执行的第三方请求；当前请求结束后停止后续步骤。待审批工具不会执行。

## 6. Web 设计

### 6.1 启动与打包

- trafilatura 是默认正文提取依赖；新增 `browser` 可选依赖组安装 Playwright；
- 新增 Python 可选依赖组 `web`：FastAPI、Uvicorn；
- 新增独立 `web/` React/Vite 工程；
- 开发模式分别运行 FastAPI 和 Vite，由 Vite 代理 `/api`；
- 生产构建由 FastAPI 托管 `web/dist`，wheel 构建显式包含该静态目录；
- 新增 `scout-web` 命令启动本地服务，现有 `scout` CLI 参数保持兼容。

### 6.2 页面结构

采用三栏调研工作台：

- 左栏：会话列表和当前计划；
- 中栏：用户问题、LLM 流式回答、最终报告、输入框；
- 右栏：实时事件、运行指标和证据来源；
- 顶部：新建会话、运行状态、停止按钮；
- 模态卡片：计划确认和高风险工具审批。

前端只使用 React 自身状态管理和原生 `fetch`/`EventSource`。本版不引入全局状态框架。

### 6.3 HTTP API

- `GET /api/sessions`：列出会话；
- `POST /api/sessions`：创建会话；
- `GET /api/sessions/{session_id}`：读取消息、计划、来源和用量；
- `POST /api/sessions/{session_id}/runs`：提交问题并创建 run；
- `GET /api/runs/{run_id}/events`：SSE 事件流；
- `POST /api/approvals/{approval_id}`：提交审批决定与 feedback；
- `POST /api/runs/{run_id}/cancel`：请求取消；
- `GET /api/health`：健康检查。

同一时刻已有活动 run 时，再次创建 run 返回 HTTP 409。

### 6.4 SSE 与状态恢复

`EventBus` 新增 `approval_required` 和 `approval_resolved` 事件。`RunManager` 给当前主 Agent 及其 worker 发出的每个 Web 事件关联 `run_id` 和 `session_id`，Web 订阅者再把事件写入线程安全队列。每个 run 维护单调递增 event id 和有限内存环形缓冲区。SSE 断线重连时使用 `Last-Event-ID` 回放尚在缓冲区内的事件。

前端按事件归并状态：

- `llm_delta` 追加到当前 assistant 消息；
- `plan_updated` 更新左栏计划；
- `tool_start/tool_end` 更新右栏活动；
- `approval_required/resolved` 显示或关闭审批卡；
- `run_end/error` 结束运行状态。

SQLite 仍是会话、消息和来源的事实来源；SSE 缓冲只用于本次运行的实时恢复，不承担长期持久化。

### 6.5 线程与资源生命周期

- FastAPI 请求线程不直接执行 `Agent.run()`；
- `RunManager` 在一个后台工作线程中运行主 Agent；
- 本版只保留一个活动主 run，避免 Session 和 SQLite 连接并发写入；
- 应用关闭时先取消活动 run、唤醒待审批线程，再关闭 Playwright、Runtime 和 Store。

## 7. 正文提取升级

### 7.1 模块边界

将抓取与正文解析从 `tools/web.py` 拆出：

- `PageFetcher`：HTTP 获取、Content-Type 判断、静态提取、动态回退；
- `HtmlExtractor`：trafilatura 标题与正文提取；
- `BrowserRenderer`：Playwright 懒加载和渲染；
- `ArxivAdapter`：URL 识别、标准化和摘要页结构化解析。

`fetch_url` 继续负责 URL 参数校验、调用 fetcher、写入 EvidenceStore 和生成 ToolResult。EvidenceStore 无需感知页面来源。

### 7.2 提取流水线

1. 校验 `http://` 或 `https://`；
2. 如果是 arxiv.org URL，先标准化 URL；
3. 使用 httpx 获取内容并检查状态码；
4. HTML 先交给 trafilatura；
5. 提取为空或正文少于 80 字符时，调用 Playwright；
6. 将渲染后的 HTML 再交给同一个 trafilatura 提取器；
7. 仍然过短则返回失败，不写入证据库；
8. 成功文本沿用现有切块、去重、预览和 `[S<n>]` 标签逻辑。

非 HTML 响应保持当前文本处理，但明确拒绝把 PDF 二进制当文本入库。

### 7.3 Playwright 行为

- 使用同步 Playwright API，因为 Agent 和工具当前均为同步模型；
- 浏览器只在第一次需要回退时启动，并由 Runtime 关闭；
- 等待 `DOMContentLoaded`，设置有限超时，不等待可能永不静默的 network idle；
- Playwright 包或 Chromium 未安装时返回明确安装提示，静态页面功能仍可使用；
- 不保存登录态，不执行用户自定义脚本。

## 8. arXiv URL 支持

`ArxivAdapter` 识别以下形式：

- `https://arxiv.org/abs/2401.12345`；
- `https://arxiv.org/pdf/2401.12345` 和带 `.pdf` 后缀；
- 旧式分类 ID，例如 `cs/9901001`。

识别后统一请求 HTTPS `abs` 页面，并保留版本号（如 `v2`）。适配器从 arXiv 的 citation meta 和摘要区域提取：

- 标题；
- 作者；
- 提交/更新时间；
- 分类；
- 摘要；
- canonical URL。

这些字段渲染为结构化纯文本后进入 EvidenceStore。解析缺少字段时降级到通用 trafilatura；页面不可用时沿用普通抓取错误，不尝试 PDF。

本版不新增 `arxiv_search` 工具。用户或模型通过已有 `fetch_url` 传入 arXiv URL 即可。

## 9. 错误处理

- HTTP 超时、状态错误和反爬：返回失败 ToolResult，建议模型换来源；
- 静态提取失败且浏览器不可用：说明缺失依赖或浏览器安装命令；
- 动态渲染后仍为空：不入库并返回正文过短；
- 审批 ID 不存在或已处理：HTTP 404/409；
- SSE 断开：不取消 run，允许按 event id 重连；
- Web 页面刷新：重新读取 Session 快照并重连活动 run；
- Agent 异常：发出 `error` 和 `run_end`，释放活动 run；
- 应用关闭或用户取消：所有待审批请求以 cancel 决定唤醒。

错误信息不得包含 API key、完整请求头或未截断的网页正文。

## 10. 测试策略

### 10.1 Python 单元测试

- 审批请求、允许一次、会话总是允许、拒绝、修改和取消；
- 首个计划暂停，后续进度更新不暂停；
- `update_plan` 与其他工具同批时不提前执行其他工具；
- 取消标记在各执行边界生效；
- trafilatura 对正文、标题、导航噪音和空页面的处理；
- Playwright 回退触发条件和浏览器不可用错误；
- arXiv 新式/旧式/版本化/abs/pdf URL 标准化；
- arXiv 元数据完整与缺字段降级。

网络、LLM 和 Playwright 均使用 fake 或 mock，测试默认离线运行。

### 10.2 API 集成测试

使用 FastAPI TestClient 和 FakeLLM 覆盖：

- 创建会话并发起 run；
- SSE 事件顺序和断线回放；
- 计划确认后继续；
- 修改计划后再次确认；
- 高风险工具拒绝后模型继续；
- 取消等待审批的 run；
- 第二个并发 run 返回 409。

### 10.3 前端测试

- Vitest/Testing Library：SSE reducer、流式消息拼接、计划/来源更新、审批按钮和错误状态；
- 一条 Playwright E2E：创建会话 → 提问 → 确认计划 → 审批工具 → 查看带引用报告；
- 前端测试使用 mock API，不依赖真实 LLM 和公网。

### 10.4 回归验证

- 现有 pytest 全量通过；
- Ruff 无新增错误；
- React TypeScript 检查、单测和生产构建通过；
- 现有 CLI 的 ask/auto/readonly 模式继续工作。

## 11. 实施阶段

1. **正文基础层**：trafilatura、PageFetcher、离线夹具和现有 `fetch_url` 接入；
2. **arXiv 适配**：URL 标准化和摘要元数据提取；
3. **浏览器回退**：Playwright 懒加载、生命周期和错误处理；
4. **人在环核心**：统一审批协议、首个计划 gate、取消；
5. **Web 后端**：RunManager、REST、SSE、审批与单运行约束；
6. **React 工作台**：三栏页面、流式状态、审批和来源；
7. **端到端验证与文档**：集成/E2E、启动说明和设计文档更新。

前四阶段可先通过 CLI 和 Python 测试验证；Web 建立在已稳定的暂停/恢复语义之上。

## 12. 验收标准

### 人在环

- 复杂调研首次调用 `update_plan` 后，在任何搜索/抓取工具执行前等待用户确认；
- 修改意见会触发计划重拟，确认后恢复执行；
- CAUTION/DANGEROUS 工具支持三种审批决定，CLI 与 Web 语义一致。

### Web

- `scout-web` 能在本机启动 React 工作台；
- 页面完成会话、提问、流式事件、计划、来源、审批、取消和报告展示；
- 刷新或短暂断线后能从 Session 快照和 SSE 缓冲恢复；
- 同一时刻只允许一个活动主 run。

### 正文提取

- 静态测试页能排除导航/脚本噪音并保留主要段落；
- 静态提取过短时才触发 Playwright；
- 两次提取均失败时不会污染证据库；
- 未安装 Playwright 浏览器时错误信息可操作。

### arXiv

- abs、pdf 和旧式 URL 都规范化到正确摘要页；
- 标题、作者、时间、分类、摘要和 URL 以一个来源进入证据库；
- 不下载 PDF，不新增 arXiv 搜索。

## 13. 兼容性与迁移

- 不修改已有 SQLite schema；
- 不改变现有 Session、EvidenceStore 和引用标签格式；
- `scout` CLI 保持原入口和参数；
- 新事件类型只增加消费者能力，TraceRecorder 继续记录结构化事件；
- 当前 `docs/design.md` 和 `docs/flows.md` 的未提交修改不在本功能中覆盖，实施完成后只追加对应章节。
