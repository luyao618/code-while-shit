# vibe-coding-while-shit v0.2 — Consensus Plan (DRAFT)

Source spec: `.omc/specs/deep-interview-v0.2.md` (ambiguity 11.5%)
Mode: ralplan consensus (short / non-interactive)
Target: bump from 0.1.0 → 0.2.0

## RALPLAN-DR Summary

### Principles
1. **Preserve codex fidelity** — the existing codex JSON-RPC app-server integration is the reference quality tier; multi-agent refactor must not regress its audit-carded approvals or incremental streaming.
2. **Single Feishu bot → single agent process** — enforce globally via PID lockfile; never allow two `serve` instances against one bot.
3. **Capability tiers are explicit, not emergent** — each agent declares `full` or `reduced` capability; runtime commands (`/cancel`, approvals) gracefully degrade instead of silently failing.
4. **Behavior parity over code unification** — different agents may use different protocols (JSON-RPC / SDK / HTTP), but share the same `AgentBackend` Protocol surface (`process_turn`, `cancel_turn`, `kill`).
5. **Fail loudly on misconfiguration** — unknown `--agent`, missing SDK, stale/active lockfile: exit non-zero with actionable message, never half-start.

### Decision Drivers (top 3)
1. **Backward compatibility** — 0.1's Feishu UX (`/workspace`, `/status`, approval cards, input cards) must keep working byte-for-byte; state file must be readable.
2. **Integration-protocol heterogeneity** — three agents with three shapes; abstraction must be narrow enough to fit all, broad enough to not lose codex's native richness.
3. **Stop-semantics correctness** — `/cancel` preserves thread; `/kill` + `/clear` destroys thread and restarts process; Feishu reports status without mutating the running progress card.

### Viable Options

**Option A — Refactor-in-place `CodexBackend` → `AgentBackend` + sibling modules**
- Rename Protocol, add `cancel_turn(thread_id)` and `kill()` methods.
- Cons: Protocol bakes codex's `thread_id` shape into the common surface — opencode has no persistent thread, claude-agent-sdk owns its session handle. Interface bloat materializes as fake thread-ids.

**Option B — Adapter pattern via `AgentSession` object**
- `serve` obtains an `AgentSession` handle; session holds its own lifecycle and event queue.
- Cons: larger refactor in `service.py`; higher regression risk to 0.1 behavior.

**Option B′ (CHOSEN, synthesis of A + B)**
- File layout from A (low-risk shim for codex), signatures from B: `AgentBackend.begin_turn(ctx, prompt) -> AgentTurn` where `AgentTurn` is a context-manager handle with `.cancel()`, `.kill_scope()`, async iteration for deltas, and per-instance `.supports_approval` / `.supports_cancel` capability flags.
- No `thread_id` in the Protocol — thread persistence is codex-internal.
- Pros: no leaked abstractions; maps naturally to claude-agent-sdk's `async with` idiom and opencode's request-scoped model; single refactor (no 0.3 rework predicted).
- Cons: slightly more scaffolding in `_run_turn` (wrap in `with backend.begin_turn(...) as turn:`), but conversion is mechanical.

**Option C — Process-per-agent sidecar (rejected)**
- Invalidation rationale: supervision layer with no product benefit; bot-single-agent constraint already gives single-process runtime.

**Chosen: B′.** Honors Principle 1 (codex fidelity via file-layout shim) and Principle 4 (behavior parity) without the `thread_id` leak that Option A would bake in.

## ADR

- **Decision**: Adopt **Option B′** — `AgentBackend.begin_turn() -> AgentTurn` context-manager handle; split `codex_app_server.py` into `src/vcws/agents/{base,codex,claude_code,opencode}.py` with a single shim release; PID lockfile with explicit `--force`/`VCWS_TAKEOVER_STALE=1` for stale takeover; add `--agent`/`--workspace` CLI flags with declared CLI-over-env precedence (conflict = exit non-zero); `/cancel|/stop` + `/kill|/clear` routes; stdout `terminal_sink`; opencode capability banner + `--allow-auto-approve` gate.
- **Drivers**: backward compatibility (state file + Feishu UX); integration-protocol heterogeneity (codex thread, claude session, opencode ephemeral); stop-semantics correctness without silent asymmetry.
- **Alternatives considered**:
  - Option A (rename-in-place) — rejected: bakes codex `thread_id` into shared Protocol, guarantees 0.3 rework.
  - Option B (session adapter) — rejected as standalone: too much `service.py` churn.
  - Option C (sidecar subprocess) — rejected: complexity without product value.
- **Why chosen**: B′ synthesizes A's file-layout safety with B's clean session semantics. `thread_id` stays codex-internal; capability flags are per-instance, not fake tier labels; all three agents fit without degradation of the contract.
- **Consequences**:
  - `AgentTurn` becomes the primary lifecycle object; `_run_turn` wraps `with backend.begin_turn(...) as turn:`.
  - State file migration required: `codex_thread_id` field rename to `agent_thread_id` with one-shot migration on load.
  - Shim for `vcws.codex_app_server` kept **one release only**; no pickle support (documented).
  - opencode auto-approve gated behind explicit flag — default refuses tool invocations with a Feishu explanation.
- **Follow-ups**: (1) 0.3 can remove the shim; (2) Consider `/switch <agent>` command in 0.3; (3) Evaluate whether opencode capability banner should move into `/status`.

## Requirements Summary

Cross-reference to spec acceptance criteria; each section below maps to one feature goal.

### Goal 1 — Multi-agent support (codex, claude-code, opencode)
- Abstract `AgentBackend` Protocol (`src/vcws/agents/base.py`):
  - `begin_turn(conversation, workspace_path, prompt, existing_thread_id, callbacks) -> AgentTurn` (context manager)
  - `kill() -> None` (process-level teardown; agents without persistent process may noop)
  - class attribute `agent_type: str`; per-turn capability flags live on `AgentTurn` instances (`.supports_cancel`, `.supports_approval`)
- `AgentTurn` Protocol:
  - `state: TurnState` enum — `RUNNING | COMPLETED | CANCELLED | KILLED`; starts `RUNNING` in `__enter__`.
  - `kill_event: threading.Event` — **per-turn**, created in `__enter__`, owned by the `AgentTurn` instance; never shared across conversations or processes.
  - `__enter__`/`__exit__` — starts and tears down turn; `__exit__` calls `cancel()` **only if `state == RUNNING`** (no double-cancel on successful completion).
  - `run() -> TurnOutcome` — synchronous run-to-completion; sets `state = COMPLETED` on normal return, `CANCELLED` / `KILLED` on those paths. `run()` is the primary driver.
  - `cancel() -> None` — non-blocking; sets `state = CANCELLED` and raises `CancelNotSupported` if `supports_cancel=False` (service layer catches).
  - `deltas()` iteration — optional streaming view alongside `run()`; codex/claude-code provide, opencode may provide.
- Codex backend (`src/vcws/agents/codex.py`): ported from current `codex_app_server.py`; `AgentTurn.cancel` sends `turn/cancel` JSON-RPC; `kill` terminates subprocess and resets `_process`/`_pending`. `thread_id` stays **internal** to codex backend, persisted via the renamed state field.
- Claude-code backend (`src/vcws/agents/claude_code.py`): wraps `claude-agent-sdk` (optional dep). `AgentTurn` drives SDK's async interface via `asyncio.run()` inside a worker thread (matches existing sync service contract). Approval/input hooks route through existing `ApprovalRequest`/`InputRequest` dataclasses. `cancel` uses SDK's native cancel; if the SDK only exposes `asyncio.CancelledError`, the turn worker thread sets a `threading.Event` checked at the SDK integration boundary.
- Opencode backend (`src/vcws/agents/opencode.py`):
  - Spawns `opencode serve --port <random-loopback-only>` on `127.0.0.1` exclusively
  - Generates a per-run token, passes via `--auth-token` if supported, otherwise verifies loopback-only binding + socket-level ACL via `SO_PEERCRED` (Linux) / process-owner check (macOS)
  - `AgentTurn.supports_cancel = True` if HTTP abort endpoint responds; else `False`
  - `AgentTurn.supports_approval = False` always; tool calls rejected unless `--allow-auto-approve` was set at `serve` startup
  - Sends a **capability banner** to Feishu at `serve` startup: "opencode 模式：审批不支持；/cancel 能力待运行时探测；建议用 /kill 终止"
- Factory `create_backend(config)` in `src/vcws/agents/__init__.py` dispatches by `config.agent.agent_type`.

### Goal 2 — CLI parameterization
- `__main__.py`: `serve` subcommand gains `--agent {codex,claude-code,opencode}` (required; no env default), `--workspace PATH` (optional), `--allow-auto-approve` (opencode only; default False), `--force` (allow takeover of stale lockfile)
- `AppConfig.from_sources(args, env)`: merges argparse and env with **documented precedence: CLI > env**. If both are set to **different** values for `agent` or `workspace`, exit non-zero with clear conflict message (Principle 5).
- `doctor` subcommand gains optional `--agent X`; validates X's dependencies (importable SDK / command available); exits non-zero with install hint if missing.

### Goal 3 — Foreground terminal + stop semantics
- `src/vcws/terminal_sink.py`: thread-safe stdout writer guarded by `threading.Lock`; each `write_line(line)` call is atomic (no torn ANSI); honors `NO_COLOR` env; prints timestamped lines for: agent deltas, Feishu inbound/outbound, status transitions, errors.
- `BridgeService` injects sink via DI; sink consumed inside `publish_status` and around adapter calls.
- `src/vcws/lockfile.py`:
  - `acquire(runtime_dir, force: bool = False) -> Lock` writes PID atomically (`O_EXCL`)
  - If an existing lockfile is found:
    - Live PID (`os.kill(pid, 0)` succeeds) → **always refuse** (exit non-zero with PID, agent, workspace of existing process)
    - Stale PID (signal 0 raises `ProcessLookupError`) → **refuse unless `force=True` or env `VCWS_TAKEOVER_STALE=1`**; log WARN with old PID + lockfile mtime on takeover
  - `release()` idempotent, registered on `atexit` and SIGINT/SIGTERM handler
- `service.py` command router: before existing flow in `handle_message`, check `message.text.strip()`:
  - `/cancel` or `/stop` → `_handle_cancel(conversation)`
  - `/kill` or `/clear` → `_handle_kill(conversation)`
- `_handle_cancel`: look up active `AgentTurn` for conversation; if `turn.supports_cancel`: call `turn.cancel()` **outside** the conversation lock; else send Feishu reply referencing the startup capability banner. Feishu receives a **new** message "已停止：用户取消当前 turn". Progress card is not mutated (explicit assertion: no `update_card` call in this path).
- `_handle_kill`: release conversation lock **before** `backend.kill()`; use a dedicated `_kill_channel` (an `Event`) that the worker observes so it can exit its `begin_turn` context cleanly; join worker with 3 s timeout, then force subprocess SIGKILL. Wipes `agent_thread_id` for all bindings (safe per single-bot-single-agent constraint). Feishu receives **new** message "已停止：已重置 agent 进程".
- SIGINT/SIGTERM handler: terminate active turn, kill agent subprocess, release lockfile, exit 0.

## Acceptance Criteria (testable, inherits deep-interview spec)

All criteria from `.omc/specs/deep-interview-v0.2.md` §"Acceptance Criteria" are inherited verbatim. Operational additions:

- [ ] `pytest` green (including new unit tests below)
- [ ] `ruff check` clean on all new/modified files
- [ ] `pyproject.toml` version bumped to `0.2.0`
- [ ] Module `src/vcws/agents/` exists with `base.py`, `codex.py`, `claude_code.py`, `opencode.py`, `__init__.py`
- [ ] Running `python -m vcws serve` without `--agent` prints argparse error listing valid values
- [ ] Running `python -m vcws serve --agent codex --workspace /x` while `CWS_DEFAULT_WORKSPACE=/y` with `/x != /y` exits non-zero with explicit conflict message
- [ ] `python -m vcws doctor --agent claude-code` exits 0 iff `claude_agent_sdk` importable; otherwise non-zero with `pip install` hint
- [ ] Second `serve` against **live** lockfile exits non-zero **before binding Feishu WebSocket** (measured: no `Feishu websocket mode active.` ever printed by second process); exit message contains existing PID, agent, workspace
- [ ] Second `serve` against **stale** lockfile exits non-zero **unless** `--force` or `VCWS_TAKEOVER_STALE=1`; on takeover, WARN line contains old PID and stale lockfile mtime
- [ ] `/cancel` handler: mock adapter records exactly **zero** `update_card` calls and **one** `send_result` (or equivalent "new message") call during the handler; fake backend's `AgentTurn.cancel()` invocation count == 1
- [ ] `/kill` path: fake backend's `kill()` invocation count == 1; worker thread joins within 3 s (asserted via `threading.Event.wait(timeout=3.5)`); all persisted `agent_thread_id` values for active bindings are `None` after the call; subsequent `handle_message` produces a new non-matching thread id (asserted via two consecutive outcomes with `outcome.thread_id` differing)
- [ ] Opencode without `--allow-auto-approve`: tool-approval callback path returns a deny response and Feishu receives explanatory message (one assertion per contract-test)
- [ ] Parity contract test passes for all three backends (see Test Plan)
- [ ] State file written by 0.1 loads under 0.2 without errors; `codex_thread_id` field is migrated to `agent_thread_id` on first load (migration covered by `test_state_migration.py`)
- [ ] Terminal sink concurrency test: 8 writer threads × 200 writes each produce exactly 1600 output lines with no interleaved bytes (asserted by parsing stdout capture, each line matches the expected regex)
- [ ] **Double-cancel guard**: in a test that runs `AgentTurn.run()` to normal completion inside a `with` block, `cancel()` is invoked **zero** times (verified by fake turn's invocation counter). `turn.state == COMPLETED` at `__exit__`.
- [ ] **Per-turn kill_event isolation**: in a test that simulates two concurrent `AgentTurn` instances (conversation A and B), calling `_handle_kill` on A sets `turnA.kill_event` to `True` and leaves `turnB.kill_event` as `False`. `state.wipe_agent_threads()` is invoked exactly once and the untouched conversation's state is not mutated outside the thread-id field.

## Implementation Steps

### Phase 2a — Abstraction layer (no behavior change for codex)
1. Create `src/vcws/agents/__init__.py` exposing `AgentBackend`, `AgentTurn`, `CancelNotSupported`, `create_backend(config)`.
2. Create `src/vcws/agents/base.py`: declare `AgentBackend` and `AgentTurn` Protocols per the design above; `CancelNotSupported(Exception)`.
3. Create `src/vcws/agents/codex.py`: port `CodexAppServerClient` + `CodexAppServerBackend`; implement `begin_turn` by constructing a `CodexAgentTurn` object that wraps the existing `process_turn` logic and exposes `cancel()` (JSON-RPC `turn/cancel`) + context-manager semantics; `kill()` terminates subprocess and clears `_process`/`_pending`.
4. Keep `src/vcws/codex_app_server.py` as a shim module that re-exports `CodexBackend = AgentBackend` + `CodexAppServerBackend` from `agents.codex` — **explicitly documents no pickle compatibility** in module docstring. Scheduled for removal in 0.3.
5. Update `src/vcws/service.py`: type hint `CodexBackend → AgentBackend`; refactor `_run_turn` to `with self.backend.begin_turn(...) as turn:`; capture `turn` into a `_active_turns: dict[session_key, AgentTurn]` (lock-guarded) so `_handle_cancel` can reach it.

### Phase 2b — Config + CLI
6. Refactor `src/vcws/config.py`: split `CodexConfig` into `AgentConfig` base (`agent_type`) + per-agent subclasses (`CodexAgentConfig` keeps current fields; `ClaudeCodeAgentConfig`/`OpencodeAgentConfig` declare theirs). `AppConfig.agent: AgentConfig`.
7. Add `AppConfig.from_sources(args, env)`:
   - CLI > env precedence; conflict = `ConfigConflictError` surfaced as exit 2
   - Deserializes per-agent fields from env
8. Update `__main__.py`:
   - `serve` adds `--agent` (required), `--workspace`, `--allow-auto-approve`, `--force`
   - `doctor` adds optional `--agent`; dependency check per agent type

### Phase 2c — Lockfile + terminal sink
9. Create `src/vcws/lockfile.py` per design: live-PID refusal; stale-PID requires `force` or env; atomic `O_EXCL` write; `release()` idempotent; atexit + signal cleanup.
10. Create `src/vcws/terminal_sink.py`: `TerminalSink` class with `write_line(str)` (lock-guarded atomic write + flush); `NO_COLOR` honored; timestamp format `%H:%M:%S.%f` truncated to ms.
11. Wire sink into `__main__.py` (serve entry, pass to service) and `BridgeService` (publish_status and adapter-send shims).

### Phase 2d — Stop-command routing
12. `service.py` `handle_message` command router: `/cancel`|`/stop` → `_handle_cancel`; `/kill`|`/clear` → `_handle_kill`. Routes checked **before** session/pending handling; acknowledge always.
13. `_handle_cancel`: read `_active_turns[session_key]`; if missing or turn not `supports_cancel`, Feishu replies explaining (references startup capability banner for reduced-tier agents). Else call `turn.cancel()` **outside** the conversation lock; mark session `idle`; Feishu `send_result` a new message (progress card untouched — **mocked adapter asserts zero `update_card` calls in this path**).
14. `_handle_kill`:
   a. Read active `AgentTurn` references from `_active_turns` under lock; **release the lock** before joining.
   b. Set **that turn's** `kill_event` (per-turn, owned by the `AgentTurn`); worker observes it inside its turn context and raises to exit `__exit__`. Never share `kill_event` across conversations.
   c. Call `backend.kill()` (outside any conversation lock).
   d. Join worker thread with 3 s timeout; on timeout SIGKILL the subprocess.
   e. Wipe `agent_thread_id` (formerly `codex_thread_id`) on all bindings via a dedicated `state.wipe_agent_threads()` method.
   f. Feishu sends new message.
15. SIGINT/SIGTERM handler: set the active turn's `kill_event`, call `backend.kill()`, release lockfile, `sys.exit(0)`.

### Phase 2e — New agent backends
16. Implement `src/vcws/agents/claude_code.py`:
    - Detect `claude_agent_sdk`; raise clear ImportError with install hint if missing.
    - Wrap SDK's async interface in a worker-thread event loop; `AgentTurn.cancel()` sets a `threading.Event` and issues SDK cancel.
    - Translate SDK events to `ApprovalRequest` / `InputRequest` + `ProgressUpdate` deltas.
17. Implement `src/vcws/agents/opencode.py`:
    - Spawn `opencode serve --port <random>` on `127.0.0.1`; read stdout until it prints "ready"-equivalent; fail with clear message after 5 s timeout.
    - Confirm loopback-only binding; if `--auth-token` unsupported, log the socket-owner check result.
    - Probe for abort endpoint; set `AgentTurn.supports_cancel` accordingly.
    - Tool-approval callback **denies** unless `config.allow_auto_approve` is True; on deny, Feishu receives explanation.
    - Emit startup capability banner to Feishu and terminal sink.
18. Escape hatch: if opencode subprocess fails to come up across 3 retries with exponential backoff, `serve --agent opencode` exits non-zero with diagnostic; `doctor --agent opencode` performs the same check dry-run. Document in `README.md` and `CHANGELOG.md`.

### Phase 2f — State migration + version + release notes
19. `src/vcws/state.py`: on load, if `codex_thread_id` field present rename to `agent_thread_id`; write updated file back atomically; dedicated migration tested in `tests/test_state_migration.py`.
20. Bump `pyproject.toml` to `0.2.0`. Add optional deps: `[project.optional-dependencies] claude = ["claude-agent-sdk>=<version>"]`; `opencode = []` (binary-only dep documented in README).
21. Update `README.md`: new CLI, commands, agents, capability table, rollback guidance ("to revert to 0.1, `pip install vibe-coding-while-shit==0.1.0` — state file forward-compatible only").
22. Add `CHANGELOG.md` entry for 0.2 with BREAKING CHANGES block (CLI now requires `--agent`; state field rename).
23. **Rollback plan**: document in `CHANGELOG.md` — revert merge commit restores 0.1; state file with new `agent_thread_id` field is **ignored** by 0.1 (field not read), so no data loss; users pinning 0.1 after 0.2 release continue to work.

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| `claude-agent-sdk` cancel primitive differs from assumed shape | Medium | High | Step 16 begins with SDK doc read; `AgentTurn.cancel()` sets a threading.Event that SDK integration polls at each await boundary — works even if SDK only exposes `asyncio.CancelledError`. Optional dep so core install is unaffected. |
| `opencode serve` HTTP mode unavailable / undocumented | High | Medium | Step 18 fail-fast + `doctor` dry-run. If blocking, release notes flag opencode as "requires opencode >= vX.Y"; not a release blocker. |
| Codex regression from refactor | Low | Critical | Shim re-export (step 4) + full existing test suite must pass after phase 2a before starting 2b; CI gate. |
| Lockfile stale after hard crash | Medium | Low | Detected by signal-0 probe; refusal default; takeover requires explicit opt-in (Principle 5). |
| Stop command races with running turn | Medium | Medium | `_handle_kill` releases conversation lock before subprocess join (step 14); `_kill_channel` event pattern avoids deadlock; SIGKILL fallback after 3 s. |
| `/kill` wipes other conversations' thread ids | Low | Medium | Single-bot single-agent invariant guarantees all active bindings belong to the killed process; state migration step documents this semantic. |
| State file migration corrupts 0.1 file | Medium | High | Atomic write via tmpfile + rename; migration only runs if old field present; dedicated test `test_state_migration.py`; keep original file as `.bak` on first migration. |
| Shim pickle break surprises downstream users | Low | Medium | Module docstring explicitly declares "not pickle-stable"; `CHANGELOG.md` BREAKING CHANGES. |
| Opencode random port hijack (local-only threat) | Low | Medium | Bind `127.0.0.1` only; per-run auth token when supported; else document limitation in README ("run on trusted single-user hosts only"). |
| Terminal sink torn ANSI | Medium | Low | Lock-guarded `write_line`; test with 8 writers × 200 lines proves atomicity. |

## Verification Steps

1. `ruff check src tests` — clean
2. `pytest` — all green
3. **Automated parity contract test** (`tests/test_agent_parity.py`): a parametrized test that runs the same scripted scenario against a `FakeCodex`, `FakeClaudeCode`, `FakeOpencode` (each subclass the real backends with injected transport seams) — scenario: start turn → receive 2 deltas → cancel → outcome is `interrupted` → next turn reuses thread for codex/claude-code, opens fresh for opencode.
4. **Manual smoke (codex)**: `python -m vcws serve --agent codex --workspace .`; send Feishu test; observe stdout; `/cancel`; next message retains thread; `/kill`; next message new thread; Ctrl+C exits 0.
5. **Manual smoke (claude-code)**: same as #4.
6. **Manual smoke (opencode)**: same as #4 or verify fail-fast path if opencode unavailable.
7. **Regression**: 0.1 `/workspace`, `/status`, approval card, input card under `--agent codex` all behave identically — captured by `test_regression_0_1.py` that diffs Feishu API calls against a recorded 0.1 trace.
8. **Double-start**: start serve, try second; assert second exits non-zero **before** printing "Feishu websocket mode active." (assertion: subprocess stdout does not contain that string; exit code ≥ 1).
9. **Stale-lock takeover**: write a lockfile with dead PID, run `serve` → refused; run `serve --force` → takeover succeeds with WARN line.
10. **Ctrl+C**: send SIGINT; assert lockfile removed, agent subprocess PID no longer alive (`os.kill(pid, 0)` raises), exit code 0.
11. **State migration**: place a 0.1 `bridge-state.json` with `codex_thread_id`; start 0.2 serve; assert file rewritten with `agent_thread_id` and `.bak` preserved.

## Test Plan (unit additions)

- `tests/test_agents_base.py`: Protocol conformance via `FakeBackend` + `FakeTurn`; context-manager exit cancels; `CancelNotSupported` propagates.
- `tests/test_lockfile.py`: acquire on empty dir; refuse on live PID; refuse on stale PID without force; succeed on stale PID with `--force`; release idempotent.
- `tests/test_service_stop_commands.py`: `/cancel` invokes `turn.cancel()` exactly once, emits one `send_result`, zero `update_card`; `/kill` invokes `backend.kill()` exactly once, wipes `agent_thread_id`, joins worker within 3 s.
- `tests/test_config_factory.py`: CLI > env precedence; conflict raises `ConfigConflictError`; unknown agent_type raises.
- `tests/test_terminal_sink.py`: 8 writers × 200 writes = 1600 well-formed lines; `NO_COLOR` disables ANSI.
- `tests/test_agent_parity.py`: parametrized parity contract across fakes of three backends.
- `tests/test_state_migration.py`: 0.1 file → 0.2 file round-trip; `.bak` preserved; idempotent second load is a noop.
- `tests/test_regression_0_1.py`: replay recorded Feishu trace under `--agent codex`; assert outputs identical modulo timestamps.

## Changelog (improvements applied after Architect/Critic review)

### Iteration 1 — Option B′ synthesis + tightened semantics
- **Adopted Option B′** synthesis (AgentTurn context manager) instead of Option A; removed `thread_id` from Protocol (Architect #1 / Critic #1).
- **Opencode capability banner at startup** + `/cancel` references it; no silent `NotImplementedError` (Architect #2 / Critic #2).
- **`--allow-auto-approve` flag** required for opencode tool calls; default denies (Architect #3 / Critic #3).
- **Stale lockfile takeover** requires `--force` or `VCWS_TAKEOVER_STALE=1`; live PID always refuses (Architect #4 / Critic #4).
- **`_handle_kill` releases conversation lock before joining worker**; kill-signal event prevents deadlock (Architect #5a / Critic #5).
- **State migration** for `codex_thread_id` → `agent_thread_id` with `.bak` preservation (Architect #5b / Critic #6).
- **Shim documents no pickle compatibility**; scheduled removal in 0.3 (Architect #5c / Critic #7).
- **CLI vs env precedence** explicit; conflict exits non-zero (Critic #9 / issue G).
- **Acceptance criteria tightened**: invocation counts, exact thresholds, observable assertions (Critic issues A/B/F).
- **Terminal sink concurrency test** specifies 8×200 lines with parse assertion (Critic issue C).
- **Parity contract test** added across all three backends (Critic issue D).
- **Rollback plan** added to `CHANGELOG.md` (Critic issue E).
- **Opencode random-port auth** tightened: loopback-only + per-run token when supported (risk table).

### Iteration 2 — Semantic blockers resolved
- **B1 — `AgentTurn.state` enum** (`RUNNING|COMPLETED|CANCELLED|KILLED`) introduced; `__exit__` calls `cancel()` only if `state == RUNNING`; AC added ("double-cancel guard").
- **B2 — `kill_event` is per-`AgentTurn`**, created in `__enter__`, owned by the turn instance; never shared across conversations; AC added ("per-turn kill_event isolation"); `_handle_kill` updated to set the specific active turn's `kill_event` only.

### Iteration 2 — Non-blocker nits filed as follow-ups (execute during implementation)
- **N1** — `run()` is the primary driver, `deltas()` is an optional streaming view. Clarified in AgentTurn Protocol.
- **N2** — Claude-code cancel path: `threading.Event` is polled at each `await` boundary; implementation adds a **3 s cancel latency timeout**, on expiry falls through to `backend.kill()` automatically. (Execution lane: add to `test_claude_code_cancel_latency.py`.)
- **N3** — macOS socket-peer check uses `getpeereid` / `LOCAL_PEERCRED`, not `SO_PEERCRED`; plan text updated in Goal 1 (reviewer note).
- **N4** — Opencode `supports_cancel` is probed **lazily** on first cancel attempt and cached; capability banner states "/cancel capability will be detected on first use".
- **N5** — `state.wipe_agent_threads()` covered by new AC ("per-turn kill_event isolation") which also asserts only the thread-id field is mutated for active bindings.
