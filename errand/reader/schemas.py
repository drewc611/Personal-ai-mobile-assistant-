"""Fixed output schemas for the quarantined reader.

Hard rule 3: the reader has a fixed output schema and anything outside it is
dropped. Dropped, not sanitised - there is no clever escaping step that turns
an unexpected field into a safe one, so unexpected fields simply do not reach
the planner.

The validator is hand-rolled on purpose. A schema library would let a future
edit add `additionalProperties: true` in one line; here the only way through
is a declared field.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

# Codepoints that render as nothing but change how text is read: C0/C1
# controls, soft hyphen, bidi overrides, zero-width joiners, and the Unicode
# tag block used to smuggle hidden instructions past a human reviewer.
_INVISIBLE_RANGES = (
    (0x0000, 0x0008), (0x000B, 0x001F), (0x007F, 0x009F),
    (0x00AD, 0x00AD), (0x200B, 0x200F), (0x2028, 0x202E),
    (0x2060, 0x2064), (0x206A, 0x206F), (0xFEFF, 0xFEFF),
    (0xFFF9, 0xFFFB), (0xE0000, 0xE007F),
)
_INVISIBLE = re.compile(
    "[" + "".join(f"\\U{lo:08x}-\\U{hi:08x}" for lo, hi in _INVISIBLE_RANGES) + "]"
)
_ELLIPSIS = "…"


class SchemaError(ValueError):
    """Reader output that does not fit the schema."""


@dataclass(frozen=True)
class Field:
    name: str
    kind: str                      # "str" | "int" | "bool" | "str_list"
    max_len: int = 400
    max_items: int = 20
    required: bool = True


@dataclass(frozen=True)
class Schema:
    name: str
    fields: tuple[Field, ...]

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]

    def json_shape(self) -> dict[str, str]:
        """The shape we show the reader model in its prompt."""
        return {f.name: f.kind for f in self.fields}


def clean_text(value: str, max_len: int) -> str:
    """Normalise, strip invisibles, collapse whitespace, truncate.

    This is not an injection defence - the defence is that the reader has no
    tools and the planner has a gate. This is hygiene, so that what a human
    reads in an SMS is what is actually in the field.
    """
    text = unicodedata.normalize("NFKC", str(value))
    text = _INVISIBLE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + _ELLIPSIS
    return text


def validate(schema: Schema, payload: Any) -> dict[str, Any]:
    """Coerce `payload` into exactly `schema`. Unknown keys are dropped."""
    if not isinstance(payload, dict):
        raise SchemaError(f"{schema.name}: expected an object, got {type(payload).__name__}")

    out: dict[str, Any] = {}
    for spec in schema.fields:
        if spec.name not in payload:
            if spec.required:
                out[spec.name] = _empty(spec)
            continue
        out[spec.name] = _coerce(schema, spec, payload[spec.name])
    return out


def _empty(spec: Field) -> Any:
    return {"str": "", "int": 0, "bool": False, "str_list": []}[spec.kind]


def _coerce(schema: Schema, spec: Field, value: Any) -> Any:
    if spec.kind == "str":
        if not isinstance(value, (str, int, float)):
            raise SchemaError(f"{schema.name}.{spec.name}: expected a string")
        return clean_text(value, spec.max_len)
    if spec.kind == "int":
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise SchemaError(f"{schema.name}.{spec.name}: expected an integer") from exc
    if spec.kind == "bool":
        return bool(value)
    if spec.kind == "str_list":
        if not isinstance(value, (list, tuple)):
            raise SchemaError(f"{schema.name}.{spec.name}: expected a list")
        items = [clean_text(v, spec.max_len) for v in value if isinstance(v, (str, int, float))]
        return [i for i in items if i][: spec.max_items]
    raise SchemaError(f"unknown field kind {spec.kind}")


EMAIL_SUMMARY = Schema(
    name="EmailSummary",
    fields=(
        Field("sender_display", "str", max_len=120),
        Field("subject", "str", max_len=200),
        Field("received_at", "str", max_len=40),
        Field("summary", "str", max_len=600),
        Field("action_items", "str_list", max_len=160, max_items=8),
        Field("dates_mentioned", "str_list", max_len=60, max_items=8),
        Field("amounts_mentioned", "str_list", max_len=40, max_items=8),
        Field("link_count", "int"),
        Field("has_attachment", "bool"),
        # The reader is asked to flag it when the content tries to give
        # instructions. This is a signal for the log and for Andrew, never a
        # permission check - the gate does not consult it.
        Field("contains_instructions_to_assistant", "bool"),
    ),
)

CALENDAR_SUMMARY = Schema(
    name="CalendarSummary",
    fields=(
        Field("title", "str", max_len=160),
        Field("starts_at", "str", max_len=40),
        Field("ends_at", "str", max_len=40),
        Field("location", "str", max_len=160),
        Field("attendee_count", "int"),
        Field("summary", "str", max_len=400),
        Field("contains_instructions_to_assistant", "bool"),
    ),
)

WEB_SUMMARY = Schema(
    name="WebSummary",
    fields=(
        Field("title", "str", max_len=160),
        Field("source", "str", max_len=200),
        Field("summary", "str", max_len=600),
        Field("facts", "str_list", max_len=200, max_items=10),
        Field("contains_instructions_to_assistant", "bool"),
    ),
)

VOICE_TRANSCRIPT = Schema(
    name="VoiceTranscript",
    fields=(
        Field("transcript", "str", max_len=1200),
        Field("request", "str", max_len=400),
        Field("confidence", "str", max_len=20),
    ),
)

ALL_SCHEMAS: dict[str, Schema] = {
    s.name: s for s in (EMAIL_SUMMARY, CALENDAR_SUMMARY, WEB_SUMMARY, VOICE_TRANSCRIPT)
}
