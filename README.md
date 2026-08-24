# Daily Founder Notifications

One notification a day, at **6:00 PM ET**, naming the most expensive company
acquisition announced the day before. Tapping through opens the most widely
read article about the deal.

```
AI company, Cursor, was acquired by Aerospace company, SpaceX, for $60 billion. Tap to read more.
```

Figures at or above a billion read as billions; smaller deals read as millions.
The linked article is always the most widely read coverage that isn't behind a
paywall.

The email has two parts: the acquisition above (which is also the subject line)
and the biggest US/Canada property deals below, each with a line on why it is
worth knowing.

Delivery is a plain email to your own Gmail, sent over SMTP with an App
Password — no third-party service, nothing to install, no monthly cost. **The
notification text is the email's subject line**, because the subject is what
iOS renders on the lock screen. The body is just a large tappable button to the
article.

## How it works

```
GitHub Actions cron (22:00 + 23:00 UTC, gated to 18:00 ET)
  │
  ├─ 1. scripts/fetch_candidates.py     stdlib only, no API keys
  │       7 Google News RSS queries + PR Newswire + GlobeNewswire
  │       → filter to acquisitions with a parsed USD price ≥ $50M
  │       → work/candidates.json  (~120 candidates)
  │
  ├─ 2. scripts/select_deal.py           Gemini API, free tier, JSON schema
  │       → throws out bad price parses (AUM, pay packages, funding rounds)
  │       → picks the largest valid deal, labels industries, picks the article
  │       → work/deal.json
  │
  ├─ 3. the same two steps in --mode realestate
  │       → US/Canada property transactions, top 3, deduped against history
  │       → work/realestate.json          (best-effort: never blocks the email)
  │
  ├─ 4. scripts/send_notification.py     validates, dedupes, emails via Gmail SMTP
  │
  └─ 5. commits state/ back to the repo (also the daily heartbeat)
```

**Why the split.** Python does the numeric work, because regex parses
"$4.1 billion" into `4100` perfectly and an LLM sometimes doesn't. The LLM does
the judgment work, because only judgment catches that "expands its $164 billion
platform by acquiring X" is not a $164 billion purchase price. Each step does
what it is actually good at.

Step 2 is a single API call with a response schema attached, not an agent — the
job is one structured extraction, so the model cannot return a shape the sender
does not expect. About 25k tokens a day across both calls, inside the free tier.

**The selector returns a ranked list, not one pick, and the sender walks it.**
That matters: the dedup list is given to the model as prose, and when it once
failed to recognise a deal it had already sent, the sender found the duplicate,
exited 0, wrote no state, and sent nothing — silently, every day, for as long
as that deal stayed the biggest in the window. Now a bad first pick just costs
one list position. Anything wrong with the *model's output* skips to the next
candidate and exits clean; only infrastructure problems (no credentials,
unreachable Gmail) fail the run.

Deals that fail validation are recorded in `state.skipped` and excluded from
future selections, so one malformed record cannot stall the system again.

## Setup — pick one

**[Option A: run it on this Mac](#option-a--local-mac-recommended)** — no GitHub
account, no tokens, no repo, no secrets page. Two credentials and one command.
Runs whenever the Mac is awake.

**[Option B: run it on GitHub Actions](#option-b--github-actions)** — always on,
independent of your Mac, but needs a GitHub repo, a Personal Access Token, and
three secrets configured by hand.

The pipeline is identical either way. Only the scheduler differs.

---

## Option A — local Mac (recommended)

### 1. Two credentials

- **Gemini API key** — [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
  → *Create API key* → copy.
- **Gmail App Password** — see [step 1 below](#1-gmail-app-password-3-minutes-free).

### 2. One command

```bash
bash "/Users/kaigreaves/Daily Founder Notifications/scripts/setup_local.sh"
```

It prompts for both credentials with the input hidden, writes them to
`~/.config/daily-deals/env` with `600` permissions, and schedules a launchd job
for 6:00 PM daily. Nothing is stored in the repo.

### 3. Test it

```bash
bash "/Users/kaigreaves/Daily Founder Notifications/scripts/run_daily.sh" --dry-run
```

Drop `--dry-run` to actually send. Logs land in `~/Library/Logs/daily-deals.log`.

### Managing it

```bash
launchctl print "gui/$(id -u)/com.kaigreaves.dailydeals" | head -20
```

To stop it permanently:

```bash
launchctl bootout "gui/$(id -u)/com.kaigreaves.dailydeals" && rm ~/Library/LaunchAgents/com.kaigreaves.dailydeals.plist
```

**The catch:** launchd only fires while the Mac is running. If it's asleep at
6:00 PM the job runs when the machine next wakes, so a closed lid means a late
notification rather than a lost one — but a Mac that's off all evening skips
that day. The 7-day fallback covers you: the next successful run picks up the
biggest deal you haven't been sent yet.

---

## Option B — GitHub Actions

Use this if you want it running whether or not your Mac is on.

I can't create accounts or handle your credentials, so these four are yours.
Everything else is already built.

### 1. Gmail App Password (~3 minutes, free)

Google blocks plain password logins from scripts, so you need an App Password —
a 16-character key that only works for mail and can be revoked on its own.

1. Turn on 2-Step Verification if it is not already on:
   [myaccount.google.com/signinoptions/twosv](https://myaccount.google.com/signinoptions/twosv).
   App Passwords do not exist without it.
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
   name it `Daily Deals`, and create it.
3. Copy the 16-character password. Google shows it with spaces
   (`abcd efgh ijkl mnop`) — **strip the spaces**. Shown once only.

### 2. Gemini API key (~1 minute, free)

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and
   sign in with the same Google account.
2. **Create API key** → pick or create a project → copy the key.

No OAuth, no CLI, no browser callback. At one run a day this stays inside the
free tier permanently.

If the default model is ever retired, check what your key can reach:

```bash
GEMINI_API_KEY=your-key python3 scripts/select_deal.py --list-models
```

Then set a repo variable named `GEMINI_MODEL` (Settings → Secrets and variables
→ Actions → **Variables**) to override it. The default is `gemini-2.5-flash`.

### 3. Push to GitHub

There is no SSH key on this machine, so these use HTTPS. Git is already set to
save credentials to the macOS keychain, so you authenticate once.

```bash
git config --global user.name "Kai Greaves" && git config --global user.email "kaigreaves18@gmail.com"
```

```bash
cd "/Users/kaigreaves/Daily Founder Notifications" && git init -b main && git add -A && git commit -m "Daily deal notifications"
```

Create an empty repo at [github.com/new](https://github.com/new) named
`daily-founder-notifications` — no README, no .gitignore, no license. Then:

```bash
cd "/Users/kaigreaves/Daily Founder Notifications" && git remote add origin https://github.com/<your-username>/daily-founder-notifications.git && git push -u origin main
```

When prompted, the username is your GitHub username and the **password is a
Personal Access Token**, not your account password. Create one at
[github.com/settings/tokens](https://github.com/settings/tokens) →
*Generate new token (classic)* → scope `repo`.

### 4. Add three secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Value |
|---|---|
| `GMAIL_ADDRESS` | `kaigreaves18@gmail.com` |
| `GMAIL_APP_PASSWORD` | the 16 characters from step 1.3, no spaces |
| `GEMINI_API_KEY` | key from step 2 |

`NOTIFY_TO` is optional — add it only to deliver somewhere other than the
sending address.

### 5. Let Mail actually notify you

An email only becomes a lock-screen notification if Mail is allowed to make
one. On the iPhone: **Settings → Notifications → Mail →** your Gmail account →
Allow Notifications on, Alerts set to **Lock Screen + Banners**. If you read
Gmail in the Gmail app instead of Apple Mail, do the same under **Settings →
Notifications → Gmail**. Skip this and the mail lands silently, which looks
exactly like the system being broken.

Worth doing too: a Mail VIP or a filter that never sends `Daily Deals` to spam.

### 6. Test it

**Actions → Daily deal notification → Run workflow**, with `dry_run` checked.
That runs the entire pipeline and prints the notification without sending. Run
it again unchecked to get real mail on your phone.

Scheduled runs start automatically. GitHub disables cron on repos with no
activity for 60 days, but this one commits `state/sent.json` on every send, so
it keeps itself alive.

## The property section

Three US/Canada property transactions per email, ranked by price, chosen for
variety of type and city rather than three of the same thing. Each carries a
`why_notable` line explaining the structure or the signal — sale-leaseback,
price per square foot, distressed sale — because the point is learning what
kinds of deals get made, not just the numbers.

Property deals are deduped separately in `state.re_sent`, so you see new ones
each day rather than the same tower all week. The window is the same 7 days as
the acquisition section, because there is not always a big trade yesterday.

If the property feeds or the second Gemini call fail, those steps are marked
`continue-on-error` and the email goes out with the acquisition alone.

## Behavior

**Empty days.** Most days with no deal are weekends, and roughly two-thirds of
acquisitions never disclose a price. When yesterday has nothing usable, the
system widens to the previous 7 days and sends the biggest deal you have not
already been sent — so you get a notification nearly every day, never a
repeat. `state/sent.json` is the memory that makes that work.

**Nothing at all.** If 7 days produce nothing valid, no notification is sent and
the run succeeds quietly. That is rare.

**Bad data.** The sender refuses to send if a field is missing, the amount is
not a positive number, the URL is malformed, or the LLM marked the deal
`confidence: low`. The run fails and GitHub emails you. **A failed run is
always silence, never a wrong notification.**

**Scope.** All industries, worldwide, USD only. Minority stakes, asset and
property sales, funding rounds, rumors, and unconfirmed bids are excluded.

## Running it locally

```bash
python3 scripts/fetch_candidates.py
```

```bash
GEMINI_API_KEY=your-key python3 scripts/select_deal.py && python3 scripts/send_notification.py --dry-run
```

`fetch_candidates.py` needs no keys and no dependencies. `--dry-run` validates
and prints the notification without sending it or recording anything.

## Files

| Path | What it does |
|---|---|
| `.github/workflows/daily-deal.yml` | Schedule, gating, and the four steps |
| `scripts/fetch_candidates.py` | RSS → filtered, priced candidate list |
| `scripts/select_deal.py` | Gemini call that picks and labels the winner(s) |
| `prompts/select_realestate.md` | Rules for the property section |
| `scripts/setup_local.sh` | Option A: stores credentials, schedules launchd |
| `scripts/run_daily.sh` | Option A: the daily run (also handy for testing) |
| `scripts/send_notification.py` | Validation gate, template, Gmail SMTP, state |
| `scripts/common.py` | Time windows, money parsing, dedup keys |
| `prompts/select_deal.md` | The selection spec the LLM step follows |
| `state/sent.json` | Every deal already sent — the dedup memory |
| `state/last_run.txt` | Date of the last run — the "already ran today" marker |
| `work/` | Per-run scratch, gitignored, uploaded as a run artifact |

## Watchdog

Three consecutive days with nothing sent triggers a self-report email, then one
a week after that. Silence has twice been this system's failure mode — a green
run that quietly sends nothing looks exactly like a slow news week. The
watchdog makes the two distinguishable without anyone checking.

`state.quiet_days` holds the counter; a successful send resets it.

## Feed health

Checked 2026-08-23. Google News RSS supplies ~95% of raw items; the direct
feeds are a supplement, and any of them failing is tolerated and logged.

| Feed | Status |
|---|---|
| Google News RSS (18 queries) | working — the backbone |
| PR Newswire M&A | working |
| GlobeNewswire M&A | times out; left in, costs one 15s timeout |
| Commercial Observer | working |
| The Real Deal | fixed — bare `/feed/` is empty, `/national/feed/` works |
| Bisnow | working |
| STOREYS (Canada) | working |
| RENX | **removed** — returns 410 Gone on every path |

## Known rough edges

- **Tap-through links are often `news.google.com`** URLs. They open the article
  fine on a phone, just with a redirect. The publisher URL lives only inside
  Google's JavaScript payload, so resolving it server-side would mean a
  scraper that breaks whenever Google changes their page.
- **Paywall detection is by outlet, not by article.** Known hard-paywall
  outlets are excluded (see `PAYWALLED_SOURCES` in `scripts/common.py`), but a
  normally-free outlet can still put one specific piece behind a meter. Add any
  offender to that set.
- **Coverage depends on English-language reporting.** A large deal reported only
  in the local press, or quoted only in a non-USD currency without a USD figure,
  will be skipped.
- **Timing.** 6:00 PM ET catches the full prior day. Deals announced overnight in
  Asia occasionally get attributed to the following day by the news feed.
- **Email costs you one extra tap.** A real push notification opens the article
  directly; email opens Mail, and you tap the button. That is the price of not
  installing anything. iOS also truncates long subject lines on the lock screen,
  so the trailing "Tap to read more." may be cut — the companies and the price
  are what survive, which is the part that matters.
