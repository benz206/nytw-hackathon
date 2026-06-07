"""Local command-line entry point for the Intern."""

from __future__ import annotations

import argparse
import asyncio

from .agent import run_turn
from .config import InternConfig
from .env import load_env_file
from .github import (
    check_github_app_config,
    check_github_repo,
    ensure_github_app_token_from_env,
    format_github_app_report,
    format_github_report,
    open_pull_request,
)
from .heartbeat import heartbeat_loop, heartbeat_once
from .linear import (
    check_linear_setup,
    discover_linear_mcp_tools,
    format_linear_report,
    format_linear_tools_report,
    write_linear_planner_tools_env,
)
from .memory import InternMemory
from .perseus import check_perseus, format_perseus_report
from .slack.app import (
    PrintPoster,
    SlackConfig,
    SlackEnvCheck,
    SlackWebPoster,
    handle_slack_text,
    run_socket_mode,
)


async def _print_message(text: str) -> None:
    print(text)


async def _run(args: argparse.Namespace) -> None:
    load_env_file(".env.local")

    if args.command == "turn":
        config = InternConfig.from_env()
        memory = InternMemory(config.memory_path)
        memory.ensure_exists()
        result = await run_turn(
            args.prompt,
            cwd=_configured_cwd(args.cwd, config),
            model=config.claude_model,
            permission_mode=config.permission_mode,
            git_author_name=config.git_author_name,
            git_author_email=config.git_author_email,
            logger=print,
        )
        if result.text.strip():
            print(result.text.strip())
        memory.append_event("manual_turn", args.prompt, cost_usd=result.total_cost_usd)
        return

    if args.command == "heartbeat-once":
        config = InternConfig.from_env()
        memory = InternMemory(config.memory_path)
        memory.ensure_exists()
        await heartbeat_once(config=config, memory=memory, post_message=_print_message)
        return

    if args.command == "heartbeat":
        config = InternConfig.from_env()
        memory = InternMemory(config.memory_path)
        memory.ensure_exists()
        await heartbeat_loop(config=config, memory=memory, post_message=_print_message)
        return

    if args.command == "run":
        config = SlackConfig.from_env(env_file=args.env_file)
        run_socket_mode(config)
        return

    if args.command == "perseus" and args.perseus_command == "doctor":
        report = check_perseus(
            cwd=_configured_cwd(args.cwd, InternConfig.from_env()),
            run_doctor=not args.skip_cli_doctor,
            run_index_status=not args.skip_index_status,
            run_query_probe=not args.skip_query_probe,
        )
        print(format_perseus_report(report))
        if not report.ok:
            raise SystemExit(1)
        return

    if args.command == "github" and args.github_command == "doctor":
        if args.require_app:
            ensure_github_app_token_from_env()
        report = check_github_repo(
            cwd=_configured_cwd(args.cwd, InternConfig.from_env()),
            remote=args.remote,
            hostname=args.hostname,
            run_auth_status=not args.skip_auth_status,
        )
        print(format_github_report(report))
        ok = report.ok
        if args.with_perseus:
            perseus_report = check_perseus(
                cwd=_configured_cwd(args.cwd, InternConfig.from_env()),
                run_query_probe=True,
            )
            print()
            print(format_perseus_report(perseus_report))
            ok = ok and perseus_report.ok
        if args.require_app:
            app_report = check_github_app_config()
            print()
            print(format_github_app_report(app_report))
            ok = ok and app_report.ok
        if not ok:
            raise SystemExit(1)
        return

    if args.command == "github" and args.github_command == "app-token":
        token = ensure_github_app_token_from_env(force=True)
        if token is None:
            print("GitHub App env vars are missing; set GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, and GITHUB_APP_PRIVATE_KEY_PATH.")
            raise SystemExit(1)
        print("GitHub App installation token: minted")
        if token.expires_at:
            print(f"expires_at: {token.expires_at}")
        return

    if args.command == "github" and args.github_command == "open-pr":
        result = open_pull_request(
            cwd=_configured_cwd(args.cwd, InternConfig.from_env()),
            title=args.title,
            summary=args.summary,
            tests=args.tests,
            ticket=args.ticket,
            notes=args.notes,
            base=args.base,
            branch=args.branch,
            required_branch_prefix=args.required_branch_prefix,
            draft=not args.ready,
        )
        print(f"pr_url: {result.url}")
        print(f"branch: {result.branch}")
        print(f"base: {result.base}")
        print(f"title: {result.title}")
        return

    if args.command == "linear" and args.linear_command == "check":
        report = check_linear_setup()
        print(format_linear_report(report))
        if args.require_config and not report.ok:
            raise SystemExit(1)
        return

    if args.command == "linear" and args.linear_command == "tools":
        report = await discover_linear_mcp_tools(timeout_seconds=args.timeout_seconds)
        print(format_linear_tools_report(report))
        if args.write_env and report.ok:
            tools = report.recommended_planner_tools
            if not tools:
                print("No recommended planner-safe Linear tools matched; not writing env.")
                raise SystemExit(1)
            write_linear_planner_tools_env(tools, env_file=args.env_file)
            print(f"wrote INTERN_LINEAR_PLANNER_TOOLS to {args.env_file}")
        if args.require_tools and not report.ok:
            raise SystemExit(1)
        return

    if args.command == "slack" and args.slack_command == "check":
        config = SlackConfig.from_env(env_file=args.env_file)
        print("\n".join(SlackEnvCheck(config).lines()))
        if args.require_socket_mode and config.missing_for_socket_mode():
            raise SystemExit(1)
        if args.require_events_api and config.missing_for_events_api():
            raise SystemExit(1)
        return

    if args.command == "slack" and args.slack_command == "simulate":
        config = SlackConfig.from_env(env_file=args.env_file)
        runtime_config = InternConfig.from_env()
        memory = InternMemory(runtime_config.memory_path)
        memory.ensure_exists()
        poster = (
            PrintPoster()
            if args.dry_run
            else SlackWebPoster(_require(config.bot_token, "SLACK_BOT_TOKEN"))
        )

        async def runner(prompt: str):
            if not args.no_agent:
                return await run_turn(
                    prompt,
                    cwd=_configured_cwd(args.cwd, runtime_config),
                    model=runtime_config.claude_model,
                    permission_mode=runtime_config.permission_mode,
                    git_author_name=runtime_config.git_author_name,
                    git_author_email=runtime_config.git_author_email,
                    logger=print,
                )
            from .agent import TurnResult

            return TurnResult(text=f"Slack plumbing OK. Received:\n{prompt}")

        await handle_slack_text(
            args.text,
            channel=args.channel or config.default_channel or "local-test",
            user=args.user,
            thread_ts=args.thread_ts,
            poster=poster,
            runner=runner,
            memory=memory,
            perseus_logs_channel_id=config.logs_channel_id,
        )
        return

    if args.command == "slack" and args.slack_command == "socket":
        config = SlackConfig.from_env(env_file=args.env_file)
        run_socket_mode(config)
        return

    raise ValueError(f"Unknown command: {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Intern agent.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    turn = subparsers.add_parser("turn", help="Run one orchestrator turn.")
    turn.add_argument("prompt", help="Prompt to send to the orchestrator.")
    turn.add_argument("--cwd", help="Working directory for the SDK session.")

    run = subparsers.add_parser("run", help="Run the Slack Socket Mode bot.")
    run.add_argument("--env-file", default=".env.local", help="Env file to load.")

    subparsers.add_parser("heartbeat-once", help="Run one heartbeat tick.")
    subparsers.add_parser("heartbeat", help="Run the heartbeat loop forever.")

    linear = subparsers.add_parser("linear", help="Linear integration helpers.")
    linear_subparsers = linear.add_subparsers(dest="linear_command", required=True)
    linear_check = linear_subparsers.add_parser("check", help="Check Linear MCP and bot policy config.")
    linear_check.add_argument(
        "--require-config",
        action="store_true",
        help="Exit nonzero unless local MCP prerequisites and Linear team allowlist are configured.",
    )
    linear_tools = linear_subparsers.add_parser(
        "tools",
        help="Connect to Linear MCP and print the tool names exposed to the SDK.",
    )
    linear_tools.add_argument(
        "--require-tools",
        action="store_true",
        help="Exit nonzero unless Linear MCP is connected and returns tools.",
    )
    linear_tools.add_argument(
        "--write-env",
        action="store_true",
        help="Write discovered tools to INTERN_LINEAR_PLANNER_TOOLS in the env file.",
    )
    linear_tools.add_argument("--env-file", default=".env.local", help="Env file to update with --write-env.")
    linear_tools.add_argument(
        "--timeout-seconds",
        type=float,
        default=60.0,
        help="How long to wait for the Linear MCP server to leave pending status.",
    )

    perseus = subparsers.add_parser("perseus", help="Perseus integration helpers.")
    perseus_subparsers = perseus.add_subparsers(dest="perseus_command", required=True)
    doctor = perseus_subparsers.add_parser("doctor", help="Check local Perseus setup.")
    doctor.add_argument("--cwd", help="Repo directory to check. Defaults to the current directory.")
    doctor.add_argument(
        "--skip-cli-doctor",
        action="store_true",
        help="Skip `perseus doctor` and only check version/index status.",
    )
    doctor.add_argument(
        "--skip-index-status",
        action="store_true",
        help="Skip `perseus index --status`.",
    )
    doctor.add_argument(
        "--skip-query-probe",
        action="store_true",
        help="Skip the read-only `perseus query` probe used when index status is not ready.",
    )

    github = subparsers.add_parser("github", help="GitHub repo integration helpers.")
    github_subparsers = github.add_subparsers(dest="github_command", required=True)
    github_doctor = github_subparsers.add_parser(
        "doctor",
        help="Check local GitHub repo, gh auth, and optional Perseus setup.",
    )
    github_doctor.add_argument("--cwd", help="Repo directory to check. Defaults to the current directory.")
    github_doctor.add_argument("--remote", default="origin", help="Git remote used for PR branches.")
    github_doctor.add_argument("--hostname", default="github.com", help="GitHub hostname for `gh auth status`.")
    github_doctor.add_argument(
        "--skip-auth-status",
        action="store_true",
        help="Skip `gh auth status` for offline checks.",
    )
    github_doctor.add_argument(
        "--with-perseus",
        action="store_true",
        help="Also run the Perseus doctor for this repo.",
    )
    github_doctor.add_argument(
        "--require-app",
        action="store_true",
        help="Also require GitHub App env vars and a private 0600 PEM key.",
    )
    github_app_token = github_subparsers.add_parser(
        "app-token",
        help="Mint a GitHub App installation token and export it for this process.",
    )
    github_app_token.set_defaults(github_command="app-token")
    github_open_pr = github_subparsers.add_parser(
        "open-pr",
        help="Push the current feature branch and open an Intern-authored draft PR.",
    )
    github_open_pr.add_argument("--cwd", help="Repo directory. Defaults to INTERN_TARGET_REPO or cwd.")
    github_open_pr.add_argument("--title", required=True, help="PR title.")
    github_open_pr.add_argument("--summary", required=True, help="Short description of the code change.")
    github_open_pr.add_argument("--tests", required=True, help="Short test/check result.")
    github_open_pr.add_argument("--ticket", help="Linked ticket ID, e.g. TOT-12.")
    github_open_pr.add_argument("--notes", help="Short reviewer note.")
    github_open_pr.add_argument("--base", help="Base branch. Defaults to origin/HEAD or main.")
    github_open_pr.add_argument("--branch", help="Head branch. Defaults to current branch.")
    github_open_pr.add_argument(
        "--required-branch-prefix",
        default="intern/",
        help="Require the head branch to start with this prefix. Defaults to intern/.",
    )
    github_open_pr.add_argument("--ready", action="store_true", help="Open a ready PR instead of a draft PR.")
    github_open_pr.set_defaults(github_command="open-pr")

    slack = subparsers.add_parser("slack", help="Slack integration helpers.")
    slack_subparsers = slack.add_subparsers(dest="slack_command", required=True)

    slack_check = slack_subparsers.add_parser("check", help="Check Slack env configuration.")
    slack_check.add_argument("--env-file", default=".env.local", help="Env file to load.")
    slack_check.add_argument(
        "--require-events-api",
        action="store_true",
        help="Exit nonzero unless Events API env vars are ready.",
    )
    slack_check.add_argument(
        "--require-socket-mode",
        action="store_true",
        help="Exit nonzero unless Socket Mode env vars are ready.",
    )

    simulate = slack_subparsers.add_parser("simulate", help="Simulate a Slack mention locally.")
    simulate.add_argument("text", help="Slack message text to send to the Intern.")
    simulate.add_argument("--env-file", default=".env.local", help="Env file to load.")
    simulate.add_argument("--cwd", help="Working directory for the SDK session.")
    simulate.add_argument("--channel", help="Slack channel ID/name for the simulated event.")
    simulate.add_argument("--user", help="Slack user ID/name for the simulated event.")
    simulate.add_argument("--thread-ts", help="Slack thread timestamp for the simulated event.")
    simulate.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print the Slack reply instead of posting to Slack.",
    )
    simulate.add_argument(
        "--post",
        dest="dry_run",
        action="store_false",
        help="Post the reply to Slack using SLACK_BOT_TOKEN.",
    )
    simulate.add_argument(
        "--no-agent",
        action="store_true",
        help="Test Slack plumbing without invoking the Claude Agent SDK.",
    )

    socket = slack_subparsers.add_parser("socket", help="Run Slack Socket Mode app.")
    socket.add_argument("--env-file", default=".env.local", help="Env file to load.")
    return parser


def main() -> None:
    asyncio.run(_run(build_parser().parse_args()))


def _require(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _configured_cwd(explicit_cwd: str | None, config: InternConfig) -> str | None:
    if explicit_cwd:
        return explicit_cwd
    if config.target_repo_path is None:
        return None
    return str(config.target_repo_path)


if __name__ == "__main__":
    main()
