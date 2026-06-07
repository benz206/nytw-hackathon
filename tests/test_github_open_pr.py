from types import SimpleNamespace

from intern_bot.github import open_pr
from intern_bot.github.open_pr import build_intern_pr_body, open_pull_request


def test_build_intern_pr_body_is_short_and_intern_coded():
    body = build_intern_pr_body(
        ticket="TOT-12",
        summary="Added the Intern to contributors.",
        tests="bun run build passed.",
        notes="Check the contributor wording.",
    )

    assert body == (
        "tiny PR for TOT-12.\n"
        "\n"
        "changed:\n"
        "- Added the Intern to contributors.\n"
        "\n"
        "checked:\n"
        "- bun run build passed.\n"
        "\n"
        "notes:\n"
        "- Check the contributor wording.\n"
        "\n"
        "review pls, I think this is the small version."
    )


def test_open_pull_request_uses_app_token_push_and_draft_pr(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, *, cwd, env, text, capture_output, check):
        calls.append((command, cwd, env.copy()))
        if command[:3] == ["git", "branch", "--show-current"]:
            return SimpleNamespace(returncode=0, stdout="intern/TOT-12-contributors\n", stderr="")
        if command[:3] == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]:
            return SimpleNamespace(returncode=0, stdout="origin/main\n", stderr="")
        if command[:3] == ["git", "push", "-u"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:3] == ["gh", "pr", "create"]:
            return SimpleNamespace(returncode=0, stdout="https://github.com/example/repo/pull/12\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(open_pr, "ensure_github_app_token_from_env", lambda force: object())
    monkeypatch.setattr(open_pr.subprocess, "run", fake_run)
    monkeypatch.setenv("GH_TOKEN", "ghs_app")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_app")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.https://github.com/.extraheader")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "AUTHORIZATION: basic token")

    result = open_pull_request(
        cwd=tmp_path,
        title="TOT-12 add intern contributor",
        summary="Added the Intern to contributors.",
        tests="bun run build passed.",
        ticket="TOT-12",
    )

    assert result.url == "https://github.com/example/repo/pull/12"
    assert result.branch == "intern/TOT-12-contributors"
    assert result.base == "main"
    push = calls[2]
    create = calls[3]
    assert push[0] == ["git", "push", "-u", "origin", "intern/TOT-12-contributors"]
    assert create[0][:4] == ["gh", "pr", "create", "--draft"]
    assert "--body" in create[0]
    assert "tiny PR for TOT-12." in create[0][create[0].index("--body") + 1]
    assert create[2]["GH_TOKEN"] == "ghs_app"
    assert create[2]["GIT_TERMINAL_PROMPT"] == "0"


def test_open_pull_request_refuses_main(monkeypatch, tmp_path):
    def fake_run(command, *, cwd, env, text, capture_output, check):
        if command[:3] == ["git", "branch", "--show-current"]:
            return SimpleNamespace(returncode=0, stdout="main\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(open_pr, "ensure_github_app_token_from_env", lambda force: object())
    monkeypatch.setattr(open_pr.subprocess, "run", fake_run)

    try:
        open_pull_request(
            cwd=tmp_path,
            title="bad",
            summary="bad",
            tests="not run",
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert "Refusing to open a PR directly from main" in message


def test_open_pull_request_explains_bot_write_permission_failure(monkeypatch, tmp_path):
    def fake_run(command, *, cwd, env, text, capture_output, check):
        if command[:3] == ["git", "branch", "--show-current"]:
            return SimpleNamespace(returncode=0, stdout="intern/TOT-12-contributors\n", stderr="")
        if command[:3] == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]:
            return SimpleNamespace(returncode=0, stdout="origin/main\n", stderr="")
        if command[:3] == ["git", "push", "-u"]:
            return SimpleNamespace(
                returncode=128,
                stdout="",
                stderr=(
                    "remote: Permission to Plum1234/task-manager.git denied to bob-the-intern[bot].\n"
                    "fatal: unable to access 'https://github.com/Plum1234/task-manager/': "
                    "The requested URL returned error: 403\n"
                ),
            )
        raise AssertionError(command)

    monkeypatch.setattr(open_pr, "ensure_github_app_token_from_env", lambda force: object())
    monkeypatch.setattr(open_pr.subprocess, "run", fake_run)

    try:
        open_pull_request(
            cwd=tmp_path,
            title="TOT-12 add intern contributor",
            summary="Added the Intern to contributors.",
            tests="bun run build passed.",
            ticket="TOT-12",
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert "denied to bob-the-intern[bot]" in message
    assert "Contents: Read and write" in message
    assert "Do not fall back to a personal token" in message
