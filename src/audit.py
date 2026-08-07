"""Audit trail for severance bot runs — appends rows to logs/audit_log.csv and Google Sheets."""

import csv
import json
import os
import time
import string
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT_DIR = os.path.join(PROJECT_ROOT, "logs")
AUDIT_FILE = os.path.join(AUDIT_DIR, "audit_log.csv")

AUDIT_COLUMNS = [
    "Run ID",
    "Date",
    "Time (UTC)",
    "Trigger",
    "Status",
    "Total MIDs",
    "Agreement Found",
    "No Agreement",
    "Manual Review Needed",
    "Errors",
    "Duration (sec)",
    "Ticket",
    "MID",
    "Name",
    "Result",
    "Failure Reason",
    "Manual Review",
]


@dataclass
class AuditRecord:
    run_id: str
    timestamp: str
    trigger: str
    total_mids: int = 0
    source: str = ""
    tickets: list = field(default_factory=list)
    status: str = "success"
    errors: list = field(default_factory=list)
    results_total: int = 0
    results_agreement_found: int = 0
    results_no_agreement: int = 0
    results_manual_review: int = 0
    results_errors: int = 0
    result_rows: list = field(default_factory=list)
    spot_checks: dict = field(default_factory=dict)
    duration_seconds: Optional[float] = None
    _start_time: float = field(default_factory=time.time, repr=False)


def start_run(trigger: str, total_mids: int = 0, source: str = "", tickets: list = None) -> AuditRecord:
    now = datetime.now(timezone.utc)
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    run_id = now.strftime("%Y%m%d_%H%M%S") + "_" + suffix
    return AuditRecord(
        run_id=run_id,
        timestamp=now.isoformat(),
        trigger=trigger,
        total_mids=total_mids,
        source=source,
        tickets=tickets or [],
    )


def finalize_run(record: AuditRecord, results_file: str) -> None:
    record.duration_seconds = round(time.time() - record._start_time, 1)

    if os.path.isfile(results_file):
        try:
            with open(results_file, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            record.results_total = len(rows)
            for row in rows:
                status = row.get("Agreement Found", "")
                if status == "Yes":
                    record.results_agreement_found += 1
                elif status == "No":
                    record.results_no_agreement += 1
                elif status.startswith("Could not extract"):
                    record.results_manual_review += 1
                elif status.startswith("Error"):
                    record.results_errors += 1
                record.result_rows.append({
                    "ticket": row.get("Cobra Key", "").strip(),
                    "mid": row.get("MID", "").strip(),
                    "name": row.get("Name", "").strip(),
                    "result": status,
                    "failure_reason": row.get("Failure Reason", "").strip(),
                })
        except Exception as e:
            record.errors.append(f"Could not read results: {e}")

    if record.results_errors > 0 and record.results_agreement_found > 0:
        record.status = "partial"
    elif record.results_total == 0 or record.results_errors == record.results_total:
        record.status = "error"

    _write(record)


def _write(record: AuditRecord) -> None:
    os.makedirs(AUDIT_DIR, exist_ok=True)
    needs_header = not os.path.isfile(AUDIT_FILE) or os.path.getsize(AUDIT_FILE) == 0

    dt = datetime.fromisoformat(record.timestamp)
    date_str = dt.strftime("%m/%d/%Y")
    time_str = dt.strftime("%H:%M:%S")

    with open(AUDIT_FILE, "a+", newline="", encoding="utf-8") as f:
        # Ensure we start on a new line if file doesn't end with one
        f.seek(0, 2)
        if f.tell() > 0:
            f.seek(f.tell() - 1)
            if f.read(1) != "\n":
                f.write("\n")
        writer = csv.DictWriter(f, fieldnames=AUDIT_COLUMNS)
        if needs_header:
            writer.writeheader()

        for i, result in enumerate(record.result_rows):
            row = {
                "Ticket": result["ticket"],
                "MID": result["mid"],
                "Name": result["name"],
                "Result": result["result"],
                "Failure Reason": result["failure_reason"],
                "Manual Review": "",
            }
            # Put run summary on the first row only
            if i == 0:
                row.update({
                    "Run ID": record.run_id,
                    "Date": date_str,
                    "Time (UTC)": time_str,
                    "Trigger": record.trigger,
                    "Status": record.status,
                    "Total MIDs": record.total_mids,
                    "Agreement Found": record.results_agreement_found,
                    "No Agreement": record.results_no_agreement,
                    "Manual Review Needed": record.results_manual_review,
                    "Errors": record.results_errors,
                    "Duration (sec)": record.duration_seconds,
                })
            writer.writerow(row)

        # If no results at all (e.g. crashed before processing), write one summary row
        if not record.result_rows:
            writer.writerow({
                "Run ID": record.run_id,
                "Date": date_str,
                "Time (UTC)": time_str,
                "Trigger": record.trigger,
                "Status": record.status,
                "Total MIDs": record.total_mids,
                "Agreement Found": 0,
                "No Agreement": 0,
                "Manual Review Needed": 0,
                "Errors": record.results_errors,
                "Duration (sec)": record.duration_seconds,
            })

    # Push to Google Sheets dashboard
    _push_to_sheets(record, date_str, time_str)


def _push_to_sheets(record, date_str, time_str, bot_type="Severance Bot"):
    from config import AUDIT_SHEET_URL
    if not AUDIT_SHEET_URL:
        return

    failed = record.results_no_agreement + record.results_manual_review + record.results_errors

    # Post run summary
    try:
        requests.post(AUDIT_SHEET_URL, json={
            "type": "run",
            "run_id": record.run_id,
            "date": date_str,
            "time_utc": time_str,
            "bot_type": bot_type,
            "trigger": record.trigger,
            "status": record.status,
            "total_processed": record.results_total,
            "successful": record.results_agreement_found,
            "failed": failed,
            "duration_sec": record.duration_seconds,
        }, timeout=15)
    except Exception as e:
        print(f"[audit] Warning: could not push run summary to Sheets — {e}")

    # Post detail rows
    if record.result_rows:
        detail_rows = []
        for r in record.result_rows:
            detail_rows.append({
                "run_id": record.run_id,
                "date": date_str,
                "bot_type": bot_type,
                "ticket": r["ticket"],
                "mid": r["mid"],
                "name": r["name"],
                "result": r["result"],
                "failure_reason": r["failure_reason"],
                "manual_review": "Pending" if r["failure_reason"] else "",
            })
        try:
            requests.post(AUDIT_SHEET_URL, json={
                "type": "detail",
                "rows": detail_rows,
            }, timeout=30)
        except Exception as e:
            print(f"[audit] Warning: could not push details to Sheets — {e}")
