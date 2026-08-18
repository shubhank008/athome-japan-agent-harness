"""Typed browser-session handoff for curl-cffi workers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, cast
from urllib.parse import urlsplit

HANDOFF_SCHEMA_VERSION: Final = 1
ImpersonateProfile = Literal["chrome", "safari_ios"]
SUPPORTED_IMPERSONATE_PROFILES: Final = frozenset({"chrome", "safari_ios"})


class CookieHandoffError(ValueError):
    """Raised when a browser-session handoff is invalid or cannot be loaded."""


def proxy_identity(proxy_url: str | None) -> str:
    """Return a filename- and log-safe identity for a proxy endpoint."""
    if not proxy_url:
        return "direct"
    parts = urlsplit(proxy_url)
    host = parts.hostname or "unknown"
    port = f"_{parts.port}" if parts.port else ""
    return f"{host}{port}".replace(".", "_").replace(":", "_")


@dataclass(frozen=True, slots=True)
class CookieHandoff:
    """The cookies and headers harvested by one browser/proxy session."""

    proxy_identity: str
    proxy_url: str | None
    user_agent: str
    headers: dict[str, str]
    cookies: tuple[dict[str, object], ...]
    created_at: str
    impersonate: ImpersonateProfile = "chrome"
    schema_version: int = HANDOFF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Validate and defensively copy mutable handoff collections."""
        if self.schema_version != HANDOFF_SCHEMA_VERSION:
            raise CookieHandoffError(
                f"Unsupported cookie handoff schema version {self.schema_version}"
            )
        if self.impersonate not in SUPPORTED_IMPERSONATE_PROFILES:
            raise CookieHandoffError(
                f"Unsupported curl-cffi impersonation profile {self.impersonate}"
            )
        if not self.user_agent.strip():
            raise CookieHandoffError("Cookie handoff requires a user agent")
        if not self.headers:
            raise CookieHandoffError("Cookie handoff requires request headers")
        for cookie in self.cookies:
            if not isinstance(cookie.get("name"), str) or not isinstance(cookie.get("value"), str):
                raise CookieHandoffError("Every cookie requires string name and value")
        object.__setattr__(self, "headers", dict(self.headers))
        object.__setattr__(self, "cookies", tuple(dict(cookie) for cookie in self.cookies))

    @classmethod
    def from_browser(
        cls,
        *,
        proxy_url: str | None,
        user_agent: str,
        headers: dict[str, str],
        cookies: list[dict[str, object]],
        impersonate: ImpersonateProfile = "chrome",
    ) -> CookieHandoff:
        """Build a validated handoff from Patchright context data."""
        return cls(
            proxy_identity=proxy_identity(proxy_url),
            proxy_url=proxy_url,
            user_agent=user_agent,
            headers=headers,
            cookies=tuple(cookies),
            created_at=datetime.now(UTC).isoformat(),
            impersonate=impersonate,
        )

    @property
    def cookie_header(self) -> str:
        """Return all harvested cookies in HTTP ``Cookie`` header form."""
        return "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in self.cookies)

    @property
    def cookie_values(self) -> dict[str, str]:
        """Return cookies in the mapping form accepted by curl-cffi."""
        return {str(cookie["name"]): str(cookie["value"]) for cookie in self.cookies}

    def to_curl_cffi_kwargs(self) -> dict[str, object]:
        """Return request keyword arguments bound to this browser session."""
        headers = dict(self.headers)
        headers.pop("cookie", None)
        headers.pop("Cookie", None)
        headers["User-Agent"] = self.user_agent
        result: dict[str, object] = {
            "headers": headers,
            "cookies": self.cookie_values,
            "default_headers": False,
            "impersonate": self.impersonate,
        }
        if self.proxy_url:
            result["proxy"] = self.proxy_url
        return result

    def to_dict(self) -> dict[str, object]:
        """Return the JSON representation used for local persistence."""
        return {
            "schema_version": self.schema_version,
            "proxy_identity": self.proxy_identity,
            "proxy_url": self.proxy_url,
            "user_agent": self.user_agent,
            "headers": self.headers,
            "cookies": list(self.cookies),
            "created_at": self.created_at,
            "impersonate": self.impersonate,
        }

    def save(self, path: Path, cookies_path: Path) -> None:
        """Atomically save the handoff JSON and diagnostic cookie header."""
        path.parent.mkdir(parents=True, exist_ok=True)
        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, json.dumps(self.to_dict(), indent=2, ensure_ascii=False))
        self._atomic_write(cookies_path, self.cookie_header + "\n")

    @classmethod
    def load(cls, path: Path) -> CookieHandoff:
        """Load and validate a handoff JSON file."""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CookieHandoffError(f"Unable to load cookie handoff: {path}") from exc
        if not isinstance(payload, dict):
            raise CookieHandoffError("Cookie handoff JSON must contain an object")
        try:
            return cls(
                proxy_identity=str(payload["proxy_identity"]),
                proxy_url=payload.get("proxy_url"),
                user_agent=str(payload["user_agent"]),
                headers={str(key): str(value) for key, value in payload["headers"].items()},
                cookies=tuple(payload["cookies"]),
                created_at=str(payload["created_at"]),
                impersonate=cast(ImpersonateProfile, str(payload.get("impersonate", "chrome"))),
                schema_version=int(payload["schema_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CookieHandoffError("Cookie handoff JSON has an invalid schema") from exc

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Replace a target file only after its complete content is written."""
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
