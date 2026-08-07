import os
import sys
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
import requests
from dotenv import load_dotenv
import scraper
import pdf_parser
import audit
from output import read_mids, write_result, clear_results
from config import RESULTS_FILE

# Load Jira credentials for ticket lookup
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DASHBOARD_ENV = os.path.expanduser(
    "~/Projects/cobra-severance-dashboard/backend/.env"
)
if os.path.isfile(_DASHBOARD_ENV):
    load_dotenv(_DASHBOARD_ENV, override=False)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"), override=False)

JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
JIRA_TOKEN = os.environ.get("JIRA_API_TOKEN", "")
JIRA_BASE = "https://justworks.atlassian.net"


def _lookup_ticket_for_mid(mid: str) -> str:
    """Search Jira for an open COBRA/BOH ticket containing this MID."""
    if not JIRA_EMAIL or not JIRA_TOKEN:
        return ""
    jql = (
        f'project IN (BOH, COBRA) AND text ~ "{mid}" '
        f'ORDER BY created DESC'
    )
    try:
        resp = requests.get(
            f"{JIRA_BASE}/rest/api/3/search/jql",
            params={"jql": jql, "fields": "summary", "maxResults": 1},
            auth=(JIRA_EMAIL, JIRA_TOKEN),
            timeout=10,
        )
        resp.raise_for_status()
        issues = resp.json().get("issues", [])
        if issues:
            return issues[0]["key"]
    except Exception as e:
        print(f"[jira] Warning: could not look up ticket for {mid} — {e}")
    return ""


def _progress_bar(current: int, total: int, mid: str = "", name: str = "", company: str = "", status: str = ""):
    """Print a progress bar line to the terminal."""
    width = 20
    filled = int(width * current / total) if total else 0
    bar = "█" * filled + "░" * (width - filled)
    info = f"{mid}"
    if name and company:
        info += f" | {name} @ {company}"
    elif name:
        info += f" | {name}"
    if status:
        info += f" — {status}"
    print(f"[{bar}] {current}/{total} | {info}")


def _months_of_severance(start_str: str, end_str: str):
    """Return the number of calendar months spanned (inclusive) between two MM/DD/YYYY dates."""
    if not start_str or not end_str:
        return ""
    try:
        start = datetime.strptime(start_str.strip(), "%m/%d/%Y")
        end = datetime.strptime(end_str.strip(), "%m/%d/%Y")
        return (end.year * 12 + end.month) - (start.year * 12 + start.month) + 1
    except ValueError:
        return ""

RATE_LIMIT_SECONDS = 1.5


def run(headless=False, dry_run=False):
    members = read_mids()
    if not members:
        print("No entries found in mid.txt. Exiting.")
        return

    total = len(members)
    print(f"Found {total} entry/entries to process.\n")

    if dry_run:
        print("--- DRY RUN (no processing) ---")
        for i, entry in enumerate(members, 1):
            mid = entry["mid"] or "(none)"
            cid = entry.get("cid", "") or "(none)"
            ticket = entry["ticket"] or "(none)"
            print(f"  [{i}/{total}] Ticket: {ticket}  CID: {cid}  MID: {mid}")
        print("\nDone. No changes made.")
        return

    triggered_by_agent = os.environ.get("SEVERANCE_TRIGGER") == "inbox_agent"
    record = None
    if not triggered_by_agent:
        tickets = [e["ticket"] for e in members if e["ticket"]]
        record = audit.start_run(
            trigger="manual",
            total_mids=total,
            source="file",
            tickets=tickets,
        )

    clear_results()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        state_file = scraper.load_auth_state()
        context = browser.new_context(
            accept_downloads=True,
            storage_state=state_file,
        )
        page = context.new_page()

        scraper.login(page)
        scraper.save_auth_state(context)

        for i, entry in enumerate(members, 1):
            ticket = entry["ticket"]
            ww_case = entry["ww_case"]
            mid = entry["mid"]
            cid = entry.get("cid", "")

            # Look up Jira ticket key if not provided in mid.txt
            if not ticket:
                ticket = _lookup_ticket_for_mid(mid)
                if ticket:
                    print(f"[jira] Resolved {mid} → {ticket}")

            base_row = {
                "Cobra Key": ticket,
                "WW Case": ww_case,
                "CID": cid,
                "MID": mid,
            }

            try:
                member_data = scraper.search_member(page, cid, mid)

                if member_data is None:
                    write_result({**base_row, "Agreement Found": "No", "Failure Reason": "Member not found in Customer Central"})
                    _progress_bar(i, total, mid=mid, status="member not found")
                    time.sleep(RATE_LIMIT_SECONDS)
                    continue

                member_name = member_data.get("name", "")
                company_name = member_data.get("company_name", "")

                pdf_bytes = scraper.download_cobra_pdf(page, member_name, mid)

                if pdf_bytes is None:
                    write_result({
                        **base_row,
                        "Company Name": company_name,
                        "CID": member_data.get("cid", ""),
                        "Name": member_name,
                        "Agreement Found": "No",
                        "Failure Reason": "COBRA PDF not found (file name may not contain member name)",
                    })
                    _progress_bar(i, total, mid=mid, name=member_name, company=company_name, status="no COBRA form")
                    time.sleep(RATE_LIMIT_SECONDS)
                    continue

                fields = pdf_parser.extract_fields(pdf_bytes)

                extractable_fields = ["Medical", "Dental", "Vision", "Admin Fee", "Severance Start", "Severance End"]
                if not any(fields.get(f) for f in extractable_fields):
                    fields["Agreement Found"] = "Could not extract - manual review needed"
                    fields["Failure Reason"] = "PDF found but could not read/extract fields (scanned or unusual format)"

                if not fields.get("Company Name"):
                    fields["Company Name"] = company_name

                fields["CID"] = member_data.get("cid", "") or cid


                fields["# of months ER is paying severance"] = _months_of_severance(
                    fields.get("Severance Start", ""),
                    fields.get("Severance End", ""),
                )
                agreement_status = fields.pop("Agreement Found", "Yes")
                write_result({**base_row, **fields, "Agreement Found": agreement_status})
                _progress_bar(i, total, mid=mid, name=member_name, company=company_name, status=agreement_status)

            except Exception as exc:
                _progress_bar(i, total, mid=mid, status=f"ERROR: {exc}")
                write_result({**base_row, "Agreement Found": f"Error: {exc}", "Failure Reason": str(exc)})

                # Recover from browser crash by opening a new page
                try:
                    page.url
                except Exception:
                    print("  Browser page closed — reopening...")
                    try:
                        page = context.new_page()
                    except Exception:
                        state_file = scraper.load_auth_state()
                        context = browser.new_context(
                            accept_downloads=True,
                            storage_state=state_file,
                        )
                        page = context.new_page()
                    scraper.login(page)
                    scraper.save_auth_state(context)

            time.sleep(RATE_LIMIT_SECONDS)

        browser.close()

    if record:
        audit.finalize_run(record, RESULTS_FILE)

    print(f"Done. Results saved to results.csv")


if __name__ == "__main__":
    headless = "--headless" in sys.argv
    dry_run = "--dry-run" in sys.argv
    try:
        run(headless=headless, dry_run=dry_run)
    except Exception as exc:
        triggered_by_agent = os.environ.get("SEVERANCE_TRIGGER") == "inbox_agent"
        if not triggered_by_agent and not dry_run:
            record = audit.start_run(trigger="manual", total_mids=0, source="file")
            record.errors.append(str(exc))
            record.status = "error"
            audit.finalize_run(record, RESULTS_FILE)
        raise
