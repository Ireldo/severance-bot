import os
import re
import time
from typing import Optional
from playwright.sync_api import Page, BrowserContext, TimeoutError as PlaywrightTimeout
from config import SITE_URL, SITE_USERNAME, SITE_PASSWORD, AUTH_STATE_FILE

MAX_AUTH_AGE_HOURS = 12


def load_auth_state() -> Optional[str]:
    """Return path to saved auth state if it exists and is fresh, else None."""
    if not os.path.isfile(AUTH_STATE_FILE):
        return None
    age_hours = (time.time() - os.path.getmtime(AUTH_STATE_FILE)) / 3600
    if age_hours > MAX_AUTH_AGE_HOURS:
        os.remove(AUTH_STATE_FILE)
        return None
    return AUTH_STATE_FILE


def save_auth_state(context: BrowserContext) -> None:
    """Persist browser cookies/localStorage for session reuse."""
    context.storage_state(path=AUTH_STATE_FILE)


def login(page: Page) -> None:
    """Navigate to customer-central and complete Okta SSO login."""
    page.goto(SITE_URL, wait_until="domcontentloaded", timeout=30_000)
    # Wait briefly for redirects to settle
    page.wait_for_timeout(3_000)

    if "customer-central.justworks.com" in page.url:
        print("[login] Reusing saved session.")
        page.wait_for_load_state("networkidle", timeout=15_000)
        return

    if "okta.com" not in page.url:
        raise Exception(f"Expected Okta login page, got: {page.url}")

    # Wait for the Okta login form to render
    page.wait_for_load_state("networkidle", timeout=15_000)

    fastpass_selectors = [
        "a:has-text('Sign in with Okta FastPass')",
        "a:has-text('Okta FastPass')",
        "button:has-text('Okta FastPass')",
        "[data-se='okta-fastpass']",
        "button:has-text('Fast')",
    ]
    clicked = False
    for sel in fastpass_selectors:
        try:
            page.click(sel, timeout=5_000)
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        print("[login] Could not find FastPass button — please log in manually in the browser.")

    print("[login] Waiting up to 2 minutes for you to complete login...")
    page.wait_for_url("**/customer-central.justworks.com/**", timeout=120_000)
    print("[login] Authenticated successfully.")


def _find_search_input(page: Page, query: str) -> bool:
    """Find and use the global search input. Returns True on success."""
    for sel in [
        "header input",
        "input[type='search']",
        "input[placeholder='Search']",
        "input[aria-label*='search' i]",
        "input[role='searchbox']",
        "input",
    ]:
        try:
            el = page.locator(sel).first
            el.wait_for(timeout=3_000)
            el.click()
            el.fill(query)
            el.press("Enter")
            return True
        except Exception:
            continue
    return False


def _extract_cid_from_page(page: Page) -> str:
    """Extract CID (e.g. C91813) from the current company page text."""
    try:
        page.wait_for_timeout(2000)
        body_text = page.inner_text("body", timeout=5_000)
        match = re.search(r'\b(C\d{4,8})\b', body_text)
        return match.group(1) if match else ""
    except Exception:
        return ""


def search_member(page: Page, cid: str, mid: str) -> Optional[dict]:
    """
    Search for a member by MID on customer-central.
    Flow: search MID → get member name from results → click company name → land on company page.
    Returns dict with {name, company_name, cid} or None.
    """
    search_term = mid or cid
    if not search_term:
        print(f"[search] No MID or CID provided.")
        return None

    # Search by MID with one retry
    for attempt in range(2):
        try:
            page.goto(SITE_URL, wait_until="networkidle", timeout=30_000)
            page.wait_for_timeout(1500)

            if _find_search_input(page, search_term):
                page.wait_for_load_state("networkidle", timeout=10_000)
                page.wait_for_timeout(1000)
                break
            else:
                if attempt == 0:
                    continue
                print(f"[search] MID {mid}: could not find any search input.")
                return None
        except PlaywrightTimeout as e:
            if attempt == 0:
                continue
            print(f"[search] MID {mid}: page did not load after search — {e}")
            return None

    data = {"name": "", "company_name": "", "cid": cid}

    # From the search results: the member result is a single link containing
    # name, status, company, email, phone. Click it to go to member page,
    # then click the company name on the left to reach the company page.
    try:
        page.wait_for_timeout(1500)

        # Find the member result link (href contains /members/)
        member_link = page.locator("a[href*='/members/']").first
        member_link.wait_for(timeout=8_000)

        # Parse the member name and company from the link text
        link_text = member_link.inner_text(timeout=3_000)
        lines = [l.strip() for l in link_text.split("\n") if l.strip()]
        skip_words = {"member", "terminated", "active"}
        for line in lines:
            if not data["cid"] and re.match(r'^C\d+$', line):
                data["cid"] = line
            elif (not re.match(r'^[CM]\d+', line)
                    and line.lower() not in skip_words
                    and not re.match(r'^[\(\d\+]', line)
                    and "@" not in line
                    and len(line) > 2):
                if not data["name"]:
                    data["name"] = line
                elif not data["company_name"]:
                    data["company_name"] = line
                    break

        # Click the member link to go to the member page
        member_link.click(timeout=5_000)
        page.wait_for_load_state("networkidle", timeout=10_000)
        page.wait_for_timeout(1500)

        # Now click the company name on the left sidebar to navigate to company page
        if data["company_name"]:
            try:
                page.locator(f"a:has-text('{data['company_name']}')").first.click(timeout=5_000)
                page.wait_for_load_state("networkidle", timeout=10_000)
                if not data["cid"]:
                    data["cid"] = _extract_cid_from_page(page)
                print(f"[search] MID {mid}: found member '{data['name']}' at '{data['company_name']}' (CID: {data['cid']})")
                return data
            except PlaywrightTimeout:
                pass

        # Fallback: look for any company link in sidebar
        try:
            company_link = page.locator("a[href*='/companies/']").first
            company_link.wait_for(timeout=5_000)
            data["company_name"] = company_link.inner_text().strip()
            company_link.click(timeout=5_000)
            page.wait_for_load_state("networkidle", timeout=10_000)
            if not data["cid"]:
                data["cid"] = _extract_cid_from_page(page)
            print(f"[search] MID {mid}: found member '{data['name']}' at '{data['company_name']}' (CID: {data['cid']})")
            return data
        except PlaywrightTimeout:
            pass

        print(f"[search] MID {mid}: could not navigate to company page.")
        return None

    except PlaywrightTimeout:
        print(f"[search] MID {mid}: no member result found in search.")
        return None
    except Exception as e:
        print(f"[search] MID {mid}: error processing search results — {e}")
        return None


def download_cobra_pdf(page: Page, member_name: str, mid: str) -> Optional[bytes]:
    """
    Navigate to the COBRA document on customer-central:
    1. Click 'Documents' in the left sidebar
    2. Click 'Internal' tab (handles both page layouts)
    3. Click 'View all' next to 'Uncategorized'
    4. Find COBRA form by member name (prefer most recent)
    5. Open viewer and download PDF
    """
    # Step 1: Click Documents in the left sidebar
    try:
        page.wait_for_timeout(1000)
        page.locator("nav a:has-text('Documents'), aside a:has-text('Documents')").first.click(timeout=8_000)
        page.wait_for_load_state("networkidle", timeout=10_000)
        page.wait_for_timeout(2000)
    except PlaywrightTimeout:
        print(f"[pdf] MID {mid}: could not find 'Documents' in sidebar.")
        return None

    # Step 2: Click the Internal tab (next to the "Company" tab)
    try:
        page.wait_for_timeout(1500)

        # Scroll down to find the tabs section if needed
        for _ in range(5):
            page.evaluate("window.scrollBy(0, 400)")
            page.wait_for_timeout(300)

        # Look for tab-like elements containing "Internal" — exclude the info banner
        # The tab is near the "Company" tab text
        internal_tab = None
        candidates = page.get_by_text("Internal", exact=True).all()
        for candidate in candidates:
            # Skip if it's inside a long text block (the info banner)
            parent_text = candidate.inner_text().strip()
            if len(parent_text) < 20:
                internal_tab = candidate
                break

        if internal_tab is None and candidates:
            internal_tab = candidates[0]

        if internal_tab is None:
            print(f"[pdf] MID {mid}: could not find 'Internal' tab.")
            return None

        internal_tab.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        internal_tab.click(timeout=5_000)
        page.wait_for_load_state("networkidle", timeout=10_000)
        page.wait_for_timeout(1500)
    except PlaywrightTimeout:
        print(f"[pdf] MID {mid}: could not find 'Internal' tab.")
        return None

    # Step 3: Click "View all" next to "Uncategorized"
    try:
        uncategorized_row = page.locator("*:has-text('Uncategorized')").filter(has_text="View all").last
        uncategorized_row.locator("text=View all").click(timeout=8_000)
        page.wait_for_load_state("networkidle", timeout=10_000)
        page.wait_for_timeout(1500)
    except PlaywrightTimeout:
        print(f"[pdf] MID {mid}: could not find Uncategorized 'View all' link.")
        return None

    # Step 4: Find the COBRA row matching the member name
    name_parts = member_name.split(",")
    if len(name_parts) == 2:
        first_last = f"{name_parts[1].strip()} {name_parts[0].strip()}"
    else:
        first_last = member_name.strip()

    last_name = name_parts[0].strip() if name_parts else ""
    # Also try just the last name for partial matches
    last_name_only = name_parts[0].strip() if "," in member_name else member_name.split()[-1] if member_name else ""

    cobra_row = None
    for name_variant in [first_last, member_name, last_name, last_name_only]:
        if not name_variant:
            continue
        try:
            matches = page.locator("tr:has-text('COBRA')").filter(has_text=name_variant).all()
            if matches:
                cobra_row = matches[-1]
                cobra_row.wait_for(timeout=3_000)
                break
        except PlaywrightTimeout:
            cobra_row = None
            continue

    # Also try searching for the member name without requiring "COBRA" in the row
    # (some files are named like "Miriam Nadler_Employer COBRA Contribution Form")
    if cobra_row is None:
        for name_variant in [first_last, member_name, last_name]:
            if not name_variant:
                continue
            try:
                matches = page.locator("tr").filter(has_text=name_variant).filter(has_text="Contribution").all()
                if matches:
                    cobra_row = matches[-1]
                    cobra_row.wait_for(timeout=3_000)
                    break
            except PlaywrightTimeout:
                cobra_row = None
                continue

    # Fallback: search by first name + COBRA/Severance keywords (handles last-name typos in filenames)
    if cobra_row is None:
        first_name = first_last.split()[0] if first_last else ""
        if first_name and len(first_name) >= 3:
            for keyword in ["COBRA", "Severance", "Agreement"]:
                try:
                    matches = page.locator("tr").filter(has_text=first_name).filter(has_text=keyword).all()
                    if matches:
                        cobra_row = matches[-1]
                        cobra_row.wait_for(timeout=3_000)
                        break
                except PlaywrightTimeout:
                    cobra_row = None
                    continue

    if cobra_row is None:
        print(f"[pdf] MID {mid}: no COBRA document found for '{member_name}'.")
        return None

    # Step 5: Open the viewer and download the PDF
    try:
        cobra_row.locator("a").first.click(timeout=5_000)
        page.wait_for_load_state("networkidle", timeout=10_000)
        page.wait_for_timeout(1500)
    except PlaywrightTimeout:
        print(f"[pdf] MID {mid}: could not open document viewer.")
        return None

    try:
        with page.expect_download(timeout=15_000) as download_info:
            page.locator("a:has-text('Download'), button:has-text('Download')").first.click(timeout=5_000)
        download = download_info.value
        path = download.path()
        content = open(path, "rb").read()

        if content[:4] == b"%PDF" or b"%PDF" in content[:10]:
            print(f"[pdf] MID {mid}: PDF downloaded successfully ({len(content)} bytes).")
            return content

        print(f"[pdf] MID {mid}: downloaded file is not a PDF.")
        return None
    except PlaywrightTimeout:
        print(f"[pdf] MID {mid}: download timed out.")
        return None
    except Exception as e:
        print(f"[pdf] MID {mid}: download failed — {e}")
        return None
