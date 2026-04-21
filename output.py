import csv
import os
import json
import re
import requests
from config import MIDS_FILE, RESULTS_FILE, OUTPUT_COLUMNS, GOOGLE_SHEET_URL, GOOGLE_SHEET_READ_URL

MID_PATTERN = re.compile(r"^M\d+$")


def read_mids() -> list:
    """
    Fetch MIDs from Google Sheet via Apps Script, falling back to mids.txt.
    Returns list of dicts with keys: ticket, ww_case, mid
    """
    mids = []

    if GOOGLE_SHEET_READ_URL:
        try:
            resp = requests.get(GOOGLE_SHEET_READ_URL, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
            mids = [m.strip() for m in raw if isinstance(m, str) and MID_PATTERN.match(m.strip())]
            print(f"[sheets] Fetched {len(mids)} MID(s) from Google Sheet.")
        except Exception as e:
            print(f"[sheets] Warning: could not read from Google Sheet — {e}")
            print("[sheets] Falling back to mids.txt")

    if not mids:
        entries = []
        with open(MIDS_FILE, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if "," in line:
                    ticket, mid = line.split(",", 1)
                    ticket, mid = ticket.strip(), mid.strip()
                    if MID_PATTERN.match(mid):
                        entries.append({"ticket": ticket, "ww_case": "", "mid": mid})
                elif MID_PATTERN.match(line):
                    entries.append({"ticket": "", "ww_case": "", "mid": line})
        return entries

    return [{"ticket": "", "ww_case": "", "mid": mid} for mid in mids]


def clear_results() -> None:
    """Overwrite results.csv with just the header row."""
    with open(RESULTS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()


def write_result(fields: dict) -> None:
    """Append one result row to results.csv and Google Sheets."""
    needs_header = not os.path.isfile(RESULTS_FILE) or os.path.getsize(RESULTS_FILE) == 0
    with open(RESULTS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        if needs_header:
            writer.writeheader()
        writer.writerow(fields)

    if GOOGLE_SHEET_URL:
        try:
            payload = {col: fields.get(col, "") for col in OUTPUT_COLUMNS}
            requests.post(GOOGLE_SHEET_URL, json=payload, timeout=10)
        except Exception as e:
            print(f"[sheets] Warning: could not write to Google Sheet — {e}")
