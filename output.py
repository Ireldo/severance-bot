import csv
import os
import json
import re
import requests
from config import MIDS_FILE, RESULTS_FILE, OUTPUT_COLUMNS, GOOGLE_SHEET_URL, GOOGLE_SHEET_READ_URL

MID_PATTERN = re.compile(r"^M\d+$")
CID_PATTERN = re.compile(r"^C\d+$")


def read_mids() -> list:
    """
    Fetch entries from Google Sheet via Apps Script, falling back to mid.txt.
    Returns list of dicts with keys: ticket, ww_case, mid, cid

    mid.txt format (space-separated, one entry per line):
      M1234567                     — MID only
      COBRA-5467 M1234567          — ticket + MID
      COBRA-5467 C92657 M1234567   — ticket + CID + MID
    """
    mids = []

    if GOOGLE_SHEET_READ_URL:
        try:
            resp = requests.get(GOOGLE_SHEET_READ_URL, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
            mids = [m.strip() for m in raw if isinstance(m, str) and MID_PATTERN.match(m.strip())]
            print(f"[sheets] Fetched {len(mids)} MID(s) from Google Sheet.")
        except Exception:
            pass

    if not mids:
        entries = []
        with open(MIDS_FILE, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                cid = ""
                mid = ""
                ticket = ""
                for p in parts:
                    if MID_PATTERN.match(p):
                        mid = p
                    elif CID_PATTERN.match(p):
                        cid = p
                    elif not ticket:
                        ticket = p
                if mid:
                    entries.append({"ticket": ticket, "ww_case": "", "mid": mid, "cid": cid})
        return entries

    # Enrich sheet-sourced MIDs with ticket keys and CIDs from mid.txt
    mid_to_ticket = {}
    mid_to_cid = {}
    if os.path.isfile(MIDS_FILE):
        with open(MIDS_FILE, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                ticket = ""
                mid_val = ""
                cid_val = ""
                for p in parts:
                    if MID_PATTERN.match(p):
                        mid_val = p
                    elif CID_PATTERN.match(p):
                        cid_val = p
                    elif not ticket:
                        ticket = p
                if mid_val:
                    if ticket:
                        mid_to_ticket[mid_val] = ticket
                    if cid_val:
                        mid_to_cid[mid_val] = cid_val

    return [{"ticket": mid_to_ticket.get(mid, ""), "ww_case": "", "mid": mid, "cid": mid_to_cid.get(mid, "")} for mid in mids]


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
