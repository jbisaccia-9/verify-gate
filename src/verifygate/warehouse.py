"""The HR data warehouse: the only source of truth the gate will accept.

In production this is a read replica of the HRIS export (Workday/ADP/UKG ->
warehouse). Here it is a SQLite file seeded from ``samples/employees.json`` so
the whole pipeline runs in CI with no credentials. Every fact that appears in a
letter must trace back to a column in this table -- that is the contract the
gate enforces.

Everyone in the seed is fictional.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

SEED = Path(__file__).resolve().parents[2] / "samples" / "employees.json"

COLUMNS = [
    "employee_id", "first_name", "last_name", "job_title", "department",
    "work_location", "employment_type", "employment_status", "hire_date",
    "termination_date", "annual_salary", "comp_disclosure_consent",
    "background_check_status", "background_check_date", "background_check_vendor",
    "ssn_last4", "date_of_birth", "home_address", "manager_name",
]


@dataclass(frozen=True)
class Employee:
    employee_id: str
    first_name: str
    last_name: str
    job_title: str
    department: str
    work_location: str
    employment_type: str            # Full-time / Part-time / Per-diem
    employment_status: str          # Active / Terminated / Leave
    hire_date: str                  # ISO yyyy-mm-dd
    termination_date: Optional[str]
    annual_salary: int
    comp_disclosure_consent: bool   # employee signed the salary-release form
    background_check_status: str    # Cleared / Pending / Adverse
    background_check_date: Optional[str]
    background_check_vendor: Optional[str]
    ssn_last4: str                  # never appears in any letter
    date_of_birth: str              # never appears in any letter
    home_address: str               # never appears in any letter
    manager_name: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


class Warehouse:
    """Thin read-only accessor. ``lookup`` is the single entry point."""

    def __init__(self, db_path: Path | str = ":memory:", seed: Path = SEED):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._ensure(seed)

    def _ensure(self, seed: Path) -> None:
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='employees'")
        if cur.fetchone():
            return
        cols = ", ".join(f"{c} TEXT" for c in COLUMNS)
        self.conn.execute(f"CREATE TABLE employees ({cols}, PRIMARY KEY (employee_id))")
        rows = json.loads(seed.read_text())
        for r in rows:
            r = {c: r.get(c) for c in COLUMNS}
            r["comp_disclosure_consent"] = "1" if r["comp_disclosure_consent"] else "0"
            self.conn.execute(
                f"INSERT INTO employees ({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})",
                [r[c] for c in COLUMNS])
        self.conn.commit()

    def lookup(self, employee_id: str) -> Optional[Employee]:
        rows = self.conn.execute(
            "SELECT * FROM employees WHERE employee_id = ?", (employee_id.strip(),)).fetchall()
        if len(rows) != 1:
            return None
        r = dict(rows[0])
        r["annual_salary"] = int(r["annual_salary"])
        r["comp_disclosure_consent"] = r["comp_disclosure_consent"] == "1"
        return Employee(**r)

    def all_ids(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT employee_id FROM employees ORDER BY 1")]
