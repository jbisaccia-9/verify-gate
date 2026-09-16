"""The packet: one directory per request, every stage leaves a file behind.

    out/<request_id>/
      request.json     what Monday asked for
      draft.json       what the drafter produced (facts + paragraphs)
      <id>-<type>.docx / .pdf
      grading.json     the gate's last verdict
      review.md        what the human reads
      approval.json    who approved, when, and the hashes of exactly what they saw
      sent.eml         the email that went out (or would have, in dry-run)

``send`` re-runs the gate with ``require_approval=True``. An approval is not
a checkbox -- it is a signature over the bytes of the two files.
"""
from __future__ import annotations

import json
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from .draft import COMPANY, Draft, make_draft
from .gate import Verdict, check, sha256, write_grading
from .intake import LetterRequest
from .render import TITLES, render_all
from .warehouse import Warehouse


class Packet:
    def __init__(self, root: Path, request_id: str):
        self.dir = Path(root) / request_id
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- files
    @property
    def request_path(self): return self.dir / "request.json"
    @property
    def draft_path(self): return self.dir / "draft.json"
    @property
    def grading_path(self): return self.dir / "grading.json"
    @property
    def approval_path(self): return self.dir / "approval.json"
    @property
    def review_path(self): return self.dir / "review.md"
    @property
    def eml_path(self): return self.dir / "sent.eml"

    def request(self) -> LetterRequest:
        return LetterRequest.from_dict(json.loads(self.request_path.read_text()))

    def draft(self) -> Draft:
        return Draft.from_dict(json.loads(self.draft_path.read_text()))

    def docs(self) -> tuple[Path, Path]:
        d = self.draft()
        stem = f"{d.request_id}-{d.letter_type}"
        return self.dir / f"{stem}.docx", self.dir / f"{stem}.pdf"

    def approval(self) -> Optional[dict]:
        return json.loads(self.approval_path.read_text()) if self.approval_path.exists() else None

    @staticmethod
    def open(root: Path, request_id: str) -> "Packet":
        p = Packet(root, request_id)
        if not p.request_path.exists():
            raise FileNotFoundError(f"no packet {request_id} under {root}")
        return p


def prepare(req: LetterRequest, wh: Warehouse, root: Path, *, drafter: str = "auto",
            letter_date: Optional[str] = None) -> tuple[Packet, Verdict]:
    """Intake -> lookup -> draft -> render -> gate. Never sends."""
    pk = Packet(root, req.request_id)
    pk.request_path.write_text(json.dumps(req.as_dict(), indent=2))
    emp = wh.lookup(req.employee_id)
    if emp is None:
        # Still produce a grading so the refusal is on disk and visible to the requester's board.
        v = Verdict(req.request_id)
        v.add("G1 identity", False, f"employee_id {req.employee_id} does not resolve to one warehouse row")
        write_grading(v, pk.grading_path)
        return pk, v
    d = make_draft(emp, req, drafter=drafter, letter_date=letter_date)
    pk.draft_path.write_text(json.dumps(d.as_dict(), indent=2))
    docx_path, pdf_path = render_all(d, pk.dir)
    v = check(req, wh, d, docx_path, pdf_path)
    write_grading(v, pk.grading_path)
    pk.review_path.write_text(review_markdown(req, emp.full_name, d, v, docx_path, pdf_path))
    return pk, v


def regrade(pk: Packet, wh: Warehouse, *, require_approval: bool = False) -> Verdict:
    req, d = pk.request(), pk.draft()
    docx_path, pdf_path = pk.docs()
    v = check(req, wh, d, docx_path, pdf_path, approval=pk.approval(), require_approval=require_approval)
    write_grading(v, pk.grading_path)
    return v


def review_markdown(req, full_name, d, v, docx_path, pdf_path) -> str:
    lines = [f"# Review: {TITLES[d.letter_type]} for {full_name}", "",
             f"- Request: `{req.request_id}` (Monday item {req.monday_item_id or 'n/a'})",
             f"- Requester: {req.requester_name} <{req.requester_email}>",
             f"- Drafter: {d.drafter}", f"- Files: `{docx_path.name}`, `{pdf_path.name}`", "",
             "## Facts the gate verified against the warehouse", ""]
    lines += [f"- **{k}**: {val}" for k, val in d.facts.items()]
    lines += ["", "## Gate", "", "```", v.summary(), "```", "", "## Letter body", ""]
    lines += [p + "\n" for p in d.paragraphs]
    lines += ["To approve: `python -m verifygate approve " + req.request_id + " --reviewer \"Your Name\"`"]
    return "\n".join(lines)


def approve(pk: Packet, wh: Warehouse, reviewer: str) -> Verdict:
    """A human signs the exact bytes they reviewed. Refuses to sign a failing packet."""
    v = regrade(pk, wh)
    if not v.passed:
        raise PermissionError("refusing to record approval on a packet that fails the gate:\n" + v.summary())
    docx_path, pdf_path = pk.docs()
    pk.approval_path.write_text(json.dumps({
        "request_id": pk.request().request_id,
        "reviewer": reviewer,
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "docx_sha256": sha256(docx_path),
        "pdf_sha256": sha256(pdf_path),
        "gate_passed": True,
    }, indent=2))
    return v


def send(pk: Packet, wh: Warehouse, *, dry_run: Optional[bool] = None) -> Verdict:
    """Re-run every rule plus G6. Only a fully passing verdict produces an email."""
    v = regrade(pk, wh, require_approval=True)
    if not v.passed:
        raise PermissionError("refusing to send:\n" + v.summary())
    req, d = pk.request(), pk.draft()
    docx_path, pdf_path = pk.docs()
    msg = EmailMessage()
    msg["From"] = COMPANY["hr_email"]
    msg["To"] = req.requester_email
    msg["Subject"] = f"{TITLES[d.letter_type]} - ref {req.request_id}"
    msg.set_content(
        f"Hello {req.requester_name},\n\nAttached is the {TITLES[d.letter_type].lower()} you requested "
        f"(reference {req.request_id}), in Word and PDF. It was reviewed and approved by "
        f"{pk.approval()['reviewer']} of People Operations.\n\nRegards,\n{COMPANY['name']} People Operations\n")
    msg.add_attachment(docx_path.read_bytes(), maintype="application",
                       subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
                       filename=docx_path.name)
    msg.add_attachment(pdf_path.read_bytes(), maintype="application", subtype="pdf", filename=pdf_path.name)
    pk.eml_path.write_bytes(bytes(msg))
    if dry_run is None:
        dry_run = not os.environ.get("SMTP_HOST")
    if not dry_run:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.send_message(msg)
    return v
