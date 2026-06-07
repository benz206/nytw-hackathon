from types import SimpleNamespace

from intern_bot.github import preflight
from intern_bot.github.preflight import (
    GitHubCommandResult,
    GitHubRepoReport,
    check_github_app_config,
    check_github_repo,
    format_github_app_report,
    format_github_report,
)


def test_check_github_repo_reports_missing_git(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    report = check_github_repo()

    assert report.git_executable is None
    assert not report.ok
    assert "git: missing" in format_github_report(report)


def test_check_github_repo_runs_expected_commands(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, cwd, text, capture_output, timeout, check):
        stdout = "" if tuple(command) == ("/bin/git", "status", "--short") else "ok\n"
        calls.append(
            {
                "command": tuple(command),
                "cwd": cwd,
                "text": text,
                "capture_output": capture_output,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    report = check_github_repo(
        cwd=tmp_path,
        git_executable="/bin/git",
        gh_executable="/bin/gh",
        timeout_seconds=5,
    )

    assert report.ok
    assert calls == [
        {
            "command": ("/bin/git", "rev-parse", "--show-toplevel"),
            "cwd": tmp_path,
            "text": True,
            "capture_output": True,
            "timeout": 5,
            "check": False,
        },
        {
            "command": ("/bin/git", "branch", "--show-current"),
            "cwd": tmp_path,
            "text": True,
            "capture_output": True,
            "timeout": 5,
            "check": False,
        },
        {
            "command": ("/bin/git", "symbolic-ref", "refs/remotes/origin/HEAD", "--short"),
            "cwd": tmp_path,
            "text": True,
            "capture_output": True,
            "timeout": 5,
            "check": False,
        },
        {
            "command": ("/bin/git", "remote", "get-url", "origin"),
            "cwd": tmp_path,
            "text": True,
            "capture_output": True,
            "timeout": 5,
            "check": False,
        },
        {
            "command": ("/bin/git", "status", "--short"),
            "cwd": tmp_path,
            "text": True,
            "capture_output": True,
            "timeout": 5,
            "check": False,
        },
        {
            "command": ("/bin/gh", "--version"),
            "cwd": tmp_path,
            "text": True,
            "capture_output": True,
            "timeout": 5,
            "check": False,
        },
        {
            "command": ("/bin/gh", "auth", "status", "--hostname", "github.com"),
            "cwd": tmp_path,
            "text": True,
            "capture_output": True,
            "timeout": 5,
            "check": False,
        },
    ]


def test_format_github_report_points_to_auth_when_gh_is_logged_out(tmp_path):
    report = GitHubRepoReport(
        git_executable="/bin/git",
        gh_executable="/bin/gh",
        repo_root=GitHubCommandResult(("/bin/git", "rev-parse", "--show-toplevel"), 0, stdout=str(tmp_path)),
        branch=GitHubCommandResult(("/bin/git", "branch", "--show-current"), 0, stdout="intern/ENG-1-demo\n"),
        remote_url=GitHubCommandResult(("/bin/git", "remote", "get-url", "origin"), 0, stdout="git@github.com:x/y.git\n"),
        worktree_status=GitHubCommandResult(("/bin/git", "status", "--short"), 0, stdout=""),
        gh_version=GitHubCommandResult(("/bin/gh", "--version"), 0, stdout="gh version 2.0\n"),
        gh_auth_status=GitHubCommandResult(
            ("/bin/gh", "auth", "status", "--hostname", "github.com"),
            1,
            stderr="not logged in",
        ),
    )

    rendered = format_github_report(report)

    assert "gh_auth: failed (1)" in rendered
    assert "gh auth login --hostname github.com" in rendered


def test_format_github_report_warns_when_worktree_dirty(tmp_path):
    report = GitHubRepoReport(
        git_executable="/bin/git",
        gh_executable="/bin/gh",
        repo_root=GitHubCommandResult(("/bin/git", "rev-parse", "--show-toplevel"), 0, stdout=str(tmp_path)),
        branch=GitHubCommandResult(("/bin/git", "branch", "--show-current"), 0, stdout="intern/ENG-1-demo\n"),
        remote_url=GitHubCommandResult(("/bin/git", "remote", "get-url", "origin"), 0, stdout="git@github.com:x/y.git\n"),
        worktree_status=GitHubCommandResult(("/bin/git", "status", "--short"), 0, stdout=" M app.py\n"),
        gh_version=GitHubCommandResult(("/bin/gh", "--version"), 0, stdout="gh version 2.0\n"),
        gh_auth_status=GitHubCommandResult(
            ("/bin/gh", "auth", "status", "--hostname", "github.com"),
            0,
            stdout="Logged in\n",
        ),
    )

    rendered = format_github_report(report)

    assert "worktree: dirty" in rendered
    assert "commit or intentionally discard local changes" in rendered


def test_check_github_app_config_reports_missing_values():
    report = check_github_app_config(env={})

    assert not report.ok
    rendered = format_github_app_report(report)
    assert "GITHUB_APP_ID: missing" in rendered
    assert "GITHUB_APP_INSTALLATION_ID: missing" in rendered
    assert "GITHUB_APP_PRIVATE_KEY_PATH: missing" in rendered


def test_check_github_app_config_requires_private_key_permissions(tmp_path):
    key_path = tmp_path / "app.private-key.pem"
    key_path.write_text("secret", encoding="utf-8")
    key_path.chmod(0o644)

    report = check_github_app_config(
        env={
            "GITHUB_APP_ID": "123",
            "GITHUB_APP_INSTALLATION_ID": "456",
            "GITHUB_APP_PRIVATE_KEY_PATH": str(key_path),
        }
    )

    assert not report.ok
    assert report.private_key_mode == 0o644
    assert "chmod 600" in format_github_app_report(report)


def test_check_github_app_config_accepts_private_key_permissions(tmp_path):
    key_path = tmp_path / "app.private-key.pem"
    key_path.write_text("secret", encoding="utf-8")
    key_path.chmod(0o600)

    report = check_github_app_config(
        env={
            "GITHUB_APP_ID": "123",
            "GITHUB_APP_INSTALLATION_ID": "456",
            "GITHUB_APP_PRIVATE_KEY_PATH": str(key_path),
        }
    )

    assert report.ok
