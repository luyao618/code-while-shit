# vibe-coding-while-shit

一个通过飞书 WebSocket 远程驱动本机 Codex 的轻量桥接服务。

## 1. 项目概览

它做三件事：

- 接收飞书消息和交互事件
- 把会话绑定到工作目录和 Codex thread
- 在需要时把确认/补充信息回流给飞书

它是一个单机、单飞书入口的 MVP 形态，适合先把“人在飞书里操作 Codex”这条链路跑稳。

## 2. 架构概览

- **Feishu WebSocket gateway**：处理飞书事件入口
- **Bridge service**：维护会话、工作目录、状态、恢复信息
- **Codex app-server 子进程**：由 `CODEX_COMMAND` + `CODEX_APP_SERVER_ARGS` 启动

运行时状态默认写到 `.omx/runtime/bridge-state.json`。

## 3. 前置条件

- Python 3.11+
- `codex` 命令可用，或用 `CODEX_COMMAND` 指向你的 Codex 可执行文件
- 一个可写的本地工作目录
- 飞书开放平台应用的 **App ID** 和 **App Secret**
- 已安装依赖（`lark-oapi` 会随 `pip install -e .` 安装）

## 4. Quick Start

```bash
python3 -m pip install -e .

export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export CWS_DEFAULT_WORKSPACE=/absolute/path/to/workspace

python3 -m vcws doctor
python3 -m vcws serve
```

`doctor` 通过时会输出：

```text
配置看起来可启动（Feishu WebSocket mode）。
```

`serve` 启动后会输出：

```text
Feishu websocket mode active.
```

## 5. 飞书应用配置

不需要配置 webhook callback URL、verification token 或 encrypt key。

在飞书开放平台里：

1. 创建应用并拿到 **App ID** 和 **App Secret**
2. 开启 **Bot** 能力
3. 在 **Event Subscription** 中选择 **长连接（WebSocket）**
4. 至少订阅 `im.message.receive_v1`
5. 如果要用确认卡片，确保卡片交互能力可用

## 6. 日常使用 / 操作流程

1. 先跑 `doctor`
2. 再跑 `serve`
3. 直接给机器人发消息，启动一个 Codex 任务
4. 后续消息会继续沿用当前会话绑定的 Codex thread
5. Codex 要你补充信息时，直接回复文本
6. Codex 要你确认时，在卡片里完成确认/拒绝
7. 用 `/workspace <path>` 切换当前会话的工作目录
8. 用 `/status` 查看当前 transport、会话、工作目录、Codex thread 和最近事件

补充：

- `/workspace <path>` 会把路径解析为绝对路径，并在不存在时创建目录
- 如果设置了 `FEISHU_ALLOWED_USERS`，只有名单里的 `open_id` 能操作；不设置则不限制

## 7. 命令与环境变量

### CLI 命令

| 命令 | 作用 |
| --- | --- |
| `python3 -m vcws doctor` | 检查 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 和 `lark-oapi` 是否可用 |
| `python3 -m vcws serve` | 启动飞书 WebSocket bridge |

### Feishu / 运行目录

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `FEISHU_APP_ID` | 无 | 飞书应用 ID；`doctor` 和 `serve` 都需要 |
| `FEISHU_APP_SECRET` | 无 | 飞书应用 Secret；`doctor` 和 `serve` 都需要 |
| `FEISHU_DOMAIN` | `https://open.feishu.cn` | 飞书 API 域名；国际版可改成对应 Lark 域名 |
| `FEISHU_BASE_URL` | `${FEISHU_DOMAIN}/open-apis` | 飞书 Open API 基址 |
| `FEISHU_ALLOWED_USERS` | 空 | 逗号分隔的 `open_id` allowlist；为空表示不限制 |
| `CWS_DEFAULT_WORKSPACE` | `.` | 默认工作目录 |
| `CWS_RUNTIME_DIR` | `.omx/runtime` | 运行时目录；状态文件写在这里 |

### Codex

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `CODEX_COMMAND` | `codex` | 启动 Codex app-server 的可执行文件 |
| `CODEX_APP_SERVER_ARGS` | `app-server` | 传给 Codex 的 app-server 参数，按空格切分 |
| `CODEX_MODEL` | `gpt-5.4` | 传给 Codex 的模型 |
| `CODEX_APPROVAL_POLICY` | `on-request` | Codex approval policy |
| `CODEX_APPROVALS_REVIEWER` | `user` | Codex approvals reviewer |
| `CODEX_SANDBOX` | `workspace-write` | Codex sandbox 模式 |
| `CODEX_SERVICE_TIER` | 空 | Codex service tier；不设置则不传 |

## 8. 开发与测试

```bash
python3 -m pip install -e .
python3 -m vcws doctor
python3 -m unittest discover -s tests -p 'test_*.py'
```

## 9. 已知限制 / 排障

- 目前只支持 **Feishu WebSocket** 入口，不支持 webhook 主路径
- `serve` 启动前必须有 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`
- 如果 `FEISHU_ALLOWED_USERS` 被设置，名单外用户会被拒绝
- 如果 `/status` 里显示没有活跃 thread，说明这个会话还没真正开始过任务
- 重启恢复会沿用同一会话的 Codex thread；它不是“回到中断 RPC 的精确现场”
- `CWS_RUNTIME_DIR` 和 `CWS_DEFAULT_WORKSPACE` 必须可写
- 如果 `doctor` 失败，先看是否缺少 `lark-oapi`、`FEISHU_APP_ID` 或 `FEISHU_APP_SECRET`
- 如果 `serve` 先打印 active，随后又报 Feishu 连接错误，优先检查 WebSocket 订阅、`FEISHU_DOMAIN` / `FEISHU_BASE_URL` 和网络可达性
