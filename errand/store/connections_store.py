"""Connected accounts.

What is connected, with which scopes, and when. The token itself is not here -
`token_ref` names where AgentCore Identity holds it (hard rule 6). Storing a
reference rather than a token means this table can be read in the console
without reading anyone's mailbox.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from errand.common import clock, config
from errand.store import backend as backend_mod

CONNECTED = "CONNECTED"
DISCONNECTED = "DISCONNECTED"


@dataclass
class Connection:
    provider: str
    state: str = CONNECTED
    scopes: list[str] = field(default_factory=list)
    token_ref: str = ""
    connected_at: str = ""
    disconnected_at: str = ""

    def to_item(self) -> dict[str, Any]:
        return {
            "pk": f"provider#{self.provider}",
            "sk": "meta",
            "provider": self.provider,
            "state": self.state,
            "scopes": json.dumps(self.scopes),
            "token_ref": self.token_ref,
            "connected_at": self.connected_at,
            "disconnected_at": self.disconnected_at,
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Connection:
        scopes = item.get("scopes") or "[]"
        return cls(
            provider=item["provider"],
            state=item.get("state", CONNECTED),
            scopes=json.loads(scopes) if isinstance(scopes, str) else list(scopes),
            token_ref=item.get("token_ref", ""),
            connected_at=item.get("connected_at", ""),
            disconnected_at=item.get("disconnected_at", ""),
        )


def _table() -> str:
    return config.load().table("connections")


def record_connected(provider: str, scopes: list[str], token_ref: str) -> Connection:
    connection = Connection(
        provider=provider,
        state=CONNECTED,
        scopes=list(scopes),
        token_ref=token_ref,
        connected_at=clock.now_iso(),
    )
    backend_mod.get_backend().put(_table(), connection.to_item())
    return connection


def get(provider: str) -> Connection | None:
    item = backend_mod.get_backend().get(_table(), f"provider#{provider}", "meta")
    return Connection.from_item(item) if item else None


def record_disconnected(provider: str) -> Connection:
    connection = get(provider) or Connection(provider=provider)
    connection.state = DISCONNECTED
    connection.disconnected_at = clock.now_iso()
    # The scopes and the token reference go with the token. Keeping them after
    # a disconnect would leave a description of access that no longer exists.
    connection.scopes = []
    connection.token_ref = ""
    backend_mod.get_backend().put(_table(), connection.to_item())
    return connection


def connected() -> list[Connection]:
    rows = [r for r in backend_mod.get_backend().scan(_table()) if r.get("sk") == "meta"]
    return [c for c in (Connection.from_item(r) for r in rows) if c.state == CONNECTED]
