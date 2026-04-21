import os
from dotenv import load_dotenv

load_dotenv()

SITE_URL = "https://cstools-workforce.justworks.com/internal"
SITE_USERNAME = os.environ["SITE_USERNAME"]
SITE_PASSWORD = os.environ["SITE_PASSWORD"]
GOOGLE_SHEET_URL = os.environ.get("GOOGLE_SHEET_URL", "")
GOOGLE_SHEET_READ_URL = os.environ.get("GOOGLE_SHEET_READ_URL", "")

DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "downloads")
MIDS_FILE = os.path.join(os.path.dirname(__file__), "mids.txt")
RESULTS_FILE = os.path.join(os.path.dirname(__file__), "results.csv")

OUTPUT_COLUMNS = [
    "Key",
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
]
