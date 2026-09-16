"""Regenerate RESULTS.md from real command output. Run from the repo root."""
import subprocess, sys, datetime, shutil
from pathlib import Path

DATE = "September 15, 2026"
RID = "req-55b477a426"   # request_id derived from samples/monday_webhook.json
STEPS = [
    ("Unit tests", "python -m pytest -q", 0),
    ("Happy path: every seed employee, both letter types", f'python -m verifygate demo --out out/packets --drafter template --letter-date "{DATE}"', 0),
    ("Lifecycle: prepare from the Monday.com webhook sample", f'python -m verifygate prepare samples/monday_webhook.json --drafter template --letter-date "{DATE}"', 0),
    ("Lifecycle: send before approval is refused", f"python -m verifygate send {RID} --dry-run", 1),
    ("Lifecycle: a human approves the exact bytes", f'python -m verifygate approve {RID} --reviewer "Dana Whitcombe"', 0),
    ("Lifecycle: send after approval (dry run writes the .eml)", f"python -m verifygate send {RID} --dry-run", 0),
    ("Build the counterexamples", "python -m verifygate counterexamples out/counterexamples", 0),
    ("Gate refuses every counterexample, each for its own rule", "python -m verifygate check-dir out/counterexamples", 1),
]

shutil.rmtree("out", ignore_errors=True)
out = [f"# Results\n\nGenerated {datetime.date.today()} by `scripts/make_results.py` — every block below is captured command output, not prose.\n"]
ok_all = True
for title, cmd, want in STEPS:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    good = (r.returncode == 0) if want == 0 else (r.returncode != 0)
    ok_all &= good
    exp = "exit 0" if want == 0 else "expected non-zero exit"
    body = (r.stdout + r.stderr).strip()
    if "pytest" in cmd:
        body = "\n".join(l for l in body.splitlines() if not l.startswith("=") or "passed" in l)
    out.append(f"## {title}\n\n`{cmd}` — {exp}, {'OK' if good else 'UNEXPECTED'}\n\n```\n{body}\n```\n")
Path("RESULTS.md").write_text("\n".join(out))
print("wrote RESULTS.md", "OK" if ok_all else "WITH UNEXPECTED RESULTS")
sys.exit(0 if ok_all else 1)
