"""Webshare rotating proxy provider (T09).

Reads the Webshare credentials from :class:`Settings` (``WEBSHARE_PROXY_USER`` /
``WEBSHARE_PROXY_PASS``) and exposes a single per-session rotating endpoint,
``http://USER:PASS@p.webshare.io:80``, through the direct-first policy in
:class:`BaseProxyProvider`. Webshare rotates the underlying egress IP per
request, so one URL is enough; the pool has length one and the retry budget
bounds consecutive proxy attempts.

Credential handling obeys the marker contract: the proxy URL includes the
credentials internally (httpx needs them in the URL) but the provider never
logs it. The [BLOCK_DETECTED] guard in ``base.redact_url`` strips any userinfo
before a URL is logged, and :meth:`get_proxy` result is consumed by the adapter
which never logs the full URL (``PROXY_CREDENTIALS_IN_URL_LOG`` guard).
"""

from __future__ import annotations

from athome_harness.config import Budgets, Settings
from athome_harness.scraping.proxy.base import BaseProxyProvider

# Webshare's rotating (per-request IP) gateway endpoint. One session maps to one
# credentialed URL; Webshare cycles the egress IP in the background.
_WEBSHARE_HOST = "p.webshare.net"
_WEBSHARE_PORT = 80


def _build_webshare_url(user: str, password: str) -> str:
    """Build the credentialed Webshare proxy URL for one session.

    The userinfo is required by httpx to authenticate; it must never be logged.
    """
    return f"http://{user}:{password}@{_WEBSHARE_HOST}:{_WEBSHARE_PORT}"


class WebshareProxyProvider(BaseProxyProvider):
    """Webshare rotating proxy backed by Settings credentials.

    Requires both ``WEBSHARE_PROXY_USER`` and ``WEBSHARE_PROXY_PASS`` to be set;
    they are optional in Settings, so a missing pair makes the provider
    raise``ValueError`` at construction rather than fail mid-session with a
    proxy that cannot authenticate.
    """

    def __init__(self, settings: Settings, budgets: Budgets) -> None:
        if not settings.webshare_proxy_user or not settings.webshare_proxy_pass:
            raise ValueError(
                "WebshareProxyProvider requires WEBSHARE_PROXY_USER and WEBSHARE_PROXY_PASS"
            )
        self._proxy_url = _build_webshare_url(
            settings.webshare_proxy_user, settings.webshare_proxy_pass
        )
        super().__init__(budgets)

    def _build_pool(self) -> list[str]:
        """Return the per-session Webshare endpoint as a one-entry pool."""
        return [self._proxy_url]
