# Severance Bot

Fetches ER Paid COBRA/Severance Jira tickets, downloads employer COBRA contribution PDFs from customer-central, and extracts key fields into `results.csv`.

## Setup

**1. Clone the repo**
```
git clone https://github.com/Ireldo/severance-bot.git
cd severance-bot
```

**2. Create a virtual environment**
```
python3 -m venv .venv
source .venv/bin/activate
```

**3. Install dependencies**
```
pip install -r requirements.txt
playwright install chromium
```

**4. Set up credentials**
```
cp .env.example .env
```
Open `.env` and fill in:
- `SITE_USERNAME` / `SITE_PASSWORD` — customer-central (Okta) credentials
- `LITELLM_API_KEY` — (optional) enables vision OCR fallback for scanned PDFs
- `GOOGLE_SHEET_URL` / `GOOGLE_SHEET_READ_URL` — (optional) Google Sheets integration

## Usage

### Running with mid.txt

Add entries to `mid.txt` (one per line, space-separated):
```
M1234567                     — MID only
COBRA-5467 M1234567          — ticket + MID
COBRA-5467 C92657 M1234567   — ticket + CID + MID
```

Then run:
```
python main.py
```

Options:
- `--headless` — run browser without a visible window
- `--dry-run` — list entries without processing

### Running with the Inbox Agent

The inbox agent fetches unassigned COBRA/Severance tickets from Jira, classifies them, and runs the bot automatically:
```
python inbox_agent.py
```

## Output

Results are written to `results.csv` with the following columns:

| Column | Description |
|--------|-------------|
| Cobra Key | Jira ticket key |
| WW Case | WW case number |
| Company Name | Extracted from PDF |
| CID | Company ID (extracted from customer-central) |
| MID | Member ID |
| Name | Member name (from severance agreement) |
| Medical | Contribution amount or percentage |
| Dental | Contribution amount or percentage |
| Vision | Contribution amount or percentage |
| Admin Fee | Whether company pays the admin fee |
| Severance Start | Start date of employer COBRA contribution |
| Severance End | End date of employer COBRA contribution |
| # of months ER is paying severance | Calculated from start/end |
| Term date | Employee termination date |
| Agreement Found | Yes / No / Error |
