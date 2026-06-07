"""GitHub repository and PR-creation preflight helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
from typing import Sequence


GH_INSTALL_URL = "https://cli.github.com/"
DEFAULT_REMOTE = "origin"
DEFAULT_HOSTNAME = "github.com"
GITHUB_APP_ID_ENV = "GITHUB_APP_ID"
GITHUB_APP_INSTALLATION_ID_ENV = "GITHUB_APP_INSTALLATION_ID"
GITHUB_APP_PRIVATE_KEY_PATH_ENV = "GITHUB_APP_PRIVATE_KEY_PATH"


@dataclass(frozen=True)
class GitHubCommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout.strip(), self.stderr.strip()) if part)


@dataclass(frozen=True)
class GitHubRepoReport:
    git_executable: str | None
    gh_executable: str | None
    remote: str = DEFAULT_REMOTE
    hostname: str = DEFAULT_HOSTNAME
    repo_root: GitHubCommandResult | None = None
    branch: GitHubCommandResult | None = None
    default_branch: GitHubCommandResult | None = None
    remote_url: GitHubCommandResult | None = None
    worktree_status: GitHubCommandResult | None = None
    gh_version: GitHubCommandResult | None = None
    gh_auth_status: GitHubCommandResult | None = None

    @property
    def ok(self) -> bool:
        required = [
            self.repo_root,
            self.branch,
            self.remote_url,
            self.worktree_status,
            self.gh_version,
            self.gh_auth_status,
        ]
        return (
            self.git_executable is not None
            and self.gh_executable is not None
            and all(check is not None and check.ok for check in required)
            and self.worktree_clean
        )

    @property
    def worktree_clean(self) -> bool:
        return self.worktree_status is not None and self.worktree_status.ok and not self.worktree_status.stdout.strip()


@dataclass(frozen=True)
class GitHubAppConfigReport:
    app_id: str | None
    installation_id: str | None
    private_key_path: Path | None
    private_key_exists: bool = False
    private_key_mode: int | None = None

    @property
    def private_key_is_private(self) -> bool:
        return self.private_key_mode is not None and self.private_key_mode & 0o077 == 0

    @property
    def ok(self) -> bool:
        return (
            bool(self.app_id)
            and bool(self.installation_id)
            and self.private_key_path is not None
            and self.private_key_exists
            and self.private_key_is_private
        )


def check_github_repo(
    *,
    cwd: str | Path | None = None,
    git_executable: str | None = None,
    gh_executable: str | None = None,
    remote: str = DEFAULT_REMOTE,
    hostname: str = DEFAULT_HOSTNAME,
    run_auth_status: bool = True,
    timeout_seconds: int = 30,
) -> GitHubRepoReport:
    """Check whether a repo can support branch pushes and PR creation."""
    found_git = git_executable or shutil.which("git")
    found_gh = gh_executable or shutil.which("gh")

    if found_git is None:
        return GitHubRepoReport(
            git_executable=None,
            gh_executable=found_gh,
            remote=remote,
            hostname=hostname,
        )

    repo_root = _run((found_git, "rev-parse", "--show-toplevel"), cwd=cwd, timeout_seconds=timeout_seconds)
    branch = _run((found_git, "branch", "--show-current"), cwd=cwd, timeout_seconds=timeout_seconds)
    default_branch = _run(
        (found_git, "symbolic-ref", f"refs/remotes/{remote}/HEAD", "--short"),
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
    remote_url = _run((found_git, "remote", "get-url", remote), cwd=cwd, timeout_seconds=timeout_seconds)
    worktree_status = _run((found_git, "status", "--short"), cwd=cwd, timeout_seconds=timeout_seconds)

    gh_version = None
    gh_auth_status = None
    if found_gh is not None:
        gh_version = _run((found_gh, "--version"), cwd=cwd, timeout_seconds=timeout_seconds)
        gh_auth_status = (
            _run((found_gh, "auth", "status", "--hostname", hostname), cwd=cwd, timeout_seconds=timeout_seconds)
            if run_auth_status
            else GitHubCommandResult((found_gh, "auth", "status", "--hostname", hostname), 0)
        )

    return GitHubRepoReport(
        git_executable=found_git,
        gh_executable=found_gh,
        remote=remote,
        hostname=hostname,
        repo_root=repo_root,
        branch=branch,
        default_branch=default_branch,
        remote_url=remote_url,
        worktree_status=worktree_status,
        gh_version=gh_version,
        gh_auth_status=gh_auth_status,
    )


def check_github_app_config(env: dict[str, str] | None = None) -> GitHubAppConfigReport:
    """Check GitHub App env vars and private-key file permissions."""
    values = os.environ if env is None else env
    key_path_value = values.get(GITHUB_APP_PRIVATE_KEY_PATH_ENV)
    key_path = Path(key_path_value).expanduser() if key_path_value else None
    key_exists = False
    key_mode = None
    if key_path is not None:
        try:
            stat_result = key_path.stat()
        except OSError:
            pass
        else:
            key_exists = True
            key_mode = stat_result.st_mode & 0o777

    return GitHubAppConfigReport(
        app_id=values.get(GITHUB_APP_ID_ENV),
        installation_id=values.get(GITHUB_APP_INSTALLATION_ID_ENV),
        private_key_path=key_path,
        private_key_exists=key_exists,
        private_key_mode=key_mode,
    )


def format_github_report(report: GitHubRepoReport) -> str:
    """Render a human-readable GitHub repo preflight report."""
    lines: list[str] = ["GitHub repo preflight"]

    if report.git_executable is None:
        lines.extend(["git: missing", "next: install Git and clone the target repository."])
        if report.gh_executable is None:
            lines.extend(["gh: missing", f"install: {GH_INSTALL_URL}"])
        return "\n".join(lines)

    lines.append(f"git: {report.git_executable}")
    _append_result(lines, "repo", report.repo_root)
    _append_result(lines, "branch", report.branch)
    _append_result(lines, "default_branch", report.default_branch, optional=True)
    _append_result(lines, f"remote {report.remote}", report.remote_url)
    _append_worktree(lines, report)

    if report.gh_executable is None:
        lines.extend(["gh: missing", f"install: {GH_INSTALL_URL}"])
    else:
        lines.append(f"gh: {report.gh_executable}")
        _append_result(lines, "gh_version", report.gh_version)
        _append_result(lines, "gh_auth", report.gh_auth_status)

    lines.extend(_next_steps(report))
    return "\n".join(lines)


def format_github_app_report(report: GitHubAppConfigReport) -> str:
    """Render a human-readable GitHub App credential preflight report."""
    lines: list[str] = ["GitHub App credential preflight"]
    lines.append(f"{GITHUB_APP_ID_ENV}: {'set' if report.app_id else 'missing'}")
    lines.append(
        f"{GITHUB_APP_INSTALLATION_ID_ENV}: {'set' if report.installation_id else 'missing'}"
    )
    if report.private_key_path is None:
        lines.append(f"{GITHUB_APP_PRIVATE_KEY_PATH_ENV}: missing")
    else:
        lines.append(f"{GITHUB_APP_PRIVATE_KEY_PATH_ENV}: {report.private_key_path}")
        lines.append(f"private_key: {'found' if report.private_key_exists else 'missing'}")
        if report.private_key_mode is not None:
            lines.append(f"private_key_mode: {oct(report.private_key_mode)}")

    if not report.ok:
        lines.extend(_github_app_next_steps(report))
    return "\n".join(lines)


def _append_result(
    lines: list[str],
    label: str,
    result: GitHubCommandResult | None,
    *,
    optional: bool = False,
) -> None:
    if result is None:
        lines.append(f"{label}: skipped" if optional else f"{label}: missing")
        return
    status = "ok" if result.ok else f"failed ({result.returncode})"
    output = result.output
    if result.ok and output:
        first_line = output.splitlines()[0]
        lines.append(f"{label}: {first_line}")
        return
    lines.append(f"{label}: {status}")
    if output:
        lines.append(_indent(_truncate(output)))


def _append_worktree(lines: list[str], report: GitHubRepoReport) -> None:
    result = report.worktree_status
    if result is None:
        lines.append("worktree: missing")
        return
    if not result.ok:
        lines.append(f"worktree: failed ({result.returncode})")
        if result.output:
            lines.append(_indent(_truncate(result.output)))
        return
    if report.worktree_clean:
        lines.append("worktree: clean")
    else:
        lines.append("worktree: dirty")
        lines.append(_indent(_truncate(result.stdout)))


def _next_steps(report: GitHubRepoReport) -> list[str]:
    steps: list[str] = []
    if report.repo_root is not None and not report.repo_root.ok:
        steps.append("next: run this from a cloned Git repository or pass `--cwd /path/to/repo`.")
    if report.remote_url is not None and not report.remote_url.ok:
        steps.append(f"next: add a `{report.remote}` remote that points at the GitHub repository.")
    if report.gh_executable is None:
        steps.append("next: install GitHub CLI or wire GitHub MCP tools into `create_options()`.")
    elif report.gh_auth_status is not None and not report.gh_auth_status.ok:
        steps.append(f"next: run `gh auth login --hostname {report.hostname}` with repo-scoped access.")
    if report.branch is not None and report.branch.ok and not report.branch.stdout.strip():
        steps.append("next: check out a named branch before asking the Intern to open a PR.")
    if not report.worktree_clean:
        steps.append("next: commit or intentionally discard local changes before the Shipper opens a PR.")
    return steps


def _github_app_next_steps(report: GitHubAppConfigReport) -> list[str]:
    steps: list[str] = []
    missing = []
    if not report.app_id:
        missing.append(GITHUB_APP_ID_ENV)
    if not report.installation_id:
        missing.append(GITHUB_APP_INSTALLATION_ID_ENV)
    if report.private_key_path is None:
        missing.append(GITHUB_APP_PRIVATE_KEY_PATH_ENV)
    if missing:
        steps.append(f"next: set {', '.join(missing)} in `.env.local` or the process environment.")
    if report.private_key_path is not None and not report.private_key_exists:
        steps.append("next: point `GITHUB_APP_PRIVATE_KEY_PATH` at the downloaded GitHub App PEM file.")
    if report.private_key_exists and not report.private_key_is_private:
        steps.append(f"next: run `chmod 600 {report.private_key_path}`.")
    return steps


def _run(
    command: Sequence[str],
    *,
    cwd: str | Path | None,
    timeout_seconds: int,
) -> GitHubCommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitHubCommandResult(tuple(command), 1, stderr=str(exc))

    return GitHubCommandResult(
        tuple(command),
        completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _truncate(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.splitlines())
