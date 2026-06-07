# The Intern

A goofy-but-useful always-on AI teammate built on the Claude Agent SDK. The
orchestrator owns Slack-facing personality and delegates real work to three
tool-restricted specialists:

- `planner`: Linear/backlog planning and intern-safe ticket triage. By default it
  gets no built-in filesystem tools; pass explicit Linear MCP tool names when you
  wire your MCP server.
- `coder`: repo work, Perseus-assisted orientation, tests, and local commits.
- `shipper`: GitHub PR creation and updates.

The important safety rule is enforced in code: the `PreToolUse` hook in
`intern_bot/merge_guard.py` blocks `gh pr merge`, `git merge`, and force pushes
unless a human-authorized merge flag is present.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Run

Run one orchestrator turn:

```bash
intern turn "Please triage the easiest intern-safe ticket and implement it."
```

Run one heartbeat tick:

```bash
intern heartbeat-once
```

Run the always-on loop:

```bash
intern heartbeat
```

The CLI currently prints messages to stdout. A Slack app or webhook listener can
call `intern_bot.agent.run_turn()` for reactive events and
`intern_bot.heartbeat.heartbeat_loop()` for scheduled ticks, passing its own
`post_message` function.

## Configuration

Environment variables:

- `INTERN_PAUSED=1`: kill switch. Heartbeats do nothing while set.
- `INTERN_MEMORY_PATH`: Markdown audit/memory path. Default: `.intern/memory.md`.
- `INTERN_HEARTBEAT_SECONDS`: scheduler interval. Default: `1800`.
- `INTERN_RANDOM_BANTER_CHANCE`: idle banter chance. Default: `0.10`.
- `INTERN_MAX_CONCURRENT_TASKS`: default `1`.
- `INTERN_MAX_SELF_STARTED_PRS_PER_DAY`: default `3`.
- `INTERN_DAILY_SPEND_CAP_USD`: default `5.00`.
- `INTERN_QUIET_HOURS_START` / `INTERN_QUIET_HOURS_END`: optional hour values
  in local time, e.g. `18` and `8`.
- `INTERN_MERGE_AUTHORIZED=1`: manual/local override for merge commands. In a
  real Slack flow, prefer setting SDK session state only after an explicit human
  command such as `merge PR #123`.

## Project Shape

```text
intern_bot/
  agent.py        Claude Agent SDK options, subagent definitions, run_turn()
  cli.py          local command entry point
  config.py       env-backed runtime configuration
  heartbeat.py    OpenClaw-style heartbeat loop and cap checks
  memory.py       Markdown audit log and daily accounting
  merge_guard.py  hard merge/force-push blocker
  slack/          Slack orchestrator prompt, heartbeat prompt, tool defaults
  linear/         Linear planner prompt and MCP tool defaults
  codebase/       Coder prompt and repo/Bash tool defaults
  github/         Shipper prompt and PR tool defaults
tests/
  test_memory.py
  test_merge_guard.py
```

## Operator Setup

Before letting the coder agent work autonomously:

1. Install and authenticate Perseus outside the agent. Do not ask the agent to run
   `perseus login`.
2. Index every allowlisted repo on boot and after pulls:

   ```bash
   perseus index /path/to/repo
   ```

3. Connect scoped Slack, Linear, and GitHub MCP servers in the SDK options. Keep
   tokens least-privilege and repo-limited. Use `create_options(planner_tools=[...])`
   and `create_options(shipper_tools=[...])` to add only the exact MCP tools each
   specialist needs.
4. Wire Slack events to `run_turn()`, and wire the scheduler to
   `heartbeat_loop()`.

## Safety Model

- Prompts forbid merges, branch deletion, force pushes, production changes, and
  high-risk autonomous work.
- `block_merges()` enforces the merge guard at the tool layer.
- `InternMemory` records every heartbeat/manual turn with cost markers and PR
  markers so daily caps are auditable.
- `INTERN_PAUSED=1` is the kill switch checked before every heartbeat.
- Randomness only affects cosmetic heartbeat banter, never code changes or
  ticket edits.

## Tests

```bash
source .venv/bin/activate
pytest
```
