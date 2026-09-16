"""Every rule has a test that makes it fire and a test that lets it pass."""
import json
from pathlib import Path

import pytest

from verifygate import Warehouse, build_request
from verifygate.draft import expected_facts
from verifygate.gate import check
from verifygate.intake import IntakeError, parse_monday_webhook
from verifygate.pipeline import approve, prepare, send
from verifygate.render import render_all, render_docx, text_of_docx, text_of_pdf

DATE = "September 15, 2026"


@pytest.fixture
def wh():
    return Warehouse(":memory:")


def req(eid="E10021", lt="verification", comp=False):
    return build_request(employee_id=eid, letter_type=lt, requester_email="t@example.org",
                         requester_name="Test", include_compensation=comp,
                         monday_item_id=f"t-{eid}-{lt}-{comp}")


def packet(tmp_path, wh, r):
    return prepare(r, wh, tmp_path, drafter="template", letter_date=DATE)


# ---------------------------------------------------------------- intake

def test_monday_webhook_parses_to_request():
    body = json.loads((Path(__file__).parents[1] / "samples" / "monday_webhook.json").read_text())
    r = parse_monday_webhook(body)
    assert r.employee_id == "E10021" and r.letter_type == "verification" and r.include_compensation
    assert r.monday_item_id == "9911002233"


def test_monday_challenge_is_echoed():
    assert parse_monday_webhook({"challenge": "abc"}) == {"challenge": "abc"}


def test_intake_rejects_bad_id_and_type():
    with pytest.raises(IntakeError):
        build_request(employee_id="10021", letter_type="verification", requester_email="a@b.co")
    with pytest.raises(IntakeError):
        build_request(employee_id="E10021", letter_type="reference", requester_email="a@b.co")


# ------------------------------------------------------------ happy paths

@pytest.mark.parametrize("lt", ["verification", "attestation"])
def test_every_cleared_employee_passes(tmp_path, wh, lt):
    for eid in wh.all_ids():
        emp = wh.lookup(eid)
        if lt == "attestation" and emp.background_check_status != "Cleared":
            continue
        _, v = packet(tmp_path, wh, req(eid, lt, comp=True))
        assert v.passed, v.summary()


def test_salary_only_with_consent(tmp_path, wh):
    _, v_yes = packet(tmp_path, wh, req("E10021", comp=True))    # consent on file
    pk_no, v_no = packet(tmp_path, wh, req("E10077", comp=True))  # no consent
    assert v_yes.passed and v_no.passed
    assert "$41,600.00" not in text_of_docx(pk_no.docs()[0])
    assert "annual_salary" not in pk_no.draft().facts


def test_terminated_letter_states_end_date(tmp_path, wh):
    pk, v = packet(tmp_path, wh, req("E10103"))
    assert v.passed
    assert "December 12, 2025" in text_of_docx(pk.docs()[0])


# ------------------------------------------------------------- refusals

def test_g1_unknown_employee(tmp_path, wh):
    _, v = packet(tmp_path, wh, req("E10999"))
    assert [f.rule for f in v.fails()] == ["G1 identity"]


def test_g2_draft_that_echoes_correct_fact_but_writes_wrong_one(tmp_path, wh):
    pk, _ = packet(tmp_path, wh, req("E10021"))
    d = pk.draft()
    d.paragraphs = [p.replace("March 8, 2021", "March 18, 2021") for p in d.paragraphs]
    render_all(d, pk.dir)
    v = check(pk.request(), wh, d, *pk.docs())
    rules = {f.rule for f in v.fails()}
    assert "G2 facts" in rules and "G3 disclosure" in rules


def test_g2_draft_that_lies_in_the_facts_block(tmp_path, wh):
    pk, _ = packet(tmp_path, wh, req("E10021"))
    d = pk.draft()
    d.facts["job_title"] = "Staff Engineer"
    v = check(pk.request(), wh, d, *pk.docs())
    assert any(f.rule == "G2 facts" and "job_title" in f.detail for f in v.fails())


@pytest.mark.parametrize("leak", ["4417", "November 2, 1988", "1490 Larkspur Ct"])
def test_g3_never_these(tmp_path, wh, leak):
    pk, _ = packet(tmp_path, wh, req("E10021"))
    d = pk.draft()
    d.paragraphs.append(f"For reference: {leak}.")
    render_all(d, pk.dir)
    v = check(pk.request(), wh, d, *pk.docs())
    assert any(f.rule == "G3 disclosure" for f in v.fails())


def test_g3_unsourced_date_is_refused_even_if_facts_are_right(tmp_path, wh):
    pk, _ = packet(tmp_path, wh, req("E10021"))
    d = pk.draft()
    d.paragraphs.append("Their last performance review was on January 4, 2026.")
    render_all(d, pk.dir)
    v = check(pk.request(), wh, d, *pk.docs())
    assert any("unsourced date" in f.detail for f in v.fails())


def test_g4_stale_pdf(tmp_path, wh):
    pk, _ = packet(tmp_path, wh, req("E10103", comp=True))
    d = pk.draft()
    d.paragraphs[-1] += " Updated per requester."
    render_docx(d, pk.docs()[0])
    v = check(pk.request(), wh, d, *pk.docs())
    assert [f.rule for f in v.fails()] == ["G4 fidelity"]


def test_g5_attestation_needs_cleared_check(tmp_path, wh):
    _, v = packet(tmp_path, wh, req("E10150", "attestation"))
    assert [f.rule for f in v.fails()] == ["G5 eligibility"]


def test_g6_send_refuses_without_approval_then_sends_with_it(tmp_path, wh):
    pk, _ = packet(tmp_path, wh, req("E10188"))
    with pytest.raises(PermissionError):
        send(pk, wh, dry_run=True)
    approve(pk, wh, "Dana Whitcombe")
    v = send(pk, wh, dry_run=True)
    assert v.passed and pk.eml_path.exists()
    eml = pk.eml_path.read_bytes()
    assert b"Content-Type: application/pdf" in eml and b"wordprocessingml" in eml


def test_g6_edit_after_approval_invalidates_it(tmp_path, wh):
    pk, _ = packet(tmp_path, wh, req("E10188"))
    approve(pk, wh, "Dana Whitcombe")
    d = pk.draft()
    d.letter_date = "September 16, 2026"
    render_all(d, pk.dir)
    pk.draft_path.write_text(json.dumps(d.as_dict()))
    with pytest.raises(PermissionError) as e:
        send(pk, wh, dry_run=True)
    assert "changed after approval" in str(e.value)


def test_approve_refuses_failing_packet(tmp_path, wh):
    pk, _ = packet(tmp_path, wh, req("E10150", "attestation"))
    with pytest.raises(PermissionError):
        approve(pk, wh, "Dana Whitcombe")
    assert not pk.approval_path.exists()


# --------------------------------------------------------------- render

def test_docx_and_pdf_read_back_identically_on_the_body(tmp_path, wh):
    pk, _ = packet(tmp_path, wh, req("E10021", comp=True))
    docx_t, pdf_t = text_of_docx(pk.docs()[0]), text_of_pdf(pk.docs()[1])
    for p in pk.draft().paragraphs:
        assert p in docx_t and p in pdf_t


def test_expected_facts_never_include_forbidden_columns(wh):
    for eid in wh.all_ids():
        emp = wh.lookup(eid)
        for lt in ("verification", "attestation"):
            f = expected_facts(emp, req(eid, lt, comp=True))
            assert not {"ssn_last4", "date_of_birth", "home_address"} & set(f)
