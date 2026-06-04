"""
inbox_agent.py — Filter ER Paid COBRA inbox tickets and run the severance bot.

Steps:
  1. Fetch unassigned inbox tickets from Jira
  2. Filter: must have an MID + be an EmployER Paid COBRA/Severance ticket
  3. Write filtered MIDs to mids.txt
  4. Prompt user, then run main.py (browser opens for Okta login)
"""

import os
import re
import json
import subprocess
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
MIDS_FILE = os.path.join(AGENT_DIR, "mid.txt")
STATUS_FILE = os.path.join(AGENT_DIR, "agent_status.json")
DASHBOARD_ENV = os.path.expanduser(
    "~/Projects/cobra-severance-dashboard/backend/.env"
)

load_dotenv(DASHBOARD_ENV)
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_TOKEN = os.environ["JIRA_API_TOKEN"]
JIRA_BASE = "https://justworks.atlassian.net"

# ---------------------------------------------------------------------------
# Filter keywords
# ---------------------------------------------------------------------------
ER_PAID_PATTERNS = re.compile(
    r"employer\s+paid|er[\s\-]paid|cobra\s+severance|severance",
    re.IGNORECASE,
)
MID_PATTERN = re.compile(r"\bM\d{5,}\b")
SKIP_PATTERNS = re.compile(
    r"clarification|nomad\s+fees|billing",
    re.IGNORECASE,
)
# Patterns for tickets that are NOT severance applications
NOT_SEVERANCE_PATTERNS = [
    (re.compile(r"cobra\s+transfer", re.IGNORECASE), "COBRA transfer"),
    (re.compile(r"cobra\s+change|change\s+cobra|plan\s+change|address\s+change|dependent\s+(add|remove|change)", re.IGNORECASE), "COBRA change"),
    (re.compile(r"\bquestion\b|\basking\b|\bconfirm\b|\bclarif", re.IGNORECASE), "Question / clarification"),
    (re.compile(r"cancel\s*(cobra|coverage)|term(inat|\.)|end\s+coverage|opt[\s\-]?out", re.IGNORECASE), "Cancellation / termination"),
    (re.compile(r"refund|credit|overpay|overcharge", re.IGNORECASE), "Refund / billing issue"),
    (re.compile(r"nomad|remote\s+worker", re.IGNORECASE), "Nomad / remote worker"),
    (re.compile(r"reinstate|re-enroll|re[\s\-]?activate", re.IGNORECASE), "Reinstatement"),
]
FLAGGED_FILE = os.path.join(AGENT_DIR, "flagged_tickets.json")

# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def _write_status(state: str, message: str, current: int = 0, total: int = 0):
    payload = {
        "state": state,
        "message": message,
        "progress": {"current": current, "total": total},
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }
    if state in ("done", "error"):
        payload["finished_at"] = payload["started_at"]
    with open(STATUS_FILE, "w") as f:
        json.dump(payload, f)


# ---------------------------------------------------------------------------
# Jira helpers
# ---------------------------------------------------------------------------

def _jira_get(path, params):
    resp = requests.get(
        f"{JIRA_BASE}{path}",
        params=params,
        auth=(JIRA_EMAIL, JIRA_TOKEN),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


_ASSIGNEES = (
    "62bca79a9e6ba34c9936d311,"
    "712020:136a1659-a83d-493f-b197-ebab0009d602,"
    "712020:50e30179-53ac-4095-b0fb-4052b2bd6c55"
)


def fetch_inbox_tickets():
    jql = (
        f'('
        f'(assignee IN ({_ASSIGNEES}) OR assignee IS EMPTY) '
        f'AND project IN (BOH, COBRA) '
        f'AND labels = COBRA_Severance '
        f'AND summary !~ "retroterm" '
        f'AND statusCategory IN ("To Do", "In Progress") '
        f'AND status NOT IN ("Waiting on Vendor", "Waiting on Customer")'
        f') OR ('
        f'project = COBRA '
        f'AND "Request Type[Dropdown]" = "COBRA Billing" '
        f'AND (assignee IN ({_ASSIGNEES}) OR assignee IS EMPTY) '
        f'AND summary !~ "retroterm" '
        f'AND statusCategory IN ("To Do", "In Progress") '
        f'AND status NOT IN ("Waiting on Vendor", "Waiting on Customer")'
        f') ORDER BY created DESC'
    )
    data = _jira_get(
        "/rest/api/3/search/jql",
        {
            "jql": jql,
            "fields": "summary,status,priority,created,description",
            "maxResults": 100,
        },
    )
    tickets = []
    for issue in data.get("issues", []):
        fields = issue["fields"]
        # Flatten ADF description to plain text for keyword matching
        desc_text = _adf_to_text(fields.get("description") or {})
        tickets.append(
            {
                "key": issue["key"],
                "summary": fields.get("summary", ""),
                "description": desc_text,
                "url": f"{JIRA_BASE}/browse/{issue['key']}",
            }
        )
    return tickets


def _adf_to_text(node) -> str:
    """Recursively extract plain text from an Atlassian Document Format node."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    text = node.get("text", "")
    for child in node.get("content", []):
        text += " " + _adf_to_text(child)
    return text.strip()


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

def classify_ticket(ticket):
    """Returns (mid_or_None, reason_str, flag_category_or_None)."""
    summary = ticket["summary"]
    description = ticket["description"]
    combined = summary + " " + description

    mid_match = MID_PATTERN.search(combined)
    er_paid = bool(ER_PAID_PATTERNS.search(combined))
    skip = bool(SKIP_PATTERNS.search(summary)) and not er_paid

    # Check if ticket matches a non-severance category
    flag_category = None
    if not er_paid:
        for pattern, category in NOT_SEVERANCE_PATTERNS:
            if pattern.search(combined):
                flag_category = category
                break

    if not mid_match:
        return None, "no MID found", flag_category
    if skip:
        return None, "skipped — question/billing keyword in summary", flag_category
    if not er_paid:
        return None, "no ER Paid / Severance keyword", flag_category

    return mid_match.group(0), "ER Paid + MID", None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(no_confirm: bool = False, only_keys=None):
    print("=" * 60)
    print("COBRA Inbox Agent")
    if only_keys:
        print(f"  Running on selected tickets: {', '.join(only_keys)}")
    print("=" * 60)

    _write_status("running", "Fetching inbox tickets from Jira...")

    try:
        tickets = fetch_inbox_tickets()
    except Exception as e:
        _write_status("error", f"Jira fetch failed: {e}")
        print(f"[error] Could not fetch tickets: {e}")
        return

    # Filter to only selected keys if provided
    if only_keys:
        only_set = set(only_keys)
        tickets = [t for t in tickets if t["key"] in only_set]
        print(f"\nFiltered to {len(tickets)} selected ticket(s).\n")
    else:
        print(f"\nFetched {len(tickets)} unassigned inbox tickets.\n")
    _write_status("running", f"Filtering {len(tickets)} tickets...")

    included = []
    skipped = []
    flagged = []

    col_w = 14
    print(f"{'Ticket':<{col_w}} {'Decision':<10} Reason")
    print("-" * 60)
    for t in tickets:
        mid, reason, flag_category = classify_ticket(t)
        if mid:
            included.append({"key": t["key"], "mid": mid})
            icon = "✅"
        else:
            skipped.append(t["key"])
            icon = "❌"
            flagged.append({
                "key": t["key"],
                "summary": t["summary"],
                "category": flag_category or "Needs review",
                "reason": reason,
                "url": t.get("url", ""),
            })
        print(f"{t['key']:<{col_w}} {icon:<10} {reason}  —  {t['summary'][:50]}")

    # Merge new flags with existing flagged tickets (don't overwrite)
    existing_flagged = []
    if os.path.isfile(FLAGGED_FILE):
        try:
            with open(FLAGGED_FILE) as f:
                existing_flagged = json.load(f).get("flagged", [])
        except Exception:
            pass
    existing_keys = {f["key"] for f in flagged}
    for ef in existing_flagged:
        if ef["key"] not in existing_keys:
            flagged.append(ef)
    with open(FLAGGED_FILE, "w") as f:
        json.dump({"flagged": flagged, "total_skipped": len(skipped)}, f, indent=2)

    print()
    print(f"Included: {len(included)}  |  Skipped: {len(skipped)}  |  Flagged (not severance): {len(flagged)}")

    if not included:
        _write_status("error", "No ER Paid tickets found — nothing to process.")
        print("\nNo ER Paid tickets found. Exiting.")
        return

    # Preserve rows for tickets NOT being re-processed (merge back after main.py)
    results_file = os.path.join(AGENT_DIR, "results.csv")
    if os.path.isfile(results_file):
        import csv as _csv
        from config import OUTPUT_COLUMNS
        keys_to_process = {e["key"] for e in included}
        preserved_rows = []
        with open(results_file, newline="", encoding="utf-8-sig") as f:
            preserved_rows = [r for r in _csv.DictReader(f) if r.get("Cobra Key", "") not in keys_to_process]
        cols = list(dict.fromkeys(OUTPUT_COLUMNS + ["Note", "Category"]))
        with open(results_file + ".preserved", "w", newline="", encoding="utf-8") as f:
            writer = _csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(preserved_rows)

    # Write mid.txt as TICKET MID pairs (space-separated)
    mids = [e["mid"] for e in included]
    with open(MIDS_FILE, "w") as f:
        for e in included:
            f.write(f"{e['key']} {e['mid']}\n")

    mid_preview = ", ".join(mids[:5]) + ("..." if len(mids) > 5 else "")
    print(f"\nWrote {len(mids)} MIDs to mids.txt: {mid_preview}")
    _write_status(
        "running",
        f"Found {len(included)} tickets. Ready to process.",
        current=0,
        total=len(included),
    )

    print()
    if no_confirm:
        print("Running in headless mode — launching severance bot directly.")
    else:
        print("A browser window will open.")
        print("Complete the Okta login → the bot will process all MIDs automatically.")
        try:
            input("Press Enter to launch, or Ctrl+C to cancel... ")
        except KeyboardInterrupt:
            _write_status("idle", "Cancelled by user.")
            print("\nCancelled.")
            return

    total = len(mids)
    _write_status("running", "Launching severance bot...", current=0, total=total)

    try:
        log_path = os.path.join(AGENT_DIR, "bot.log")
        log_file = open(log_path, "w")
        cmd = [sys.executable, "main.py"]
        proc = subprocess.Popen(
            cmd,
            cwd=AGENT_DIR,
            stdout=log_file,
            stderr=log_file,
        )

        # Monitor results.csv row count and update status as each MID is processed
        while proc.poll() is None:
            current = 0
            if os.path.isfile(results_file):
                try:
                    with open(results_file, encoding="utf-8-sig") as f:
                        # Count non-header, non-empty rows
                        current = sum(1 for line in f if line.strip()) - 1
                        current = max(0, current)
                except Exception:
                    pass
            if current > 0:
                _write_status(
                    "running",
                    f"Processing MID {current} of {total}: {mids[min(current - 1, total - 1)]}",
                    current=current,
                    total=total,
                )
            time.sleep(2)

        log_file.close()
        exit_code = proc.returncode
        if exit_code == 0:
            # Merge new results with preserved rows from prior runs
            import csv as _csv
            from config import OUTPUT_COLUMNS
            new_rows = []
            if os.path.isfile(results_file):
                with open(results_file, newline="", encoding="utf-8-sig") as f:
                    new_rows = list(_csv.DictReader(f))
            new_keys = {r.get("Cobra Key", "") for r in new_rows}
            # Re-read preserved rows (written before main.py ran)
            # They were saved to a temp file before launch
            if os.path.isfile(results_file + ".preserved"):
                with open(results_file + ".preserved", newline="", encoding="utf-8-sig") as f:
                    preserved = [r for r in _csv.DictReader(f) if r.get("Cobra Key", "") not in new_keys]
                combined = preserved + new_rows
                cols = list(dict.fromkeys(OUTPUT_COLUMNS + ["Note", "Category"]))
                with open(results_file, "w", newline="", encoding="utf-8") as f:
                    writer = _csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(combined)
                os.remove(results_file + ".preserved")
            _write_status("done", f"Done. {total} MIDs processed.", current=total, total=total)
            print("\nSeverance bot finished. Check results.csv.")
        else:
            _write_status("error", f"main.py exited with code {exit_code}")
            print(f"\n[error] main.py exited with code {exit_code}")

    except Exception as e:
        _write_status("error", str(e))
        print(f"\n[error] {e}")


if __name__ == "__main__":
    import sys
    _no_confirm = "--no-confirm" in sys.argv
    _keys = None
    if "--keys" in sys.argv:
        idx = sys.argv.index("--keys")
        if idx + 1 < len(sys.argv):
            _keys = [k.strip() for k in sys.argv[idx + 1].split(",") if k.strip()]
    run(no_confirm=_no_confirm, only_keys=_keys)
