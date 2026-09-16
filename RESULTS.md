# Results

Generated 2026-09-16 by `scripts/make_results.py` — every block below is captured command output, not prose.

## Unit tests

`python -m pytest -q` — exit 0, OK

```
.....................                                                    [100%]
21 passed in 1.83s
```

## Happy path: every seed employee, both letter types

`python -m verifygate demo --out out/packets --drafter template --letter-date "September 15, 2026"` — exit 0, OK

```
req-a2361e78be  Marisol Okafor-Reyes   verification  PASS
req-7cd504676c  Marisol Okafor-Reyes   attestation   PASS
req-4dde6f6c63  Tomas Lindqvist        verification  PASS
req-0c2784b4e4  Tomas Lindqvist        attestation   PASS
req-094b5bd324  Priya Venkataraman     verification  PASS
req-205cffbedf  Priya Venkataraman     attestation   PASS
req-7439022f5a  Andre Castellanos      verification  PASS
req-48c40aca1d  Helen Adeyemi          verification  PASS
req-204102b389  Helen Adeyemi          attestation   PASS
VERIFY GATE demo: all packets cleared -> out/packets
```

## Lifecycle: prepare from the Monday.com webhook sample

`python -m verifygate prepare samples/monday_webhook.json --drafter template --letter-date "September 15, 2026"` — exit 0, OK

```
packet -> out/packets/req-55b477a426
PASS  G1 identity    E10021 -> Marisol Okafor-Reyes
PASS  G2 facts       9 facts match the warehouse
PASS  G3 disclosure  no forbidden fields, no unsourced dates or amounts
PASS  G4 fidelity    .docx and .pdf carry the same letter
PASS  G5 eligibility verification is permitted for this record
VERIFY GATE: PASSED - req-55b477a426
```

## Lifecycle: send before approval is refused

`python -m verifygate send req-55b477a426 --dry-run` — expected non-zero exit, OK

```
refusing to send:
PASS  G1 identity    E10021 -> Marisol Okafor-Reyes
PASS  G2 facts       9 facts match the warehouse
PASS  G3 disclosure  no forbidden fields, no unsourced dates or amounts
PASS  G4 fidelity    .docx and .pdf carry the same letter
PASS  G5 eligibility verification is permitted for this record
FAIL  G6 approval    no approval record
VERIFY GATE: FAILED - req-55b477a426
```

## Lifecycle: a human approves the exact bytes

`python -m verifygate approve req-55b477a426 --reviewer "Dana Whitcombe"` — exit 0, OK

```
PASS  G1 identity    E10021 -> Marisol Okafor-Reyes
PASS  G2 facts       9 facts match the warehouse
PASS  G3 disclosure  no forbidden fields, no unsourced dates or amounts
PASS  G4 fidelity    .docx and .pdf carry the same letter
PASS  G5 eligibility verification is permitted for this record
VERIFY GATE: PASSED - req-55b477a426
approved by Dana Whitcombe
```

## Lifecycle: send after approval (dry run writes the .eml)

`python -m verifygate send req-55b477a426 --dry-run` — exit 0, OK

```
PASS  G1 identity    E10021 -> Marisol Okafor-Reyes
PASS  G2 facts       9 facts match the warehouse
PASS  G3 disclosure  no forbidden fields, no unsourced dates or amounts
PASS  G4 fidelity    .docx and .pdf carry the same letter
PASS  G5 eligibility verification is permitted for this record
PASS  G6 approval    approved by Dana Whitcombe at 2026-09-16T03:07:21+00:00
VERIFY GATE: PASSED - req-55b477a426
dry-run: wrote out/packets/req-55b477a426/sent.eml
```

## Build the counterexamples

`python -m verifygate counterexamples out/counterexamples` — exit 0, OK

```
built 6 counterexample packets -> out/counterexamples
```

## Gate refuses every counterexample, each for its own rule

`python -m verifygate check-dir out/counterexamples` — expected non-zero exit, OK

```
FAIL  g1-unknown-employee  <- G1 identity: employee_id E10999 does not resolve to one warehouse row
FAIL  g2-hallucinated-hire-date  <- G2 facts: hire_date 'March 8, 2021' not stated in rendered text; G3 disclosure: unsourced date 'March 18, 2021'
FAIL  g3-salary-without-consent  <- G3 disclosure: unsourced amount '$41,600.00'
FAIL  g4-stale-pdf  <- G4 fidelity: paragraph 3 not in .pdf
FAIL  g5-attestation-pending-check  <- G5 eligibility: background check is Pending, not Cleared; no background check date on file
FAIL  g6-edited-after-approval  <- G6 approval: .docx changed after approval; .pdf changed after approval
VERIFY GATE: FAILED - 6 of 6 packet(s) refused; do not send.
```
