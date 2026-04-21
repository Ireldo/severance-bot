from typing import Optional
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout
from config import SITE_URL, SITE_USERNAME, SITE_PASSWORD


def login(page: Page) -> None:
    """Navigate to the app and complete Okta SSO login."""
    page.goto(SITE_URL, wait_until="networkidle", timeout=20_000)

    if "okta.com" not in page.url:
        raise Exception(f"Expected Okta login page, got: {page.url}")

    # Click "Sign in with Okta FastPass"
    fastpass_selectors = [
        "button:has-text('Okta FastPass')",
        "a:has-text('Okta FastPass')",
        "[data-se='okta-fastpass']",
        "button:has-text('Fast')",
    ]
    clicked = False
    for sel in fastpass_selectors:
        try:
            page.click(sel, timeout=4_000)
            print(f"[login] Clicked FastPass with selector: {sel}")
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        print("[login] Could not find FastPass button — please log in manually in the browser.")

    print("\n[login] Waiting up to 2 minutes for you to complete login...")


    # Wait until redirected back to the app
    page.wait_for_url("**/cstools-workforce.justworks.com/**", timeout=120_000)
    print("[login] Authenticated successfully.")

    # Step 1: Fill username
    username_selectors = [
        "#okta-signin-username",
        "input[name='identifier']",
        "input[type='email']",
        "input[autocomplete='username']",
    ]
    for sel in username_selectors:
        try:
            page.fill(sel, SITE_USERNAME, timeout=5_000)
            print(f"[login] Filled username with selector: {sel}")
            break
        except Exception:
            continue


    page.wait_for_url("**/cstools-workforce.justworks.com/**", timeout=30_000)
    print("[login] Authenticated successfully.")


def search_member(page: Page, mid: str) -> Optional[dict]:
    """
    Search for a member by MID.
    Flow:
      1. Type MID in top-right search bar → Enter
      2. "Request diagnostic authorization" screen appears
      3. Select "Other" from the category options
      4. Append " -" to the MID in the input field
      5. Click Submit
      6. Land on Company profile page
    Returns dict with {name, company_name, cid} or None.
    """
    try:
        # Navigate to the main page first to ensure search bar is available
        page.goto(SITE_URL, wait_until="networkidle", timeout=15_000)

        # Step 1: Type MID in the search bar and press Enter
        search_input = page.locator(
            "input[placeholder*='Search'], input[type='search'], [data-testid*='search'] input"
        ).first
        search_input.wait_for(timeout=8_000)
        search_input.click()
        search_input.fill(mid)
        search_input.press("Enter")

        # Step 2: Wait briefly to see which screen loads
        page.wait_for_load_state("networkidle", timeout=10_000)

    except PlaywrightTimeout as e:
        print(f"[search] MID {mid}: page did not load after search — {e}")
        return None

    # Check if authorization screen appeared by looking for its heading
    try:
        page.locator("h1:has-text('Request diagnostic authorization'), h2:has-text('Request diagnostic authorization')").wait_for(timeout=12_000)
        print(f"[search] MID {mid}: authorization screen appeared, filling form.")

        # Select "Other" radio
        page.locator("label:has-text('Other')").click(timeout=5_000)

        # Fill "-" in the last text input (Other's field)
        page.locator("input[type='text']").last.fill("-", timeout=5_000)
        print(f"[search] MID {mid}: typed '-' in Other field.")

        # Small pause to ensure form is ready, then click Submit
        page.wait_for_timeout(500)
        submit_btn = page.get_by_role("button", name="Submit")
        submit_btn.wait_for(timeout=5_000)
        submit_btn.click()
        page.wait_for_load_state("networkidle", timeout=15_000)
        print(f"[search] MID {mid}: submitted, now on company profile.")

    except PlaywrightTimeout:
        print(f"[search] MID {mid}: authorization screen bypassed, continuing.")

    return _extract_profile_data(page, mid)


def _extract_profile_data(page: Page, mid: str) -> Optional[dict]:
    """Scrape Company Name, CID, and member Name from the Company profile page."""
    import re as _re
    data = {"name": "", "company_name": "", "cid": ""}

    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except PlaywrightTimeout:
        pass

    # CID — appears in the top nav as "C17455" (C + digits)
    try:
        nav_text = page.locator("nav, .navbar, header").first.inner_text(timeout=5_000)
        cid_match = _re.search(r'\b(C\d{4,})\b', nav_text)
        if cid_match:
            data["cid"] = cid_match.group(1)
    except Exception:
        pass

    # Company Name — in the top nav, between PRODUCTION badge and plan badge
    # Skip known non-company labels: CSTools, PRODUCTION, plan badge text, CID, MID
    skip_contains = ["cstools", "production", "peo plan", "aso plan", "plus plan", "basic plan"]
    try:
        nav_links = page.locator("nav a, .navbar a, header a").all()
        for el in nav_links:
            text = el.inner_text().strip()
            text_lower = text.lower()
            if (text
                    and not _re.match(r'^[CM]\d+', text)
                    and len(text) > 3
                    and not any(s in text_lower for s in skip_contains)):
                data["company_name"] = text
                break
    except Exception:
        pass

    # Member Name — check "You last searched for" section first, then Terminated tab
    try:
        row = page.locator(f"tr:has-text('{mid}')").first
        row.wait_for(timeout=4_000)
        cells = row.locator("td").all()
        if len(cells) >= 4:
            data["name"] = cells[3].inner_text().strip()
    except Exception:
        pass


    print(f"[search] MID {mid}: name='{data['name']}' company='{data['company_name']}' cid='{data['cid']}'")
    return data if any(data.values()) else None


def download_cobra_pdf(page: Page, member_name: str, mid: str) -> Optional[str]:
    """
    On the Company profile page:
    1. Click 'Internal Documents'
    2. Search by member name
    3. Find 'Employer COBRA Contribution Form - [Name] - Executed.pdf'
    4. Download and return local path, or None if not found.
    """
    # Step 1: Click Internal Documents button (visible on right side of company profile)
    try:
        page.wait_for_selector("text=Internal Documents", timeout=12_000)
        page.get_by_text("Internal Documents", exact=True).click(timeout=8_000)
        page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeout:
        print(f"[pdf] MID {mid}: could not find 'Internal Documents' button.")
        return None

    # Step 2: Find the COBRA row by member name in the Title column
    # Name from profile is "Last, First" — convert to "First Last" for title matching
    import re as _re
    name_parts = member_name.split(",")
    if len(name_parts) == 2:
        first_last = f"{name_parts[1].strip()} {name_parts[0].strip()}"
    else:
        first_last = member_name.strip()

    # Also get just the last name as a fallback
    last_name = name_parts[0].strip() if name_parts else ""

    page.wait_for_selector("table", timeout=8_000)

    # Find a row whose Title cell contains the member name and "COBRA"
    cobra_row = None
    for name_variant in [first_last, last_name]:
        if not name_variant:
            continue
        try:
            cobra_row = page.locator(f"tr:has-text('COBRA')").filter(has_text=name_variant).first
            cobra_row.wait_for(timeout=3_000)
            break
        except PlaywrightTimeout:
            cobra_row = None
            continue

    if cobra_row is None:
        print(f"[pdf] MID {mid}: no COBRA document row found for '{member_name}'.")
        return None

    # Step 3: Get the View link href and fetch the PDF directly using browser cookies
    import requests as _requests

    try:
        view_link = cobra_row.locator("a:has-text('View'):not(:has-text('Delete'))").first
        href = view_link.get_attribute("href", timeout=5_000)
        print(f"[pdf] MID {mid}: View href = {href}")
    except PlaywrightTimeout:
        print(f"[pdf] MID {mid}: could not get View link href.")
        return None

    if not href:
        print(f"[pdf] MID {mid}: View link has no href.")
        return None

    # Build full URL if relative
    if href.startswith("/"):
        href = f"https://cstools-workforce.justworks.com{href}"

    # Use browser cookies to fetch the PDF directly
    cookies = page.context.cookies()
    session = _requests.Session()
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

    try:
        resp = session.get(href, timeout=20, allow_redirects=True)
        resp.raise_for_status()
        content = resp.content
        ct = resp.headers.get("content-type", "")
        print(f"[pdf] MID {mid}: fetched {len(content)} bytes, content-type={ct}")

        # Accept if it's a PDF (by content-type or magic bytes)
        if "pdf" in ct.lower() or content[:4] == b"%PDF":
            print(f"[pdf] MID {mid}: PDF confirmed.")
            return content

        # Not a PDF — log a sample of what we got and fall through
        print(f"[pdf] MID {mid}: response is not a PDF. First 200 chars: {content[:200]}")
        return None
    except Exception as e:
        print(f"[pdf] MID {mid}: direct fetch failed — {e}")
        return None
