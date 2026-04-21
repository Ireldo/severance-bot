# Severance Bot

Fetches ER Paid COBRA/Severance Jira tickets, downloads employer COBRA contribution PDFs, and extracts key fields into `results.csv`.

## Setup

**1. Clone the repo**
```
git clone https://github.com/Ireldo/severance-bot.git
cd severance-bot
```

**2. Create a virtual environment**
```
python3 -m venv venv
source venv/bin/activate
```

**3. Install dependencies**
```
pip install -r requirements.txt
```

**4. Set up credentials**
```
cp .env.example .env
```
Open `.env` and fill in your `SITE_USERNAME` and `SITE_PASSWORD`.

**5. Run the bot**
```
python main.py
```
