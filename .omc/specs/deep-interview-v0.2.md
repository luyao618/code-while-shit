# Deep Interview Spec: vibe-coding-while-shit v0.2

## Metadata
- Interview ID: vcws-v02-2026-04-17
- Rounds: 6
- Final Ambiguity Score: 11.5%
- Type: brownfield
- Generated: 2026-04-17
- Threshold: 20% (PASSED)
- Status: PASSED
- Base version: 0.1.0 (existing feishu + codex app-server bridge)

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.95 | 0.35 | 0.333 |
| Constraint Clarity | 0.88 | 0.25 | 0.220 |
| Success Criteria | 0.85 | 0.25 | 0.213 |
| Context Clarity (brownfield) | 0.80 | 0.15 | 0.120 |
| **Total Clarity** | | | **0.885** |
| **Ambiguity** | | | **0.115** |

## Goal

将 `vibe-coding-while-shit` 从 0.1 升级到 0.2，完成三项能力扩展：

1. **多 agent 支持**：除 codex 外，新增 claude-code 和 opencode 作为可选的后端 agent；通过分层接入（JSON-RPC / Agent SDK / HTTP server），让同一份飞书桥接服务能驱动任一 agent。
2. **CLI 启动参数化**：`serve` 子命令新增 `--agent` 和 `--workspace` 参数，允许启动时显式选择 agent 类型和工作目录，不再只靠环境变量。
3. **前台 terminal 运行形态 + 可控停止**：`serve` 在前台运行，把 agent 的增量消息与飞书事件按时间顺序打印到 stdout（C 模式）；提供三种停止语义 —— 打断当前 turn、硬杀 agent 进程、通过飞书命令远程停止。

## Constraints

### 单例约束（架构级）
- 同一 Feishu App 同一时刻**只允许一个 `serve` 进程存在**，通过 `.omx/runtime/serve.lock` 的 PID lockfile 保证
- 第二个 `serve` 启动必须立即报错退出（显示现有进程 PID 和 agent/workspace）
- 切换 agent 或 workspace = 停掉旧服务 + 用新参数启动新服务

### 接入分层
- **codex** → 复用现有 JSON-RPC app-server 协议（`src/vcws/codex_app_server.py`），完整能力：审批、增量进度、turn/cancel
- **claude-code** → 使用官方 `claude-agent-sdk`（Python），完整能力
- **opencode** → 优先使用 `opencode serve` HTTP 模式；能力降级（可能不支持结构化审批，退化为文本对话）
- **逃生条款**：如果实现阶段证实 opencode 没有任何可用编程接入（HTTP/SDK 都不可行），允许降级为 stretch goal，在 0.2 release notes 明示

### 能力一致化契约（所有 agent 都必须提供的最小表面）
- 启动/关闭 agent 子进程或 session
- 提交一轮 turn 并拿到最终文本
- 打断当前 turn（`cancel_turn`）
- 硬杀 agent 进程（`kill_process`）—— 所有 agent 共享
- 发增量文本到 `publish_status` 回调（可降级为"没有增量，只有最终答复"）

### 兼容性约束
- 0.1 的飞书交互行为（`/workspace`、`/status`、审批卡片、输入补充卡片）保持不变
- 0.1 的状态文件格式兼容或提供平滑迁移；`bridge-state.json` 路径保持 `.omx/runtime/`

## Non-Goals

- **不做并行多实例**：一个飞书 bot 永远对应一个 agent，不提供"同时跑多个 agent"能力
- **不做 TUI dashboard**（B 方案）或 agent 原生 TUI 接管（A 方案）—— 明确选择 C 模式（stdout 打印）
- **不做热切换 agent**：运行中换 agent 必须重启服务
- **不保留旧 agent 的 thread 跨 agent 迁移**：换 agent 后上下文从零开始
- **不改 Feishu 网关传输层**：仍然只支持 WebSocket 模式
- **不做 Ctrl+C 两段式停止**：Ctrl+C 直接退出整个 python 服务

## Acceptance Criteria

### 启动与单例
- [ ] `python -m vcws serve --agent codex --workspace .` 能正常启动，terminal 打印 `codex ready, waiting feishu messages...`（或等价提示）
- [ ] `python -m vcws serve --agent claude-code --workspace ~/other` 能正常启动，其他 agent 的等价流程通过
- [ ] `--agent` 支持 `codex | claude-code | opencode`；`--workspace` 接受绝对路径或 `.`，不存在时自动创建
- [ ] 第二个 `serve` 启动时，检测到 lockfile 存在且 PID 活跃，立即报错退出并显示现有进程信息
- [ ] 进程正常退出时清理 lockfile；进程被 kill 时下次启动能检测到 stale lockfile 并接管

### Terminal 前台运行（C 模式）
- [ ] `serve` 在前台运行，不 detach
- [ ] agent 产生的每条增量消息 / 飞书进出消息 / 状态变化，按时间顺序打印到 stdout
- [ ] Ctrl+C 直接退出整个 python 服务，lockfile 被清理，agent 子进程被终止

### 飞书侧消息流
- [ ] 对任一 agent，用户在飞书发消息 → terminal 实时打印 agent 的响应增量（claude-code / codex 必须支持；opencode 尽力而为）
- [ ] 飞书收到最终答复的行为与 0.1 一致

### 停止语义
- [ ] 飞书发 `/cancel` 或 `/stop`（二者等价）→ 当前 turn 被打断 → 飞书收到"已停止"新消息 → 进度消息不变 → 下一条飞书消息继续复用同一 thread（上下文保留）
- [ ] 飞书发 `/kill` 或 `/clear`（二者等价）→ agent 子进程被 SIGTERM 杀掉（超时降级 SIGKILL）→ python 服务自动拉起新 agent 实例 → 下一条飞书消息是全新 thread（上下文丢弃）
- [ ] 上述两种停止都会在飞书发新的"已停止"消息（不动"处理中"进度消息）
- [ ] `/cancel` 在没有活跃 turn 时返回友好提示，不崩溃

### 跨 agent 验收
- [ ] 以 `--agent codex` 跑完整流程：消息往返 + `/cancel` + `/kill` + Ctrl+C
- [ ] 以 `--agent claude-code` 跑完整流程：同上
- [ ] 以 `--agent opencode` 跑基础流程（消息往返 + `/kill` + Ctrl+C）；`/cancel` 如因 opencode 接入能力不足而降级，须在 release notes 注明
- [ ] **逃生条款触发时**：如果 opencode 接入完全不可行，至少保留 CLI 报错提示 "opencode not yet supported in 0.2"，并在 release notes 明示降级

### 回归
- [ ] 0.1 的 `doctor`、`/workspace`、`/status`、审批卡片、输入补充卡片 全部仍然可用
- [ ] 0.1 的 `bridge-state.json` 不因 0.2 新字段而不可读

## Assumptions Exposed & Resolved

| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| "Agent 节目 = 原生 TUI" | 当前 codex 是 headless JSON-RPC，根本没 TUI | 选 C 模式（stdout 打印），放弃原生 TUI 接管 |
| "多 agent 都能用统一协议接入" | claude-code / opencode 没有等价的 JSON-RPC app-server | 采用分层接入（codex=JSON-RPC, claude-code=SDK, opencode=HTTP），能力差异显式化 |
| "可以并行跑多个 serve" | 单个 Feishu bot 的消息流无法被多进程同时消费，会双重回复 | 单例锁，第二个 serve 立即退出 |
| "停止 agent 是一件事" | 实际有至少 4 种不同语义（打断 turn / 硬杀 / 键盘 / 远程） | 拆成两语义：cancel（保留 thread）/ kill（丢弃 thread），分别映射多种入口 |
| "opencode 和 claude-code 必须同权重支持" | 两者生态成熟度差异大，捆绑会拖慢发布 | opencode 仍是硬需求，但加"接入不可行则降级"的明确逃生条款 |
| "Ctrl+C 要优雅两段式" | 增加实现复杂度，用户实际偏好简单直接 | Ctrl+C 直接整体退出，不做两段式 |

## Technical Context

### 现有代码锚点
- CLI 入口：`src/vcws/__main__.py` —— 需新增 argparse 的 `--agent` / `--workspace`
- Agent 抽象：`src/vcws/codex_app_server.py:21` 已有 `CodexBackend` Protocol —— **这是多 agent 的天然落脚点**，建议重命名为 `AgentBackend` 并新增 `cancel_turn()` / `kill()` 方法
- 配置：`src/vcws/config.py:18-26` 的 `CodexConfig` 需泛化为"按 agent 类型分派的配置"
- 飞书消息路由：`src/vcws/service.py:62-91` 的 `handle_message` —— 需新增 `/cancel`、`/stop`、`/kill`、`/clear` 的命令分支
- 运行时目录：`.omx/runtime/` —— 需新增 `serve.lock`

### 新建模块建议
- `src/vcws/agents/base.py` —— `AgentBackend` Protocol + 通用 `cancel_turn()` / `kill()` 声明
- `src/vcws/agents/codex.py` —— 从现 `codex_app_server.py` 抽出
- `src/vcws/agents/claude_code.py` —— 新增（claude-agent-sdk 封装）
- `src/vcws/agents/opencode.py` —— 新增（HTTP 封装，含能力降级逻辑）
- `src/vcws/lockfile.py` —— 单例 PID lock
- `src/vcws/terminal_sink.py` —— stdout 打印（前台进度流）

### 外部依赖调研（实现阶段验证）
- `claude-agent-sdk` Python 包的流式事件、取消 API、工具拦截
- `opencode serve` HTTP API 的 endpoint、事件流、审批支持

## Ontology (Key Entities)

| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| BridgeService | core domain | config, adapter, backend, state, policy | 使用 Agent、产出 Turn |
| Agent | core domain | agent_type, integration_mode (jsonrpc/sdk/http/pty), capability_tier (full/reduced), command/config | 被 BridgeService 使用 |
| AgentProcess | supporting | pid, status, agent_type | 1 个 Agent 运行时对应 0..1 个 AgentProcess |
| Turn | core domain | turn_id, thread_id, status, prompt, summary | 属于一个 Agent |
| StopCommand | core domain | scope (turn_only / process_hard), preserve_context, source (feishu/signal) | 作用于 Turn 或 AgentProcess |
| ServeInstance | supporting | pid, agent_type, workspace, lockfile_path | 单例，全局唯一 |
| Terminal UI | supporting | stdout_sink | 显示 Agent 事件和 Feishu Message |
| Feishu Message | external | conversation, actor, text, kind(inbound/outbound) | 驱动 Turn 或 StopCommand |
| Workspace | supporting | path | 绑定到 ServeInstance |

## Ontology Convergence

| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 6 | 6 | - | - | N/A |
| 2 | 8 | 2 (StopCommand, AgentProcess) | 0 | 6 | 75% |
| 3 | 8 | 0 | 1 (StopCommand fields) | 7 | 100% |
| 4 | 8 | 0 | 1 (Agent fields 扩充) | 7 | 100% |
| 5 | 9 | 1 (ServeInstance) | 0 | 8 | 100% |
| 6 | 9 | 0 | 0 | 9 | 100% |

连续 3 轮 100% stability，本体已完全收敛。

## Interview Transcript

<details>
<summary>Full Q&A (6 rounds)</summary>

### Round 1
**Q:** 前台 terminal 运行形态选 A（原生 TUI）/ B（自建 TUI dashboard）/ C（stdout 打印）/ D 其他？
**A:** C，但需要讨论怎么及时停止 agent。
**Ambiguity:** 60.1%

### Round 2
**Q:** "停止" 指哪种：A 打断 turn / B 硬杀进程 / C terminal 快捷键 / D 飞书命令 / E 切换 agent 时自动停旧？
**A:** A & B & D 三种都要。
**Ambiguity:** 50.7%

### Round 3
**Q:** 三种停止的 thread 保留策略？飞书反馈形式？命令命名？Ctrl+C 行为？
**A:** `/cancel` 只停当前 task 并保留上下文；`/kill` / `/stop` 完全停止不保留上下文。飞书侧新发一条"已停止"。命令对应 c（两个都要，`/stop` = `/cancel`）。Ctrl+C 直接退出整个服务。
**Ambiguity:** 43.2%

### Round 4 (Contrarian Mode)
**Q:** claude-code / opencode 如果没有 headless 接入方式，选 A PTY / B 砍 opencode / C 分层 / D 最小公约数 / E 其他？opencode 是否必须？
**A:** C（分层），opencode 可以砍。
**Ambiguity:** 31.2%

### Round 5 (Simplifier Mode)
**Q:** 6 条验收清单草稿审阅 + opencode 地位 + 并行需要吗？
**A:** 不能并行（一个 Feishu bot 必须单实例），避免消息被双重消费。
**Ambiguity:** 19.7%

### Round 6
**Q:** 验收清单去掉并行后改成 6 条纯流程 + opencode 定硬需求还是 stretch？
**A:** 支持 `/clear` 作为 `/kill` 别名。opencode 是硬需求，除非实现阶段发现不可行。
**Ambiguity:** 11.5% ✅

</details>
