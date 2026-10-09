"""Refuses state-changing requests that a web page on another site sent to this server.

The server only listens on 127.0.0.1, but any website open in the user's browser can still
make that browser send a form or a simple POST to ``http://127.0.0.1:8765``. CORS does not
stop this: it only decides whether the other site may *read* the answer, and a multipart
upload needs no preflight at all. So every request that changes something must carry an
``Origin`` header naming this server (the page the app serves, including the pywebview
window) or the Vite dev server. Requests without an ``Origin`` header come from tools on
this PC (curl, scripts, the tests), not from a browser page, and pass.

The server's own origin is taken from the request's ``Host`` header, so it works whatever
port ``ccs serve --port`` chose; the host must be a loopback name so a DNS-rebinding page
cannot pose as it.

The WebSocket at ``/api/ws`` gets the same check: CORS never applies to WebSocket handshakes,
so without it any page open in the browser could listen to the whole event stream (project
titles, folder paths, progress messages). A handshake with a foreign ``Origin`` is closed
with code 1008 (policy violation) before it is accepted.
"""

from __future__ import annotations

from collections.abc import Iterable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})

REFUSED_MESSAGE = (
    "This request came from another website, so CashCow Studio refused it. "
    "Use the app's own window or page."
)
WS_POLICY_VIOLATION = 1008
WS_REFUSED_REASON = "This connection came from another website."


class SameOriginGuard:
    """Pure ASGI middleware; see the module docstring."""

    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str] = ()) -> None:
        self.app = app
        self.allowed_origins = {_normalize(origin) for origin in allowed_origins}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"].upper() not in SAFE_METHODS:
            if self._foreign(scope):
                response = JSONResponse(status_code=403, content={"detail": REFUSED_MESSAGE})
                await response(scope, receive, send)
                return
        elif scope["type"] == "websocket" and self._foreign(scope):
            close = WebSocketClose(code=WS_POLICY_VIOLATION, reason=WS_REFUSED_REASON)
            await close(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _foreign(self, scope: Scope) -> bool:
        """True when the request names an ``Origin`` that is not this app or the dev server."""
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        return origin is not None and not self.allows(origin, headers.get("host", ""))

    def allows(self, origin: str, host: str) -> bool:
        """True for the dev server, or for this server itself (``Origin`` == scheme + ``Host``)."""
        origin = _normalize(origin)
        if origin in self.allowed_origins:
            return True
        host = host.strip().lower()
        if _hostname(host) not in LOOPBACK_HOSTS:
            return False
        return origin in {f"http://{host}", f"https://{host}"}


def _normalize(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def _hostname(host: str) -> str:
    """``127.0.0.1:8765`` -> ``127.0.0.1``; ``[::1]:8765`` -> ``[::1]``."""
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host
