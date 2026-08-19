"""Typed browser-session state handed to curl-cffi workers.

This module holds the shared, curl-cffi-friendly session representation used by
both the operator ``scripts/playwright_manual_probe.py`` and the production
:class:`~athome_harness.scraping.playwright_cookie_fetcher.PlaywrightCookieFetcher`,
so the two browser paths produce the same ``session_state.json`` shape without
duplicating header, cookie, or Chrome-version logic. It also owns the shared
browser launch options (:func:`build_launch_options`) so both entry points
present the identical Chrome fingerprint (viewport, locale, timezone, UA) that
AtHome expects; a lean fetcher that skips these gets flagged by the WAF.

A :class:`SessionState` is a plain, serializable snapshot of the cookies, user
agent, and request headers a patched browser established for AtHome. It converts
to the immutable :class:`~athome_harness.scraping.cookie_handoff.CookieHandoff`
that the curl-cffi :class:`HttpDomAdapter` consumes, so a session produced by
either browser entry point can be replayed by the HTTP adapter.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from athome_harness.scraping.cookie_handoff import (
    CookieHandoff,
    ImpersonateProfile,
    proxy_identity,
)

SESSION_STATE_SCHEMA_VERSION: Final = 1

# Fallback used when the installed Chrome binary cannot be detected. Kept high
# enough that AtHome treats the impersonation as current.
DEFAULT_CHROME_VERSION: Final = "151.0.0.0"

# Browser-language envelope sent on every request so AtHome serves Japanese.
DEFAULT_ACCEPT: Final = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
)
DEFAULT_ACCEPT_LANGUAGE: Final = "ja,en-US;q=0.9,en;q=0.8"
DEFAULT_SEC_CH_UA_PLATFORM: Final = '"Linux"'

_CHROME_VERSION_RE = re.compile(r"Google Chrome ([\d.]+)")
_CHROME_BINARY_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")

# Shared persistent-Chrome launch geometry. A 1280x720 window matches the
# viewport so headless rendering keeps a real-screen shape.
LAUNCH_VIEWPORT: Final = {"width": 1280, "height": 720}
LAUNCH_LOCALE: Final = "ja-JP"
LAUNCH_TIMEZONE_ID: Final = "Asia/Tokyo"


def build_launch_options(
    *,
    user_data_dir: str,
    chrome_version: str,
    proxy_url: str | None = None,
) -> dict[str, object]:
    """Return the common Patchright ``launch_persistent_context`` options.

    Both the operator probe and the production cookie fetcher must launch
    Chrome with this exact fingerprint (viewport, Japanese locale, Tokyo
    timezone, real-Chrome user agent, automation flags disabled). Deviating
    from it is what gets the fetcher flagged by the AtHome WAF. Callers add
    their own extras on top (proxy is merged here, DEBUG video in the probe).
    """
    options: dict[str, object] = {
        "user_data_dir": user_data_dir,
        "channel": "chrome",
        "headless": True,
        "viewport": dict(LAUNCH_VIEWPORT),
        "locale": LAUNCH_LOCALE,
        "timezone_id": LAUNCH_TIMEZONE_ID,
        "args": [
            "--window-size=1280,720",
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--lang=ja-JP",
            (
                "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"
            ),
        ],
    }
    if proxy_url:
        options["proxy"] = {"server": proxy_url}
    return options


def chrome_major(chrome_version: str) -> str:
    """Return the leading major-version segment of a Chrome version string."""
    return chrome_version.split(".", 1)[0]


def get_installed_chrome_version() -> str:
    """Return the installed Chrome/Chromium version, or a safe fallback.

    A browser is preferred to a hardcoded string because Patchright's
    impersonation is most trustworthy when it matches the real local binary.
    """
    for binary in _CHROME_BINARY_NAMES:
        try:
            output = subprocess.check_output([binary, "--version"]).decode("utf-8")
        except (OSError, subprocess.CalledProcessError):
            continue
        match = _CHROME_VERSION_RE.search(output)
        if match:
            return match.group(1)
    return DEFAULT_CHROME_VERSION


def build_chrome_headers(chrome_version: str) -> dict[str, str]:
    """Return the sec-ch-ua browser-header envelope for a Chrome version.

    Headers mimic what the real Chrome client sends on a fresh documented
    navigation so the curl-cffi ``chrome`` impersonation looks consistent.
    """
    major = chrome_major(chrome_version)
    return {
        "accept": DEFAULT_ACCEPT,
        "accept-language": DEFAULT_ACCEPT_LANGUAGE,
        "sec-ch-ua": (f'"Google Chrome";v="{major}", "Chromium";v="{major}", "Not?A_Brand";v="24"'),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": DEFAULT_SEC_CH_UA_PLATFORM,
        "user-agent": _chrome_user_agent(chrome_version),
    }


def _chrome_user_agent(chrome_version: str) -> str:
    """Build the full Linux Chrome user-agent string for a version."""
    return (
        f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"
    )


@dataclass
class SessionState:
    """Serializable curl-cffi-friendly snapshot of one browser session."""

    target_domain: str = "athome.co.jp"
    last_updated: int = field(default_factory=lambda: int(datetime.now(UTC).timestamp()))
    proxy_url: str | None = None
    user_agent: str = ""
    chrome_major_version: str = DEFAULT_CHROME_VERSION
    impersonate_profile: ImpersonateProfile = "chrome"
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Copy mutable collections so a loaded state never shares references."""
        self.headers = dict(self.headers)
        self.cookies = dict(self.cookies)

    @classmethod
    def from_browser(
        cls,
        *,
        cookies: list[dict[str, object]],
        user_agent: str,
        chrome_version: str,
        proxy_url: str | None = None,
        impersonate_profile: ImpersonateProfile = "chrome",
    ) -> SessionState:
        """Build a state from a patched browser context and Chrome version."""
        cookie_values = {
            str(cookie["name"]): str(cookie["value"])
            for cookie in cookies
            if isinstance(cookie.get("name"), str) and isinstance(cookie.get("value"), str)
        }
        return cls(
            proxy_url=proxy_url,
            user_agent=user_agent,
            chrome_major_version=chrome_major(chrome_version),
            impersonate_profile=impersonate_profile,
            headers=build_chrome_headers(chrome_version),
            cookies=cookie_values,
        )

    def to_cookie_handoff(self) -> CookieHandoff:
        """Return the typed handoff consumed by the curl-cffi HTTP adapter."""
        cookie_list: list[dict[str, object]] = [
            {"name": name, "value": value} for name, value in self.cookies.items()
        ]
        return CookieHandoff(
            proxy_identity=proxy_identity(self.proxy_url),
            proxy_url=self.proxy_url,
            user_agent=self.user_agent,
            headers=dict(self.headers),
            cookies=tuple(cookie_list),
            created_at=datetime.fromtimestamp(self.last_updated, tz=UTC).isoformat(),
            impersonate=self.impersonate_profile,
        )

    @classmethod
    def from_cookie_handoff(cls, handoff: CookieHandoff) -> SessionState:
        """Rebuild a state from an existing typed browser handoff."""
        return cls(
            proxy_url=handoff.proxy_url,
            user_agent=handoff.user_agent,
            chrome_major_version=_major_from_handoff(handoff),
            impersonate_profile=handoff.impersonate,
            headers=dict(handoff.headers),
            cookies=dict(handoff.cookie_values),
        )

    def save(self, path: Path) -> None:
        """Atomically persist this state as ``session_state.json``."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(_json_dumps(payload), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> SessionState:
        """Load and validate a persisted ``session_state.json``."""
        payload = _json_loads(path)
        try:
            return cls(
                target_domain=str(payload.get("target_domain", "athome.co.jp")),
                last_updated=int(str(payload.get("last_updated", 0))),
                proxy_url=_optional_str(payload.get("proxy")),
                user_agent=str(payload.get("user_agent", "")),
                chrome_major_version=str(
                    payload.get("chrome_major_version", DEFAULT_CHROME_VERSION)
                ),
                impersonate_profile=cast(
                    ImpersonateProfile, str(payload.get("impersonate_profile", "chrome"))
                ),
                headers=_string_dict(payload.get("headers", {})),
                cookies=_string_dict(payload.get("cookies", {})),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid session state: {path}") from exc

    def to_dict(self) -> dict[str, object]:
        """Return the JSON representation persisted as ``session_state.json``."""
        return {
            "target_domain": self.target_domain,
            "last_updated": self.last_updated,
            "proxy": self.proxy_url,
            "user_agent": self.user_agent,
            "chrome_major_version": self.chrome_major_version,
            "impersonate_profile": self.impersonate_profile,
            "headers": dict(self.headers),
            "cookies": dict(self.cookies),
        }


def _major_from_handoff(handoff: CookieHandoff) -> str:
    """Derive a Chrome major version from a handoff user agent if possible."""
    match = re.search(r"Chrome/([\d.]+)", handoff.user_agent)
    return chrome_major(match.group(1)) if match else DEFAULT_CHROME_VERSION


def _string_dict(value: object) -> dict[str, str]:
    """Coerce a raw JSON mapping into a string-keyed string mapping."""
    if not isinstance(value, dict):
        raise ValueError("Expected a mapping")
    return {str(key): str(item) for key, item in value.items()}


def _optional_str(value: object) -> str | None:
    """Coerce a raw JSON scalar into a string, preserving ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ValueError("Expected a string or null")


def _json_dumps(payload: dict[str, object]) -> str:
    """Serialize a state payload with stable formatting."""
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)


def _json_loads(path: Path) -> dict[str, object]:
    """Load a JSON object from disk, raising on missing or malformed input."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to load session state: {path}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("Session state JSON must contain an object")
    return loaded
