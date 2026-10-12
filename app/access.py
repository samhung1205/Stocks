"""Whole-app HTTP Basic protection for a small private Railway deployment.

Local use stays unauthenticated unless explicitly enabled. Railway always
requires credentials; missing/partial configuration fails closed. The sole
public route is GET/HEAD /api/health, which reveals no application data.
"""
import base64
import binascii
import hashlib
import secrets
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


class PrivateAccessMiddleware:
    def __init__(self, app, *, required=False, username="", password=""):
        self.app = app
        self.enabled = required or bool(username or password)
        self.configured = bool(username and password)
        self.username = hashlib.sha256(username.encode()).digest()
        self.password = hashlib.sha256(password.encode()).digest()

    async def __call__(self, scope, receive, send):
        if not self.enabled or scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})

        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() != b"cache-control"]
                headers.extend([(b"cache-control", b"no-store"),
                                (b"vary", b"Authorization")])
                message = {**message, "headers": headers}
            await send(message)

        if scope["path"] == "/api/health" and scope["method"] in {"GET", "HEAD"}:
            return await self.app(scope, receive, private_send)
        if not self.configured:
            return await JSONResponse({"detail": "Private access is not configured"}, 503)(
                scope, receive, private_send)
        headers = dict(scope["headers"])
        authorized = False
        raw = headers.get(b"authorization", b"")
        try:
            scheme, value = raw.split(b" ", 1)
            if scheme.lower() == b"basic" and len(value) <= 4096:
                user, password = base64.b64decode(value, validate=True).decode("utf-8").split(":", 1)
                user_ok = secrets.compare_digest(hashlib.sha256(user.encode()).digest(), self.username)
                password_ok = secrets.compare_digest(hashlib.sha256(password.encode()).digest(), self.password)
                authorized = user_ok and password_ok
        except (ValueError, UnicodeError, binascii.Error):
            pass
        if not authorized:
            return await JSONResponse({"detail": "Authentication required"}, 401,
                                      headers={"WWW-Authenticate": 'Basic realm="Stocks", charset="UTF-8"'})(
                scope, receive, private_send)
        # Browsers attach Basic credentials automatically. Reject cross-site
        # writes so another site cannot make an authenticated browser mutate data.
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origin = headers.get(b"origin")
            host = headers.get(b"host", b"").decode("latin-1").lower()
            cross_site = headers.get(b"sec-fetch-site") == b"cross-site"
            if origin:
                try:
                    parsed = urlsplit(origin.decode("latin-1"))
                    cross_site = cross_site or parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != host
                except ValueError:
                    cross_site = True
            if cross_site:
                return await JSONResponse({"detail": "Cross-site writes are forbidden"}, 403)(
                    scope, receive, private_send)
        return await self.app(scope, receive, private_send)
