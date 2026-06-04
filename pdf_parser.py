import re
import os
import io
import base64
import json
import pdfplumber
import requests
import fitz  # pymupdf
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

LITELLM_BASE = os.getenv("LITELLM_BASE_URL", "https://litellm.justworksai.net")
LITELLM_KEY = os.getenv("LITELLM_API_KEY", "")

# Regex patterns tuned to Employer COBRA Contribution Form language.
# Adjust after first test run if the PDF uses different phrasing.
_PATTERNS = {
    "Company Name": [
        # "We, PACT ("Customer")" or "We, TRUEPIC Inc ("Customer")"
        r"We,\s+([^\"(]+?)\s*\(",
    ],
    "Name": [
        # Captures name between "acknowledge that" and " ("
        r'acknowledge that ([^\n(]+?) \(',
    ],
    "Medical": [
        r"Medical\s+(\d{1,3}%|\$[\d,]+\.\d{2}|N/A)",
    ],
    "Dental": [
        r"Dental\s+(\d{1,3}%|\$[\d,]+\.\d{2}|N/A)",
    ],
    "Vision": [
        r"Vision\s+(\d{1,3}%|\$[\d,]+\.\d{2}|N/A)",
    ],
    "Admin Fee": [
        # "Customer will be responsible for the 2% admin fee" → Yes
        r"(Customer will be responsible for the \d+% admin fee)",
    ],
    "Severance Start": [
        # "starting on 04/01/26" or "starting on 04/01/2026"
        r"starting on (\d{1,2}/\d{1,2}/\d{2,4})",
    ],
    "Severance End": [
        # "(i) 6 months ending on 09/30/26" — \s+ handles newline between "on" and date
        r"\(i\)\s+\d+\s+months?\s+ending on\s+(\d{1,2}/\d{1,2}/\d{2,4})",
    ],
    "Term date": [
        # "termination date of 04/01/2026" or "termination date: 04/01/2026"
        r"termination date\s*(?:of|:)\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        # "terminated on 04/01/2026" or "terminated effective 04/01/2026"
        r"terminated\s+(?:on|effective)\s+(\d{1,2}/\d{1,2}/\d{2,4})",
        # "term date: 04/01/2026"
        r"term\s+date\s*[:\-]\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        # "effective date of termination" followed by date
        r"effective date of termination\s*(?:is|:)?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        # "Ineligible for benefits on 9/1/25"
        r"[Ii]neligible for benefits on\s+(\d{1,2}/\d{1,2}/\d{2,4})",
    ],
}


def extract_fields(pdf_bytes: bytes, debug: bool = False) -> dict:
    """Extract COBRA contribution form fields from PDF bytes. Returns dict of field→value."""
    text = _read_pdf_text(pdf_bytes)
    if debug:
        print("\n--- PDF TEXT SAMPLE ---")
        print(text[:3000])
        print("--- END SAMPLE ---\n")

    fields = {}

    for field, patterns in _PATTERNS.items():
        value = ""
        for pattern in patterns:
            match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
            if match:
                raw = match.group(1).strip()
                value = _normalize(field, raw)
                break
        fields[field] = value

    # If no key fields extracted, try vision-based OCR fallback
    key_fields = ("Medical", "Dental", "Vision", "Severance Start", "Severance End")
    if not any(fields.get(f) for f in key_fields):
        if debug:
            print("No fields extracted via text — trying vision OCR fallback...")
        vision_fields = _extract_via_vision(pdf_bytes)
        if vision_fields:
            fields = vision_fields

    return fields


def _pdf_to_images(pdf_bytes: bytes) -> list:
    """Convert PDF pages to base64-encoded PNG images."""
    images = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img_bytes = pix.tobytes("png")
        images.append(base64.b64encode(img_bytes).decode("utf-8"))
    doc.close()
    return images


def _extract_via_vision(pdf_bytes: bytes) -> dict | None:
    """Use Claude vision to extract fields from scanned PDF pages."""
    if not LITELLM_KEY:
        return None

    images = _pdf_to_images(pdf_bytes)
    if not images:
        return None

    content = [
        {
            "type": "text",
            "text": (
                "Extract the following fields from this COBRA Employer Contribution Form. "
                "Return ONLY a JSON object with these exact keys:\n"
                '- "Company Name": the employer/customer name\n'
                '- "Name": the employee/member name\n'
                '- "Medical": percentage (e.g. "100%") or dollar amount (e.g. "$1,550.00") or "0%" if N/A\n'
                '- "Dental": same format as Medical\n'
                '- "Vision": same format as Medical\n'
                '- "Admin Fee": "Yes" if the customer pays an admin fee, otherwise ""\n'
                '- "Severance Start": start date in MM/DD/YYYY format\n'
                '- "Severance End": end date in MM/DD/YYYY format\n'
                '- "Term date": termination date in MM/DD/YYYY format\n'
                "\nIf a field is not found, use an empty string. Return only the JSON, no other text."
            ),
        }
    ]
    for img_b64 in images[:3]:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
        })

    try:
        resp = requests.post(
            f"{LITELLM_BASE}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {LITELLM_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": content}],
            },
            timeout=60,
        )
        if not resp.ok:
            return None
        raw = resp.json()["choices"][0]["message"]["content"]
        # Parse JSON from response (handle markdown code blocks)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
        # Normalize extracted values
        for field in ("Medical", "Dental", "Vision", "Admin Fee", "Severance Start", "Severance End", "Term date", "Company Name"):
            if field in data:
                data[field] = _normalize(field, data[field]) if data[field] else ""
        return data
    except Exception:
        return None


def _read_pdf_text(pdf_bytes: bytes) -> str:
    import io
    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                pages.append(page_text)
    return "\n".join(pages)


def _normalize(field: str, value: str) -> str:
    if field in ("Medical", "Dental", "Vision"):
        if value.upper() == "N/A" or value == "$0.00":
            value = "0%"
        elif not value.endswith("%") and not value.startswith("$"):
            value = value + "%"
    elif field == "Admin Fee":
        # Any match on this pattern means Yes
        value = "Yes"
    elif field in ("Severance Start", "Severance End", "Term date"):
        # Normalize to MM/DD/YYYY with zero-padded month and day
        parts = value.split("/")
        if len(parts) == 3:
            month = parts[0].zfill(2)
            day = parts[1].zfill(2)
            year = "20" + parts[2] if len(parts[2]) == 2 else parts[2]
            value = f"{month}/{day}/{year}"
    elif field == "Company Name":
        value = value.strip().rstrip(".")
    return value
