# Job Alert Bot (free setup)

Checks job sources every 30 minutes and posts NEW India roles (remote, hybrid, onsite; full-time and contract) to Slack.

## One-time setup (about 30 to 45 minutes)

### 1. Slack webhook
1. Create a free Slack workspace and a channel called `#job-alerts`.
2. Go to https://api.slack.com/apps, choose **Create New App > From scratch**.
3. Open **Incoming Webhooks**, turn it on, click **Add New Webhook to Workspace**, pick `#job-alerts`.
4. Copy the webhook URL (starts with `https://hooks.slack.com/services/...`). Treat it like a password.

### 2. Adzuna API key (free, broad India job coverage)
Sign up at https://developer.adzuna.com and copy your **App ID** and **App Key**. Check their current free-plan request limits; the bot calls Adzuna about once an hour with 8 queries (about 190 calls a day).

### 3. GitHub repo
1. Create a free GitHub account and a new **public** repo (public keeps Actions free).
2. Upload everything in this folder. Keep the path `.github/workflows/job-alerts.yml` exactly.
3. Repo **Settings > Secrets and variables > Actions > New repository secret**. Add:
   - `SLACK_WEBHOOK_URL`
   - `ADZUNA_APP_ID`
   - `ADZUNA_APP_KEY`
4. Repo **Settings > Actions > General > Workflow permissions**: choose **Read and write permissions**, Save.

Never paste the webhook or keys into any file in a public repo.

### 4. First run
1. Open the **Actions** tab, choose **Job Alerts**, click **Run workflow**.
2. Normal first run: saves a baseline of jobs already open and sends "Setup complete". From then on you only get NEW postings.
3. To see sample alerts right away, set `alert_existing` to `true` for that first run. It alerts on jobs from the last 24 hours.
4. After that it runs by itself every 30 minutes.

If a run fails, open it in the Actions tab, copy the error, and paste it to Claude.

## Customising
Edit `config.json`:
- `title_keywords`: job titles to match.
- `companies`: add company names for Greenhouse, Lever and Ashby career pages. Find the name in the URL, for example `boards.greenhouse.io/<name>`, `jobs.lever.co/<name>`, `jobs.ashbyhq.com/<name>`. Wrong names are skipped safely.
- `max_age_hours`: ignore postings older than this (default 24).
- `max_years_required`: skip jobs asking for more years than this (default 7).
- `include_ambiguous_remote`: set `true` to also get "Remote" roles with no country listed (noisier).

## Known limits
- Alerts arrive within about 30 minutes of a job appearing in a source, plus possible GitHub scheduling delay.
- LinkedIn, Naukri and Indeed have no free official API. Use their own email alerts for those.
- Scoring is keyword-based, not AI.
- GitHub may pause scheduled workflows in a repo with no activity for a long period. The bot's state commits usually keep it active, but check the Actions tab now and then.
