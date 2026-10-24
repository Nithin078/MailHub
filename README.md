# Inbox Agent

A personal mail desk. It watches Gmail, applies your rules before any model is involved, and sends a high-priority push for the mail that actually matters. Placement mail from different senders can share one company timeline. Bank debits stay silent and never spend a token.

There is no shared API key. If you have not added one, the app keeps working: rules, search, folders, compose, and notifications. Model features stay off and say so.

## Try the demo

From this folder, with Python 3.12:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

In another terminal:

```powershell
cd web
npm install
npm run dev
```

Open http://localhost:5173 and choose **Open the demo inbox**.

What you should see:

- A Kotak fraud alert in the inbox, marked urgent by a rule. The trace says the model was not called.
- Kotak and Slice debits only under **Silenced**. Same rule, with an exception for fraud, blocked, and suspicious.
- An Accenture timeline that already holds the campus announcement, the HirePro link, and the recruiter follow-up. Those were attached by hand, which is how timelines work with no API key.
- **Drop in a sample** to fire the pipeline again. A bank debit does not notify. A fraud alert does.
- In Settings, **Enable demo model** turns on an on-device classifier. It does not call the network. New recruiter samples can open a timeline by themselves. Old mail is not backfilled.

Sign-in for the form, if you want it: `demo@inboxagent.local` / `demopass`.

```powershell
cd backend
..\.venv\Scripts\python -m pytest
```

## What was built

Phases 0 through 4 of the brief are in this repo, plus the AI-off checklist that does not need a later phase (folders, search, grouping, unsubscribe, snooze, reminders, backup). These phase-5 items are not built, because the brief says to add them only when asked: semantic search, attachment understanding, a home-screen widget, expense parsing, and automatic thread summaries.

| Piece | Where |
| --- | --- |
| API, pipeline, rules, Gmail, FCM | `backend/app` |
| Tests | `backend/tests` |
| Web UI | `web/` |
| Android app | `android/` |
| Compose, including a Redis-free mode | `docker-compose.yml`, `docker-compose.lite.yml` |

Decisions, so nothing is hidden:

- Package name `com.solarquack.inboxagent`. The public host in the examples is `mail.solarquack.in`.
- Local demo uses SQLite and an in-process worker. Compose uses Postgres. Redis wakes the worker; the job row still lives in Postgres, so the lite file can drop Redis.
- Demo accounts never call Google. Real accounts use `gmail.readonly` until you grant modify from the Accounts screen.
- Sender rules match the DKIM or SPF domain, not the display name.
- Uncertain mail is normal priority. Nothing is dropped because a model was unsure or missing.
- The same Message-ID delivered to two accounts is stored twice and notified once.
- Folder depth is `MAX_FOLDER_DEPTH` in `backend/app/constants.py` (10).
- A group is display-only, needs at least 3 mails, and is not computed until you answer the prompt.

## Google Cloud

One project and one OAuth client serve every account.

1. Create a project. Enable the Gmail API and Cloud Pub/Sub.
2. OAuth consent screen. External + Testing is fine for yourself, with two consequences: only listed test users can connect, and refresh tokens expire after 7 days (`invalid_grant`). Publishing the app to Production removes the 7-day limit. Sensitive Gmail scopes may then require verification. Add yourself as a test user while you stay in Testing.
3. Create an OAuth client (Web). Authorized redirect: `https://mail.solarquack.in/api/accounts/connect/callback` and, for local, `http://localhost:8000/api/accounts/connect/callback`.
4. Create a Pub/Sub topic, for example `gmail-push`. Grant **Pub/Sub Publisher** to `gmail-api-push@system.gserviceaccount.com` on that topic.
5. Create a push subscription to `https://mail.solarquack.in/api/gmail/push?token=YOUR_TOKEN`. Enable authentication and set the audience to that URL if you want OIDC instead of, or as well as, the token.
6. Put the client id, secret, topic (`projects/PROJECT/topics/gmail-push`), audience, and token in `.env`.

Watches expire after at most 7 days. The worker renews any watch that will lapse within 24 hours. If a watch lapses, mail goes quiet until the next renewal; the account is not marked broken for that alone.

OAuth errors:

- `admin_policy_enforced` (and similar policy errors) sets the account to **blocked** and shows: this account may be blocked by its administrator. There is no workaround.
- `invalid_grant` and other auth failures set **needs_reconnect**. Other accounts keep working.
- `access_denied` means the person cancelled. The account status is left as it was.

Connect asks only for `gmail.readonly`. Archive, trash, labels, and send ask for `gmail.modify` the first time you use them.

## Firebase

1. Create a Firebase project and add an Android app with package `com.solarquack.inboxagent`.
2. Download `google-services.json` into `android/app/`. The project builds without it; pushes start working after you add it and apply the Google services plugin.
3. Create a service account with Firebase Cloud Messaging rights. Save the JSON outside the repo and set `FCM_PROJECT_ID` and `FCM_SERVICE_ACCOUNT_FILE`.

Messages are data-only, with `android.priority` set to `HIGH`. The app builds the notification on the urgent, normal, or digest channel. Android will deprioritize high-priority data if the app does not show a notification, so the service always does.

Until FCM is configured, the same notification is stored and shown in the web inbox. Delivery status is `stored`.

## Phone

Open `android/` in Android Studio (JDK 17). Set the API base URL on the sign-in screen. For a local server over USB you can use `adb reverse tcp:8000 tcp:8000` and `http://127.0.0.1:8000`.

The first launch explains battery optimization. Aggressive OEM power management delays or drops pushes even when FCM is healthy. The screen shows whether the exemption is granted and links to the system dialog.

The app acknowledges a notification when it arrives. Urgent mail that is not acknowledged is sent again, up to the limit in Settings.

## Deploy on the free tier

Target: an Oracle Cloud Always Free Ampere A1 VM in the home region (Mumbai `ap-mumbai-1` or Hyderabad `ap-hyderabad-1`), shaped at **2 OCPU and 12 GB**. As of mid-2026 Oracle's Always Free A1 allowance is 1,500 OCPU-hours and 9,000 GB-hours per month, which is that shape running all month. Confirm the current numbers in the Oracle console before you create the instance. Free-tier limits change without much notice.

Fallback: a Google Cloud `e2-micro` (1 GB). Use the lite file so Redis is not running:

```bash
docker compose -f docker-compose.lite.yml up -d --build
```

Do not use a host that sleeps. Pub/Sub will not wait.

```bash
# DNS A record: mail.solarquack.in -> the VM
cp .env.example .env   # fill secrets, set DEMO_MODE=false
docker compose --profile edge up -d --build
```

Caddy fetches a certificate for `SITE_ADDRESS`. Open ports 80 and 443 in the Oracle security list and on the VM firewall. Pub/Sub needs a valid public certificate.

Cost: stay inside Always Free. In Oracle, watch **Billing → Cost analysis** and set a budget alert at a small amount so a shape mistake cannot run up a bill. In Google Cloud, set a budget alert on the billing account and confirm the VM badge says Always Free.

Backups, from the VM, writing somewhere that is not the boot disk:

```bash
BACKUP_DIR=/mnt/backups ./deploy/backup.sh
./deploy/restore.sh /mnt/backups/inbox-YYYYMMDDTHHMMSSZ.sql.gz
```

Put that script on a daily cron. To rebuild: new VM, install Docker, point DNS, restore the dump, `docker compose --profile edge up -d --build`, then reconnect Gmail if the tokens were encrypted with a key you still have. Keep `TOKEN_ENCRYPTION_KEY`. Without it the stored refresh tokens and model keys cannot be read.

Oracle may reclaim an Always Free instance it considers idle, or disable one that no longer fits a quota change. Check the instance once a week. If it is gone: create a new A1 (or the e2-micro fallback) in the same home region, attach or restore the backup, update DNS, and start Compose. The Gmail watch will renew on the worker's next pass. Expect to press Reconnect if the OAuth client was in Testing and a week has passed.

## Layout of a decision

For each new message, in order: your rules (first match wins), then free heuristics, then a fingerprint you or a rule silenced before, then the model if a valid key exists and the daily cap is not spent. A miss at every step becomes normal priority with category `other`. Processing the same Gmail id twice does not notify twice and does not call the model twice.

Heuristics only suggest digest or silent, and only when the signal is boring (promotions, or bulk no-reply with no urgency words). They never override a rule.

## Known limits

- Phase 5 items listed above are absent.
- Search understands Gmail operators and applies them to mail stored here. A connected account also receives the Gmail half of the query; paging tests cover the splitter. Typo-tolerant search is the local trigram used for grouping, not a Postgres index.
- Remote images in HTML are removed. The message is shown in a sandboxed frame with scripts off.
- “Add to calendar” is a Google Calendar template link plus a local reminder, not a Calendar API insert.
- The Android project needs Android Studio to compile. FCM needs `google-services.json`.
- Demo mode is on by default so a checkout runs. Turn it off on a server, and set `TOKEN_ENCRYPTION_KEY` and `SECRET_KEY`.
