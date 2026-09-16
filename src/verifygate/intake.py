"""Intake: a Monday.com board item becomes a LetterRequest.

The real trigger is a Monday.com automation ("when status changes to
*Requested*, send a webhook"). Monday POSTs a JSON body with an ``event``
object; on first registration it POSTs ``{"challenge": "..."}`` and expects
the same value echoed back. Both shapes are handled here. Column ids are
board-specific -- ``COLUMN_MAP`` is the one place to change them.

Nothing here talks to the warehouse or the model. Intake only decides *what
was asked*; it never decides what is true.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Optional

LETTER_TYPES = {"verification", "attestation"}

# Monday column id -> request field. Edit to match your board.
COLUMN_MAP = {
    "text_employee_id": "employee_id",
    "status_letter_type": "letter_type",
    "email_requester": "requester_email",
    "text_requester_name": "requester_name",
    "text_addressee": "addressee",
    "checkbox_include_comp": "include_compensation",
}

_LETTER_ALIASES = {
    "employment verification": "verification",
    "verification letter": "verification",
    "verification": "verification",
    "background check attestation": "attestation",
    "attestation": "attestation",
}


@dataclass(frozen=True)
class LetterRequest:
    request_id: str
    employee_id: str
    letter_type: str                 # verification | attestation
    requester_name: str
    requester_email: str
    addressee: str = "To Whom It May Concern"
    include_compensation: bool = False
    monday_item_id: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "LetterRequest":
        return LetterRequest(**{k: d[k] for k in LetterRequest.__dataclass_fields__ if k in d})


class IntakeError(ValueError):
    pass


def _column_text(col: dict) -> str:
    """Monday column values arrive in several shapes; reduce to a string."""
    if col is None:
        return ""
    if isinstance(col, str):
        return col
    for key in ("text", "label", "email", "value"):
        v = col.get(key)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            inner = v.get("label") or v.get("text") or v.get("email")
            if inner:
                return str(inner)
    if col.get("checked") in (True, "true"):
        return "true"
    return ""


def parse_monday_webhook(body: dict) -> dict | LetterRequest:
    """Return ``{"challenge": ...}`` for the handshake, else a LetterRequest."""
    if "challenge" in body:
        return {"challenge": body["challenge"]}
    event = body.get("event") or {}
    if not event:
        raise IntakeError("no event in webhook body")
    item_id = str(event.get("pulseId") or event.get("itemId") or "")
    cols = event.get("columnValues") or {}
    fields: dict = {}
    for col_id, target in COLUMN_MAP.items():
        fields[target] = _column_text(cols.get(col_id))
    return build_request(monday_item_id=item_id or None, **fields)


def build_request(*, employee_id: str, letter_type: str, requester_email: str,
                  requester_name: str = "", addressee: str = "",
                  include_compensation: str | bool = False,
                  monday_item_id: Optional[str] = None) -> LetterRequest:
    employee_id = (employee_id or "").strip().upper()
    if not re.fullmatch(r"E\d{5}", employee_id):
        raise IntakeError(f"employee_id {employee_id!r} is not in the E##### format")
    lt = _LETTER_ALIASES.get((letter_type or "").strip().lower())
    if lt not in LETTER_TYPES:
        raise IntakeError(f"letter_type {letter_type!r} is not one of {sorted(LETTER_TYPES)}")
    requester_email = (requester_email or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", requester_email):
        raise IntakeError(f"requester_email {requester_email!r} is not an email address")
    inc = include_compensation in (True, "true", "True", "1", "yes")
    seed = f"{monday_item_id or ''}|{employee_id}|{lt}|{requester_email}|{inc}"
    request_id = "req-" + hashlib.sha256(seed.encode()).hexdigest()[:10]
    return LetterRequest(
        request_id=request_id, employee_id=employee_id, letter_type=lt,
        requester_name=requester_name.strip() or "Requester",
        requester_email=requester_email,
        addressee=addressee.strip() or "To Whom It May Concern",
        include_compensation=inc, monday_item_id=monday_item_id)


def load_request(path) -> LetterRequest:
    body = json.loads(open(path).read())
    if "event" in body or "challenge" in body:
        out = parse_monday_webhook(body)
        if isinstance(out, dict):
            raise IntakeError("that file is a challenge handshake, not a request")
        return out
    return LetterRequest.from_dict(body)
