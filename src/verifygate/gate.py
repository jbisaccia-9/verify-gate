"""The gate. Six rules; any FAIL and nothing leaves the building.

| rule | what it refuses |
|---|---|
| G1 identity    | an employee id that doesn't resolve to exactly one warehouse row, or a draft for a different id |
| G2 facts       | any stated fact that differs from the warehouse, any required fact missing from the text |
| G3 disclosure  | SSN / DOB / address anywhere; salary without consent; any date or dollar amount the warehouse didn't supply |
| G4 fidelity    | a PDF whose text differs from the .docx it was supposedly printed from |
| G5 eligibility | an attestation for someone whose check isn't Cleared; a letter for a status the template can't state |
| G6 approval    | sending without a human approval, or with one whose file hashes no longer match |

The checker grades the *rendered text*, not the draft object. The draft is
what the model claimed; the text is what the recipient reads.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .draft import Draft, expected_facts, forbidden_values
from .intake import LetterRequest
from .render import normalize, text_of_docx, text_of_pdf
from .warehouse import Employee, Warehouse

DATE_RE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|"
                     r"November|December) \d{1,2}, \d{4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
EMPID_RE = re.compile(r"\bE\d{5}\b")


@dataclass
class Finding:
    rule: str
    ok: bool
    detail: str


@dataclass
class Verdict:
    request_id: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(f.ok for f in self.findings)

    def fails(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]

    def add(self, rule: str, ok: bool, detail: str) -> None:
        self.findings.append(Finding(rule, ok, detail))

    def as_dict(self) -> dict:
        return {"request_id": self.request_id, "passed": self.passed,
                "findings": [asdict(f) for f in self.findings]}

    def summary(self) -> str:
        lines = [f"{'PASS' if f.ok else 'FAIL'}  {f.rule:<14} {f.detail}" for f in self.findings]
        head = f"VERIFY GATE: {'PASSED' if self.passed else 'FAILED'} - {self.request_id}"
        return "\n".join(lines + [head])


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(req: LetterRequest, wh: Warehouse, draft: Draft, docx_path: Path, pdf_path: Path,
          approval: Optional[dict] = None, require_approval: bool = False) -> Verdict:
    v = Verdict(req.request_id)

    # G1 identity ---------------------------------------------------------
    emp = wh.lookup(req.employee_id)
    if emp is None:
        v.add("G1 identity", False, f"employee_id {req.employee_id} does not resolve to one warehouse row")
        return v  # nothing below is meaningful without a record
    if draft.employee_id != emp.employee_id or draft.request_id != req.request_id:
        v.add("G1 identity", False,
              f"draft is for {draft.employee_id}/{draft.request_id}, request is {emp.employee_id}/{req.request_id}")
        return v
    v.add("G1 identity", True, f"{emp.employee_id} -> {emp.full_name}")

    docx_text = text_of_docx(docx_path)
    pdf_text = text_of_pdf(pdf_path)

    # G2 facts ------------------------------------------------------------
    want = expected_facts(emp, req)
    problems = []
    if draft.schema_violation:
        problems.append("drafter returned non-conforming output (kept as prose, schema_violation=true)")
    extra = sorted(set(draft.facts) - set(want))
    missing = sorted(set(want) - set(draft.facts))
    wrong = sorted(k for k in set(want) & set(draft.facts) if str(draft.facts[k]) != str(want[k]))
    if extra:
        problems.append(f"facts not permitted for this letter: {extra}")
    if missing:
        problems.append(f"required facts absent from draft: {missing}")
    for k in wrong:
        problems.append(f"{k}: draft says {draft.facts[k]!r}, warehouse says {want[k]!r}")
    for k, val in want.items():
        if val and normalize(str(val)) not in docx_text:
            problems.append(f"{k} {val!r} not stated in rendered text")
    v.add("G2 facts", not problems, "; ".join(problems) or f"{len(want)} facts match the warehouse")

    # G3 disclosure -------------------------------------------------------
    leaks = []
    for k, val in forbidden_values(emp, req).items():
        needles = [normalize(str(val))]
        if k == "home_address":
            needles.append(normalize(str(val).split(",")[0]))   # street line alone is enough to leak
        if any(n in docx_text for n in needles):
            leaks.append(f"{k} present")
    allowed_dates = {normalize(str(x)) for x in want.values() if x and DATE_RE.fullmatch(str(x))}
    allowed_dates.add(normalize(draft.letter_date))
    for m in set(DATE_RE.findall(docx_text)):
        if m not in allowed_dates:
            leaks.append(f"unsourced date {m!r}")
    allowed_money = {str(x) for x in want.values() if x and MONEY_RE.fullmatch(str(x))}
    for m in set(MONEY_RE.findall(docx_text)):
        if m.replace(" ", "") not in allowed_money:
            leaks.append(f"unsourced amount {m!r}")
    for m in set(EMPID_RE.findall(docx_text)):
        if m != emp.employee_id:
            leaks.append(f"foreign employee id {m}")
    v.add("G3 disclosure", not leaks, "; ".join(leaks) or "no forbidden fields, no unsourced dates or amounts")

    # G4 fidelity ---------------------------------------------------------
    drift = []
    for i, p in enumerate(draft.paragraphs, 1):
        np_ = normalize(p)
        if np_ not in docx_text:
            drift.append(f"paragraph {i} not in .docx")
        if np_ not in pdf_text:
            drift.append(f"paragraph {i} not in .pdf")
    for token in (draft.letter_date, draft.request_id):
        if normalize(token) not in pdf_text:
            drift.append(f"{token!r} not in .pdf")
    v.add("G4 fidelity", not drift, "; ".join(drift) or ".docx and .pdf carry the same letter")

    # G5 eligibility ------------------------------------------------------
    inel = []
    if req.letter_type == "attestation":
        if emp.background_check_status != "Cleared":
            inel.append(f"background check is {emp.background_check_status}, not Cleared")
        if not emp.background_check_date:
            inel.append("no background check date on file")
    if req.letter_type == "verification":
        if emp.employment_status not in ("Active", "Terminated", "Leave"):
            inel.append(f"unsupported employment status {emp.employment_status!r}")
        if emp.employment_status == "Terminated" and not emp.termination_date:
            inel.append("terminated with no termination date on file")
    note = ""
    if req.letter_type == "verification" and req.include_compensation and not emp.comp_disclosure_consent:
        note = " (compensation requested; omitted - no disclosure consent on file)"
    v.add("G5 eligibility", not inel, "; ".join(inel) or f"{req.letter_type} is permitted for this record{note}")

    # G6 approval ---------------------------------------------------------
    if require_approval or approval is not None:
        a = approval or {}
        why = []
        if not a:
            why.append("no approval record")
        else:
            if a.get("request_id") != req.request_id:
                why.append("approval is for a different request")
            if not a.get("reviewer"):
                why.append("approval has no reviewer")
            if a.get("docx_sha256") != sha256(docx_path):
                why.append(".docx changed after approval")
            if a.get("pdf_sha256") != sha256(pdf_path):
                why.append(".pdf changed after approval")
            if a.get("gate_passed") is not True:
                why.append("approval was recorded against a failing gate")
        v.add("G6 approval", not why, "; ".join(why) or f"approved by {a.get('reviewer')} at {a.get('approved_at')}")
    return v


def write_grading(v: Verdict, path: Path) -> None:
    path.write_text(json.dumps(v.as_dict(), indent=2))
