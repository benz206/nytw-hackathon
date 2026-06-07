"""GitHub App installation-token authentication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import base64
import json
import os
import shutil
import subprocess
import time
from urllib import error, request

from .preflight import (
    GITHUB_APP_ID_ENV,
    GITHUB_APP_INSTALLATION_ID_ENV,
    GITHUB_APP_PRIVATE_KEY_PATH_ENV,
)


GITHUB_API_URL_ENV = "GITHUB_API_URL"
DEFAULT_GITHUB_API_URL = "https://api.github.com"

_CACHED_TOKEN: "GitHubInstallationToken | None" = None


@dataclass(frozen=True)
class GitHubAppConfig:
    app_id: str
    installation_id: str
    private_key_path: Path
    api_url: str = DEFAULT_GITHUB_API_URL

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "GitHubAppConfig | None":
        values = os.environ if env is None else env
        app_id = values.get(GITHUB_APP_ID_ENV)
        installation_id = values.get(GITHUB_APP_INSTALLATION_ID_ENV)
        private_key_path = values.get(GITHUB_APP_PRIVATE_KEY_PATH_ENV)
        if not app_id or not installation_id or not private_key_path:
            return None
        return cls(
            app_id=app_id,
            installation_id=installation_id,
            private_key_path=Path(private_key_path).expanduser(),
            api_url=values.get(GITHUB_API_URL_ENV, DEFAULT_GITHUB_API_URL).rstrip("/"),
        )


@dataclass(frozen=True)
class GitHubInstallationToken:
    token: str
    expires_at: str | None = None

    @property
    def expires_at_epoch(self) -> float | None:
        if not self.expires_at:
            return None
        try:
            return time.mktime(time.strptime(self.expires_at, "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            return None


def ensure_github_app_token_from_env(*, force: bool = False) -> GitHubInstallationToken | None:
    """Mint and export a GitHub App installation token when app env vars are set."""
    config = GitHubAppConfig.from_env()
    if config is None:
        return None

    global _CACHED_TOKEN
    now = time.time()
    if not force and _CACHED_TOKEN is not None:
        expires_at = _CACHED_TOKEN.expires_at_epoch
        if expires_at is None or expires_at - now > 5 * 60:
            _export_token(_CACHED_TOKEN.token)
            return _CACHED_TOKEN

    token = mint_installation_token(config)
    _CACHED_TOKEN = token
    _export_token(token.token)
    return token


def mint_installation_token(config: GitHubAppConfig) -> GitHubInstallationToken:
    """Exchange a GitHub App JWT for an installation access token."""
    jwt = generate_app_jwt(config)
    url = f"{config.api_url}/app/installations/{config.installation_id}/access_tokens"
    payload = b"{}"
    req = request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub App token request failed ({exc.code}): {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"GitHub App token request failed: {exc}") from exc

    token = body.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("GitHub App token response did not include a token.")
    expires_at = body.get("expires_at") if isinstance(body.get("expires_at"), str) else None
    return GitHubInstallationToken(token=token, expires_at=expires_at)


def generate_app_jwt(config: GitHubAppConfig, *, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else now
    payload = {
        "iat": issued_at - 60,
        "exp": issued_at + 9 * 60,
        "iss": config.app_id,
    }
    signing_input = ".".join(
        [
            _base64url_json({"alg": "RS256", "typ": "JWT"}),
            _base64url_json(payload),
        ]
    ).encode("ascii")
    signature = _openssl_sign(signing_input, config.private_key_path)
    return signing_input.decode("ascii") + "." + _base64url(signature)


def _export_token(token: str) -> None:
    os.environ["GH_TOKEN"] = token
    os.environ["GITHUB_TOKEN"] = token
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    _export_git_auth_header(token)


def _export_git_auth_header(token: str) -> None:
    """Let child-process `git push` use the app token without writing credentials."""
    key = "http.https://github.com/.extraheader"
    basic_token = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    value = f"AUTHORIZATION: basic {basic_token}"
    try:
        count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        count = 0

    for index in range(count):
        if os.environ.get(f"GIT_CONFIG_KEY_{index}") == key:
            os.environ[f"GIT_CONFIG_VALUE_{index}"] = value
            return

    os.environ["GIT_CONFIG_COUNT"] = str(count + 1)
    os.environ[f"GIT_CONFIG_KEY_{count}"] = key
    os.environ[f"GIT_CONFIG_VALUE_{count}"] = value


def _openssl_sign(data: bytes, private_key_path: Path) -> bytes:
    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("Missing openssl; required to sign GitHub App JWTs without extra Python deps.")
    try:
        completed = subprocess.run(
            [openssl, "dgst", "-sha256", "-sign", str(private_key_path)],
            input=data,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Could not sign GitHub App JWT: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Could not sign GitHub App JWT: {stderr}")
    return completed.stdout


def _base64url_json(value: dict[str, object]) -> str:
    return _base64url(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
