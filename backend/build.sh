#!/usr/bin/env bash
# Render build command. Exits on first failure so a broken build never deploys.
set -o errexit

pip install -r requirements.txt

# Chromium is only the fallback strategy, but it must exist for the fallback
# to work. If this line is removed, the scraper still runs — it just loses its
# last resort.
playwright install --with-deps chromium

python manage.py collectstatic --no-input
python manage.py migrate
