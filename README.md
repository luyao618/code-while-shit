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
curl -fsSL https://raw.githubusercontent.com/luyao618/code-while-shit/main/scripts/install.sh | sh
```

然后：

```bash
cws init                                          # 生成 ~/.config/cws/config.toml
cws config set feishu.app_id YOUR_APP_ID
cws config set feishu.app_secret YOUR_APP_SECRET
cd /path/to/your/workspace   # 重要：cws serve 以 cwd 作为 workspace
cws serve
```

`cws serve` 默认 fork 到后台，日志写到 `$CWS_RUNTIME_DIR/serve.log`；前台运行加 `--foreground`。切换 agent：`cws serve --agent codex` 或 `cws serve --agent opencode --allow-auto-approve`。

### Developers

```bash
git clone https://github.com/luyao618/code-while-shit.git
cd code-while-shit
uv pip install -e .
cws doctor
```

## CLI 命令

| 命令 | 作用 |
| --- | --- |
| `cws init` | 生成全局配置模板 `~/.config/cws/config.toml` |
| `cws config path` | 打印全局配置文件路径 |
| `cws config list` | 列出所有配置项 |
| `cws config get <key>` | 获取单个配置值（如 `feishu.app_id`）|
| `cws config set <key> <value>` | 写入配置项 |
| `cws config unset <key>` | 删除配置项 |
| `cws config edit` | 用 `$EDITOR` 打开配置文件 |
| `cws doctor [--agent X]` | 检查 Feishu 凭证、`lark-oapi` 及 Agent 依赖 |
| `cws serve [--agent X] [--allow-auto-approve] [--force] [--foreground]` | 启动飞书 WebSocket bridge（workspace = cwd） |
| `cws status` | 显示当前 serve 进程（pid / agent / workspace） |
| `cws stop [--timeout S] [--all]` | SIGTERM → 超时后 SIGKILL，并清理 lockfile；`--all` 扫描进程表强杀全部 cws/vcws serve（含 orphan、改名前的旧二进制） |
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
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | — | 飞书应用凭证（必需；也可通过 `cws config set feishu.app_id` 设置） |
| `FEISHU_DOMAIN` | `https://open.feishu.cn` | 飞书 API 域名（Lark 国际版可改） |
| `FEISHU_BASE_URL` | `${FEISHU_DOMAIN}/open-apis` | Open API 基址 |
| `FEISHU_ALLOWED_USERS` | 空 | 逗号分隔 `open_id` allowlist；空=不限制 |
| `CWS_RUNTIME_DIR` | `~/.local/share/cws/runtime/<hash>/` | 运行时目录（覆盖默认） |
| `CWS_AGENT` | `claude-code` | 默认 agent（覆盖 `[agent] default` in config.toml） |
| `CODEX_*` | — | Codex 后端配置（`CODEX_COMMAND` / `CODEX_MODEL` / `CODEX_APPROVAL_POLICY` / `CODEX_SANDBOX` 等） |

## 开发与测试

```bash
uv pip install -e .
uv run pytest
```

## 升级与卸载

```bash
# 升级（推荐）：重跑安装脚本即可
curl -fsSL https://raw.githubusercontent.com/luyao618/code-while-shit/main/scripts/install.sh | sh
# 或在已装好的环境里：
cws update

# 卸载
uv tool uninstall code-while-shit
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
curl -fsSL https://raw.githubusercontent.com/luyao618/code-while-shit/main/scripts/install.sh | sh
```

Then:

```bash
cws init                                          # creates ~/.config/cws/config.toml
cws config set feishu.app_id YOUR_APP_ID
cws config set feishu.app_secret YOUR_APP_SECRET
cd /path/to/your/workspace   # important: cws serve uses cwd as workspace
cws serve
```

`cws serve` forks to background by default and writes logs to `$CWS_RUNTIME_DIR/serve.log`; pass `--foreground` to run in foreground. Switch agents with `cws serve --agent codex` or `cws serve --agent opencode --allow-auto-approve`.

### Developers

```bash
git clone https://github.com/luyao618/code-while-shit.git
cd code-while-shit
uv pip install -e .
cws doctor
```

## CLI

| Command | Purpose |
| --- | --- |
| `cws init` | Create global config template at `~/.config/cws/config.toml` |
| `cws config path` | Print path to global config file |
| `cws config list` | Print all config values (TOML format) |
| `cws config get <key>` | Print one value (e.g. `feishu.app_id`) |
| `cws config set <key> <value>` | Write a config value |
| `cws config unset <key>` | Remove a config key |
| `cws config edit` | Open config in `$EDITOR` |
| `cws doctor [--agent X]` | Validate Feishu creds, `lark-oapi`, and agent deps |
| `cws serve [--agent X] [--allow-auto-approve] [--force] [--foreground]` | Start the Feishu WebSocket bridge (workspace = cwd) |
| `cws status` | Show running serve (pid / agent / workspace) |
| `cws stop [--timeout S] [--all]` | SIGTERM, SIGKILL on timeout, cleans lockfile; `--all` scans the process table and force-kills every cws/vcws serve (catches orphans and the legacy binary name) |
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
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | — | Feishu app credentials (required; also settable via `cws config set feishu.app_id`) |
| `FEISHU_DOMAIN` | `https://open.feishu.cn` | Feishu API domain (override for Lark) |
| `FEISHU_BASE_URL` | `${FEISHU_DOMAIN}/open-apis` | Open API base URL |
| `FEISHU_ALLOWED_USERS` | empty | Comma-separated `open_id` allowlist; empty = unrestricted |
| `CWS_RUNTIME_DIR` | `~/.local/share/cws/runtime/<hash>/` | Runtime directory (overrides default) |
| `CWS_AGENT` | `claude-code` | Default agent (overrides `[agent] default` in config.toml) |
| `CODEX_*` | — | Codex backend (`CODEX_COMMAND` / `CODEX_MODEL` / `CODEX_APPROVAL_POLICY` / `CODEX_SANDBOX`, etc.) |

## Develop & test

```bash
uv pip install -e .
uv run pytest
```

## Upgrade & uninstall

```bash
# Upgrade (recommended): just re-run the installer
curl -fsSL https://raw.githubusercontent.com/luyao618/code-while-shit/main/scripts/install.sh | sh
# Or, from an existing install:
cws update

# Uninstall
uv tool uninstall code-while-shit
```
