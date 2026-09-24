""":mod:`tender_intelligence.mail.sendlib` — the Sendlib provider adapter (prompt 11 §5).

Sendlib (``sendlib.samueltuoyo.com``) is the confirmed first provider (docs/08 §8.2): it
relays through a connected Gmail/Google Workspace mailbox and needs no domain or DNS setup.
The wire contract in v1.1 §5.10.3 is a single authenticated ``POST /api/send`` with a Bearer
API key and a JSON body (``from``, ``to``, ``subject``, ``html``, optional CC/BCC/Reply-To and
base64 attachments), which is exactly what is implemented here. Providers 2 and 3 stay TBD
(open decisions O4/O13) — nothing else about this adapter knows that.

Everything Sendlib-specific lives in this module. The rest of the notification system depends
only on :class:`MailProvider` (PROJECT_RULES #9).
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from email.utils import formataddr
from typing import Any

import httpx2

from tender_intelligence.core.errors import MAIL_TIMEOUT
from tender_intelligence.mail.errors import (
    MailError,
    configuration_error,
    http_status_error,
    retryable,
    timeout_error,
)
from tender_intelligence.mail.message import EmailMessage, MailAttachment
from tender_intelligence.mail.provider import Capabilities, MailProvider, SendResult

log = logging.getLogger("tender_intelligence.mail.sendlib")

DEFAULT_BASE_URL = "https://sendlib.samueltuoyo.com"
SEND_PATH = "/api/send"

#: Free-tier limits from docs/08 §8.8 (v1.1 §5.10.3 research notes). Capabilities come from
#: the DB row in production; this is only the constructor default while a tier is chosen
#: (open decision O4). Pro raises attachments to 20 x 10 MB and messages to 5 MB.
SENDLIB_FREE_CAPABILITIES = Capabilities(
    max_attachments=5,
    max_attachment_mb=1,
    # Sendlib documents a 2 MB HTML body limit on Free.  The generic capability field is the
    # total message limit, so use the documented body ceiling as a conservative default rather
    # than allowing an unbounded message.  A DB row may override it when a tier is selected.
    max_message_mb=2,
    daily_limit=200,
    rate_limit_per_min=30,
    needs_verified_domain=False,
)


def _base64(attachment: MailAttachment) -> str:
    return base64.b64encode(attachment.content).decode()


def _json_from(
    message: EmailMessage,
    *,
    from_address: str,
    from_name: str | None,
    reply_to: str | None,
) -> dict[str, Any]:
    """Build the current Sendlib ``POST /api/send`` JSON contract.

    Sendlib's current API accepts ``from`` as a mailbox string (optionally with a display
    name), ``replyTo`` in camel case, and attachment MIME metadata under ``type``.  The
    notification dedupe value is sent as a request header in :meth:`SendlibProvider.send`;
    the API does not document an arbitrary MIME-header field in its JSON body.
    """
    payload: dict[str, Any] = {
        "from": formataddr((from_name or "", from_address)),
        "to": list(message.to),
        "subject": message.subject,
        "text": message.text_body,
        "html": message.html_body or message.text_body,
        "attachments": [
            {
                "filename": attachment.filename,
                "type": attachment.content_type or "application/octet-stream",
                "content": _base64(attachment),
            }
            for attachment in message.attachments
        ],
    }
    if message.cc:
        payload["cc"] = list(message.cc)
    if message.bcc:
        payload["bcc"] = list(message.bcc)
    if reply_to:
        payload["replyTo"] = reply_to
    return payload


class SendlibProvider(MailProvider):
    """Concrete :class:`MailProvider` speaking the documented Sendlib API.

    The adapter is constructed with already-decrypted credentials; it never reads the
    database and never logs request bodies or credentials (docs/10 §10.1).
    """

    name = "sendlib"

    def __init__(
        self,
        *,
        api_key: str,
        from_address: str,
        from_name: str | None = None,
        reply_to: str | None = None,
        capabilities: Capabilities | None = None,
        base_url: str = DEFAULT_BASE_URL,
        client_factory: Callable[[], httpx2.Client] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("sendlib api key must not be empty")
        self._api_key = api_key
        self._from_address = from_address
        self._from_name = from_name
        self._reply_to = reply_to
        self._capabilities = capabilities or SENDLIB_FREE_CAPABILITIES
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("sendlib base_url must be an http(s) URL")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = float(timeout_seconds)
        # Tests inject a factory bound to a MockTransport; production uses the default client.
        self._client_factory = client_factory or (lambda: httpx2.Client())

    @property
    def capabilities(self) -> Capabilities:
        return self._capabilities

    @property
    def from_address(self) -> str:
        return self._from_address

    def send(self, message: EmailMessage) -> SendResult:
        message.validate()
        payload = _json_from(
            message,
            from_address=self._from_address,
            from_name=self._from_name,
            reply_to=self._reply_to,
        )
        url = f"{self._base_url}{SEND_PATH}"
        factory_error: MailError | None = None
        client: httpx2.Client | None = None
        try:
            client = self._client_factory()
        except Exception:  # noqa: BLE001 - adapter factory boundary
            # The original exception may retain an Authorization header through a request
            # object.  Keep only a sanitized provider error.
            factory_error = configuration_error("sendlib HTTP client could not be created")
        if factory_error is not None or client is None:
            if factory_error is None:
                factory_error = configuration_error("sendlib HTTP client could not be created")
            factory_error.__context__ = None
            raise factory_error

        headers = {"Authorization": f"Bearer {self._api_key}"}
        if message.dedupe_key:
            # Sendlib does not document custom MIME headers in its JSON schema.  Preserve the
            # required idempotency metadata on the authenticated API request instead of sending
            # an undocumented body field that the endpoint may reject.
            headers["X-Tender-Notification-Dedupe-Key"] = message.dedupe_key
        response: httpx2.Response | None = None
        transport_error: MailError | None = None
        try:
            try:
                response = client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=httpx2.Timeout(self._timeout_seconds),
                )
            except httpx2.ConnectError as exc:
                # A connection refusal happens before a request is accepted; unlike a read
                # timeout it is not by itself evidence of a duplicate.
                transport_error = retryable(
                    MAIL_TIMEOUT,
                    f"sendlib unreachable: {type(exc).__name__}",
                )
            except (
                httpx2.ReadError,
                httpx2.TimeoutException,
                httpx2.RemoteProtocolError,
                httpx2.TransportError,
            ) as exc:
                # The request may have reached the provider; a failover resend could duplicate.
                transport_error = timeout_error(
                    f"sendlib transport error: {type(exc).__name__}"
                )
            except Exception as exc:  # noqa: BLE001 - sanitize unknown transport failures
                transport_error = timeout_error(
                    f"sendlib transport error: {type(exc).__name__}"
                )
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - close must not mask a provider result
                log.warning(
                    "sendlib client close failed",
                    extra={"stage": "email", "status": "warning"},
                )
        if transport_error is not None:
            # Raising outside the handler blocks prevents the original request-bearing
            # exception from becoming an inspectable cause/context.
            transport_error.__context__ = None
            raise transport_error
        if response is None:  # defensive: a client implementation returned without a response
            raise configuration_error("sendlib HTTP client returned no response")

        if not 200 <= response.status_code < 300:
            raise http_status_error(
                response.status_code,
                retry_after=response.headers.get("retry-after"),
                error_code=_provider_error_code(response),
            )

        provider_message_id = _response_id(response)

        return SendResult(
            provider_name=self.name,
            ok=True,
            provider_message_id=provider_message_id,
            duration_ms=None,
        )


def _provider_error_code(response: httpx2.Response) -> str | None:
    """Return only a short provider error code, never its response body."""

    try:
        data = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("error_code", "code", "error"):
        value = data.get(key)
        if isinstance(value, str) and len(value) <= 80:
            return value
    return None


def _response_id(response: httpx2.Response) -> str | None:
    """Extract a provider message id from a 2xx JSON response, tolerating its absence."""
    try:
        data = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("id", "message_id", "request_id"):
        value = data.get(key)
        if value:
            rendered = str(value)
            if len(rendered) <= 255:
                return rendered
    return None


def build_from_context(context: Any) -> SendlibProvider:
    """Factory used by :class:`ProviderAdapterRegistry`."""

    api_key = context.credentials.get("api_key")
    if not api_key:
        raise configuration_error("Sendlib credentials missing 'api_key'")
    return SendlibProvider(
        api_key=str(api_key),
        from_address=context.from_address,
        from_name=context.from_name,
        reply_to=context.reply_to,
        capabilities=context.capabilities,
        client_factory=context.client_factory,
    )


def provider_from_row(
    *,
    credentials: dict[str, Any],
    from_address: str,
    from_name: str | None,
    reply_to: str | None,
    capabilities: Capabilities,
) -> SendlibProvider:
    """Build a :class:`SendlibProvider` from a decrypted ``MailProvider`` credentials dict."""
    api_key = credentials.get("api_key")
    if not api_key:
        raise configuration_error("Sendlib credentials missing 'api_key'")
    return SendlibProvider(
        api_key=str(api_key),
        from_address=from_address,
        from_name=from_name,
        reply_to=reply_to,
        capabilities=capabilities,
    )
