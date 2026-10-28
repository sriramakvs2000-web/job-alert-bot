#!/usr/bin/env python3
"""Job alert bot.

Polls free job sources, keeps only India roles that match your target titles,
and posts NEW matches to Slack. Uses only the Python standard library.

Usage:
    python job_alerts.py            # normal run (used by GitHub Actions)
    python job_alerts.py --test     # sends a test message to Slack and exits
    DRY_RUN=1 python job_alerts.py  # prints alerts instead of sending to Slack
"""
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import parse, request

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
SEEN_FILE = ROOT / "seen_jobs.json"
UA = "job-alert-bot/1.0"
NOW = datetime.now(timezone.utc)
DRY_RUN = os.getenv("DRY_RUN") == "1"
WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")


@dataclass
class Job:
    id: str
    title: str
    company: str
    location: str
    url: str
    posted: datetime = None
    mode: str = ""
    jtype: str = ""
    text: str = ""


# ---------- helpers ----------
TAG_RE = re.compile(r"<[^>]+>")


def clean(s):
    s = html.unescape(s or "")
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_iso(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def get_json(url, timeout=30):
    req = request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # a bad source must never crash the whole run
        print(f"[warn] {url.split('?')[0]} -> {e}")
        return None


# ---------- sources ----------
def fetch_greenhouse(token):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    out = []
    for j in (data or {}).get("jobs", []):
        out.append(Job(
            id=f"gh:{token}:{j.get('id')}",
            title=j.get("title", ""),
            company=j.get("company_name") or token,
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""),
            posted=parse_iso(j.get("first_published") or j.get("updated_at")),
            text=clean(j.get("content", "")),
        ))
    return out


def fetch_lever(slug):
    data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in data if isinstance(data, list) else []:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location", "")]
        created = j.get("createdAt")
        out.append(Job(
            id=f"lv:{slug}:{j.get('id')}",
            title=j.get("text", ""),
            company=slug,
            location=" / ".join(l for l in locs if l),
            url=j.get("hostedUrl", ""),
            posted=datetime.fromtimestamp(created / 1000, tz=timezone.utc) if created else None,
            mode=j.get("workplaceType", "") or "",
            jtype=cats.get("commitment", "") or "",
            text=j.get("descriptionPlain", "") or clean(j.get("description", "")),
        ))
    return out


def fetch_ashby(name):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{name}")
    out = []
    for j in (data or {}).get("jobs", []):
        locs = [j.get("location", "")] + [
            (s.get("location") if isinstance(s, dict) else str(s))
            for s in (j.get("secondaryLocations") or [])
        ]
        mode = "Remote" if j.get("isRemote") else (j.get("workplaceType") or "")
        out.append(Job(
            id=f"ab:{name}:{j.get('id')}",
            title=j.get("title", ""),
            company=name,
            location=" / ".join(l for l in locs if l),
            url=j.get("jobUrl", ""),
            posted=parse_iso(j.get("publishedAt")),
            mode=mode,
            jtype=j.get("employmentType", "") or "",
            text=j.get("descriptionPlain") or clean(j.get("descriptionHtml", "")),
        ))
    return out


def fetch_adzuna():
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        print("[info] Adzuna keys not set; skipping Adzuna")
        return []
    # Scheduled runs hit Adzuna once an hour (first run of each hour) to save API quota.
    if os.getenv("GITHUB_EVENT_NAME") == "schedule" and NOW.minute >= 30:
        return []
    out = []
    for q in CFG.get("adzuna_queries", []):
        params = parse.urlencode({
            "app_id": app_id, "app_key": app_key, "results_per_page": 50,
            "sort_by": "date", "max_days_old": 2, "what": q,
        })
        data = get_json(f"https://api.adzuna.com/v1/api/jobs/in/search/1?{params}")
        for j in (data or {}).get("results", []):
            ctime = (j.get("contract_time") or "").replace("_", "")
            ctype = j.get("contract_type") or ""
            out.append(Job(
                id=f"az:{j.get('id')}",
                title=clean(j.get("title", "")),
                company=(j.get("company") or {}).get("display_name", "Unknown"),
                location=(j.get("location") or {}).get("display_name", ""),
                url=j.get("redirect_url", ""),
                posted=parse_iso(j.get("created")),
                jtype=f"{ctime} {ctype}".strip(),
                text=clean(j.get("description", "")),
            ))
        time.sleep(1)
    return out


def collect_jobs():
    jobs = []
    comp = CFG.get("companies", {})
    for t in comp.get("greenhouse", []):
        jobs += fetch_greenhouse(t)
    for t in comp.get("lever", []):
        jobs += fetch_lever(t)
    for t in comp.get("ashby", []):
        jobs += fetch_ashby(t)
    jobs += fetch_adzuna()
    print(f"[info] fetched {len(jobs)} jobs in total")
    return jobs


# ---------- filters ----------
INDIA_RE = re.compile(r"\b(?:" + "|".join(re.escape(c) for c in CFG["india_locations"]) + r")\b", re.I)
RANGE_RE = re.compile(r"(\d{1,2})\s*(?:-|\u2013|to)\s*\d{1,2}\s*\+?\s*years?", re.I)
YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*years?", re.I)


def india_ok(job):
    if INDIA_RE.search(job.location):
        return True
    if "remote" in (job.location + " " + job.mode).lower():
        # "Remote" with no country: accept only if the posting itself says India
        if CFG.get("include_ambiguous_remote"):
            return True
        return bool(re.search(r"\bindia\b", job.text[:1500], re.I))
    return False


def type_ok(jtype):
    t = jtype.lower().replace(" ", "").replace("_", "").replace("-", "")
    return not any(bad in t for bad in ("intern", "parttime"))


def years_too_high(text):
    limit = CFG.get("max_years_required", 99)
    flat = RANGE_RE.sub(lambda m: f"{m.group(1)} years", text)
    vals = [int(m.group(1)) for m in YEARS_RE.finditer(flat)]
    return bool(vals) and max(vals) > limit


def passes(job):
    tl = job.title.lower()
    if not any(k in tl for k in CFG["title_keywords"]):
        return False
    if any(re.search(p, tl) for p in CFG["exclude_title_patterns"]):
        return False
    return type_ok(job.jtype) and india_ok(job) and not years_too_high(job.text)


def score(job):
    blob = f"{job.title} {job.text}".lower()
    hits = [s for s in CFG["skills"] if s in blob]
    return min(100, 50 + min(50, len(hits) * 8)), hits


def infer_mode(job):
    blob = f"{job.mode} {job.location} {job.title}".lower()
    if "hybrid" in blob:
        return "Hybrid"
    if "remote" in blob:
        return "Remote"
    if re.search(r"on[\s-]?site", blob):
        return "Onsite"
    return "Not stated"


def dedupe_key(job):
    return "k:" + "|".join(re.sub(r"\W+", "", x.lower()) for x in (job.company, job.title, job.location))


# ---------- state ----------
def load_seen():
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_seen(seen):
    cutoff = NOW - timedelta(days=60)
    pruned = {k: v for k, v in seen.items() if (parse_iso(v) or NOW) > cutoff}
    SEEN_FILE.write_text(json.dumps(pruned, indent=0, sort_keys=True), encoding="utf-8")


def is_seen(seen, job):
    return job.id in seen or dedupe_key(job) in seen


def mark(seen, job):
    stamp = NOW.isoformat()
    seen[job.id] = stamp
    seen[dedupe_key(job)] = stamp


# ---------- slack ----------
def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def ago(d):
    if not d:
        return "post time not provided"
    m = max(0, int((NOW - d).total_seconds() // 60))
    if m < 60:
        return f"{m} min ago"
    h = m // 60
    return f"{h} h ago" if h < 48 else f"{h // 24} d ago"


def post_slack(text):
    if DRY_RUN:
        print("-----\n" + text)
        return True
    req = request.Request(
        WEBHOOK, data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except Exception as e:
        print(f"[warn] Slack post failed: {e}")
        return False


def format_job(job, sc, hits):
    jt = job.jtype.replace("_", " ").strip() or "type not stated"
    skills = ", ".join(hits[:6]) if hits else "title match"
    return (
        f"*{esc(job.title)}* \u2014 {esc(job.company)}\n"
        f"\U0001F4CD {esc(job.location or 'n/a')}  |  {infer_mode(job)}  |  {esc(jt)}\n"
        f"\u2B50 Fit {sc}/100 \u2014 {esc(skills)}\n"
        f"\U0001F552 Posted {ago(job.posted)}\n"
        f"<{job.url}|Open job>"
    )


# ---------- main ----------
def main():
    if not WEBHOOK and not DRY_RUN:
        sys.exit("SLACK_WEBHOOK_URL is not set")
    if "--test" in sys.argv:
        ok = post_slack("\u2705 Job alert bot is connected to this channel.")
        sys.exit(0 if ok else 1)

    seen = load_seen()
    first_run = not SEEN_FILE.exists()
    alert_first = os.getenv("ALERT_ON_FIRST_RUN", "").lower() == "true"

    jobs = collect_jobs()
    matches = [j for j in jobs if passes(j)]
    print(f"[info] {len(matches)} jobs match your filters")

    if first_run and not alert_first:
        for j in matches:
            mark(seen, j)
        save_seen(seen)
        post_slack(
            f"\u2705 Setup complete. Baseline saved: {len(matches)} matching jobs already open "
            f"(out of {len(jobs)} checked). You'll now be alerted only on NEW postings."
        )
        return

    max_age = timedelta(hours=CFG.get("max_age_hours", 24))
    to_alert = []
    for j in matches:
        if is_seen(seen, j):
            continue
        if j.posted and NOW - j.posted > max_age:
            mark(seen, j)  # too old to be useful; don't re-evaluate every run
            continue
        sc, hits = score(j)
        if sc < CFG.get("min_score", 50):
            mark(seen, j)
            continue
        to_alert.append((sc, hits, j))

    to_alert.sort(key=lambda x: x[2].posted or NOW, reverse=True)
    cap = CFG.get("max_alerts_per_run", 15)
    sent = 0
    for sc, hits, j in to_alert[:cap]:
        if post_slack(format_job(j, sc, hits)):
            mark(seen, j)  # only mark after a successful send, so failures retry next run
            sent += 1
            time.sleep(1.2)  # Slack webhook rate limit is ~1 message/second
    if len(to_alert) > cap:
        print(f"[info] {len(to_alert) - cap} more will be sent on the next run")
    save_seen(seen)
    print(f"[info] sent {sent} alerts")


if __name__ == "__main__":
    main()
