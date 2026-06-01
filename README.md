# JobTracker
## What it does

- **Auto-detects emails** in your Gmail related to job applications, recruiter outreach, interview scheduling, rejections, and offers
- **Reads your calendar** for interview events and links them to the right application
- **Classifies everything with Claude AI** so it works across all HR platforms and email phrasings
- **Tracks stages**: Applied → Phone Screen → Interview → Technical Assessment → HR Interview → Offer / Rejected / Declined / Ghosted
- **Shows a funnel dashboard** with conversion rates at each stage (e.g. 100 applied → 40% phone screen → 20% interview → 5% offer)
- **Manual entry and stage movement** for anything without an email trail (referral applications, recruiter phone calls, verbal decisions)

### What it replaces — and why that matters

Most job seekers manage their search in a spreadsheet. That means opening Gmail, hunting down the confirmation email, copying the company name and role title into a row, and repeating that for every single application. It's tedious enough that most people either fall behind or give up on tracking entirely — which means the effort they're putting in becomes invisible, even to themselves.

JobTracker eliminates that loop. Your applications are captured automatically, stages update as emails arrive, and every sync click adds to a complete record of your search without you touching a spreadsheet.

The bigger payoff is what that record shows you. Without it, your sense of how you're doing is just a feeling — *"I think I'm getting callbacks maybe 30% of the time?"*. With JobTracker, you get exact conversion rates at every stage: how many applications became phone screens, how many phone screens became interviews, where you're consistently dropping off. That's not just satisfying to see — it tells you where to focus, whether that's applying to more roles, tightening your resume, or improving how you perform at a specific stage.

A job search is a serious amount of work. JobTracker makes sure that work is measured, visible, and actionable.

---

## Setup

### 1. Clone and install

```bash
git clone <your-fork>
cd JobTracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Get your Anthropic API key

Sign up at [console.anthropic.com](https://console.anthropic.com) and create an API key.

```bash
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Set up Google OAuth credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (e.g. "JobTracker")
3. Go to **APIs & Services → Library** and enable:
   - **Gmail API**
   - **Google Calendar API**
4. Go to **APIs & Services → OAuth consent screen**
   - Choose **External**
   - Fill in app name (e.g. "JobTracker")
   - Add your Gmail address as a **Test user**
5. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Download the JSON file
6. Save it as `credentials/credentials.json`

### 4. Authenticate

```bash
python setup.py
```

This opens a browser window for the Google OAuth flow. After approving, a token is saved to `credentials/token.json`. You only need to do this once.

### 5. Run the app

```bash
uvicorn backend.main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000)

---

## Configuration (in-app settings)

Click the ⚙ gear icon in the top right to configure:

| Setting | Default | Description |
|---|---|---|
| **Gmail Label** | *(empty)* | If you label job-related emails in Gmail (e.g. "Job Hunt"), enter the label name here. Those emails are always synced first. Leave blank to use keyword search only. |
| **Ghosted after** | 30 days | Applications stuck in "Applied" with no update are auto-flagged as ghosted after this many days. |
| **Min. AI confidence** | 0.6 | Claude's minimum confidence score to accept an email classification. Lower = more emails caught but more false positives. |

---

## Using the app

### Syncing emails
Click **Sync Emails** — the app searches Gmail using your label (if set) plus keyword queries, then runs each thread through Claude to classify the company, role, and stage. Already-processed threads are skipped automatically.

### Syncing calendar
Click **Sync Calendar** — scans 6 months back and 3 months forward for events with external attendees. Interview/screening events are matched to existing applications by company name.

### Manual entry
Click **+ Add Application** to log anything without an email trail: referrals, in-person applications, LinkedIn Easy Apply.

### Moving stages manually
Click any row in the table to open the detail drawer. Use **Move to Stage** + an optional note to log stage changes that happened without an email (e.g. "Recruiter called to schedule interview").

### Stage history
The detail drawer shows a full chronological timeline of every detected email, calendar event, and manual update for that application.

---

## Pipeline stages

| Stage | How it's detected |
|---|---|
| Applied | Application confirmation/acknowledgment email |
| Phone Screen | Calendar event or email scheduling an initial call |
| Interview | Calendar event or email for first-round interview |
| Technical Assessment | Home assignment or technical interview (after first interview) |
| HR Interview | Final-round HR/culture interview |
| Offer | Offer letter email |
| Rejected | Rejection email |
| Declined Offer | Manually marked |
| Ghosted | Auto-detected after N days with no update |

---

## Project structure

```
backend/
  main.py          # FastAPI app and all API routes
  database.py      # SQLite schema and CRUD helpers
  models.py        # Pydantic models and stage logic
  classifier.py    # Claude Haiku email/calendar classification
  email_sync.py    # Gmail API integration
  calendar_sync.py # Google Calendar API integration
frontend/
  index.html       # Single-page app shell
  style.css        # Dark theme dashboard styles
  app.js           # All UI logic (fetch, render, modals, drawer)
credentials/       # gitignored — OAuth files go here
setup.py           # One-time OAuth2 setup script
```

---

## Data privacy

All data stays on your machine. The app uses:
- **Google APIs** — read-only access to Gmail and Calendar
- **Anthropic API** — email subjects, senders, and body excerpts are sent for classification (no full email bodies)
- **SQLite** — stored locally at `jobhunter.db`

Nothing is sent to any other third party.

---

## To Do

Features planned or open for contributions. PRs welcome!

- **CI/CD pipeline** — GitHub Actions workflow that lints the Python backend (ruff/flake8), validates the frontend (eslint or basic HTML checks), and runs tests on every push and pull request. Keeps the main branch green and makes contributions safer to merge.

- **Unit testing** — Test suite covering the classifier logic, database CRUD helpers, and stage-transition rules. Priority targets: `classifier.py` (mock the Anthropic API, assert correct stage mapping from sample email text) and `models.py` (stage ordering, ghosted-detection logic).

- **View archive** — A dedicated archive tab or modal listing applications that were manually archived or auto-closed. Should support restoring an entry back to active, and filtering/searching by company or role. Useful for reviewing past cycles before a new job search.

- **AI career coach** — An integrated coaching layer trained on your personal job search data. You provide your CV, cover letters, and any other context (target roles, preferred industries, salary expectations), and the coach analyses your funnel stats to surface specific, actionable guidance: why you might be dropping off after phone screens, which types of roles your profile lands interviews for most consistently, and where your application materials may be underselling you. Goes beyond raw numbers by connecting your conversion rates to the actual content you're submitting — so instead of "your interview rate is low", you get "your CV doesn't reflect the seniority of the roles you're targeting." The coach could also suggest which roles to prioritise applying to based on your historical hit rate in similar positions or industries.

- **Industry performance analytics** — Extend the funnel dashboard to break down conversion rates by industry or role type (e.g. "You get a phone screen 60% of the time in fintech vs 30% in enterprise SaaS"). Requires tagging each application with an industry, either manually or inferred by Claude during classification.

---

## Contributing

Issues and PRs welcome. Each user self-hosts their own instance — there's no shared server or database.
