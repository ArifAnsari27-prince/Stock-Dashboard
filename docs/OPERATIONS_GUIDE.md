# Stock Dashboard — Owner & Operations Guide

*A plain-English manual for running, maintaining, and changing the app —
written so you do not need to be a programmer to follow it.*

---

## Table of contents

1. [What this app is](#1-what-this-app-is)
2. [How it is built (the simple picture)](#2-how-it-is-built-the-simple-picture)
3. [The accounts you need](#3-the-accounts-you-need)
4. [Setting up each account, step by step](#4-setting-up-each-account-step-by-step)
5. [Where every password/key goes](#5-where-every-passwordkey-goes)
6. [How the app stays up to date (maintenance)](#6-how-the-app-stays-up-to-date-maintenance)
7. [How to check everything is healthy](#7-how-to-check-everything-is-healthy)
8. [Changing the app with an AI like Claude](#8-changing-the-app-with-an-ai-like-claude)
9. [What it costs and how it stays free](#9-what-it-costs-and-how-it-stays-free)
10. [Routine tasks (a checklist)](#10-routine-tasks-a-checklist)
11. [Troubleshooting — questions & answers](#11-troubleshooting--questions--answers)
12. [Glossary of terms](#12-glossary-of-terms)

---

## 1. What this app is

This is a **private stock-research dashboard** — a website only you (and people
you invite) can see. It shows around **3,000 US company stocks** across four
well-known groups ("indices"): the **Nasdaq-100**, the **S&P 500**, and
**Russell 1000 / Russell 3000** (the last two are close stand-ins, explained
later).

For each stock it shows:

- **Prices and charts** (daily history, with common technical lines).
- **Fundamentals** — the company's financial health (revenue, margins, debt,
  etc.), taken from official government filings.
- **News headlines**.
- **A screener** — a filter tool where you set rules ("show me stocks with an
  RSI under 30 and revenue growth above 20%") and get a custom list.
- **A heatmap** — a colorful grid of the whole market.
- **Comparisons** — put several stocks or indices side by side.

> **Important:** This is a personal research tool built on free data sources.
> The data is delayed and unofficial. **It is not investment advice**, and the
> app says so on every screen. Do not treat it as a trading system.

---

## 2. How it is built (the simple picture)

Think of the app as a **restaurant**:

- **The kitchen (data collection).** Every night, robots ("scheduled jobs")
  go out to free public sources, gather fresh stock prices, financial filings,
  and news, and cook them into neat files.
- **The pantry (storage).** Those files are stored in an online storage locker
  called **Cloudflare R2**. This keeps a big history without cluttering the app.
- **The dining room (the website).** The dashboard you look at reads the
  finished files from the pantry and displays them. **The dining room never
  cooks** — it only serves what the kitchen already prepared. That is why the
  site is always fast and never waits on the internet while you click around.

Here is the same idea as a diagram:

```
   FREE DATA SOURCES                 ROBOTS (nightly)          STORAGE            WEBSITE
   ─────────────────                 ────────────────          ───────            ───────
   Stock prices (Massive)                                    Cloudflare R2
   Company filings (SEC)      ──►    GitHub Actions   ──►    (the "pantry"   ──►  Streamlit
   News (Finnhub)                     runs the jobs          of data files)       dashboard
   Index lists (Nasdaq, etc.)                                                     (what you see)
```

**The four moving parts, in plain terms:**

| Part | What it is | Who provides it |
|------|-----------|-----------------|
| **The code** | The recipes and the website itself | Lives on **GitHub** (a code-hosting site) |
| **The robots** | Nightly workers that fetch data | **GitHub Actions** (built into GitHub, free) |
| **The pantry** | Online file storage | **Cloudflare R2** |
| **The website** | The dashboard you open in a browser | **Streamlit Community Cloud** |

You do not run anything on your own computer for this to work. Everything runs
in the cloud, on free tiers, on a schedule.

---

## 3. The accounts you need

You need **five** free accounts. Two of them (SEC and Finnhub) are optional or
nearly zero-effort.

| # | Account | What it does | Cost | Required? |
|---|---------|--------------|------|-----------|
| 1 | **GitHub** | Stores the code and runs the nightly robots | Free | **Yes** |
| 2 | **Cloudflare** | The storage pantry (R2) | Free tier | **Yes** |
| 3 | **Streamlit Community Cloud** | Hosts the actual website | Free | **Yes** |
| 4 | **Massive.com** | Provides the stock prices | Free tier | **Yes** |
| 5 | **Finnhub** | Provides news headlines | Free tier | Optional |

There is a sixth "source," the **SEC (U.S. government filings)**, but it needs
**no account** — only an email address written into a setting so the government
knows who is politely requesting data.

---

## 4. Setting up each account, step by step

You will collect a handful of **keys** (think of them as passwords the app uses
to talk to each service). Keep them somewhere safe like a password manager as
you go. Section 5 tells you exactly where to paste each one.

### 4.1 GitHub (the code home + the robots)

1. Go to **github.com** and create a free account (or sign in).
2. The project code already lives in a GitHub **repository** (a project folder)
   named **Stock-Dashboard**. Make sure you are an owner or collaborator on it.
3. That is all for now. GitHub's built-in "Actions" feature runs the nightly
   robots automatically — you do not install anything.

### 4.2 Cloudflare R2 (the pantry)

1. Go to **cloudflare.com**, create a free account, and verify your email.
2. In the left menu, click **R2** (Cloudflare's storage product). You may be
   asked to add a payment card to "activate" R2 even on the free tier — you will
   not be charged unless you far exceed the free limits (see Section 9).
3. Click **Create bucket**. A "bucket" is just a labeled storage box. Name it
   **`stock-dashboard-data`**.
4. Now create an access key so the app can read and write to that box:
   - Go to **R2 → Manage R2 API Tokens → Create API Token**.
   - Give it **Object Read & Write** permission, limited to your one bucket.
   - Click create. Cloudflare shows you three things **once** — copy all three:
     - **Access Key ID**
     - **Secret Access Key**
     - **Endpoint** (a web address that looks like
       `https://<some-id>.r2.cloudflarestorage.com`)
5. Save those three values. You will paste them in later.

> The Region for R2 is always the word **`auto`** — remember that, it comes up
> in the settings.

### 4.3 Massive.com (stock prices)

1. Go to **massive.com** and create a free account.
2. Find your **API key** in the account dashboard (sometimes labeled "API" or
   "developer"). Copy it.
3. The free plan allows a limited number of requests per minute. The app is
   already built to stay under that limit automatically.

### 4.4 Finnhub (news — optional)

1. Go to **finnhub.io** and create a free account.
2. Copy your **API key** from the dashboard.
3. If you skip this, the app works fine — the **News** section simply stays
   empty and shows a short note explaining news is turned off.

### 4.5 SEC (company filings — no account, just an email)

1. No sign-up. The U.S. Securities and Exchange Commission asks that automated
   requests identify themselves with a name and email.
2. You will provide a value that looks like:
   `Stock Dashboard yourname@example.com`.
3. That is the whole "setup."

### 4.6 Streamlit Community Cloud (the website)

1. Go to **share.streamlit.io** and sign in **with your GitHub account** (this
   links the two so Streamlit can see the code).
2. Click **Create app** (or "New app") and choose the **Stock-Dashboard**
   repository, the **main** branch, and the main file **`streamlit_app.py`**.
3. Before or right after it deploys, open the app's **Settings → Secrets** and
   paste in your keys (exact format in Section 5).
4. To keep the site private, open **Settings → Sharing** and turn on the
   **viewer allowlist** — add only the email addresses allowed to view the app.
   Everyone else gets a "you don't have access" screen.

---

## 5. Where every password/key goes

This is the part people find confusing, so here is the rule in one sentence:

> **The robots (GitHub) and the website (Streamlit) each need their own copy of
> the keys, entered in their own settings page. You never put keys in the code.**

### 5.1 In GitHub (so the nightly robots can work)

Go to the repository → **Settings → Secrets and variables → Actions**. There are
two tabs: **Secrets** (for sensitive values) and **Variables** (for non-sensitive
ones).

**Add these as Secrets:**

| Name | Value |
|------|-------|
| `R2_ENDPOINT` | the Cloudflare endpoint web address |
| `R2_ACCESS_KEY_ID` | the Cloudflare access key ID |
| `R2_SECRET_ACCESS_KEY` | the Cloudflare secret access key |
| `MASSIVE_API_KEY` | your Massive.com key |
| `SEC_USER_AGENT` | e.g. `Stock Dashboard yourname@example.com` |
| `FINNHUB_API_KEY` | your Finnhub key (skip if not using news) |

**Add these as Variables:**

| Name | Value |
|------|-------|
| `DATA_URI` | `s3://stock-dashboard-data/prod` |
| `R2_REGION` | `auto` |

### 5.2 In Streamlit (so the website can read the pantry)

Go to your app → **Settings → Secrets**, and paste this block (fill in your real
values between the quotes):

```toml
DATA_URI = "s3://stock-dashboard-data/prod"
R2_ENDPOINT = "https://your-id.r2.cloudflarestorage.com"
R2_ACCESS_KEY_ID = "your-access-key-id"
R2_SECRET_ACCESS_KEY = "your-secret-access-key"
R2_REGION = "auto"
```

The website only **reads** data, so it does not need the Massive, SEC, or
Finnhub keys — only the storage (R2) ones.

> **Golden rule of safety:** keys are like house keys. Never paste them into a
> chat, an email, a document, or the code itself. Only the two settings pages
> above. If a key is ever exposed, "rotate" it (Section 11 explains how).

---

## 6. How the app stays up to date (maintenance)

Once set up, the app **maintains itself** through scheduled robots. You mostly
leave it alone. Here is what runs and when (all times are UTC):

| Robot (workflow) | When it runs | What it does |
|------------------|--------------|--------------|
| **refresh-index-universe** | Weekly, Sunday | Refreshes the master list of ~3,000 companies and which indices they belong to |
| **refresh-index-data** | Nightly, Tue–Sat | The big one: fetches prices, filings, fundamentals, then computes the metrics and index summaries |
| **refresh-news** | Hourly, weekdays during market hours | Pulls fresh news headlines (only if Finnhub is set up) |
| **ci** | Every code change | Automatically checks that new code doesn't break anything |

A few important facts in plain terms:

- **The first nightly run is slow** — the very first time, it downloads about two
  years of history for 3,000 companies and can take **2–3 hours**. After that,
  each night only fetches the newest day and is quick.
- **The website caches data for ~10 minutes.** If you just ran a robot and don't
  see the change, click the **"↻ Refresh data"** button in the app's sidebar, or
  wait ten minutes.
- **Missing a night is harmless.** If a robot fails or is skipped, the next run
  catches up. Nothing breaks permanently.
- **The old "version 1" robots are switched off.** Earlier the app saved data
  directly into the code folder, which bloated it. Those are now disabled and
  kept only as a manual backup option. You do not need to touch them.

**Running a robot by hand (when you want fresh data now):**

1. Go to the GitHub repository → **Actions** tab.
2. Click the workflow you want on the left (e.g. **refresh-index-data**).
3. Click **Run workflow**, pick the **main** branch, and confirm.
4. Watch it run. Green check = success; red X = something went wrong (see
   Section 11).

---

## 7. How to check everything is healthy

A quick weekly once-over, no technical skill required:

1. **Open the dashboard.** Does it load? Does the top say a recent "data as of"
   date? If the date is more than a few days old, the nightly robot may be
   failing — check GitHub Actions.
2. **GitHub → Actions tab.** Are the recent runs green? A run history full of
   green checks means the kitchen is healthy. A red X is your signal to look at
   Section 11.
3. **Spot-check a stock.** Open the **Ticker** tab, pick a well-known company
   (say AAPL), and confirm the price chart and some fundamentals appear.
4. **Cloudflare R2 → your bucket.** You should see folders like `prices`,
   `metrics`, `fundamentals`, `news`, `universe`. Their "last modified" dates
   should be recent.

If all four look fine, the app is healthy.

---

## 8. Changing the app with an AI like Claude

You do **not** need to learn to code to evolve this app. The project was built to
be changed by an AI assistant (**Claude Code**), which reads the whole project,
makes edits, runs the tests, and explains what it did. Your job is to **describe
what you want clearly** and to **review and approve**.

### 8.1 How the collaboration works

1. You open the project with the AI assistant.
2. You describe a change in plain English (examples below).
3. The AI proposes a plan, then makes the edits on a **separate copy of the code**
   (a "branch"), so the live app is never touched until you approve.
4. The AI runs the automated tests to prove nothing broke.
5. You review, and if happy, the change is "merged" and goes live.

### 8.2 There is a rulebook the AI follows

The project contains a file called **`CLAUDE.md`**. Think of it as the standing
instructions the AI reads before every task — it spells out the guardrails:
stay free, never fake data, keep the data collection separate from the website,
respect the data providers' rate limits, never expose keys, and always show the
"not investment advice" disclaimer. Because of this file, the AI stays inside
the guardrails automatically. **If you want to permanently change a rule, ask the
AI to update `CLAUDE.md`.**

There is also **`docs/ARCHITECTURE_AUDIT.md`**, a long-term plan of what to build
next and in what order. Point the AI there when you want to pick up the next
planned feature.

### 8.3 Good ways to ask for changes

Be specific about the *outcome* you want; you do not need to know how it's done.

- ✅ "Add a column to the screener that shows dividend yield, and let me filter by
  it."
- ✅ "On the heatmap, add the option to color stocks by their 3-month return."
- ✅ "The news tab should let me see headlines for any stock in my saved screen."
- ✅ "Make the comparison chart also show trading volume."
- ⚠️ Vague asks like "make it better" force the AI to guess. Say what "better"
  means to you.

### 8.4 Things to always ask the AI to do

- **"Run the tests and show me they pass."** This is the safety net.
- **"Work on a branch and don't change anything unrelated."**
- **"Explain in plain English what this change does and anything I need to set
  up."**
- For anything touching money, data providers, or privacy: **"Does this cost
  money or need a new account? If so, stop and ask me first."**

### 8.5 What to be cautious about

- **Paid data or services.** The whole point is $0/month. If a request would need
  a paid plan (for example, live options data), the AI is instructed to stop and
  ask. Say yes only if you accept the cost.
- **Deleting historical data.** The stored price/filing history is valuable and
  hard to rebuild. Don't approve deletions unless you understand why.
- **Anything involving the keys.** The AI should never print or move your keys.

---

## 9. What it costs and how it stays free

Today the app runs at **about $0/month**. Here is why, and the "warning lines"
that would change that:

| Service | Free allowance | You'd only pay if… |
|---------|---------------|--------------------|
| **GitHub Actions** | Unlimited minutes for public repos | You made the repo private *and* used lots of robot time |
| **Cloudflare R2** | 10 GB storage, generous requests, **free downloads** | Your stored data exceeded ~10 GB (years of data, or adding options data) — then a few cents per GB |
| **Streamlit Cloud** | 1 free app, sleeps when idle | You needed more memory or a custom web address |
| **Massive.com** | Limited requests/minute, end-of-day prices | You wanted live/intraday or much heavier use |
| **Finnhub** | Limited requests/minute of news | You wanted much more news volume |

**The most likely first cost** would come from wanting **live options data**,
which has no free source. That is a deliberate decision you would make on
purpose, not a surprise bill. Everything currently in the app is free.

---

## 10. Routine tasks (a checklist)

**Weekly (2 minutes):**
- [ ] Open the dashboard; confirm the "data as of" date is recent.
- [ ] Glance at GitHub → Actions; confirm recent runs are green.

**Monthly (10 minutes):**
- [ ] Spot-check a few stocks' charts and fundamentals.
- [ ] Confirm the R2 bucket folders have recent dates.
- [ ] Skim any failed robot runs and re-run them if needed.

**Occasionally / as needed:**
- [ ] Rotate a key if you suspect it leaked (Section 11).
- [ ] Ask the AI to build the next planned feature from the roadmap.
- [ ] After adding a new data field, re-run **refresh-index-universe** so the
      stored data picks it up.

**Once, at setup:**
- [ ] Create the five accounts (Section 4).
- [ ] Paste keys into GitHub and Streamlit (Section 5).
- [ ] Turn on the Streamlit viewer allowlist for privacy.
- [ ] Manually run **refresh-index-data** once to fill the pantry (2–3 hours).

---

## 11. Troubleshooting — questions & answers

### General

**Q: The dashboard loads but says "No data yet" or looks empty.**
A: The pantry (R2) has no data, or the website can't reach it. Check, in order:
(1) Have the nightly robots ever run successfully? Go to GitHub → Actions and
run **refresh-index-data** manually. (2) Are the R2 keys entered correctly in
Streamlit's Secrets? A typo in `DATA_URI` or the keys will cause an empty app.
(3) Wait for the first run to finish (2–3 hours the first time).

**Q: I made a change / ran a robot, but the website still shows old numbers.**
A: The site caches data for about 10 minutes. Click **"↻ Refresh data"** in the
sidebar, or wait. This is normal and keeps the app fast.

**Q: The whole site is slow to load the first time I open it.**
A: A "cold" load has to fetch data from storage and can take a couple of minutes,
especially the comparison view. Once loaded it's fast. Speeding this up is a
known planned improvement (see the roadmap's "Phase B").

**Q: Numbers look wrong or a company shows blanks.**
A: The app never invents data — a blank means the free source didn't provide that
figure. For example, valuation ratios (P/E, P/S) are intentionally blank right
now; filling them is a planned feature. If a whole stock is blank, its data may
not have downloaded yet; re-run the nightly robot.

### The robots (GitHub Actions)

**Q: A workflow run has a red X. What do I do?**
A: Click the failed run to see which step failed. The most common causes:
(1) **A key is missing or wrong** — the error mentions credentials or "unset";
fix it in GitHub → Settings → Secrets. (2) **A data provider was temporarily
down or rate-limited** — just re-run the workflow; these are usually transient.
(3) **A recent code change broke something** — the "ci" check would also be red;
ask the AI to investigate.

**Q: The nightly robot didn't run at all last night.**
A: GitHub pauses scheduled robots on repositories with no activity for a long
time (about 60 days). Any commit or a manual run wakes them up. Also, GitHub's
schedule is "best effort" and can be delayed at busy times — a missed night is
caught up the next run.

**Q: The first big run is taking hours — is it stuck?**
A: The first **refresh-index-data** genuinely takes 2–3 hours because it
backfills ~2 years of history for 3,000 companies, deliberately slowly to respect
the free data limits. Let it finish. It only happens once.

**Q: I get errors mentioning "rate limit" or "429."**
A: A data provider is telling us to slow down. The app already paces itself, so
this is usually temporary — re-run later. If it happens constantly, a provider
may have tightened their free tier; ask the AI to adjust the pacing.

### Storage (Cloudflare R2)

**Q: Errors mention "access denied," "credentials," or "no such bucket."**
A: The R2 keys are wrong, expired, or the bucket name/endpoint doesn't match.
Recheck the three R2 values in **both** GitHub Secrets and Streamlit Secrets,
confirm the bucket is named `stock-dashboard-data`, and that `DATA_URI` is
`s3://stock-dashboard-data/prod`.

**Q: How do I "rotate" a key (I think one leaked)?**
A: Rotating means replacing a key with a new one so the old one stops working.
For R2: Cloudflare → R2 → Manage API Tokens → delete the old token → create a new
one → paste the new three values into GitHub Secrets and Streamlit Secrets. For
Massive/Finnhub: regenerate the key in their dashboard and update GitHub Secrets.
Do this immediately if a key was ever pasted somewhere public.

### The website (Streamlit)

**Q: The app won't start / shows a red error box on load.**
A: Usually a secrets problem. Open the app's **Settings → Secrets** and confirm
the block matches Section 5 exactly (correct names, values in quotes). Streamlit
also has a **"Reboot app"** button in its settings — try that after fixing
secrets.

**Q: Someone I invited can't see the app.**
A: Add their exact email under **Settings → Sharing** (the viewer allowlist).
They must sign in with that same email.

**Q: I want the app to stop being private / be more private.**
A: Toggle the viewer allowlist in **Settings → Sharing**. Off = anyone with the
link; on = only listed emails.

### News

**Q: The News tab is empty.**
A: Either Finnhub isn't set up (add `FINNHUB_API_KEY` in GitHub Secrets and run
**refresh-news**), or the news robot hasn't run yet. News is optional — an empty
tab is not an error.

### Saved screens

**Q: My saved screener filters disappeared.**
A: Saved screens live in R2 storage. If they vanished, the storage keys may be
misconfigured, or the `saved_screens` data was cleared. Recheck R2 secrets. Going
forward, screens are meant to persist across restarts.

### Changing the app

**Q: The AI made a change and now something is broken.**
A: Because changes happen on a branch and go through tests, the **live app** is
usually safe. Ask the AI to "revert the last change" or "roll back." If a bad
change did go live, ask the AI to revert that specific change — the full history
is kept and nothing is lost.

**Q: I asked for a feature and the AI said it needs a paid service.**
A: That's the guardrail working. Decide whether the feature is worth the cost. If
not, ask the AI for a free alternative — often there is one.

---

## 12. Glossary of terms

- **API key** — a secret password the app uses to talk to a service (Massive,
  Finnhub, Cloudflare). Treat like a house key.
- **Branch** — a separate copy of the code where changes are made and tested
  before going live, so the real app is never disturbed mid-edit.
- **Cache** — a short-term memory. The website remembers data for ~10 minutes so
  it stays fast.
- **Cloudflare R2** — the online storage locker ("pantry") holding the data files.
- **Commit / merge** — saving a change to the code / making an approved change
  part of the live app.
- **Cron / schedule** — the timer that runs the robots automatically.
- **Fundamentals** — a company's financial health figures (revenue, margins,
  debt), from official filings.
- **GitHub** — the website that stores the code and runs the nightly robots.
- **GitHub Actions** — GitHub's built-in robot runner (the "kitchen staff").
- **Index** — a named group of stocks (Nasdaq-100, S&P 500, etc.).
- **Ingestion** — the act of collecting data from sources (what the robots do).
- **Provenance** — the "nutrition label" stamped on every data file: where it came
  from, when, and the disclaimer.
- **Proxy (Russell)** — because the official Russell lists cost money, the app
  approximates them using the largest companies by size. Labeled as a proxy
  everywhere.
- **Rate limit** — a cap on how fast we may ask a provider for data. The app
  respects these automatically.
- **Repository (repo)** — the project folder on GitHub.
- **Rotate (a key)** — replace a key with a fresh one so the old one stops working.
- **Screener** — the filter tool that returns stocks matching your rules.
- **Secret** — a sensitive value (like a key) stored in a settings page, never in
  the code.
- **SEC / EDGAR** — the U.S. government system for official company filings (free).
- **Streamlit** — the tool that turns the code into the website you view.
- **Ticker** — a stock's short symbol (AAPL = Apple).
- **Workflow** — one scheduled robot (a job the kitchen runs).

---

*For the technical companion documents, see `README.md` (setup for developers),
`docs/ARCHITECTURE_AUDIT.md` (full architecture and roadmap),
`docs/api-contract.md`, and `docs/data-dictionary.md`.*
