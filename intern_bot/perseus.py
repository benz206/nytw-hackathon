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
    query_probe: PerseusCommandResult | None = None
    local_query_probe: PerseusCommandResult | None = None

    @property
    def ok(self) -> bool:
        checks = [self.version, self.doctor]
        if self.executable is None or not all(check is None or check.ok for check in checks):
            return False

        readiness_checks = [
            check
            for check in (self.index_status, self.query_probe, self.local_query_probe)
            if check is not None
        ]
        return not readiness_checks or any(check.ok for check in readiness_checks)

    @property
    def query_available(self) -> bool:
        if self.index_status is not None and self.index_status.ok:
            return True
        return any(
            check is not None and check.ok
            for check in (self.query_probe, self.local_query_probe)
        )

    @property
    def query_mode(self) -> str | None:
        if self.index_status is not None and self.index_status.ok:
            return "hosted"
        if self.query_probe is not None and self.query_probe.ok:
            return "hosted"
        if self.local_query_probe is not None and self.local_query_probe.ok:
            return "local"
        return None


def check_perseus(
    *,
    cwd: str | Path | None = None,
    run_doctor: bool = True,
    run_index_status: bool = True,
    run_query_probe: bool = False,
    run_local_query_probe: bool = False,
    query_probe: str = "where is the main application code?",
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
    probe = (
        _run((found, "query", query_probe), cwd=cwd, timeout_seconds=timeout_seconds)
        if run_query_probe and (index_status is None or not index_status.ok)
        else None
    )
    local_probe = (
        _run(
            (found, "query", "--local", "--files-only", query_probe),
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )
        if run_local_query_probe
        and not (
            (index_status is not None and index_status.ok)
            or (probe is not None and probe.ok)
        )
        else None
    )
    return PerseusDoctorReport(
        executable=found,
        token_path=token_path,
        token_exists=token_path.exists(),
        version=version,
        doctor=doctor,
        index_status=index_status,
        query_probe=probe,
        local_query_probe=local_probe,
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
        ("query_probe", report.query_probe),
        ("local_query_probe", report.local_query_probe),
    ):
        if result is None:
            continue
        status = "ok" if result.ok else f"failed ({result.returncode})"
        lines.append(f"{label}: {status}")
        if result.output:
            lines.append(_indent(_truncate(result.output)))

    if not report.token_exists:
        lines.append("next: run `perseus login` as the operator; do not delegate login to the agent.")
    if report.index_status is not None and not report.index_status.ok and report.query_available:
        lines.append(
            "note: `perseus index --status` failed, but a Perseus query probe works; "
            f"treating Perseus as usable via {report.query_mode} query."
        )
    elif report.index_status is not None and not report.index_status.ok:
        lines.append(
            "next: run `perseus index owner/repo` for hosted GitHub indexing, or "
            "`perseus index --local` from the target repo for local-only search."
        )
    if report.local_query_probe is not None and not report.local_query_probe.ok:
        lines.append(
            "local: `perseus query --local` is not ready; run `perseus index --local` "
            "from the target repo and ensure the local embed transport is running."
        )
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
