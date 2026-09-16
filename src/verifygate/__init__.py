"""verify-gate: an HR letter pipeline that refuses to email anything the
warehouse can't corroborate and a human hasn't signed."""
from .gate import Verdict, check
from .intake import LetterRequest, build_request, parse_monday_webhook
from .pipeline import Packet, approve, prepare, send
from .warehouse import Employee, Warehouse

__all__ = ["Verdict", "check", "LetterRequest", "build_request", "parse_monday_webhook",
           "Packet", "approve", "prepare", "send", "Employee", "Warehouse"]
__version__ = "0.1.0"
