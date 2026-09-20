# Start here — the exact order to do things

Run every command from inside the `ineproject` folder unless a step says otherwise.
On Windows use PowerShell or Git Bash.

---

## Step 0 — put the files in your cloned repo

You already made a GitHub repo and cloned it to your Desktop. Copy the contents
of this folder into that clone, so you end up with:

```
Desktop/ineproject/       <- your git clone
  README.md
  START_HERE.md
  .gitignore
  backend/
  frontend/
  docs/
  .github/
```

Check that git sees it:

```bash
cd ~/Desktop/ineproject      # Windows: cd $HOME\Desktop\ineproject
git status
```

---

## Step 1 — backend running locally

```bash
cd backend
python -m venv .venv
```

Activate it:

- macOS / Linux: `source .venv/bin/activate`
- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- Windows Git Bash: `source .venv/Scripts/activate`

Then:

```bash
pip install -r requirements.txt
playwright install chromium
```

`playwright install chromium` downloads a browser (~150 MB) and takes a few
minutes. Let it finish.

Make your local env file:

```bash
cp .env.example .env          # Windows PowerShell: copy .env.example .env
```

Open `backend/.env` in your editor. For now just set:

```
DJANGO_SECRET_KEY=any-long-random-string-here
DJANGO_DEBUG=true
CRON_SECRET=another-long-random-string
```

Leave `DATABASE_URL` blank for now — with no database URL the app uses a local
SQLite file, which is perfect for getting started. You will fill in Supabase in
Step 5.

Create the tables:

```bash
python manage.py makemigrations tracker
python manage.py migrate
```

> The `makemigrations` step generates `tracker/migrations/0001_initial.py`.
> **Commit that file to git** — the CI workflow checks it exists, and Render
> needs it to build your tables.

Run the tests to confirm the install is sane:

```bash
python manage.py test tracker
```

All tests should pass. If they do, your parsing and validation logic is correct.

---

## Step 2 — look at the store before scraping it

```bash
python manage.py probe_store
```

This opens the store in a browser, waits for it to load, and tells you:

- which JSON endpoints the page calls for itself
- which of the scraper's candidate selectors actually match
- where it saved a copy of the rendered HTML (`backend/debug/rendered.html`)

**Read this output carefully — it is the most important 30 seconds of the
project.** If a selector group says `MISSING`, open `backend/debug/rendered.html`,
find the element that actually holds the price, and add its selector to the top
of `PRICE_SELECTORS` in `tracker/scraper/config.py`. Same for stock and name.

If `probe_store` found a JSON endpoint, you are in good shape — the scraper will
use plain HTTP from here on.

---

## Step 3 — first real scrape

```bash
python manage.py seed_demo
python manage.py runserver
```

Leave this terminal running. Open http://127.0.0.1:8000/api/health/ in a browser
— you should see JSON.

---

## Step 4 — frontend running locally

Open a **second terminal**:

```bash
cd ~/Desktop/ineproject/frontend
npm install
cp .env.example .env
```

Edit `frontend/.env` to say:

```
VITE_API_BASE=http://127.0.0.1:8000
```

Then:

```bash
npm run dev
```

Open http://localhost:5173. Search for a product, track it, and watch the scrape
log fill in.

**Do not go further until this works locally.** Deploying a broken app is much
harder to debug than fixing it on your own machine.

---

## Step 5 — commit and push

```bash
cd ~/Desktop/ineproject
git add .
git commit -m "Price tracker: adaptive scraper with retry ladder and honest logging"
git push origin main
```

Double-check `.env` files did **not** get committed:

```bash
git ls-files | grep "\.env$"
```

That should print nothing. If it prints something, remove it:
`git rm --cached backend/.env` and commit again.

---

## Step 6 — deploy

Follow [`docs/DEPLOY.md`](docs/DEPLOY.md) in order: Supabase → Render → Vercel →
cron-job.org. Budget about 45 minutes, mostly waiting for builds.

---

## Step 7 — record the headed run

Start your screen recorder (OBS, or Win+G on Windows, or Cmd+Shift+5 on macOS),
then in the backend terminal:

```bash
python manage.py scrape_headed --chaos
```

Narrate roughly this, in 2–4 minutes:

1. "This is the headed run. A real Chromium window opens and a HUD on the page
   shows each decision." — point at the HUD.
2. "I've turned on fault injection, so requests are randomly stalled or
   returned as 503." — point at the terminal showing a failed try.
3. "It backs off and retries rather than giving up." — show the backoff line.
4. "When a strategy is exhausted it drops to the next one in the ladder."
5. "It waits for the price to be stable across two reads before accepting it,
   rather than reading whatever's on screen when the network goes quiet."
6. Switch to the dashboard. "The scrape log records the retry honestly — amber,
   not green. And a failed scrape writes no price point at all."

Then run it once without `--chaos` to show a clean run, if you have time.

Keep the file under about 200 MB. Upload to Google Drive or YouTube (unlisted)
and set the link permission to "anyone with the link can view".

---

## Step 8 — fill in the README and submit

At the top of `README.md`, replace the three placeholder lines with your real
Vercel URL, Render URL, and video link. Commit and push.

Email **sstephen@ine.com**, cc **ssingh@ine.com**, subject:

```
First Round: Software Engineer Intern Assignment - <Your Name>
```

Body: the live link, the GitHub URL, the video link, and a two-line summary.
Attach your resume as a PDF. The design note lives at `docs/DESIGN_NOTE.md` in
the repo — mention that in the email.

---

## If something breaks

| Symptom | What to do |
|---|---|
| `ModuleNotFoundError: django` | Virtualenv is not activated. Re-run the activate command. |
| `playwright: command not found` | `pip install -r requirements.txt` did not finish. Re-run it. |
| `probe_store` shows everything MISSING | Open `backend/debug/rendered.html`, find the real selectors, add them to `config.py`. |
| Search returns nothing | Run `probe_store` first — the catalogue endpoint has to be discovered once. |
| Frontend says "backend unreachable" | Is `runserver` still running in the other terminal? Is `VITE_API_BASE` correct? |
| `no such table: tracker_product` | You skipped `python manage.py migrate`. |
