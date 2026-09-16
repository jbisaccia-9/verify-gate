# Contributing

Thanks for considering a contribution. This repo is part of a small family of "-gate" projects that each enforce one measured bar before letting something ship — please keep contributions in that spirit.

## Ground rules

- **The gate must still pass.** Every PR needs to pass the existing gate/eval suite (CI on this repo). Making the gate stricter or changing what it measures is welcome — but the gate itself has to still run and produce a real pass/fail, in both directions: the demo packets must clear, the counterexamples must be refused.
- **New rule, new counterexample.** If you add a rule to `gate.py`, add a packet to `counterexamples.py` that only that rule catches, and a test that makes it fire and a test that lets it pass.
- **Data stays synthetic.** Every employee, company, address, vendor, and email in this repo is fictional. Don't add real PII, real credentials, real HRIS exports, or anything proprietary. `.env.example` lists variable *names* only — never commit a filled `.env`.

## Getting started

1. Fork the repo and clone your fork.
2. Follow the Quickstart in the README to get a working local environment (no API key required — the template drafter runs the whole pipeline).
3. Run `pytest`, the demo, and the counterexamples locally before opening a PR.
4. Open a PR against `main`. Small, focused PRs are easier to review than large ones.

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
