import os
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

SITE_URL = "https://customer-central.justworks.com/"
SITE_USERNAME = os.environ["SITE_USERNAME"]
SITE_PASSWORD = os.environ["SITE_PASSWORD"]
GOOGLE_SHEET_URL = os.environ.get("GOOGLE_SHEET_URL", "")
GOOGLE_SHEET_READ_URL = os.environ.get("GOOGLE_SHEET_READ_URL", "")
AUDIT_SHEET_URL = os.environ.get("AUDIT_SHEET_URL", "")

DOWNLOADS_DIR = os.path.join(PROJECT_ROOT, "downloads")
MIDS_FILE = os.path.join(PROJECT_ROOT, "mid.txt")
RESULTS_FILE = os.path.join(PROJECT_ROOT, "results.csv")
AUTH_STATE_FILE = os.path.join(PROJECT_ROOT, ".auth_state.json")

OUTPUT_COLUMNS = [
    "Cobra Key",
    "WW Case",
    "Company Name",
    "CID",
    "MID",
    "Name",
    "Medical",
    "Dental",
    "Vision",
    "Admin Fee",
    "Severance Start",
    "Severance End",
    "Is the company churning?",
    "# of months ER is paying severance",
    "Term date",
    "Agreement Found",
    "Failure Reason",
]
