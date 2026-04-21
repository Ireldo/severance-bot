import sys
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
import scraper
import pdf_parser
from output import read_mids, write_result, clear_results


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


def run(headless=False):
    members = read_mids()
    if not members:
        print("No MIDs found in mids.txt. Exiting.")
        return

    print(f"Found {len(members)} MID(s) to process.\n")
    clear_results()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        scraper.login(page)

        for entry in members:
            ticket = entry["ticket"]
            ww_case = entry["ww_case"]
            mid = entry["mid"]
            print(f"--- Processing MID {mid} (Ticket: {ticket}) ---")

            base_row = {
                "Key": ticket,
                "WW Case": ww_case,
                "MID": mid,
            }

            try:
                member_data = scraper.search_member(page, mid)

                if member_data is None:
                    write_result({**base_row, "Agreement Found": "No"})
                    print(f"[main] MID {mid}: member not found.\n")
                    time.sleep(RATE_LIMIT_SECONDS)
                    continue

                member_name = member_data.get("name", "")
                pdf_bytes = scraper.download_cobra_pdf(page, member_name, mid)

                if pdf_bytes is None:
                    write_result({
                        **base_row,
                        "Company Name": member_data.get("company_name", ""),
                        "CID": member_data.get("cid", ""),
                        "Name": member_name,
                        "Agreement Found": "No",
                    })
                    print(f"[main] MID {mid}: COBRA form not found.\n")
                    time.sleep(RATE_LIMIT_SECONDS)
                    continue

                fields = pdf_parser.extract_fields(pdf_bytes)

                # Detect scanned/unreadable PDF — no fields extracted at all
                extractable_fields = ["Medical", "Dental", "Vision", "Admin Fee", "Severance Start", "Severance End"]
                if not any(fields.get(f) for f in extractable_fields):
                    print(f"[main] MID {mid}: PDF appears to be scanned — flagging for manual review.")
                    fields["Agreement Found"] = "Scanned PDF - manual review needed"

                # PDF is primary source for Company Name; fall back to web page value
                if not fields.get("Company Name"):
                    fields["Company Name"] = member_data.get("company_name", "")

                # CID only comes from the web page
                fields["CID"] = member_data.get("cid", "")

                # Name from PDF; fall back to web page if not found
                if not fields.get("Name"):
                    fields["Name"] = member_name

                fields["# of months ER is paying severance"] = _months_of_severance(
                    fields.get("Severance Start", ""),
                    fields.get("Severance End", ""),
                )
                agreement_status = fields.pop("Agreement Found", "Yes")
                write_result({**base_row, **fields, "Agreement Found": agreement_status})
                print(f"[main] MID {mid}: done → {fields}\n")

            except Exception as exc:
                print(f"[main] MID {mid}: ERROR — {exc}\n")
                write_result({**base_row, "Agreement Found": f"Error: {exc}"})

            time.sleep(RATE_LIMIT_SECONDS)

        browser.close()

    print(f"Done. Results saved to results.csv")


if __name__ == "__main__":
    headless = "--headless" in sys.argv
    run(headless=headless)
