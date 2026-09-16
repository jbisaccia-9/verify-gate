"""Counterexamples: packets that look finished and must be refused.

Each one is a real failure mode from running this workflow, not a synthetic
edge case. The packet's directory name says which rule must catch it, and the
CI job asserts that ``check-dir`` on this directory exits non-zero.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .draft import Draft, make_draft
from .intake import build_request
from .pipeline import Packet, approve, prepare
from .render import render_all, render_docx, render_pdf
from .warehouse import Warehouse

FIXED_DATE = "September 15, 2026"


def _req(eid, lt, comp=False, tag=""):
    return build_request(employee_id=eid, letter_type=lt, requester_email=f"req{tag}@example.org",
                         requester_name="Example Requester", include_compensation=comp,
                         monday_item_id=f"cx-{eid}-{lt}-{comp}")


def _rewrite(pk: Packet, d: Draft):
    pk.draft_path.write_text(json.dumps(d.as_dict(), indent=2))
    render_all(d, pk.dir)


def build(root: Path, wh: Warehouse) -> list[str]:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    made = []

    # g1-unknown-employee: Monday item typed an id that isn't in the warehouse.
    r = _req("E10999", "verification", tag="1")
    prepare(r, wh, root, letter_date=FIXED_DATE)
    made.append(r.request_id)

    # g2-hallucinated-hire-date: the model "remembered" a different start date
    # and wrote it into the prose while echoing the correct fact back. The
    # rendered text is what's graded, so the echo doesn't save it.
    r = _req("E10021", "verification", tag="2")
    pk, _ = prepare(r, wh, root, drafter="template", letter_date=FIXED_DATE)
    d = pk.draft()
    d.paragraphs = [p.replace("March 8, 2021", "March 18, 2021") for p in d.paragraphs]
    _rewrite(pk, d)
    made.append(r.request_id)

    # g3-salary-without-consent: requester asked for compensation, employee never
    # signed the release; a "helpful" draft included it anyway.
    r = _req("E10077", "verification", comp=True, tag="3")
    pk, _ = prepare(r, wh, root, drafter="template", letter_date=FIXED_DATE)
    d = pk.draft()
    d.paragraphs.insert(1, "Their current annual base salary is $41,600.00.")
    _rewrite(pk, d)
    made.append(r.request_id)

    # g4-stale-pdf: the .docx was regenerated after an edit, the PDF was not.
    r = _req("E10103", "verification", comp=True, tag="4")
    pk, _ = prepare(r, wh, root, drafter="template", letter_date=FIXED_DATE)
    d = pk.draft()
    docx_path, pdf_path = pk.docs()
    d.paragraphs[-1] = d.paragraphs[-1] + " Updated per requester on September 15, 2026."
    pk.draft_path.write_text(json.dumps(d.as_dict(), indent=2))
    render_docx(d, docx_path)          # PDF deliberately left as-is
    made.append(r.request_id)

    # g5-attestation-pending-check: a new hire whose Checkr screen hasn't come back.
    r = _req("E10150", "attestation", tag="5")
    prepare(r, wh, root, drafter="template", letter_date=FIXED_DATE)
    made.append(r.request_id)

    # g6-edited-after-approval: a reviewer approved, then someone re-rendered
    # the files. The approval's hashes no longer match; send must refuse.
    r = _req("E10188", "verification", tag="6")
    pk, _ = prepare(r, wh, root, drafter="template", letter_date=FIXED_DATE)
    approve(pk, wh, reviewer="Dana Whitcombe")
    d = pk.draft()
    d.letter_date = "September 16, 2026"   # innocuous-looking; still not what was signed
    _rewrite(pk, d)
    made.append(r.request_id)

    (root / "MANIFEST.json").write_text(json.dumps({
        "g1-unknown-employee": made[0], "g2-hallucinated-hire-date": made[1],
        "g3-salary-without-consent": made[2], "g4-stale-pdf": made[3],
        "g5-attestation-pending-check": made[4], "g6-edited-after-approval": made[5],
    }, indent=2))
    return made
