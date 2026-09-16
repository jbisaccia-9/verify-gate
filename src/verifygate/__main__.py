"""python -m verifygate <command>

  prepare  <request.json|monday_webhook.json> [--out DIR] [--drafter auto|template|claude]
  check    <request_id> [--out DIR] [--require-approval]
  approve  <request_id> --reviewer NAME [--out DIR]
  send     <request_id> [--out DIR] [--dry-run]
  demo     [--out DIR]           happy path for every seed employee (no send)
  counterexamples <DIR>          build the packets that must be refused
  check-dir <DIR> [--require-approval]   grade every packet; exit 1 if any fails
  serve    [--port 8787] [--out DIR]     Monday.com webhook receiver (stdlib only)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import counterexamples
from .intake import IntakeError, LetterRequest, build_request, load_request, parse_monday_webhook
from .pipeline import Packet, approve, prepare, regrade, send
from .warehouse import Warehouse

DB = os.environ.get("VERIFYGATE_DB", ":memory:")


def _wh() -> Warehouse:
    return Warehouse(DB)


def cmd_prepare(a):
    req = load_request(a.request)
    pk, v = prepare(req, _wh(), Path(a.out), drafter=a.drafter, letter_date=a.letter_date)
    print(f"packet -> {pk.dir}")
    print(v.summary())
    return 0 if v.passed else 1


def cmd_check(a):
    v = regrade(Packet.open(Path(a.out), a.request_id), _wh(), require_approval=a.require_approval)
    print(v.summary())
    return 0 if v.passed else 1


def cmd_approve(a):
    try:
        v = approve(Packet.open(Path(a.out), a.request_id), _wh(), a.reviewer)
    except PermissionError as e:
        print(e)
        return 1
    print(v.summary())
    print(f"approved by {a.reviewer}")
    return 0


def cmd_send(a):
    pk = Packet.open(Path(a.out), a.request_id)
    try:
        v = send(pk, _wh(), dry_run=True if a.dry_run else None)
    except PermissionError as e:
        print(e)
        return 1
    print(v.summary())
    print(f"{'dry-run: wrote' if (a.dry_run or not os.environ.get('SMTP_HOST')) else 'sent'} {pk.eml_path}")
    return 0


def cmd_demo(a):
    wh = _wh()
    rc = 0
    for eid in wh.all_ids():
        emp = wh.lookup(eid)
        for lt in ("verification", "attestation"):
            if lt == "attestation" and emp.background_check_status != "Cleared":
                continue
            req = build_request(employee_id=eid, letter_type=lt, requester_email="demo@example.org",
                                requester_name="Demo Requester", include_compensation=True,
                                monday_item_id=f"demo-{eid}-{lt}")
            pk, v = prepare(req, wh, Path(a.out), drafter=a.drafter, letter_date=a.letter_date)
            print(f"{pk.dir.name}  {emp.full_name:<22} {lt:<13} {'PASS' if v.passed else 'FAIL'}")
            for f in v.fails():
                print(f"    {f.rule}: {f.detail}")
            rc |= 0 if v.passed else 1
    print(f"VERIFY GATE demo: {'all packets cleared' if rc == 0 else 'some packets refused'} -> {a.out}")
    return rc


def cmd_counterexamples(a):
    ids = counterexamples.build(Path(a.dir), _wh())
    print(f"built {len(ids)} counterexample packets -> {a.dir}")
    return 0


def cmd_check_dir(a):
    wh = _wh()
    root = Path(a.dir)
    manifest = {}
    mp = root / "MANIFEST.json"
    if mp.exists():
        manifest = {v: k for k, v in json.loads(mp.read_text()).items()}
    failed = 0
    total = 0
    dirs = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: manifest.get(p.name, p.name))
    for d in dirs:
        total += 1
        pk = Packet.open(root, d.name)
        req = pk.request()
        if not pk.draft_path.exists():           # prepare stopped at G1
            v = json.loads(pk.grading_path.read_text())
            fails = [f for f in v["findings"] if not f["ok"]]
            passed = v["passed"]
        else:
            verdict = regrade(pk, wh, require_approval=a.require_approval)
            fails = [f.__dict__ for f in verdict.fails()]
            passed = verdict.passed
        label = manifest.get(req.request_id, req.request_id)
        why = "; ".join(f"{f['rule']}: {f['detail']}" for f in fails)
        print(f"{'PASS' if passed else 'FAIL'}  {label}" + (f"  <- {why}" if why else ""))
        failed += 0 if passed else 1
    if failed == 0:
        print(f"VERIFY GATE: PASSED - {total} packet(s) cleared.")
    else:
        print(f"VERIFY GATE: FAILED - {failed} of {total} packet(s) refused; do not send.")
    return 0 if failed == 0 else 1


def cmd_serve(a):
    out = Path(a.out)
    wh = _wh()

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n) or b"{}")
            try:
                r = parse_monday_webhook(body)
            except IntakeError as e:
                return self._reply(400, {"error": str(e)})
            if isinstance(r, dict):                       # challenge handshake
                return self._reply(200, r)
            pk, v = prepare(r, wh, out, drafter=a.drafter)
            self._reply(200, {"request_id": r.request_id, "gate_passed": v.passed,
                              "review": str(pk.review_path)})

        def _reply(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            print(f"[{self.address_string()}] {args[0] % args[1:]}")

    print(f"verify-gate webhook listening on :{a.port}, packets -> {out}")
    HTTPServer(("0.0.0.0", a.port), H).serve_forever()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="verifygate", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)

    def out(s):
        s.add_argument("--out", default="out/packets")

    s = sp.add_parser("prepare"); s.add_argument("request"); out(s)
    s.add_argument("--drafter", default="auto", choices=["auto", "template", "claude"])
    s.add_argument("--letter-date", default=None); s.set_defaults(fn=cmd_prepare)
    s = sp.add_parser("check"); s.add_argument("request_id"); out(s)
    s.add_argument("--require-approval", action="store_true"); s.set_defaults(fn=cmd_check)
    s = sp.add_parser("approve"); s.add_argument("request_id"); s.add_argument("--reviewer", required=True)
    out(s); s.set_defaults(fn=cmd_approve)
    s = sp.add_parser("send"); s.add_argument("request_id"); s.add_argument("--dry-run", action="store_true")
    out(s); s.set_defaults(fn=cmd_send)
    s = sp.add_parser("demo"); out(s)
    s.add_argument("--drafter", default="auto", choices=["auto", "template", "claude"])
    s.add_argument("--letter-date", default=None); s.set_defaults(fn=cmd_demo)
    s = sp.add_parser("counterexamples"); s.add_argument("dir"); s.set_defaults(fn=cmd_counterexamples)
    s = sp.add_parser("check-dir"); s.add_argument("dir")
    s.add_argument("--require-approval", action="store_true"); s.set_defaults(fn=cmd_check_dir)
    s = sp.add_parser("serve"); s.add_argument("--port", type=int, default=8787); out(s)
    s.add_argument("--drafter", default="auto"); s.set_defaults(fn=cmd_serve)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
