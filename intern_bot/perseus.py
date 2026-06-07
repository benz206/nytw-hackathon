"""Perseus CLI preflight helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
from typing import Sequence


INSTALL_COMMAND = "curl -fsSL https://perseus.computer/install.sh | sh"
DEFAULT_TOKEN_PATH = Path("~/.config/perseus/token")


@dataclass(frozen=True)
class PerseusCommandResult:
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
class PerseusDoctorReport:
    executable: str | None
    token_path: Path
    token_exists: bool
    version: PerseusCommandResult | None = None
    doctor: PerseusCommandResult | None = None
    index_status: PerseusCommandResult | None = None

    @property
    def ok(self) -> bool:
        checks = [self.version, self.doctor, self.index_status]
        return self.executable is not None and all(check is None or check.ok for check in checks)


def check_perseus(
    *,
    cwd: str | Path | None = None,
    run_doctor: bool = True,
    run_index_status: bool = True,
    executable: str | None = None,
    timeout_seconds: int = 120,
) -> PerseusDoctorReport:
    """Check whether Perseus is installed, authenticated, and indexed."""
    token_path = Path(os.getenv("PERSEUS_TOKEN_PATH", str(DEFAULT_TOKEN_PATH))).expanduser()
    found = executable or shutil.which("perseus")
    if found is None:
        return PerseusDoctorReport(
            executable=None,
            token_path=token_path,
            token_exists=token_path.exists(),
        )

    version = _run((found, "--version"), cwd=cwd, timeout_seconds=timeout_seconds)
    doctor = _run((found, "doctor"), cwd=cwd, timeout_seconds=timeout_seconds) if run_doctor else None
    if doctor is not None and _is_missing_subcommand(doctor, "doctor"):
        doctor = None
    index_status = (
        _run((found, "index", "--status"), cwd=cwd, timeout_seconds=timeout_seconds)
        if run_index_status
        else None
    )
    return PerseusDoctorReport(
        executable=found,
        token_path=token_path,
        token_exists=token_path.exists(),
        version=version,
        doctor=doctor,
        index_status=index_status,
    )


def format_perseus_report(report: PerseusDoctorReport) -> str:
    """Render a human-readable Perseus preflight report."""
    lines: list[str] = ["Perseus preflight"]
    if report.executable is None:
        lines.extend(
            [
                "executable: missing",
                f"install: {INSTALL_COMMAND}",
                "login: perseus login",
                f"token: {'found' if report.token_exists else 'missing'} at {report.token_path}",
            ]
        )
        return "\n".join(lines)

    lines.append(f"executable: {report.executable}")
    lines.append(f"token: {'found' if report.token_exists else 'missing'} at {report.token_path}")
    for label, result in (
        ("version", report.version),
        ("doctor", report.doctor),
        ("index", report.index_status),
    ):
        if result is None:
            continue
        status = "ok" if result.ok else f"failed ({result.returncode})"
        lines.append(f"{label}: {status}")
        if result.output:
            lines.append(_indent(_truncate(result.output)))

    if not report.token_exists:
        lines.append("next: run `perseus login` as the operator; do not delegate login to the agent.")
    if report.index_status is not None and not report.index_status.ok:
        lines.append("next: run `perseus index` from the repo root, then rerun this check.")
    if report.doctor is None:
        lines.append("note: this Perseus CLI does not expose `perseus doctor`; skipped that check.")
    return "\n".join(lines)


def _run(
    command: Sequence[str],
    *,
    cwd: str | Path | None,
    timeout_seconds: int,
) -> PerseusCommandResult:
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
        return PerseusCommandResult(tuple(command), 1, stderr=str(exc))

    return PerseusCommandResult(
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


def _is_missing_subcommand(result: PerseusCommandResult, command_name: str) -> bool:
    if result.ok:
        return False
    output = result.output.lower()
    return f"no such command '{command_name}'" in output or f'no such command "{command_name}"' in output
