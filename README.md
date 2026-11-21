# Internship Radar

Every morning this checks ~200 employers in Northern Indiana (South Bend, Elkhart, Warsaw,
Fort Wayne, NWI), Southern Michigan (Kalamazoo, Benton Harbor/St. Joe, Grand Rapids, Lansing),
plus Chicago and remote roles. It keeps the CS / IT / cyber / data / audit / business
internships and emails you only the ones that are **new since yesterday**.

- **Email:** a digest of new matches, grouped by region.
- **Web page:** `docs/index.html`, published with GitHub Pages. It lists every open match and you can search and filter it.
- **CSV:** `docs/matches.csv` for a spreadsheet.
- **Health report:** `docs/sources.md` shows which company boards worked and which need a fix.

## How it finds jobs

1. **Company boards (`config/companies.yaml`).** For each company it finds the careers page and
   works out which hiring system runs it. It then reads that system's public job feed directly:
   Workday, Paylocity, UKG/UltiPro, ADP, iCIMS, Phenom, SuccessFactors, Greenhouse, Lever, Ashby,
   SmartRecruiters, BambooHR, Pinpoint, Oracle and others. Boards it can't read as data (Paycom,
   Hirebridge, custom sites) get a plain scan of the page for "intern" links. Detected boards are
   cached in `data/boards.json` and re-checked every 3 weeks.
2. **Community list.** The Pitt CSC + Simplify Summer 2027 list catches companies that aren't in
   the YAML.
3. **Adzuna (optional).** A job-search API with a free key. It reaches small local employers that
   only post to job boards.

It doesn't scrape Indeed, LinkedIn or Handshake. Their terms forbid it.

## Setup (about 10 minutes)

1. **Create a private GitHub repo** and push this folder to it.
2. **Make a Gmail app password.** Turn on 2-step verification on your Google account, then go to
   https://myaccount.google.com/apppasswords and create one called "internship radar".
3. **Add repo secrets** under Settings → Secrets and variables → Actions → New repository secret:

   | Secret | Value |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `587` |
   | `SMTP_USER` | your Gmail address |
   | `SMTP_PASSWORD` | the 16-character app password |
   | `EMAIL_TO` | where to send it (comma-separate several addresses) |
   | `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | *(optional)* free from https://developer.adzuna.com |

4. **Turn on the web page** under Settings → Pages → Source: *Deploy from a branch* → `main` / `/docs`.
   GitHub Pages on a private repo needs a paid plan. On a free plan, make the repo public or skip
   the page; the email and CSV still work.
5. **Run it once by hand:** Actions → Internship Radar → Run workflow. The first run emails
   everything that's currently open, because it's all new. After that you only get new postings.
   It runs daily at about 6:17 AM Eastern.

## Tuning

- **Add a company:** add a line to `config/companies.yaml` with `name` and `website`. That's usually enough.
- **A company shows 0 postings with an error in `docs/sources.md`:** open its careers site, click
  through to the job list, and paste that URL as `careers:` for the company. The header of
  `companies.yaml` shows how to pin a board exactly.
- **Too much or too little in a field:** edit the keyword lists in `config/filters.yaml`. A title
  needs an internship word *and* a field word. Engineering-only titles (mechanical, quality, HR…)
  are dropped unless they also hit a strong tech or finance word.
- **Change the area:** edit `regions` in `config/filters.yaml`. A city counts when it appears
  with its state. Add Detroit-area cities to `Southern Michigan` if you want them.
- **Companies found by the community list or Adzuna in Northern Indiana or Southern Michigan that
  you don't track yet** are collected in `data/discovered_companies.json`, ready to promote into the YAML.

## Run locally

```bash
pip install -r requirements.txt
python -m pytest -q
python -m radar.main --no-email                    # full run, writes docs/ and data/
python -m radar.main --only "Lippert,Zimmer Biomet" # debug a couple of companies (no files written)
python -m radar.main --redetect                    # ignore cached board locations
```
