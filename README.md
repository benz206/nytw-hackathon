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
export INTERN_CLAUDE_MODEL=sonnet
export INTERN_PERMISSION_MODE=bypassPermissions
export INTERN_GIT_AUTHOR_NAME="bob-the-intern[bot]"
export INTERN_GIT_AUTHOR_EMAIL="291564787+bob-the-intern[bot]@users.noreply.github.com"
export INTERN_TARGET_REPO=/Users/benz/Documents/task-manager
```

`INTERN_CLAUDE_MODEL` is passed through to Claude Code's `--model` option. This
repo defaults to `sonnet`; set it to another Claude Code model alias or full
model ID if you want a different speed/capability tradeoff.

`INTERN_PERMISSION_MODE` is passed through to Claude Code's `--permission-mode`.
The runtime defaults to `bypassPermissions` so Slack/heartbeat turns can edit
files, create branches, push, and open draft PRs without an interactive approval
prompt. The merge guard still blocks merges and force-pushes unless a human
explicitly authorizes them.
`INTERN_GIT_AUTHOR_NAME` and `INTERN_GIT_AUTHOR_EMAIL` are exported into SDK
turns as `GIT_AUTHOR_*` and `GIT_COMMITTER_*`, so commits made by the Intern do
not inherit the operator's global Git identity.

`INTERN_TARGET_REPO` is the code repository the Intern should edit. Keep this
set to the product repo, not this Intern runtime repo.

Optional quiet hours use local 24-hour clock values:

```bash
export INTERN_QUIET_HOURS_START=18
export INTERN_QUIET_HOURS_END=8
```

Linear autonomy policy:

```bash
export LINEAR_MCP_SERVER_NAME=linear
export LINEAR_MCP_URL=https://mcp.linear.app/mcp
export INTERN_LINEAR_TEAM_KEYS=ENG
export INTERN_LINEAR_ALLOWED_STATUSES=Todo,Backlog,Triage
export INTERN_LINEAR_IN_PROGRESS_STATUS="In Progress"
export INTERN_LINEAR_BLOCKED_STATUS=Blocked
export INTERN_LINEAR_DONE_STATUS=Done
export INTERN_LINEAR_MAX_ESTIMATE=2
export INTERN_LINEAR_CANDIDATE_LIMIT=20
export INTERN_LINEAR_RANDOM_TOP_N=3
export INTERN_LINEAR_COMMENT_ON_START=1
export INTERN_LINEAR_COMMENT_ON_PR=1
```

Check the local Linear launcher prerequisites and policy config:

```bash
intern linear check --require-config
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

First sign in at [perseus.computer](https://perseus.computer), open the console,
and use **Set up GitHub access** to authorize the GitHub repos the Intern is
allowed to search. Then install and authenticate Perseus locally, and index every
allowlisted repo:

```bash
curl -fsSL https://perseus.computer/install.sh | sh
perseus login
cd /path/to/repo
perseus doctor
perseus index
perseus index --status
```

Perseus stores its auth token under `~/.config/perseus/`. Keep login as a
human/operator action; the Intern can check status and query indexes, but should
not initiate OAuth in an agent session.
Some Perseus CLI versions do not expose `perseus doctor`; in that case
`perseus index --status` is the important readiness check.

This repo includes an operator preflight:

```bash
intern perseus doctor --cwd /path/to/repo
```

Re-index after pulls or on a timer so the coder subagent gets fresh search
results. The coder prompt allows read-only Perseus commands such as:

```bash
perseus doctor
perseus index --status
perseus query "where is authentication handled?"
perseus query benz206/nytw-hackathon "where is authentication handled?"
perseus open path/to/file.py:42
```

## GitHub PR Setup

The Intern can open PRs through either GitHub MCP tools you pass to
`create_options()` or the GitHub CLI available to the Shipper through Bash. For a
fully local setup, install and authenticate `gh` as the human/operator:

```bash
gh auth login --hostname github.com
gh auth status --hostname github.com
cd /path/to/repo
git remote -v
```

For the GitHub App/bot identity, keep the PEM out of Git and point the runtime at
it from `.env.local`:

```bash
GITHUB_APP_ID=123456
GITHUB_APP_INSTALLATION_ID=987654
GITHUB_APP_PRIVATE_KEY_PATH=/Users/benz/Documents/nytw-hackathon/bob-the-intern.2026-06-07.private-key.pem
```

`GITHUB_APP_ID` is shown on the GitHub App settings page. The installation ID is
visible in the URL when configuring the installed app, or can be discovered from
GitHub's App installations API after authenticating as the app. Keep the key mode
private:

```bash
chmod 600 /Users/benz/Documents/nytw-hackathon/bob-the-intern.2026-06-07.private-key.pem
```

When those three variables are present, the Intern mints a short-lived GitHub App
installation token at the start of each agent turn and exports it to `GH_TOKEN`
and `GITHUB_TOKEN` for that process. Do not paste an installation token into
`.env.local`; let the runtime refresh it. You can test token minting without
printing the token:

```bash
intern github app-token
```

Run the combined repo preflight before letting the Intern ship work:

```bash
intern github doctor --cwd /path/to/repo --with-perseus --require-app
```

That check verifies:

- the directory is a cloned Git repo with an `origin` remote
- `gh` is installed and authenticated for PR creation
- GitHub App env vars are present and the private key exists with `0600`
- the worktree state is visible before the Shipper pushes
- Perseus is installed, authenticated, and has a ready index when
  `--with-perseus` is used

Once this is green, a human can give the Intern a description and guide in Slack
or locally:

```bash
intern turn --cwd /path/to/repo \
  "Implement ENG-123: add a Slack health-check command. Guide: use the existing slack check command patterns, add focused tests, then open a draft PR."
```

The orchestrator should pass the description and guide to the Coder, the Coder
should use Perseus first, create an `intern/...` branch, test, and commit, and
the Shipper should push that branch and open a draft GitHub PR. The Shipper must
never merge.

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

## Local Memory

The Intern keeps a local, gitignored Markdown memory at `.intern/memory.md` by
default. The `## Remembered Notes` section is injected into the agent prompt on
manual, Slack, and heartbeat turns. The `## Activity Log` section records
turn/cost/cap markers and is not treated as durable preference memory.

To teach the Intern something durable, ask it to remember it or edit the
`## Remembered Notes` section directly. Keep notes short and avoid storing
secrets.

## Slack Integration Sketch

For local Slack testing, put Slack credentials in `.env.local`:

```bash
SLACK_APP_ID=...
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...
SLACK_SIGNING_SECRET=...
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_DEFAULT_CHANNEL=C...
SLACK_LOGS_CHANNELID=C0B8XQT76TB
```

`SLACK_SIGNING_SECRET` plus `SLACK_BOT_TOKEN` is enough for Events API handling.
For the easiest local loop, enable Socket Mode in the Slack app and add
`SLACK_APP_TOKEN`.
Set `SLACK_LOGS_CHANNELID` to the team logs channel; Perseus command usage and
capped output snippets from agent turns are posted there as clear fenced
code-block audit notes.

Slack bot token scopes/events used by the Socket Mode listener:

- `chat:write` to post replies and set assistant thread status/typing indicators
- `app_mentions:read` for `app_mention` events
- `im:history` for direct-message `message.im` events
- `mpim:history` for multi-person direct-message `message.mpim` events, if used
- `channels:history` for public-channel thread `message.channels` events
- `groups:history` for private-channel thread `message.groups` events
- `users:write` to call `users.setPresence` as a best-effort presence nudge
- `assistant:write` only if your workspace still requires it for
  `assistant.threads.setStatus`; Slack is moving that method to `chat:write`

The app-level Socket Mode token needs `connections:write`.

For the visible green online dot, open the Slack app settings and enable
**Bot User > Always Show My Bot as Online**. The listener calls
`users.setPresence` on startup and before replies, but Slack's Events API /
Socket Mode bot presence is ultimately controlled by that Bot User setting;
`users.setPresence` cannot force a bot to active.

Check what is ready without printing secrets:

```bash
intern slack check
intern slack check --require-socket-mode
```

Test Slack plumbing without calling the agent:

```bash
intern slack simulate "hello intern" --no-agent
```

Run the local Socket Mode listener:

```bash
intern run
```

While the listener is running, the terminal logs each received Slack message
before the Intern starts working on it. The Intern replies to direct messages,
thread replies, and app mentions; plain channel messages without a thread are
ignored unless they are app mentions.

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
- `InternMemory` stores gitignored remembered notes and records
  heartbeat/manual activity, PR markers, task markers, and cost markers in
  Markdown.

## Project Layout

```text
intern_bot/
  agent.py        Claude Agent SDK options, subagent definitions, run_turn()
  cli.py          local command entry point
  config.py       env-backed runtime configuration
  heartbeat.py    OpenClaw-style heartbeat loop and cap checks
  memory.py       Markdown audit log and daily accounting
  merge_guard.py  hard merge/force-push blocker
  perseus.py      Perseus CLI preflight checks
  slack/          Slack orchestrator prompt, heartbeat prompt, tool defaults
  linear/         Linear planner prompt and MCP tool defaults
  codebase/       Coder prompt and repo/Bash tool defaults
  github/         Shipper prompt and PR tool defaults
tests/
  test_memory.py
  test_merge_guard.py
  test_perseus.py
```

## Common Commands

```bash
source .venv/bin/activate
pytest
python -m compileall intern_bot tests
intern --help
intern run
intern perseus doctor
intern github doctor --with-perseus --require-app
intern slack check
intern heartbeat-once
```

## Troubleshooting

- `python: command not found`: use `python3` to create the venv, then activate it.
- `intern: command not found`: activate `.venv` or run `.venv/bin/intern`.
- Missing Claude SDK: run `pip install -r requirements.txt && pip install -e .`.
- Missing Perseus CLI: run `curl -fsSL https://perseus.computer/install.sh | sh`,
  then `perseus login` and `perseus index` as the operator.
- GitHub PR creation fails: run `intern github doctor --cwd /path/to/repo
  --with-perseus --require-app`, then fix the reported `gh auth`, GitHub App,
  remote, branch, worktree, or Perseus index issue.
- Slack Socket Mode will not start: add `SLACK_APP_TOKEN` from the Slack app's
  Socket Mode app-level token page.
- Heartbeat does nothing: check `INTERN_PAUSED`, quiet hours, `.intern/memory.md`
  daily caps, and `INTERN_DAILY_SPEND_CAP_USD`.
- Merge command denied: expected behavior. Get explicit human approval before
  setting merge authorization.
