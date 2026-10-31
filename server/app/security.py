import asyncio
import hashlib
import ipaddress
import logging
import re
import socket
from collections import Counter
from datetime import UTC, datetime, timedelta
from time import monotonic
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet

from app.errors import AppError

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
RATE_LIMIT_MAX_KEYS = 10000


class Redactor:
    def __init__(self):
        self._fingerprints = {}

    def register(self, key):
        # Keep hashes rather than an extra plaintext copy of every credential.
        digest = hashlib.sha256(key.encode()).digest()
        counts = self._fingerprints.setdefault(len(key), Counter())
        counts[digest] += 1

    def unregister(self, key):
        self._drop(len(key), hashlib.sha256(key.encode()).digest())

    def discard(self, value):
        # CredentialStore eviction callback: forget the fingerprint of a
        # revoked or expired credential. Tolerates job records and repeats.
        if not isinstance(value, dict):
            return
        fingerprint = value.get("key_fingerprint") or {}
        try:
            digest = bytes.fromhex(fingerprint["digest"])
            length = fingerprint["length"]
        except (KeyError, TypeError, ValueError):
            return
        self._drop(length, digest)

    def _drop(self, length, digest):
        counts = self._fingerprints.get(length)
        if not counts or digest not in counts:
            return
        counts[digest] -= 1
        if counts[digest] <= 0:
            del counts[digest]
        if not counts:
            del self._fingerprints[length]

    def text(self, value):
        value = str(value)

        # Rejected URL credentials still appear in access logs before registration.
        def redact_query(match):
            name = unquote(match[2]).lower().replace("-", "_")
            if name in {"api_key", "apikey", "authorization", "access_token", "token"}:
                return match[1] + match[2] + "=[REDACTED]"
            return match[0]

        value = re.sub(r"([?&])([^=&\s\"'<>]+)=([^&#\s\"'<>]*)", redact_query, value)
        for length, fingerprints in self._fingerprints.items():
            matches = []
            for start in range(max(0, len(value) - length + 1)):
                piece = value[start : start + length]
                if hashlib.sha256(piece.encode()).digest() in fingerprints:
                    matches.append((start, start + length))
            for start, end in reversed(matches):
                value = value[:start] + "[REDACTED]" + value[end:]
        # Secret-like patterns require realistic lengths: sk- needs 20+
        # characters and Bearer tokens need 8+ (the credential floor), so
        # prose such as "sk-learn" or "Bearer tokens must ..." passes through.
        value = re.sub(
            r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/=]{8,}",
            "Bearer [REDACTED]",
            value,
        )
        return re.sub(r"\bsk-[A-Za-z0-9_-]{20,}", "[REDACTED]", value)

    def scrub(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(k): self.scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub(v) for v in value]
        return value


class RedactionFilter(logging.Filter):
    def __init__(self, redactor):
        super().__init__()
        self.redactor = redactor

    def filter(self, record):
        # Uvicorn's access formatter unpacks these five arguments itself.
        if (
            record.name == "uvicorn.access"
            and isinstance(record.args, tuple)
            and len(record.args) == 5
        ):
            record.args = tuple(self.redactor.scrub(v) for v in record.args)
        else:
            alternate = getattr(record, "color_message", None)
            if isinstance(alternate, str):
                if record.args:
                    alternate = alternate % record.args
                record.color_message = self.redactor.text(alternate)
            record.msg = self.redactor.text(record.getMessage())
            record.args = ()
        if record.exc_info:
            record.exc_text = self.redactor.text(
                logging.Formatter().formatException(record.exc_info)
            )
            record.exc_info = None
        return True


async def _resolve_public(host, port, allow_local, local_name):
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise AppError(
            "unsafe_url", "Outbound host could not be resolved", 422
        ) from exc
    resolved = sorted({item[4][0] for item in addresses})
    if not resolved:
        raise AppError("unsafe_url", "Outbound host has no addresses", 422)
    for address in resolved:
        ip = ipaddress.ip_address(address)
        if not ip.is_global and not (allow_local and local_name and ip.is_loopback):
            raise AppError(
                "unsafe_url", "Outbound host resolves to a restricted address", 422
            )
    return resolved


async def validate_url(url, allow_local=False):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise AppError("unsafe_url", "Invalid outbound URL", 422) from exc
    if (
        not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AppError(
            "unsafe_url",
            "URL must have a host and no credentials, query or fragment",
            422,
        )
    local_name = host.lower() in LOCAL_HOSTS
    if parsed.scheme != "https" and not (
        allow_local and local_name and parsed.scheme == "http"
    ):
        raise AppError("unsafe_url", "Outbound URLs require HTTPS", 422)
    return await _resolve_public(
        host,
        port or (443 if parsed.scheme == "https" else 80),
        allow_local,
        local_name,
    )


class SafeTransport(httpx.AsyncBaseTransport):
    """Re-resolve and check the destination IP of every outbound request.

    Stored base URLs are validated at creation time, but DNS can change
    before the evaluator calls them. This transport closes that window by
    validating the addresses DNS returns at call time. Redirect hops are
    validated the same way because each hop passes through here.
    """

    def __init__(self, settings, inner=None):
        self._allow_local = settings.testlab_allow_insecure_local
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        url = request.url
        host = url.host or ""
        if url.username or url.password:
            raise AppError(
                "unsafe_url", "Outbound URLs must not embed credentials", 422
            )
        local_name = host.lower() in LOCAL_HOSTS
        if url.scheme != "https" and not (
            self._allow_local and local_name and url.scheme == "http"
        ):
            raise AppError("unsafe_url", "Outbound URLs require HTTPS", 422)
        await _resolve_public(
            host,
            url.port or (443 if url.scheme == "https" else 80),
            self._allow_local,
            local_name,
        )
        return await self._inner.handle_async_request(request)

    async def aclose(self):
        await self._inner.aclose()


def safe_http_client(settings):
    return httpx.AsyncClient(
        transport=SafeTransport(settings),
        timeout=httpx.Timeout(300.0, connect=10.0),
    )


def create_credential(data, settings, store, redactor):
    values = data.model_dump() if hasattr(data, "model_dump") else dict(data)
    key = values.pop("api_key")
    if hasattr(key, "get_secret_value"):
        key = key.get_secret_value()
    if not isinstance(key, str) or len(key) < 8 or any(c.isspace() for c in key):
        raise AppError(
            "invalid_key",
            "Supply a non-whitespace key with at least eight characters",
            422,
        )
    ttl = values.pop("ttl_seconds", 7200)
    if not isinstance(ttl, int) or not 1 <= ttl <= 86400:
        raise AppError(
            "invalid_ttl", "Credential TTL must be between 1 and 86400 seconds", 422
        )
    digest = hashlib.sha256(key.encode())
    redactor.register(key)
    identifier = str(uuid4())
    values.update(
        credential_id=identifier,
        ciphertext=Fernet(settings.app_encryption_key).encrypt(key.encode()).decode(),
        key_hint=key[-4:],
        key_fingerprint={"length": len(key), "digest": digest.hexdigest()},
        expires_at=(datetime.now(UTC) + timedelta(seconds=ttl)).isoformat(),
    )
    store.put(identifier, values, ttl)
    return credential_metadata(identifier, store)


def credential_record(identifier, store):
    value = store.get(identifier)
    if value is None:
        raise AppError(
            "credential_expired", "Credential is expired, revoked or unknown", 404
        )
    return value


def credential_metadata(identifier, store):
    value = credential_record(identifier, store)
    return {
        k: value[k]
        for k in ("credential_id", "provider", "model", "key_hint", "expires_at")
    }


def decrypt_credential(identifier, store, settings):
    return (
        Fernet(settings.app_encryption_key)
        .decrypt(credential_record(identifier, store)["ciphertext"].encode())
        .decode()
    )


def revoke_credential(identifier, store):
    store.delete(identifier)


def rate_limit(request, limit, window=60):
    now = monotonic()
    buckets = request.app.state.rate_limits
    category = "credentials" if request.url.path == "/v1/credentials" else "chat"
    key = (request.client.host if request.client else "unknown", category)
    recent = [stamp for stamp in buckets.get(key, []) if now - stamp < window]
    if len(recent) >= limit:
        raise AppError("rate_limited", "Too many requests; try again later", 429)
    buckets[key] = [*recent, now]
    while len(buckets) > RATE_LIMIT_MAX_KEYS:
        buckets.pop(next(iter(buckets)))
