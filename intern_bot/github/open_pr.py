"""Open draft PRs as the GitHub App bot without personal-token fallback."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
from typing import Sequence

from .app_auth import ensure_github_app_token_from_env


@dataclass(frozen=True)
class OpenPrResult:
    url: str
    branch: str
    base: str
    title: str
    preview_url: str | None = None


def open_pull_request(
    *,
    cwd: str | Path,
    title: str,
    summary: str,
    tests: str,
    ticket: str | None = None,
    notes: str | None = None,
    preview_url: str | None = None,
    base: str | None = None,
    branch: str | None = None,
    remote: str = "origin",
    draft: bool = True,
) -> OpenPrResult:
    """Push the current branch and open a PR using only the GitHub App token."""
    repo = Path(cwd).expanduser()
    token = ensure_github_app_token_from_env(force=True)
    if token is None:
        raise RuntimeError("GitHub App env vars are missing; cannot open an Intern-authored PR.")

    env = _github_app_env()
    current_branch = branch or _git(["branch", "--show-current"], cwd=repo, env=env).strip()
    if not current_branch:
        raise RuntimeError("Current checkout is detached; check out a named feature branch before opening a PR.")
    if current_branch in {"main", "master"}:
        raise RuntimeError(f"Refusing to open a PR directly from {current_branch}. Create a feature branch first.")

    base_branch = base or _default_base_branch(cwd=repo, remote=remote, env=env)
    _git(["push", "-u", remote, current_branch], cwd=repo, env=env)

    body = build_intern_pr_body(
        summary=summary,
        tests=tests,
        ticket=ticket,
        notes=notes,
        preview_url=preview_url,
    )
    command = [
        "gh",
        "pr",
        "create",
        "--base",
        base_branch,
        "--head",
        current_branch,
        "--title",
        title,
        "--body",
        body,
    ]
    if draft:
        command.insert(3, "--draft")

    url = _run(command, cwd=repo, env=env).strip()
    return OpenPrResult(
        url=url,
        branch=current_branch,
        base=base_branch,
        title=title,
        preview_url=preview_url,
    )


def build_intern_pr_body(
    *,
    summary: str,
    tests: str,
    ticket: str | None = None,
    notes: str | None = None,
    preview_url: str | None = None,
) -> str:
    """Build a short, intern-coded PR body."""
    lines = []
    if ticket:
        lines.append(f"tiny PR for {ticket}.")
        lines.append("")
    lines.extend(
        [
            "changed:",
            f"- {_clean_line(summary)}",
            "",
            "checked:",
            f"- {_clean_line(tests)}",
        ]
    )
    if notes:
        lines.extend(["", "notes:", f"- {_clean_line(notes)}"])
    if preview_url:
        lines.extend(["", "preview:", f"- {_clean_line(preview_url)} (live ~1 min after CI)"])
    lines.extend(["", "review pls, I think this is the small version."])
    return "\n".join(lines)


def _default_base_branch(*, cwd: Path, remote: str, env: dict[str, str]) -> str:
    try:
        value = _git(["symbolic-ref", f"refs/remotes/{remote}/HEAD", "--short"], cwd=cwd, env=env).strip()
    except RuntimeError:
        return "main"
    prefix = f"{remote}/"
    return value[len(prefix) :] if value.startswith(prefix) else value or "main"


def _github_app_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Do not let gh fall back to a human keychain token if the app token cannot act.
    env.pop("GH_CONFIG_DIR", None)
    return env


def _git(args: Sequence[str], *, cwd: Path, env: dict[str, str]) -> str:
    return _run(["git", *args], cwd=cwd, env=env)


def _run(command: Sequence[str], *, cwd: Path, env: dict[str, str]) -> str:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return completed.stdout
    output = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
    hint = _failure_hint(command, output)
    raise RuntimeError(f"`{' '.join(command)}` failed ({completed.returncode}): {_redact(output)}{hint}")


def _clean_line(value: str) -> str:
    cleaned = " ".join(value.split())
    return cleaned or "not sure, but I did the tiny check I could"


def _redact(text: str) -> str:
    return text.replace(os.getenv("GH_TOKEN", ""), "***") if os.getenv("GH_TOKEN") else text


def _failure_hint(command: Sequence[str], output: str) -> str:
    if command[:2] == ["git", "push"] and (
        "denied to bob-the-intern[bot]" in output or "requested URL returned error: 403" in output
    ):
        return (
            "\nnext: give the GitHub App Contents: Read and write access on this repo, "
            "then reinstall/update the app installation. Do not fall back to a personal token."
        )
    if command[:3] == ["gh", "pr", "create"] and ("403" in output or "Resource not accessible" in output):
        return (
            "\nnext: give the GitHub App Pull requests: Read and write access on this repo, "
            "then reinstall/update the app installation. Do not fall back to a personal token."
        )
    return ""
