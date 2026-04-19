# Changelog

## Unreleased

### Added
- `cws update` — re-installs cws from upstream `main` via `uv tool install --force`. One-liner upgrade for users.

### Changed
- **Runtime dir is now global** at `~/.local/share/cws/runtime/` (no more per-workspace `<sha256(cwd)>` sub-directory). Since a single Feishu app cannot have two concurrent WebSocket connections, `cws serve` is a singleton — having per-workspace runtime dirs only made `cws status` confusing across directories. Override via `CWS_RUNTIME_DIR` is unchanged.
- `cws init` no longer auto-invokes `cws doctor`. It now prints `cws doctor` as a suggested next step instead. (The auto-run caused stdout to interleave with the user's next typed command because the `claude-code` dep check imports the SDK, which is slow.)

### Fixed
- Progress card and milestone messages no longer hard-code "Codex". When running with the `claude-code` or `opencode` agent, the card now correctly shows "Agent 已结束当前执行" instead of misleadingly saying "Codex 已结束当前执行".
- **claude-code multi-turn context**: the `claude-code` backend now actually resumes the previous Claude Code session between turns. Previously the saved `agent_thread_id` was stored but never passed back to `claude_agent_sdk.query()`, so every Feishu message started a fresh session with no memory of prior turns. Fix: capture `session_id` from SDK events and pass it back via `ClaudeAgentOptions(resume=...)` on the next turn.

### Migration
- Users on the previous version may have leftover `~/.local/share/cws/runtime/<12-hex>/` directories. They are not used anymore — safe to delete with `rm -rf ~/.local/share/cws/runtime/[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]`. Existing thread bindings stored under those dirs will not migrate automatically; the next `cws serve` starts a fresh `bridge-state.json` at the new global path.

## 0.2.0 (2026-04-17)

### ⚠️ BREAKING CHANGES

- **CLI**: `serve` now **requires** `--agent {codex,claude-code,opencode}`. Running `python -m cws serve` without `--agent` will exit non-zero with an argparse error.
- **State file**: the `codex_thread_id` field on workspace bindings has been renamed to `agent_thread_id`. Existing `bridge-state.json` files are auto-migrated on first load; a `.bak` copy is preserved. Downgrading to 0.1 will silently ignore the new field (no data loss).
- **Module**: `cws.codex_app_server` is now a compatibility shim. Import from `cws.agents.codex` or `cws.agents` directly. The shim is scheduled for removal in 0.3 and is not guaranteed pickle-stable.

### Added

- Multi-agent support: **codex** (JSON-RPC app-server, full capabilities), **claude-code** (via `claude-agent-sdk`, full capabilities), **opencode** (loopback HTTP, reduced capabilities — approvals require `--allow-auto-approve`).
- `AgentBackend` + `AgentTurn` Protocols (`src/cws/agents/base.py`) with `TurnState` enum (`RUNNING | COMPLETED | CANCELLED | KILLED`) and per-turn `kill_event`.
- CLI flags: `--agent` (required on serve), `--workspace PATH`, `--allow-auto-approve` (opencode only), `--force` (stale-lockfile takeover).
- `doctor --agent X` validates the target agent's dependencies (codex command / claude-agent-sdk / opencode command).
- PID lockfile at `.omx/runtime/serve.lock` prevents double-start. Live PID → always refuse. Stale PID → refuse unless `--force` or `CWS_TAKEOVER_STALE=1`.
- Stop commands:
  - `/cancel` ≡ `/stop` — cancel current turn, preserve thread.
  - `/kill` ≡ `/clear` — tear down agent process, wipe thread, next message starts fresh.
- Foreground `TerminalSink` prints timestamped events (agent deltas, Feishu in/out, status) to stdout with atomic thread-safe writes and `NO_COLOR` support.
- Parity contract tests across all three backends (`tests/test_agent_parity.py`).
- 0.1 regression tests (`tests/test_regression_0_1.py`) ensure `/workspace`, `/status`, approval, and input flows unchanged under `--agent codex`.

### Changed

- `CodexConfig` → `CodexAgentConfig` (alias preserved). `AppConfig.agent: AgentConfig` replaces the direct `.codex` attribute; `AppConfig.codex` kept as a backward-compat property.
- `AppConfig.from_sources(args, env)` supersedes `from_env()` for serve-path config. CLI args take precedence over environment variables; conflicting values raise `ConfigConflictError` → exit code 2.
- `BridgeService._run_turn` now wraps execution in `with backend.begin_turn(...) as turn:` and registers the live `AgentTurn` in `_active_turns` for stop-command routing.

### Fixed

- Double-cancel guard: `AgentTurn.__exit__` no longer invokes `cancel()` on turns that completed normally (state != RUNNING).
- `_handle_kill` releases the conversation lock before joining the worker thread, eliminating a potential deadlock.
- Per-turn `kill_event` is instance-owned, never shared across conversations — killing turn A does not accidentally poison turn B's Event identity.

### Security

- Opencode HTTP server binds `127.0.0.1` only.
- Opencode tool calls are **denied by default**; `--allow-auto-approve` is required to permit automatic approval, with an explanatory Feishu message on rejection.

### Rollback

To downgrade from 0.2 → 0.1:

```bash
pip install code-while-shit==0.1.0
```

- 0.1 ignores the renamed `agent_thread_id` field; sessions effectively start fresh.
- The `.bak` state file from the migration can be restored manually if desired:
  `mv .omx/runtime/bridge-state.json.bak .omx/runtime/bridge-state.json`

## 0.1.0

Initial Feishu-driven remote Codex bridge MVP.
