from pathlib import Path
from types import SimpleNamespace

from intern_bot import perseus
from intern_bot.perseus import (
    PerseusCommandResult,
    PerseusDoctorReport,
    check_perseus,
    format_perseus_report,
)


def test_check_perseus_reports_missing_executable(monkeypatch, tmp_path):
    token_path = tmp_path / "token"
    monkeypatch.setenv("PERSEUS_TOKEN_PATH", str(token_path))
    monkeypatch.setattr(perseus.shutil, "which", lambda name: None)

    report = check_perseus()

    assert report.executable is None
    assert not report.ok
    assert report.token_path == token_path


def test_format_missing_executable_includes_operator_steps(tmp_path):
    report = PerseusDoctorReport(
        executable=None,
        token_path=tmp_path / "token",
        token_exists=False,
    )

    rendered = format_perseus_report(report)

    assert "executable: missing" in rendered
    assert "curl -fsSL https://perseus.computer/install.sh | sh" in rendered
    assert "perseus login" in rendered


def test_check_perseus_runs_expected_commands(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, cwd, text, capture_output, timeout, check):
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
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setenv("PERSEUS_TOKEN_PATH", str(tmp_path / "token"))
    monkeypatch.setattr(perseus.subprocess, "run", fake_run)

    report = check_perseus(cwd=tmp_path, executable="/bin/perseus", timeout_seconds=7)

    assert report.ok
    assert calls == [
        {
            "command": ("/bin/perseus", "--version"),
            "cwd": tmp_path,
            "text": True,
            "capture_output": True,
            "timeout": 7,
            "check": False,
        },
        {
            "command": ("/bin/perseus", "doctor"),
            "cwd": tmp_path,
            "text": True,
            "capture_output": True,
            "timeout": 7,
            "check": False,
        },
        {
            "command": ("/bin/perseus", "index", "--status"),
            "cwd": tmp_path,
            "text": True,
            "capture_output": True,
            "timeout": 7,
            "check": False,
        },
    ]


def test_format_failed_index_points_to_reindex(tmp_path):
    report = PerseusDoctorReport(
        executable="/bin/perseus",
        token_path=Path("token"),
        token_exists=True,
        version=PerseusCommandResult(("/bin/perseus", "--version"), 0, stdout="perseus 0.1\n"),
        doctor=PerseusCommandResult(("/bin/perseus", "doctor"), 0, stdout="ready\n"),
        index_status=PerseusCommandResult(
            ("/bin/perseus", "index", "--status"),
            1,
            stderr="no ready index",
        ),
    )

    rendered = format_perseus_report(report)

    assert "index: failed (1)" in rendered
    assert "perseus index" in rendered


def test_check_perseus_skips_missing_doctor_command(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, cwd, text, capture_output, timeout, check):
        calls.append(tuple(command))
        if tuple(command) == ("/bin/perseus", "doctor"):
            return SimpleNamespace(returncode=2, stdout="", stderr="No such command 'doctor'.")
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setenv("PERSEUS_TOKEN_PATH", str(tmp_path / "token"))
    monkeypatch.setattr(perseus.subprocess, "run", fake_run)

    report = check_perseus(cwd=tmp_path, executable="/bin/perseus", timeout_seconds=7)

    assert report.ok
    assert report.doctor is None
    assert calls == [
        ("/bin/perseus", "--version"),
        ("/bin/perseus", "doctor"),
        ("/bin/perseus", "index", "--status"),
    ]
