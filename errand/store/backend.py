"""Storage backends.

Two implementations behind one interface: DynamoDB for real runs, memory for
tests. The interface is deliberately small - put, get, query by partition,
delete, and a conditional put used for idempotency. Anything richer would
leak DynamoDB shapes into the policy layer.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any, Protocol


class ConditionFailed(RuntimeError):
    """A conditional write lost a race."""


class Backend(Protocol):
    def put(self, table: str, item: dict[str, Any]) -> None: ...

    def put_if_absent(self, table: str, item: dict[str, Any]) -> bool: ...

    def get(self, table: str, pk: str, sk: str) -> dict[str, Any] | None: ...

    def query(self, table: str, pk: str, sk_prefix: str = "") -> list[dict[str, Any]]: ...

    def delete(self, table: str, pk: str, sk: str) -> None: ...

    def delete_partition(self, table: str, pk: str) -> int: ...

    def scan(self, table: str) -> list[dict[str, Any]]: ...


class MemoryBackend:
    """In-process backend. Used by tests and by `make demo`."""

    def __init__(self) -> None:
        self._data: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def _table(self, table: str) -> dict[tuple[str, str], dict[str, Any]]:
        return self._data.setdefault(table, {})

    def put(self, table: str, item: dict[str, Any]) -> None:
        with self._lock:
            self._table(table)[(item["pk"], item["sk"])] = dict(item)

    def put_if_absent(self, table: str, item: dict[str, Any]) -> bool:
        with self._lock:
            key = (item["pk"], item["sk"])
            if key in self._table(table):
                return False
            self._table(table)[key] = dict(item)
            return True

    def get(self, table: str, pk: str, sk: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._table(table).get((pk, sk))
            return dict(found) if found else None

    def query(self, table: str, pk: str, sk_prefix: str = "") -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                dict(v)
                for (p, s), v in self._table(table).items()
                if p == pk and s.startswith(sk_prefix)
            ]
        return sorted(rows, key=lambda r: r["sk"])

    def delete(self, table: str, pk: str, sk: str) -> None:
        with self._lock:
            self._table(table).pop((pk, sk), None)

    def delete_partition(self, table: str, pk: str) -> int:
        with self._lock:
            keys = [k for k in self._table(table) if k[0] == pk]
            for key in keys:
                del self._table(table)[key]
            return len(keys)

    def scan(self, table: str) -> list[dict[str, Any]]:
        with self._lock:
            return sorted((dict(v) for v in self._table(table).values()),
                          key=lambda r: (r["pk"], r["sk"]))

    def reset(self) -> None:
        with self._lock:
            self._data.clear()


class DynamoBackend:
    """DynamoDB behind the same interface. Every table is pk/sk, KMS CMK."""

    def __init__(self, region: str) -> None:
        import boto3  # imported here so tests never need botocore installed

        self._ddb = boto3.resource("dynamodb", region_name=region)
        self._tables: dict[str, Any] = {}

    def _table(self, table: str):
        if table not in self._tables:
            self._tables[table] = self._ddb.Table(table)
        return self._tables[table]

    def put(self, table: str, item: dict[str, Any]) -> None:
        self._table(table).put_item(Item=_clean(item))

    def put_if_absent(self, table: str, item: dict[str, Any]) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._table(table).put_item(
                Item=_clean(item),
                ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def get(self, table: str, pk: str, sk: str) -> dict[str, Any] | None:
        response = self._table(table).get_item(Key={"pk": pk, "sk": sk})
        return response.get("Item")

    def query(self, table: str, pk: str, sk_prefix: str = "") -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        condition = Key("pk").eq(pk)
        if sk_prefix:
            condition = condition & Key("sk").begins_with(sk_prefix)
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {"KeyConditionExpression": condition}
        while True:
            response = self._table(table).query(**kwargs)
            items.extend(response.get("Items", []))
            token = response.get("LastEvaluatedKey")
            if not token:
                return items
            kwargs["ExclusiveStartKey"] = token

    def delete(self, table: str, pk: str, sk: str) -> None:
        self._table(table).delete_item(Key={"pk": pk, "sk": sk})

    def delete_partition(self, table: str, pk: str) -> int:
        rows = self.query(table, pk)
        target = self._table(table)
        with target.batch_writer() as batch:
            for row in rows:
                batch.delete_item(Key={"pk": row["pk"], "sk": row["sk"]})
        return len(rows)

    def scan(self, table: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {}
        while True:
            response = self._table(table).scan(**kwargs)
            items.extend(response.get("Items", []))
            token = response.get("LastEvaluatedKey")
            if not token:
                return items
            kwargs["ExclusiveStartKey"] = token


def _clean(item: dict[str, Any]) -> dict[str, Any]:
    """DynamoDB rejects empty strings in key-ish attributes and floats."""
    from decimal import Decimal

    def convert(value: Any) -> Any:
        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(v) for v in value]
        return value

    return {k: convert(v) for k, v in item.items() if v is not None}


_backend: Backend | None = None


def get_backend() -> Backend:
    global _backend
    if _backend is None:
        from errand.common import config

        cfg = config.load()
        if cfg.backend == "memory":
            _backend = MemoryBackend()
        else:
            _backend = DynamoBackend(cfg.region)
    return _backend


def set_backend(backend: Backend | None) -> None:
    """Tests call this. Production never does."""
    global _backend
    _backend = backend


def rows_by_prefix(backend: Backend, table: str, pk: str, prefix: str) -> Iterable[dict[str, Any]]:
    return backend.query(table, pk, prefix)
