# Linear Autonomy Scope

This scope covers the Linear side of the Intern: reading backlog tickets,
choosing safe work, implementing it through the existing coder, and opening
draft GitHub PRs through the shipper. The desired behavior is an always-on bot
that can opportunistically find small tickets, do the work on a branch, and post
links back to Slack and Linear without ever merging or making risky product
decisions.

## Current Repo State

- `intern_bot/linear/prompts.py` already defines a planner subagent with the
  right boundaries: read Linear, triage intern-safe tickets, plan work, update
  ticket status when work starts, and avoid vague or risky tickets.
- `intern_bot/linear/tools.py` is intentionally empty today:
  `DEFAULT_PLANNER_TOOLS = []`. Linear cannot work until MCP tool names are
  discovered and passed into `create_options()`.
- `intern_bot/agent.py` already wires three specialists:
  `planner -> coder -> shipper`.
- `intern_bot/slack/prompts.py` already describes the main flow:
  Planner picks a ticket, Coder implements, Shipper opens a PR.
- `intern_bot/heartbeat.py` already supports periodic autonomous work, quiet
  hours, pause mode, daily spend caps, concurrent task caps, and self-started PR
  caps.
- `intern_bot/memory.py` tracks daily PR count and active tasks, but it does not
  yet track Linear issue IDs, branch names, PR URLs, or ticket lifecycle state
  in a structured way.

## Product Behaviors

### Manual Slack Commands

The Intern should support these natural-language requests in Slack:

- "Find me an easy Linear ticket."
- "Pick a small bug and open a PR."
- "Take ENG-123."
- "Scope ENG-123 but don't code yet."
- "Open a draft PR for ENG-123."
- "What's blocking the intern queue?"

Expected behavior:

1. Orchestrator decides whether this is planning-only or implementation.
2. Planner fetches relevant Linear tickets and returns a structured choice.
3. If coding is requested, Coder creates a branch, implements, tests, and commits.
4. Shipper pushes the branch and opens a draft PR.
5. Orchestrator posts a short Slack update with the Linear ticket and PR link.
6. Planner comments back on the Linear issue with the PR link and status.

### Autonomous Heartbeat

On heartbeat ticks, the Intern should be able to self-start work only when all
guards pass:

- `INTERN_PAUSED != 1`
- outside quiet hours
- under `INTERN_MAX_CONCURRENT_TASKS`
- under `INTERN_MAX_SELF_STARTED_PRS_PER_DAY`
- under `INTERN_DAILY_SPEND_CAP_USD`
- Linear has at least one safe candidate
- repo preflight passes

"Randomly open PRs" should mean weighted opportunistic selection from a safe
candidate set, not picking any ticket blindly. The planner should rank tickets,
then optionally randomize among the top few to avoid always taking the same kind
of work.

Suggested selection model:

1. Fetch candidate tickets from configured Linear teams/views.
2. Exclude blocked, vague, high-priority incidents, infra, auth, migration,
   billing, security, and unclear product work.
3. Score remaining issues.
4. Pick from the top 3 with weighted randomness.
5. Mark the selected ticket as claimed/in-progress before coding.
6. If implementation or PR creation fails, comment on Linear and move the issue
   to a configured blocked/triage state instead of silently retrying forever.

### Ticket Scoring

Start conservative. A ticket is eligible only if it has:

- clear title and description
- explicit acceptance criteria or enough detail to infer a tiny change
- no blocker relations
- no "needs design", "needs product", "security", "infra", "migration", or
  "incident" labels
- estimate at or below the configured maximum
- status in a configured allowed set, such as Todo, Backlog, or Triage
- team/project allowlisted for the Intern

Suggested score components:

- lower estimate is better
- bugfix/docs/test tickets are safer than new features
- recently updated comments may indicate fresh context
- assigned-to-Intern tickets beat unassigned tickets
- linked files, stack traces, or repro steps increase confidence
- stale tickets are okay only when the description is still clear

## Linear Integration

Use Linear's official remote MCP server when possible. The current Linear docs
describe a hosted MCP endpoint at:

```text
https://mcp.linear.app/mcp
```

The server supports finding, creating, and updating Linear objects such as
issues, projects, and comments. Exact tool names are runtime-dependent, so this
repo should not hardcode guessed names as defaults until the MCP server is
configured and inspected.

### Required Planner Capabilities

Minimum required Linear tools:

- list/search issues
- get one issue by identifier
- list comments or history for an issue
- create issue comments
- update issue status
- update issue labels or assignee only when explicitly configured

Useful later:

- list teams
- list workflow states per team
- list labels
- list cycles/projects
- read issue relations
- create/update issues for human-requested ticket creation

### Configuration

Add explicit config rather than baking workspace assumptions into prompts:

```bash
LINEAR_MCP_SERVER_NAME=linear
INTERN_LINEAR_TEAM_KEYS=ENG,APP
INTERN_LINEAR_ALLOWED_STATUSES=Todo,Backlog,Triage
INTERN_LINEAR_IN_PROGRESS_STATUS="In Progress"
INTERN_LINEAR_BLOCKED_STATUS=Blocked
INTERN_LINEAR_DONE_STATUS=Done
INTERN_LINEAR_MAX_ESTIMATE=2
INTERN_LINEAR_CANDIDATE_LIMIT=20
INTERN_LINEAR_RANDOM_TOP_N=3
INTERN_LINEAR_COMMENT_ON_START=1
INTERN_LINEAR_COMMENT_ON_PR=1
```

Keep any Linear API key or OAuth token outside the repo. If an API key is used,
prefer a restricted key when the bot only needs read access for planning.

## GitHub and PR Linking

The shipper should create draft PRs by default. PR title/body/branch should all
include the Linear issue identifier, for example:

- branch: `intern/ENG-123-fix-health-check`
- commit: `Fix health check command (ENG-123)`
- PR title: `ENG-123 Fix health check command`
- PR body includes `Linear: ENG-123` and a direct Linear URL if available

If the workspace has Linear's GitHub integration configured, including the issue
identifier in branch names, commits, and PR text helps Linear associate the PR
with the issue. The bot should still add an explicit Linear comment containing
the PR URL so the link is visible even if automation is misconfigured.

## State Machine

Add first-class task state to memory or a small local JSONL file:

```text
candidate -> claimed -> coding -> pr_opened -> waiting_review
                         |            |
                         v            v
                      blocked      followup_needed
```

Required fields:

- Linear issue ID and URL
- selected timestamp
- selection reason
- branch name
- PR URL
- current state
- last heartbeat/action timestamp
- failure summary, if any
- cost and test status

This prevents duplicate self-starts, makes retries safer, and gives Slack
answers like "I'm already working on ENG-123."

## Safety Rules

Hard blocks:

- never merge
- never force-push
- never delete branches
- never change Linear priority or due date without explicit human instruction
- never self-start security, auth, billing, data migration, infra, incident, or
  production work
- never take a ticket with unclear acceptance criteria
- never start more than one autonomous task unless configured

Soft stops:

- failing tests after a reasonable fix attempt
- missing GitHub auth
- dirty worktree on the target repo
- Linear status update failed after claiming
- ticket changed materially while the bot was working
- PR already exists for the ticket

When stopped, the bot should post a concise Slack update and comment on Linear
with what happened.

## Implementation Plan

### Phase 1: Linear Tool Wiring

- Add `LinearConfig` to `intern_bot/config.py`.
- Add env parsing and tests for Linear allowlists, statuses, estimates, and
  randomization settings.
- Add a CLI doctor command, `intern linear doctor`, that verifies the configured
  MCP server/tool names are available.
- Update README MCP setup with Linear's remote MCP endpoint and local examples.
- Keep `DEFAULT_PLANNER_TOOLS` empty unless tool discovery becomes deterministic
  in this runtime.

### Phase 2: Planner Contract

- Tighten `PLANNER_PROMPT` so it returns machine-readable fields:
  `chosen_ticket`, `ticket_url`, `confidence`, `risk_flags`, `status_changed`,
  `plan`, and `open_questions`.
- Add explicit scoring rules and exclusion criteria to the planner prompt.
- Add orchestration guidance for when to stop versus chain planner -> coder ->
  shipper.
- Add tests that `create_options()` passes supplied planner tools through to the
  planner agent.

### Phase 3: Task Ledger

- Extend `InternMemory` or add `intern_bot/task_ledger.py`.
- Track Linear issue ID, branch, PR URL, task state, and failure reason.
- Replace heartbeat's current text sniffing for PR creation with structured
  ledger updates where possible.
- Add tests for daily caps, duplicate ticket prevention, and failed task cleanup.

### Phase 4: Autonomous Ticket Selection

- Implement heartbeat prompt/context that includes current caps, active tasks,
  and configured Linear selection policy.
- Ask planner for candidates first, then choose from the top safe candidates.
- Mark the selected issue as in progress before coding.
- On no eligible work, return `HEARTBEAT_OK`.
- On ambiguity, ask Slack instead of starting work.

### Phase 5: Coding and Shipping Loop

- Ensure coder branch names always include the Linear issue ID.
- Ensure shipper PR bodies always include ticket link, summary, tests, and review
  notes.
- After PR creation, have planner comment on Linear with PR URL and move status
  only if configured.
- Add detection for existing PRs/branches for the same ticket.

### Phase 6: Review Follow-Up

- On heartbeat, inspect open Intern PRs for failing CI or review comments.
- Delegate fixes to coder on the existing branch.
- Push follow-up commits through shipper.
- Comment back on Linear when follow-up work is ready.
- Never resolve reviews or merge automatically.

## Test Plan

Unit tests:

- Linear config env parsing
- planner tool passthrough in `create_options()`
- eligibility scoring/exclusion if scoring is implemented in Python
- task ledger state transitions
- heartbeat cap behavior with active Linear tasks
- duplicate ticket suppression
- PR URL extraction and ledger recording

Integration/dry-run tests:

- `intern linear doctor`
- `intern turn "scope ENG-123 but don't code"`
- `intern turn "take ENG-123 and open a draft PR"` with fake runner/tool output
- `intern heartbeat-once` with fake Linear candidates and fake shipper result

Manual acceptance:

- Bot can list safe candidate tickets from Linear.
- Bot refuses a vague/high-risk ticket and explains why.
- Bot self-starts one safe ticket under caps.
- Bot opens a draft PR with the Linear ID in branch/title/body.
- Bot comments on Linear with the PR link.
- Bot does not start a second task when one is active.
- Bot stops cleanly on failing tests and surfaces the blocker.

## Open Decisions

- Which Linear teams/views are allowlisted for autonomous work?
- Should the bot assign tickets to itself, or only move status/comment?
- What exact statuses map to `claimed`, `in progress`, `blocked`, and `done` in
  this workspace?
- What label should mark bot-safe tickets, if any?
- Should autonomous PRs be draft-only forever, or can humans request ready PRs?
- Should random selection be enabled by default or only after a stable dry-run
  period?
- Where should long-lived structured task state live: markdown memory, JSONL,
  SQLite, or Linear comments only?

## References

- Linear MCP docs: https://linear.app/docs/mcp/
- Linear GitHub integration docs: https://linear.app/docs/github-integration
- Linear Reviews/Diffs docs: https://linear.app/docs/diffs
