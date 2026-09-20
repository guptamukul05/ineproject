# Deployment, click by click

Order matters: database first, then backend, then frontend, then cron.

---

## 1. Supabase (database)

1. Go to https://supabase.com, sign in with GitHub, **New project**.
2. Name it `ine-price-tracker`. Choose a region near you (`ap-south-1` for India).
3. Set a database password and **save it somewhere** — you cannot see it again.
4. Wait ~2 minutes for provisioning.
5. Go to **Project Settings → Database → Connection string → URI**.
6. Pick the **Session pooler** tab (port `5432`), not "Direct connection".
   The direct connection is IPv6-only and Render's free tier cannot reach it.
   This is the single most common reason this deployment fails.
7. Copy the URI and replace `[YOUR-PASSWORD]` with the password from step 3.

You do not create any tables by hand. `manage.py migrate` does it.

---

## 2. Render (backend)

1. Push your repo to GitHub first (see the main README).
2. Go to https://render.com → **New → Web Service** → connect your repo.
3. Settings:
   - **Root Directory:** `backend`
   - **Runtime:** Python 3
   - **Build Command:** `./build.sh`
   - **Start Command:**
     `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
   - **Instance type:** Free
4. Add environment variables (**Environment** tab):

   | Key | Value |
   |---|---|
   | `PYTHON_VERSION` | `3.11.9` |
   | `DJANGO_SECRET_KEY` | a long random string |
   | `DJANGO_DEBUG` | `false` |
   | `DATABASE_URL` | the Supabase pooler URI from step 1 |
   | `CRON_SECRET` | another long random string — you will need it again |
   | `STORE_BASE_URL` | `https://demo.inelabteamdev.com` |
   | `SCRAPE_INTERVAL_MINUTES` | `120` |
   | `PLAYWRIGHT_ENABLED` | `true` |
   | `PLAYWRIGHT_BROWSERS_PATH` | `/opt/render/project/.playwright` |
   | `CORS_ALLOW_ALL` | `true` |

   To generate a random string:
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`

5. **Create Web Service.** The first build takes 5–10 minutes, mostly Chromium.
6. When it is live, open `https://YOUR-SERVICE.onrender.com/api/health/`.
   You should see JSON with `"status": "ok"`.

**If the build fails on the Playwright line:** the free tier sometimes cannot
install Chromium's system dependencies. Set `PLAYWRIGHT_ENABLED=false` and
change the build command to `./build.sh` with the `playwright install` line
commented out. The HTTP strategies still work — you lose only the fallback, and
you can still record the headed run locally, which is all the deliverable needs.

---

## 3. Seed the first product

The store search needs to discover the catalogue once. Easiest way:

- Open the Render **Shell** tab and run:
  ```
  python manage.py probe_store
  python manage.py seed_demo
  ```

If the free tier does not give you a shell, just use the deployed frontend's
search box — the first search triggers the same discovery path.

---

## 4. Vercel (frontend)

1. Go to https://vercel.com → **Add New → Project** → import the same repo.
2. Settings:
   - **Root Directory:** `frontend`
   - **Framework Preset:** Vite (usually auto-detected)
   - **Build Command:** `npm run build`
   - **Output Directory:** `dist`
3. Environment variable:

   | Key | Value |
   |---|---|
   | `VITE_API_BASE` | `https://YOUR-SERVICE.onrender.com` (no trailing slash) |

4. **Deploy.** Takes about a minute.
5. Copy the Vercel URL, go back to Render, and set
   `CORS_ALLOWED_ORIGINS` to that URL. (With `CORS_ALLOW_ALL=true` it already
   works, but setting this is the tidier configuration.)

---

## 5. cron-job.org (the 2-hour schedule)

1. Sign up at https://cron-job.org.
2. **Create cronjob**:
   - **Title:** INE price scrape
   - **URL:**
     `https://YOUR-SERVICE.onrender.com/api/cron/scrape/?key=YOUR_CRON_SECRET`
   - **Schedule:** Custom → every 2 hours (minute `0`, hours
     `0,2,4,6,8,10,12,14,16,18,20,22`)
   - **Request method:** GET
   - Enable **Save responses** so you have an audit trail.
3. Hit **Test run**. You should get HTTP 200 and a JSON body with the run
   summary.

Optionally add a second job hitting `/api/health/` every 14 minutes to keep the
instance warm. This is allowed by the brief ("keep the instance warm if needed")
and makes the 2-hourly scrape start instantly instead of waiting for a cold boot.

---

## 6. Check it end to end

- `GET /api/health/` returns `status: ok`
- The Vercel site loads and the header dot is green
- Searching the store returns products
- Tracking a product produces a scrape log row within a few seconds
- `GET /api/cron/scrape/?key=WRONG` returns 401

---

## Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `could not translate host name` | Used the direct Supabase connection | Switch to the session pooler URI |
| Frontend shows "backend unreachable" | Render instance asleep | Wait ~50 s and reload; add the warm-up cron job |
| CORS error in the browser console | `CORS_ALLOWED_ORIGINS` missing the Vercel URL | Set it, or leave `CORS_ALLOW_ALL=true` |
| Build times out on Chromium | Free-tier build limits | Set `PLAYWRIGHT_ENABLED=false` |
| `relation "tracker_product" does not exist` | Migrations did not run | Check `build.sh` ran `migrate`; run it from the Render shell |
| Cron returns 401 | Key mismatch | The `?key=` value must equal `CRON_SECRET` exactly |
