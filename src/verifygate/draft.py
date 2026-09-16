"""Drafting.

The split that makes the gate possible: **facts are computed, prose is
generated.** ``expected_facts`` derives, deterministically, the exact set of
facts a given letter type is *allowed and required* to state for a given
employee. The drafter (Claude when ``ANTHROPIC_API_KEY`` is set, a template
otherwise) writes the paragraphs around those facts and echoes them back in a
``facts`` block. The gate then checks the prose against the computed facts --
the model never gets to be the source of any date, title, or dollar amount.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

from .intake import LetterRequest
from .warehouse import Employee

COMPANY = {
    "name": "Northlight Systems",     # fictional, as in the other -gate repos
    "address": "4100 Meridian Center Dr, Suite 300, Columbus, OH 43215",
    "hr_email": "hr-verifications@northlight.example",
    "hr_phone": "(614) 555-0142",
    "signer_name": "Dana Whitcombe",
    "signer_title": "Director, People Operations",
}

# Columns that must never appear in any letter, regardless of type.
FORBIDDEN_COLUMNS = ("ssn_last4", "date_of_birth", "home_address")


def fmt_date(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    return date.fromisoformat(iso).strftime("%B %-d, %Y")


def fmt_money(n: int) -> str:
    return f"${n:,.2f}"


@dataclass
class Draft:
    request_id: str
    letter_type: str
    employee_id: str
    letter_date: str
    addressee: str
    facts: dict
    paragraphs: list[str]
    drafter: str = "template"
    schema_violation: bool = False
    raw: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Draft":
        return Draft(**{k: d[k] for k in Draft.__dataclass_fields__ if k in d})


class DraftError(ValueError):
    pass


def expected_facts(emp: Employee, req: LetterRequest) -> dict:
    """The complete, ordered set of facts this letter must state -- nothing more."""
    base = {
        "full_name": emp.full_name,
        "employee_id": emp.employee_id,
        "job_title": emp.job_title,
        "department": emp.department,
        "hire_date": fmt_date(emp.hire_date),
    }
    if req.letter_type == "verification":
        base.update({
            "employment_type": emp.employment_type,
            "employment_status": emp.employment_status,
            "work_location": emp.work_location,
        })
        if emp.employment_status == "Terminated":
            base["termination_date"] = fmt_date(emp.termination_date)
        if req.include_compensation and emp.comp_disclosure_consent:
            base["annual_salary"] = fmt_money(emp.annual_salary)
        return base
    if req.letter_type == "attestation":
        base.update({
            "background_check_status": emp.background_check_status,
            "background_check_date": fmt_date(emp.background_check_date),
            "background_check_vendor": emp.background_check_vendor,
        })
        return base
    raise DraftError(f"unknown letter type {req.letter_type}")


def forbidden_values(emp: Employee, req: LetterRequest) -> dict:
    """Values whose presence in the rendered text is a refusal by itself."""
    out = {c: getattr(emp, c) for c in FORBIDDEN_COLUMNS}
    out["date_of_birth_long"] = fmt_date(emp.date_of_birth)
    if not (req.include_compensation and emp.comp_disclosure_consent):
        out["annual_salary"] = fmt_money(emp.annual_salary)
        out["annual_salary_bare"] = f"{emp.annual_salary:,}"
    return {k: v for k, v in out.items() if v}


# ----------------------------------------------------------------- drafters

def _template_paragraphs(f: dict, req: LetterRequest) -> list[str]:
    c = COMPANY["name"]
    if req.letter_type == "verification":
        status = f["employment_status"]
        if status == "Terminated":
            tense = (f"was employed by {c} from {f['hire_date']} through {f['termination_date']} "
                     f"as a {f['job_title']} in the {f['department']} department, "
                     f"working {f['employment_type']} out of our {f['work_location']} location.")
        elif status == "Leave":
            tense = (f"has been employed by {c} since {f['hire_date']} as a {f['job_title']} "
                     f"in the {f['department']} department, working {f['employment_type']} out of "
                     f"our {f['work_location']} location, and is currently on an approved leave of absence.")
        else:
            tense = (f"has been employed by {c} since {f['hire_date']} and is currently a "
                     f"{f['employment_type']} {f['job_title']} in the {f['department']} department "
                     f"at our {f['work_location']} location.")
        ps = [
            f"This letter confirms that {f['full_name']} (Employee ID {f['employee_id']}) {tense} "
            f"Employment status of record: {status}.",
        ]
        if "annual_salary" in f:
            ps.append(f"{f['full_name']} has authorized the release of compensation information. "
                      f"Their current annual base salary is {f['annual_salary']}.")
        else:
            ps.append("Compensation details are not included in this letter. They may be released "
                      "only with the employee's written authorization on file with People Operations.")
        ps.append(f"This verification is provided at the request of {req.requester_name} and reflects "
                  f"our records as of the date above. Please contact People Operations at "
                  f"{COMPANY['hr_email']} or {COMPANY['hr_phone']} with any questions.")
        return ps
    # attestation
    ps = [
        f"{c} attests that {f['full_name']} (Employee ID {f['employee_id']}), {f['job_title']} in the "
        f"{f['department']} department, hired {f['hire_date']}, completed a pre-employment background "
        f"screening conducted by {f['background_check_vendor']}.",
        f"The screening was completed on {f['background_check_date']} with a result of "
        f"{f['background_check_status']}. It was performed in accordance with the Fair Credit Reporting "
        f"Act and applicable state requirements, and the results are retained by People Operations.",
        f"This attestation is provided at the request of {req.requester_name}. Individual report contents "
        f"are confidential and are not disclosed with this letter. Questions may be directed to "
        f"{COMPANY['hr_email']} or {COMPANY['hr_phone']}.",
    ]
    return ps


SYSTEM_PROMPT = """You draft HR letters for Northlight Systems. You will receive a
letter type and a JSON object of FACTS. Write 2-4 professional paragraphs that state every fact
exactly as given (same spelling, same date format, same dollar format). Do not add any date,
number, title, location, or personal detail that is not in FACTS. Do not mention salary unless
FACTS contains annual_salary. Respond with ONLY a JSON object: {"facts": <echo of FACTS>,
"paragraphs": ["...", "..."]} -- no markdown, no preamble."""


def _claude_paragraphs(f: dict, req: LetterRequest) -> tuple[list[str], dict, str, bool]:
    import anthropic  # optional dependency
    client = anthropic.Anthropic()
    user = json.dumps({"letter_type": req.letter_type, "requester": req.requester_name,
                       "addressee": req.addressee, "FACTS": f}, indent=2)
    msg = client.messages.create(
        model=os.environ.get("VERIFYGATE_MODEL", "claude-sonnet-4-5"),
        max_tokens=1200, temperature=0,
        system=SYSTEM_PROMPT, messages=[{"role": "user", "content": user}])
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    clean = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        obj = json.loads(clean)
        paras = [str(p) for p in obj["paragraphs"]]
        facts = dict(obj.get("facts") or {})
        return paras, facts, raw, False
    except (json.JSONDecodeError, KeyError, TypeError):
        # Schema violations are data, not exceptions: keep the prose, flag it,
        # and let the gate refuse it for whatever it actually gets wrong.
        return [clean], {}, raw, True


def make_draft(emp: Employee, req: LetterRequest, *, drafter: str = "auto",
               letter_date: Optional[str] = None) -> Draft:
    f = expected_facts(emp, req)
    letter_date = letter_date or date.today().strftime("%B %-d, %Y")
    use_claude = drafter == "claude" or (drafter == "auto" and os.environ.get("ANTHROPIC_API_KEY"))
    if use_claude:
        paras, echoed, raw, bad = _claude_paragraphs(f, req)
        return Draft(req.request_id, req.letter_type, emp.employee_id, letter_date, req.addressee,
                     echoed or {}, paras, drafter="claude", schema_violation=bad, raw=raw)
    return Draft(req.request_id, req.letter_type, emp.employee_id, letter_date, req.addressee,
                 dict(f), _template_paragraphs(f, req), drafter="template")
