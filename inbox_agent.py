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
MIDS_FILE = os.path.join(AGENT_DIR, "mids.txt")
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


def fetch_inbox_tickets():
    jql = (
        "project = COBRA "
        "AND labels NOT IN (COBRA_Nomad_Request) "
        'AND status = "To Do" '
        "AND (assignee = EMPTY OR assignee = currentUser()) "
        "AND created >= -30d "
        "ORDER BY created DESC"
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
    summary = ticket["summary"]
    description = ticket["description"]
    combined = summary + " " + description

    mid_match = MID_PATTERN.search(combined)
    er_paid = bool(ER_PAID_PATTERNS.search(combined))
    skip = bool(SKIP_PATTERNS.search(summary)) and not er_paid

    if not mid_match:
        return None, "no MID found"
    if skip:
        return None, f"skipped — question/billing keyword in summary"
    if not er_paid:
        return None, "no ER Paid / Severance keyword"

    return mid_match.group(0), "ER Paid + MID"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(no_confirm: bool = False):
    print("=" * 60)
    print("COBRA Inbox Agent")
    print("=" * 60)

    _write_status("running", "Fetching inbox tickets from Jira...")

    try:
        tickets = fetch_inbox_tickets()
    except Exception as e:
        _write_status("error", f"Jira fetch failed: {e}")
        print(f"[error] Could not fetch tickets: {e}")
        return

    print(f"\nFetched {len(tickets)} unassigned inbox tickets.\n")
    _write_status("running", f"Filtering {len(tickets)} tickets...")

    included = []
    skipped = []

    col_w = 14
    print(f"{'Ticket':<{col_w}} {'Decision':<10} Reason")
    print("-" * 60)
    for t in tickets:
        mid, reason = classify_ticket(t)
        if mid:
            included.append({"key": t["key"], "mid": mid})
            icon = "✅"
        else:
            skipped.append(t["key"])
            icon = "❌"
        print(f"{t['key']:<{col_w}} {icon:<10} {reason}  —  {t['summary'][:50]}")

    print()
    print(f"Included: {len(included)}  |  Skipped: {len(skipped)}")

    if not included:
        _write_status("done", "No ER Paid tickets found — nothing to process.")
        print("\nNo ER Paid tickets found. Exiting.")
        return

    # Clear previous results before this run
    results_file = os.path.join(AGENT_DIR, "results.csv")
    if os.path.isfile(results_file):
        import csv as _csv
        with open(results_file, "w", newline="", encoding="utf-8") as f:
            from config import OUTPUT_COLUMNS
            _csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS).writeheader()

    # Write mids.txt as TICKET,MID pairs
    mids = [e["mid"] for e in included]
    with open(MIDS_FILE, "w") as f:
        for e in included:
            f.write(f"{e['key']},{e['mid']}\n")

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
        proc = subprocess.Popen(
            [sys.executable, "main.py", "--headless"],
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
    run(no_confirm="--no-confirm" in sys.argv)
