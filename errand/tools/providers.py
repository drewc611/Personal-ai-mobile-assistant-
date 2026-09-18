"""Outbound integrations.

Two implementations of each provider: a fake that tests and local runs use,
and a real one that talks to the Google APIs over HTTPS. Access tokens come
from a TokenSource - in AWS that is AgentCore Identity, which is why no OAuth
refresh token appears anywhere in this repo (hard rule 7).

The Google REST calls are written out rather than hidden behind a client
library so the exact scopes and endpoints being used are readable. If you are
auditing what this thing can reach, it is all on this page.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"

# Read and compose only. No gmail.modify, no delete scope: Errand is not
# allowed to mutate the mailbox beyond creating and sending a draft.
GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
)
CALENDAR_SCOPES = ("https://www.googleapis.com/auth/calendar.events",)


class ProviderError(RuntimeError):
    """A provider call failed in a way the user should hear about."""


class TokenSource(Protocol):
    def access_token(self, connection: str) -> str: ...

    def revoke(self, connection: str) -> bool: ...


class StaticTokenSource:
    """Used in tests and in a local dev run against a throwaway account."""

    def __init__(self, tokens: dict[str, str] | None = None) -> None:
        self._tokens = dict(tokens or {})
        self.revoked: list[str] = []

    def access_token(self, connection: str) -> str:
        try:
            return self._tokens[connection]
        except KeyError as exc:
            raise ProviderError(f"{connection} is not connected") from exc

    def revoke(self, connection: str) -> bool:
        token = self._tokens.pop(connection, None)
        self.revoked.append(connection)
        return token is not None


class AgentCoreTokenSource:
    """AgentCore Identity holds the Google OAuth grant.

    Integration point: the workload identity and provider name are configured
    in Terraform; this class asks Identity for a fresh access token per call
    and never caches one to disk.
    """

    def __init__(self, region: str, provider_name: str) -> None:
        import boto3

        self._client = boto3.client("bedrock-agentcore", region_name=region)
        self._provider = provider_name

    def access_token(self, connection: str) -> str:
        response = self._client.get_resource_oauth2_token(
            resourceCredentialProviderName=self._provider,
            scopes=list(GMAIL_SCOPES if connection == "gmail" else CALENDAR_SCOPES),
            oauth2Flow="USER_FEDERATION",
        )
        token = response.get("accessToken")
        if not token:
            raise ProviderError(f"no access token for {connection}")
        return token

    def revoke(self, connection: str) -> bool:
        token = self.access_token(connection)
        _post_form(GOOGLE_REVOKE, {"token": token})
        return True


def _request(url: str, token: str, *, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise ProviderError(
            f"{method} {urllib.parse.urlparse(url).path} -> {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(
            f"could not reach {urllib.parse.urlparse(url).netloc}: {exc.reason}"
        ) from exc
    return json.loads(raw) if raw else {}


def _post_form(url: str, fields: dict[str, str]) -> None:
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        urllib.request.urlopen(request, timeout=20).close()
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"revoke failed: {exc.code}") from exc


# --------------------------------------------------------------------------
# Gmail
# --------------------------------------------------------------------------


class GmailProvider(Protocol):
    def search(self, query: str, limit: int) -> list[dict[str, Any]]: ...

    def get_raw(self, message_id: str) -> str: ...

    def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]: ...

    def send_draft(self, draft_id: str) -> dict[str, Any]: ...


@dataclass
class FakeGmail:
    """Deterministic Gmail for tests, the injection suite, and local runs."""

    messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    drafts: dict[str, dict[str, Any]] = field(default_factory=dict)
    sent: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 0

    def add_message(self, *, sender: str, subject: str, body: str, received: str = "") -> str:
        self._seq += 1
        message_id = f"m{self._seq}"
        self.messages[message_id] = {
            "id": message_id,
            "from": sender,
            "subject": subject,
            "body": body,
            "received": received or "2026-09-18T09:00:00Z",
        }
        return message_id

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        needle = query.lower().strip()
        hits = [
            {"id": m["id"], "subject": m["subject"], "from": m["from"], "received": m["received"]}
            for m in self.messages.values()
            if not needle
            or needle in m["subject"].lower()
            or needle in m["from"].lower()
            or needle in m["body"].lower()
        ]
        return hits[:limit]

    def get_raw(self, message_id: str) -> str:
        message = self.messages.get(message_id)
        if message is None:
            raise ProviderError(f"no message {message_id}")
        return (
            f"From: {message['from']}\n"
            f"Subject: {message['subject']}\n"
            f"Date: {message['received']}\n\n"
            f"{message['body']}"
        )

    def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]:
        self._seq += 1
        draft_id = f"d{self._seq}"
        self.drafts[draft_id] = {"id": draft_id, "to": to, "subject": subject, "body": body}
        return dict(self.drafts[draft_id])

    def send_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.drafts.get(draft_id)
        if draft is None:
            raise ProviderError(f"no draft {draft_id}")
        self.sent.append(dict(draft))
        return {"id": draft_id, "sent": True, "to": draft["to"]}


class GoogleGmail:
    def __init__(self, tokens: TokenSource) -> None:
        self._tokens = tokens

    def _token(self) -> str:
        return self._tokens.access_token("gmail")

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({"q": query, "maxResults": max(1, min(limit, 25))})
        listing = _request(f"{GMAIL_API}/users/me/messages?{params}", self._token())
        results = []
        for stub in listing.get("messages", [])[:limit]:
            meta = _request(
                f"{GMAIL_API}/users/me/messages/{stub['id']}?format=metadata"
                "&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date",
                self._token(),
            )
            payload = meta.get("payload", {})
            headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
            results.append(
                {
                    "id": stub["id"],
                    "subject": headers.get("subject", ""),
                    "from": headers.get("from", ""),
                    "received": headers.get("date", ""),
                }
            )
        return results

    def get_raw(self, message_id: str) -> str:
        message = _request(
            f"{GMAIL_API}/users/me/messages/{message_id}?format=raw", self._token()
        )
        raw = message.get("raw", "")
        return base64.urlsafe_b64decode(raw + "==").decode("utf-8", errors="replace")

    def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]:
        mime = (
            f"To: {to}\r\nSubject: {subject}\r\n"
            f"Content-Type: text/plain; charset=UTF-8\r\n\r\n{body}"
        )
        encoded = base64.urlsafe_b64encode(mime.encode()).decode().rstrip("=")
        created = _request(
            f"{GMAIL_API}/users/me/drafts",
            self._token(),
            method="POST",
            body={"message": {"raw": encoded}},
        )
        return {"id": created.get("id", ""), "to": to, "subject": subject, "body": body}

    def send_draft(self, draft_id: str) -> dict[str, Any]:
        sent = _request(
            f"{GMAIL_API}/users/me/drafts/send",
            self._token(),
            method="POST",
            body={"id": draft_id},
        )
        return {"id": sent.get("id", draft_id), "sent": True, "thread_id": sent.get("threadId", "")}


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


class CalendarProvider(Protocol):
    def events_between(self, start_iso: str, end_iso: str) -> list[dict[str, Any]]: ...

    def respond(self, event_id: str, response: str) -> dict[str, Any]: ...


@dataclass
class FakeCalendar:
    events: list[dict[str, Any]] = field(default_factory=list)
    responses: list[tuple[str, str]] = field(default_factory=list)

    def add_event(self, *, event_id: str, title: str, start: str, end: str,
                  location: str = "", attendees: int = 1) -> None:
        self.events.append(
            {
                "id": event_id,
                "title": title,
                "start": start,
                "end": end,
                "location": location,
                "attendees": attendees,
            }
        )

    def events_between(self, start_iso: str, end_iso: str) -> list[dict[str, Any]]:
        return [e for e in self.events if start_iso <= e["start"] <= end_iso]

    def respond(self, event_id: str, response: str) -> dict[str, Any]:
        self.responses.append((event_id, response))
        return {"id": event_id, "response": response}


class GoogleCalendar:
    def __init__(self, tokens: TokenSource) -> None:
        self._tokens = tokens

    def _token(self) -> str:
        return self._tokens.access_token("calendar")

    def events_between(self, start_iso: str, end_iso: str) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode(
            {
                "timeMin": start_iso,
                "timeMax": end_iso,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 25,
            }
        )
        data = _request(f"{CALENDAR_API}/calendars/primary/events?{params}", self._token())
        events = []
        for item in data.get("items", []):
            start = item.get("start", {})
            end = item.get("end", {})
            events.append(
                {
                    "id": item.get("id", ""),
                    "title": item.get("summary", "(no title)"),
                    "start": start.get("dateTime") or start.get("date", ""),
                    "end": end.get("dateTime") or end.get("date", ""),
                    "location": item.get("location", ""),
                    "attendees": len(item.get("attendees", []) or []),
                }
            )
        return events

    def respond(self, event_id: str, response: str) -> dict[str, Any]:
        _request(
            f"{CALENDAR_API}/calendars/primary/events/{event_id}?sendUpdates=all",
            self._token(),
            method="PATCH",
            body={"attendees": [{"self": True, "responseStatus": response}]},
        )
        return {"id": event_id, "response": response}


# --------------------------------------------------------------------------
# Web search
# --------------------------------------------------------------------------


class SearchProvider(Protocol):
    def search(self, query: str, limit: int) -> list[dict[str, str]]: ...


@dataclass
class FakeSearch:
    results: list[dict[str, str]] = field(default_factory=list)

    def search(self, query: str, limit: int) -> list[dict[str, str]]:
        return self.results[:limit]


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


@dataclass
class Providers:
    gmail: GmailProvider
    calendar: CalendarProvider
    search: SearchProvider
    tokens: TokenSource


_providers: Providers | None = None


def set_providers(providers: Providers | None) -> None:
    global _providers
    _providers = providers


def get_providers() -> Providers:
    if _providers is None:
        raise ProviderError(
            "providers are not wired. The Lambda entrypoint builds them at cold start."
        )
    return _providers


def build_live_providers(region: str, identity_provider_name: str,
                         search: SearchProvider) -> Providers:
    tokens = AgentCoreTokenSource(region, identity_provider_name)
    return Providers(
        gmail=GoogleGmail(tokens),
        calendar=GoogleCalendar(tokens),
        search=search,
        tokens=tokens,
    )


def build_fake_providers() -> Providers:
    tokens = StaticTokenSource({"gmail": "fake", "calendar": "fake"})
    return Providers(gmail=FakeGmail(), calendar=FakeCalendar(), search=FakeSearch(),
                     tokens=tokens)
