# AI Lead Generation Agent

A local, browser-based tool. Type a request such as

> *Find 100 real estate companies in Toronto, Ontario, Canada, including company names, phone numbers, business email addresses, websites, and addresses.*

and the app searches **Google Places** and the **open web (Tavily)**, optionally reads each company's **own public website** for a contact email/phone, removes duplicates, records where every detail came from, and lets you download **Excel** or **CSV**. Every search is saved in **SQLite** so you can reopen and re-export it later.

Stack: Python, Flask, SQLite, Bootstrap 5, Tavily API, Google Places API (New), OpenAI API (optional), pandas + openpyxl, python-dotenv.

---

## What it does and does not do

- Collects **publicly listed** business details only. Anything a source did not publish is shown as **Not Found**. Nothing is guessed, and emails are never constructed (no `info@` + domain guessing).
- Reads a company's homepage and contact page only if `robots.txt` allows it. A login wall, CAPTCHA, `401/403/429` response or paywall means the page is **skipped**, never bypassed. Private/internal addresses are blocked.
- **Verification Status** says *where* a record came from (Google Places listing, official website visited, cross-checked, or unverified search result). **Phone numbers and emails are never called or mailed**, so none is guaranteed valid. If the optional `dnspython` package is installed, the *Email Status* column also notes whether the email's domain has a mail server (MX record). That is still not proof the mailbox exists.
- You are responsible for lawful use: check anti-spam and data-protection rules (CASL in Canada, CAN-SPAM, GDPR, etc.) and each provider's terms before contacting anyone.

---

## Windows + VS Code setup

**1. Install Python** (3.10 or newer) from <https://www.python.org/downloads/>. Tick **"Add python.exe to PATH"** in the installer.

**2. Open the project** in VS Code: *File → Open Folder…* → select the `ai_lead_generation_agent` folder. Open a terminal with *Terminal → New Terminal* (PowerShell is fine).

**3. Create a virtual environment and install packages**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell refuses to run the activate script, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again. (Or use Command Prompt and `.venv\Scripts\activate.bat`.) When VS Code asks which interpreter to use, pick the one inside `.venv`.

**4. Add your API keys**

```powershell
copy .env.example .env
```

Open `.env` in VS Code and fill in the keys (see below). **You need at least one of Google Places or Tavily.** The OpenAI key is optional.

**5. Run the server**

```powershell
python app.py
```

**6. Open the app** in your browser at <http://127.0.0.1:5000>

Shortcut: double-click `run.bat` to do steps 3–5 automatically (it creates `.env` from the example the first time, then stop, add your keys, and run it again).

### Getting the API keys

| Variable | Where | Notes |
|---|---|---|
| `GOOGLE_PLACES_API_KEY` | Google Cloud Console → create a project → enable **Places API (New)** → Credentials → *Create API key* | Billing must be enabled. Requesting phone and website fields is billed at a higher tier, so set a budget alert. |
| `TAVILY_API_KEY` | <https://tavily.com> | A free tier exists. |
| `OPENAI_API_KEY` | <https://platform.openai.com/api-keys> | Optional. Used to parse prompts and to pull company names out of web results (every extracted phone/email must appear literally in the source text or it is dropped). Any OpenAI-compatible endpoint works through `OPENAI_BASE_URL`. |

Keys live only in `.env`, which is read by the Python server. They are never sent to the browser (the page only learns "configured: yes/no"). `.env` is in `.gitignore`.

---

## Using it

1. Type a prompt, or fill in **Industry** and **Location** (these fields override the prompt). **Lead quantity** is optional; if blank, the number in the prompt is used (default 50).
2. Click **Search**. A progress bar shows each stage. Searches run in the background, so you can open **History** meanwhile.
3. Filter and page through the results table, then download **Excel** or **CSV**. Excel has a styled header, filters, a frozen header row and sized columns.
4. **History** lists past searches: reopen, re-export or delete them.

**How a search works:** parse prompt → Google Places (several query variants, up to 3 pages each; with an OpenAI key large searches are also split by neighbourhood) → Tavily only if more candidates are needed → duplicates merged by normalised name and website domain (source URLs are combined) → official websites checked in batches → labelled and saved.

**Realistic expectations:** Google returns at most ~60 results per query, so very large requests (hundreds) can return fewer than requested; the app tells you when that happens instead of padding the list. Small businesses often publish no email at all, so many rows will say *Not Found*.

---

## Project layout

```
app.py                      Flask app: pages, JSON API, exports
config.py                   Settings read from .env
database/db.py              SQLite (searches + leads)
services/
  prompt_parser.py          Prompt -> industry/location/count/filters (LLM + rule fallback)
  llm.py                    OpenAI-compatible client
  business_directory.py     Google Places provider
  web_search.py             Tavily provider
  lead_extractor.py         Search results -> companies; official-website enrichment
  deduplication.py          Merge duplicates; verification labels
  export_service.py         CSV / Excel
  pipeline.py               Orchestrates a search
  base.py                   Provider interface (add new sources here)
templates/ static/          Dashboard and history pages
tests/                      Unit and integration tests
```

**Adding a provider:** subclass `LeadProvider` (`services/base.py`), return leads built with `new_lead(...)`, and add it to `build_providers()` in `services/pipeline.py`.

---

## Tests

No extra packages needed:

```powershell
python -m unittest discover -s tests -t .
```

They cover prompt parsing, deduplication, CSV/Excel export, page parsing, robots.txt handling, provider response parsing (mocked), rate-limit/auth errors, the database, and a full search through the web API with fake providers. The suite does **not** call the real Google, Tavily or OpenAI APIs; test those yourself with your keys.

---

## Troubleshooting

- **"No search provider is configured"** - `.env` is missing, the keys are still the `your_..._here` placeholders, or you didn't restart after editing.
- **Google error 403 / "API key not valid"** - Places API (New) isn't enabled, billing is off, or the key is restricted to other APIs.
- **Rate limit warnings** - the app retries, then stops that provider and keeps what it collected. Wait and retry, or lower the quantity.
- **Page looks unstyled** - Bootstrap loads from a CDN, so the browser needs internet access.
- **Port 5000 in use** - set `PORT=5050` in `.env`.
