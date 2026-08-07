"""
spot_check.py — Record manual verification of severance bot results.

Usage:
  python spot_check.py                  # interactive: pick rows from results.csv to verify
  python spot_check.py --mid M1234567   # verify a specific MID
  python spot_check.py --summary        # show spot check stats

Writes to logs/spot_checks.jsonl — one record per verification.
"""

import csv
import json
import os
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_FILE = os.path.join(PROJECT_ROOT, "results.csv")
SPOT_CHECK_FILE = os.path.join(PROJECT_ROOT, "logs", "spot_checks.jsonl")


def _load_results():
    if not os.path.isfile(RESULTS_FILE):
        return []
    with open(RESULTS_FILE, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _load_checks():
    if not os.path.isfile(SPOT_CHECK_FILE):
        return []
    checks = []
    with open(SPOT_CHECK_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                checks.append(json.loads(line))
    return checks


def _save_check(record):
    os.makedirs(os.path.dirname(SPOT_CHECK_FILE), exist_ok=True)
    with open(SPOT_CHECK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def verify_mid(mid, rows=None):
    if rows is None:
        rows = _load_results()

    row = next((r for r in rows if r.get("MID") == mid), None)
    if not row:
        print(f"MID {mid} not found in results.csv")
        return

    print(f"\n{'='*50}")
    print(f"  MID: {row.get('MID')}  |  Ticket: {row.get('Cobra Key')}")
    print(f"  Name: {row.get('Name')}  |  Company: {row.get('Company Name')}")
    print(f"  Agreement Found: {row.get('Agreement Found')}")
    if row.get("Failure Reason"):
        print(f"  Failure Reason: {row.get('Failure Reason')}")
    print(f"{'='*50}")
    print(f"  Medical: {row.get('Medical', '')}  Dental: {row.get('Dental', '')}  Vision: {row.get('Vision', '')}")
    print(f"  Admin Fee: {row.get('Admin Fee', '')}")
    print(f"  Severance: {row.get('Severance Start', '')} → {row.get('Severance End', '')}")
    print(f"  Months: {row.get('# of months ER is paying severance', '')}")
    print(f"  Term date: {row.get('Term date', '')}")
    print(f"{'='*50}\n")

    print("Is this extraction accurate?")
    print("  [1] Correct — all fields match the PDF")
    print("  [2] Partially correct — some fields wrong")
    print("  [3] Incorrect — major errors")
    print("  [4] Skip")

    choice = input("\nChoice (1-4): ").strip()
    if choice == "4":
        print("Skipped.")
        return

    verdict_map = {"1": "correct", "2": "partial", "3": "incorrect"}
    verdict = verdict_map.get(choice)
    if not verdict:
        print("Invalid choice.")
        return

    notes = ""
    if verdict in ("partial", "incorrect"):
        notes = input("What's wrong? (brief note): ").strip()

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mid": mid,
        "ticket": row.get("Cobra Key", ""),
        "agreement_found": row.get("Agreement Found", ""),
        "verdict": verdict,
        "notes": notes,
    }
    _save_check(record)
    print(f"✓ Recorded: {mid} → {verdict}")


def show_summary():
    checks = _load_checks()
    if not checks:
        print("No spot checks recorded yet.")
        return

    total = len(checks)
    correct = sum(1 for c in checks if c["verdict"] == "correct")
    partial = sum(1 for c in checks if c["verdict"] == "partial")
    incorrect = sum(1 for c in checks if c["verdict"] == "incorrect")

    accuracy = correct / total * 100 if total else 0

    print(f"\n{'='*40}")
    print(f"  Spot Check Summary")
    print(f"{'='*40}")
    print(f"  Total verified:   {total}")
    print(f"  Correct:          {correct} ({accuracy:.0f}%)")
    print(f"  Partially correct: {partial}")
    print(f"  Incorrect:        {incorrect}")
    print(f"{'='*40}\n")

    if any(c.get("notes") for c in checks):
        print("  Recent issues:")
        for c in reversed(checks):
            if c.get("notes"):
                print(f"    {c['mid']}: {c['notes']}")
        print()


def interactive():
    rows = _load_results()
    if not rows:
        print("No results.csv found.")
        return

    checks = _load_checks()
    checked_mids = {c["mid"] for c in checks}

    # Show only "Yes" results that haven't been checked yet
    unchecked = [r for r in rows if r.get("Agreement Found") == "Yes" and r.get("MID") not in checked_mids]

    if not unchecked:
        print("All successful extractions have been spot-checked!")
        show_summary()
        return

    print(f"\n{len(unchecked)} extractions haven't been verified yet.")
    print("Showing up to 5 for spot-checking:\n")

    for row in unchecked[:5]:
        verify_mid(row["MID"], rows)
        print()
        cont = input("Continue to next? (y/n): ").strip().lower()
        if cont != "y":
            break

    show_summary()


if __name__ == "__main__":
    if "--summary" in sys.argv:
        show_summary()
    elif "--mid" in sys.argv:
        idx = sys.argv.index("--mid")
        if idx + 1 < len(sys.argv):
            verify_mid(sys.argv[idx + 1])
        else:
            print("Usage: python spot_check.py --mid M1234567")
    else:
        interactive()
