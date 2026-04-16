# code-while-shit

一个通过飞书远程驱动本机 Codex 的 MVP。

## 当前实现

- Feishu **WebSocket** event subscription ingress
- 启动必填只需要：`FEISHU_APP_ID` + `FEISHU_APP_SECRET`
- 常驻 Codex app-server client
- conversation → workspace → Codex thread 绑定
- 多轮上下文延续（复用已有 thread）
- 需要确认时发 Feishu interactive card，并通过 websocket action event 回流
- 需要补充信息时暂停并等待用户后续消息回复
- 默认工作目录 + `/workspace <path>` 切换
- `/status` 查看会话状态 + Feishu transport 状态
- transport dedupe / reconnect / restart recovery 基础支持

## 安装

```bash
python3 -m pip install -e .
```

## 运行方式

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export CWS_DEFAULT_WORKSPACE=/absolute/path/to/workspace

PYTHONPATH=src python3 -m codewhileshit doctor
PYTHONPATH=src python3 -m codewhileshit serve
```

`doctor` 通过后会输出：

```bash
配置看起来可启动（Feishu WebSocket mode）。
```

启动后会输出：

```bash
Feishu websocket mode active.
```

## Feishu 侧最小配置

你不需要配置 webhook callback URL / verification token / encrypt key。

需要在飞书开放平台完成：

1. 创建应用并拿到：
   - **App ID**
   - **App Secret**
2. 开启 **Bot** 能力
3. 在 **Event Subscription** 中选择 **长连接（WebSocket）**
4. 至少订阅消息接收事件：
   - `im.message.receive_v1`

如果你使用 interactive card 做确认，确保对应卡片交互能力可用。

## 关键环境变量

### 必填
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

### 可选
- `CWS_DEFAULT_WORKSPACE`（默认当前目录）
- `FEISHU_ALLOWED_USERS`（逗号分隔的 open_id 列表）
- `FEISHU_DOMAIN`（默认 `https://open.feishu.cn`；国际版可改为 Lark 域名）
- `FEISHU_BASE_URL`（默认 `${FEISHU_DOMAIN}/open-apis`）
- `CWS_RUNTIME_DIR`（默认 `.omx/runtime`）
- `CODEX_COMMAND`（默认 `codex`）
- `CODEX_MODEL`（默认 `gpt-5.4`）

## 使用说明

- 直接给 Bot 发消息即可开始一个 Codex 任务
- 后续消息会继续复用当前会话绑定的 Codex thread
- 当 Codex 需要补充信息时，直接回复文本
- 当 Codex 需要确认时，在飞书卡片中点击按钮
- `/workspace <path>` 可切换工作目录
- `/status` 可查看：
  - 当前 Feishu transport 状态
  - 当前工作目录
  - 当前 Codex thread
  - 最近事件

## 已知限制

- 当前默认并仅支持 **Feishu WebSocket** ingress，不再以 webhook 作为主路径
- 真实线上联调仍需要你自己的 Feishu Bot 凭据与长连接事件订阅配置
- restart recovery 通过同一 Codex thread 上的 recovery turn 恢复，而不是恢复到精确的中断 RPC turn
- 目前保留现有 `FeishuApiClient` 作为出站 HTTP send surface；只把入站 transport 切到官方 websocket SDK
