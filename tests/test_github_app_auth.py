import base64
import json

from intern_bot.github import app_auth
from intern_bot.github.app_auth import (
    GitHubAppConfig,
    GitHubInstallationToken,
    ensure_github_app_token_from_env,
    generate_app_jwt,
)


def _decode_base64url(value: str) -> dict[str, object]:
    padded = value + "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def test_github_app_config_from_env_requires_all_values():
    assert GitHubAppConfig.from_env({}) is None


def test_generate_app_jwt_uses_expected_claims(monkeypatch, tmp_path):
    key_path = tmp_path / "key.pem"
    key_path.write_text("secret", encoding="utf-8")

    monkeypatch.setattr(app_auth, "_openssl_sign", lambda data, private_key_path: b"signature")

    token = generate_app_jwt(
        GitHubAppConfig(app_id="123", installation_id="456", private_key_path=key_path),
        now=1_700_000_000,
    )

    header, payload, signature = token.split(".")
    assert _decode_base64url(header) == {"alg": "RS256", "typ": "JWT"}
    assert _decode_base64url(payload) == {
        "iat": 1_699_999_940,
        "exp": 1_700_000_540,
        "iss": "123",
    }
    assert signature == "c2lnbmF0dXJl"


def test_ensure_github_app_token_exports_minted_token(monkeypatch, tmp_path):
    key_path = tmp_path / "key.pem"
    key_path.write_text("secret", encoding="utf-8")
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "456")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(key_path))
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(app_auth, "_CACHED_TOKEN", None)
    monkeypatch.setattr(
        app_auth,
        "mint_installation_token",
        lambda config: GitHubInstallationToken(token="ghs_test", expires_at="2099-01-01T00:00:00Z"),
    )

    token = ensure_github_app_token_from_env(force=True)

    assert token is not None
    assert token.token == "ghs_test"
    assert app_auth.os.environ["GH_TOKEN"] == "ghs_test"
    assert app_auth.os.environ["GITHUB_TOKEN"] == "ghs_test"
