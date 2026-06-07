# The Intern

The Intern is a goofy-but-useful always-on AI teammate built on the Claude Agent
SDK. It is designed to live in Slack, triage small Linear work, edit code on
branches, and open draft GitHub PRs for human review.

The safety model is intentionally boring in the best way: the Slack-facing
orchestrator talks to humans, specialist subagents do the quiet work, and a
`PreToolUse` hook hard-blocks merges and force-pushes unless a human has
explicitly authorized that action.

## Architecture

```text
Slack events / heartbeats
        |
        v
orchestrator  -> planner  -> Linear ticket triage and planning
              -> coder    -> codebase edits, Perseus search, tests, commits
              -> shipper  -> GitHub push/PR creation, never merge
```

- `planner`: Linear/backlog planning and intern-safe ticket triage. By default it
  gets no built-in filesystem tools; pass explicit Linear MCP tool names when you
  wire your MCP server.
- `coder`: repo work, Perseus-assisted orientation, tests, and local commits.
- `shipper`: GitHub PR creation and updates.

## Prerequisites

- macOS or Linux shell
- Python 3.11+
- Git
- Claude Agent SDK credentials/configuration for the runtime you are using
- Optional but expected for full autonomy:
  - Slack app or Slack MCP server
  - Linear MCP server
  - GitHub MCP server or `gh` CLI with repo-scoped credentials
  - Perseus CLI installed, authenticated, and indexed by the operator

## Local Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

Confirm the CLI is installed:

```bash
intern --help
```

Run the test suite:

```bash
pytest
```

If you do not activate the venv, use the explicit paths:

```bash
.venv/bin/python -m pytest
.venv/bin/intern --help
```

## Environment

The app reads runtime settings from environment variables.

```bash
export INTERN_PAUSED=0
export INTERN_MEMORY_PATH=.intern/memory.md
export INTERN_HEARTBEAT_SECONDS=1800
export INTERN_RANDOM_BANTER_CHANCE=0.10
export INTERN_MAX_CONCURRENT_TASKS=1
export INTERN_MAX_SELF_STARTED_PRS_PER_DAY=3
export INTERN_DAILY_SPEND_CAP_USD=5.00
```

Optional quiet hours use local 24-hour clock values:

```bash
export INTERN_QUIET_HOURS_START=18
export INTERN_QUIET_HOURS_END=8
```

Kill switch:

```bash
export INTERN_PAUSED=1
```

Manual/local merge override:

```bash
export INTERN_MERGE_AUTHORIZED=1
```

Only use `INTERN_MERGE_AUTHORIZED=1` for controlled local testing. In the real
Slack flow, set merge authorization in SDK session state only after a human says
something unambiguous like `merge PR #123`.

## Perseus Setup

Perseus setup is operator-side. Do not have the agent run login commands.

Install and authenticate Perseus according to your internal instructions, then
index every allowlisted repo:

```bash
perseus index /path/to/repo
perseus index --status
```

Re-index after pulls or on a timer so the coder subagent gets fresh search
results. The coder prompt allows read-only Perseus commands such as:

```bash
perseus index --status
perseus query "where is authentication handled?"
perseus open path/to/file.py:42
```

## MCP / Tool Wiring

The core SDK options are built in `intern_bot.agent.create_options()`.

By default:

- The orchestrator can use `Agent` to delegate to subagents.
- The planner has no tools until you provide Linear MCP tool names.
- The coder has repo tools: `Read`, `Write`, `Edit`, `Grep`, `Glob`, `Bash`.
- The shipper has `Bash` by default.
- Bash commands are protected by `intern_bot.merge_guard.block_merges`.

Example:

```python
from intern_bot.agent import create_options, run_turn

options = create_options(
    cwd="/path/to/repo",
    mcp_servers={
        # Add Slack, Linear, and GitHub MCP server configs for your runtime.
    },
    planner_tools=[
        "mcp__linear__list_issues",
        "mcp__linear__get_issue",
        "mcp__linear__update_issue",
    ],
    shipper_tools=[
        "Bash",
        "mcp__github__create_pull_request",
        "mcp__github__request_review",
    ],
)
```

Tool names depend on the MCP servers you choose. Keep tokens scoped and
repo-limited.

## Running Locally

Run one orchestrator turn:

```bash
intern turn "Please triage the easiest intern-safe ticket and implement it."
```

Run one heartbeat tick:

```bash
intern heartbeat-once
```

Run the heartbeat loop:

```bash
intern heartbeat
```

The local CLI prints messages to stdout. A production Slack app should call
`intern_bot.agent.run_turn()` for incoming Slack events and
`intern_bot.heartbeat.heartbeat_loop()` for scheduled ticks, passing its own
Slack `post_message` function.

## Slack Integration Sketch

Your Slack event handler should:

1. Ignore events while `INTERN_PAUSED=1`.
2. Pass the Slack text/thread context into `run_turn()`.
3. Post the returned `TurnResult.text` back to Slack.
4. Record the cost in `InternMemory`.

Minimal shape:

```python
from intern_bot.agent import run_turn
from intern_bot.config import InternConfig
from intern_bot.memory import InternMemory

config = InternConfig.from_env()
memory = InternMemory(config.memory_path)

async def handle_slack_mention(text: str, post_message):
    result = await run_turn(text)
    if result.text.strip():
        await post_message(result.text.strip())
    memory.append_event("slack_turn", text, cost_usd=result.total_cost_usd)
```

## Heartbeat Integration Sketch

```python
from intern_bot.config import InternConfig
from intern_bot.heartbeat import heartbeat_loop
from intern_bot.memory import InternMemory

config = InternConfig.from_env()
memory = InternMemory(config.memory_path)

await heartbeat_loop(
    config=config,
    memory=memory,
    post_message=post_to_slack,
)
```

Heartbeats check pause state, quiet hours, concurrent task caps, daily PR caps,
and daily spend caps before asking the orchestrator to do anything.

## Safety Guardrails

- Prompts forbid merges, branch deletion, force pushes, production changes, and
  high-risk autonomous work.
- `block_merges()` denies `gh pr merge`, `git merge`, and force-push commands at
  the Bash tool layer.
- The planner gets no filesystem tools by default.
- `INTERN_PAUSED=1` stops heartbeat work immediately.
- Randomness only affects harmless heartbeat banter, never code changes or
  ticket edits.
- `InternMemory` records heartbeat/manual activity, PR markers, task markers,
  and cost markers in Markdown.

## Project Layout

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

## Common Commands

```bash
source .venv/bin/activate
pytest
python -m compileall intern_bot tests
intern --help
intern heartbeat-once
```

## Troubleshooting

- `python: command not found`: use `python3` to create the venv, then activate it.
- `intern: command not found`: activate `.venv` or run `.venv/bin/intern`.
- Missing Claude SDK: run `pip install -r requirements.txt && pip install -e .`.
- Heartbeat does nothing: check `INTERN_PAUSED`, quiet hours, `.intern/memory.md`
  daily caps, and `INTERN_DAILY_SPEND_CAP_USD`.
- Merge command denied: expected behavior. Get explicit human approval before
  setting merge authorization.

