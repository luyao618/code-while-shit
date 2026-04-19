# code-while-shit — v0.2

通过飞书 WebSocket 远程驱动本机 AI 编程助手（codex / claude-code / opencode）的轻量桥接服务。

## 项目概览

在同一套飞书入口下选择三种 Agent 后端之一驱动本地编程任务：

- **claude-code** — 通过 `claude-agent-sdk` 驱动，完整能力（默认）
- **codex** — JSON-RPC app-server，完整能力
- **opencode** — loopback HTTP，能力受限（审批需要 `--allow-auto-approve`）

服务接收飞书消息和交互事件、把会话绑定到工作目录与 Agent thread、在需要时把确认/补充信息回流给飞书。运行时状态默认写到 `.omx/runtime/bridge-state.json`。

## Quick Start

```bash
pip install -e .          # 或 uv pip install -e .
cws init                  # 生成 .env 与 workspace
# 编辑 .env，填入 FEISHU_APP_ID / FEISHU_APP_SECRET
cws doctor                # 自检 Feishu 凭证 + agent 依赖
cws serve                 # 默认 claude-code，自动后台运行
cws status                # 查看运行状态
cws stop                  # 优雅停止
cws restart               # stop + serve
```

`cws serve` 默认 fork 到后台，日志写到 `$CWS_RUNTIME_DIR/serve.log`；前台运行加 `--foreground`。切换 agent：`cws serve --agent codex` 或 `cws serve --agent opencode --allow-auto-approve`。`.env` 自动加载（**勿 commit**）。

## CLI 命令

| 命令 | 作用 |
| --- | --- |
| `cws init [--workspace PATH] [--agent X]` | 生成模板 `.env` 并创建 workspace 目录 |
| `cws doctor [--agent X]` | 检查 Feishu 凭证、`lark-oapi` 及 Agent 依赖 |
| `cws serve [--agent X] [--workspace PATH] [--allow-auto-approve] [--force] [--foreground]` | 启动飞书 WebSocket bridge |
| `cws status` | 显示当前 serve 进程（pid / agent / workspace） |
| `cws stop [--timeout S]` | SIGTERM → 超时后 SIGKILL，并清理 lockfile |
| `cws restart [serve 选项] [--timeout S]` | 先 stop 再 serve |

### 会话内命令

| 命令 | 作用 |
| --- | --- |
| `/workspace <path>` | 切换当前会话工作目录 |
| `/status` | 查看 transport / 会话 / Agent thread / 最近事件 |
| `/cancel`、`/stop` | 打断当前 turn，保留 thread |
| `/kill`、`/clear` | 硬杀 Agent 进程，下一条消息开新 thread |

### Agent 能力对比

| Agent | 审批卡片 | 增量打印 | /cancel | /kill |
|---|---|---|---|---|
| codex | ✅ | ✅ | ✅ | ✅ |
| claude-code | ✅ | ✅ | ✅ | ✅ |
| opencode | ⚠️ 需 `--allow-auto-approve` | ✅ | ⚠️ 首次探测 | ✅ |

一个飞书 bot 一次只能有一个 `serve`，由 `.omx/runtime/serve.lock` 强制；stale lockfile 需要 `--force` 或 `CWS_TAKEOVER_STALE=1` 接管。

## 环境变量

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | — | 飞书应用凭证（必需） |
| `FEISHU_DOMAIN` | `https://open.feishu.cn` | 飞书 API 域名（Lark 国际版可改） |
| `FEISHU_BASE_URL` | `${FEISHU_DOMAIN}/open-apis` | Open API 基址 |
| `FEISHU_ALLOWED_USERS` | 空 | 逗号分隔 `open_id` allowlist；空=不限制 |
| `CWS_DEFAULT_WORKSPACE` | `.` | 默认工作目录 |
| `CWS_RUNTIME_DIR` | `.omx/runtime` | 运行时目录 |
| `CWS_AGENT` | `claude-code` | 默认 agent |
| `CODEX_*` | — | Codex 后端配置（`CODEX_COMMAND` / `CODEX_MODEL` / `CODEX_APPROVAL_POLICY` / `CODEX_SANDBOX` 等） |

## 开发与测试

```bash
uv pip install -e .
uv run pytest
```

## 升级与回滚

```bash
pip install code-while-shit==0.2.0   # 升级；状态文件自动迁移，留 .bak
pip install code-while-shit==0.1.0   # 回滚；0.1 忽略新字段，不影响运行
```

---

# code-while-shit — v0.2 (English)

Lightweight bridge that lets you drive local AI coding assistants (codex / claude-code / opencode) remotely via Feishu WebSocket.

## Overview

Pick one of three agent backends behind the same Feishu entry:

- **claude-code** — driven via `claude-agent-sdk`, full capabilities (default)
- **codex** — JSON-RPC app-server, full capabilities
- **opencode** — loopback HTTP, reduced (approvals need `--allow-auto-approve`)

The service receives Feishu messages/interactions, binds sessions to a workspace and an agent thread, and surfaces confirmation/follow-up requests back to Feishu. Runtime state defaults to `.omx/runtime/bridge-state.json`.

## Quick Start

```bash
pip install -e .          # or: uv pip install -e .
cws init                  # scaffold .env and workspace
# edit .env: fill in FEISHU_APP_ID / FEISHU_APP_SECRET
cws doctor                # validate Feishu creds + agent deps
cws serve                 # default claude-code, runs in background
cws status                # show current serve process
cws stop                  # graceful stop
cws restart               # stop + serve
```

`cws serve` forks to background by default and writes logs to `$CWS_RUNTIME_DIR/serve.log`; pass `--foreground` to run in foreground. Switch agents with `cws serve --agent codex` or `cws serve --agent opencode --allow-auto-approve`. `.env` is auto-loaded (**do not commit**).

## CLI

| Command | Purpose |
| --- | --- |
| `cws init [--workspace PATH] [--agent X]` | Scaffold `.env` template and workspace |
| `cws doctor [--agent X]` | Validate Feishu creds, `lark-oapi`, and agent deps |
| `cws serve [--agent X] [--workspace PATH] [--allow-auto-approve] [--force] [--foreground]` | Start the Feishu WebSocket bridge |
| `cws status` | Show running serve (pid / agent / workspace) |
| `cws stop [--timeout S]` | SIGTERM, SIGKILL on timeout, cleans lockfile |
| `cws restart [serve flags] [--timeout S]` | stop + serve |

### In-session commands

| Command | Purpose |
| --- | --- |
| `/workspace <path>` | Switch the current session's workspace |
| `/status` | Show transport / session / agent thread / recent events |
| `/cancel`, `/stop` | Cancel current turn, keep the thread |
| `/kill`, `/clear` | Tear down the agent process; next message starts fresh |

### Capability matrix

| Agent | Approvals | Streaming | /cancel | /kill |
|---|---|---|---|---|
| codex | ✅ | ✅ | ✅ | ✅ |
| claude-code | ✅ | ✅ | ✅ | ✅ |
| opencode | ⚠️ needs `--allow-auto-approve` | ✅ | ⚠️ probed on first use | ✅ |

One Feishu bot allows only one `serve` at a time, enforced by `.omx/runtime/serve.lock`. Stale lockfiles require `--force` or `CWS_TAKEOVER_STALE=1` to take over.

## Environment variables

| Var | Default | Purpose |
| --- | --- | --- |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | — | Feishu app credentials (required) |
| `FEISHU_DOMAIN` | `https://open.feishu.cn` | Feishu API domain (override for Lark) |
| `FEISHU_BASE_URL` | `${FEISHU_DOMAIN}/open-apis` | Open API base URL |
| `FEISHU_ALLOWED_USERS` | empty | Comma-separated `open_id` allowlist; empty = unrestricted |
| `CWS_DEFAULT_WORKSPACE` | `.` | Default workspace |
| `CWS_RUNTIME_DIR` | `.omx/runtime` | Runtime directory |
| `CWS_AGENT` | `claude-code` | Default agent |
| `CODEX_*` | — | Codex backend (`CODEX_COMMAND` / `CODEX_MODEL` / `CODEX_APPROVAL_POLICY` / `CODEX_SANDBOX`, etc.) |

## Develop & test

```bash
uv pip install -e .
uv run pytest
```

## Upgrade & rollback

```bash
pip install code-while-shit==0.2.0   # upgrade; state auto-migrates, .bak kept
pip install code-while-shit==0.1.0   # rollback; 0.1 ignores new fields safely
```
