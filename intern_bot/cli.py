"""Local command-line entry point for the Intern."""

from __future__ import annotations

import argparse
import asyncio

from .agent import run_turn
from .config import InternConfig
from .heartbeat import heartbeat_loop, heartbeat_once
from .memory import InternMemory


async def _print_message(text: str) -> None:
    print(text)


async def _run(args: argparse.Namespace) -> None:
    config = InternConfig.from_env()
    memory = InternMemory(config.memory_path)
    memory.ensure_exists()

    if args.command == "turn":
        result = await run_turn(args.prompt, cwd=args.cwd)
        if result.text.strip():
            print(result.text.strip())
        memory.append_event("manual_turn", args.prompt, cost_usd=result.total_cost_usd)
        return

    if args.command == "heartbeat-once":
        await heartbeat_once(config=config, memory=memory, post_message=_print_message)
        return

    if args.command == "heartbeat":
        await heartbeat_loop(config=config, memory=memory, post_message=_print_message)
        return

    raise ValueError(f"Unknown command: {args.command}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Intern agent.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    turn = subparsers.add_parser("turn", help="Run one orchestrator turn.")
    turn.add_argument("prompt", help="Prompt to send to the orchestrator.")
    turn.add_argument("--cwd", help="Working directory for the SDK session.")

    subparsers.add_parser("heartbeat-once", help="Run one heartbeat tick.")
    subparsers.add_parser("heartbeat", help="Run the heartbeat loop forever.")
    return parser


def main() -> None:
    asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    main()

