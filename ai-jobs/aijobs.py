#!/usr/bin/env python3
"""AI Jobs agent: find AI jobs, score them for you, and draft pitches.

    python3 aijobs.py run         # fetch + score + draft + dashboard (the daily job)
    python3 aijobs.py fetch       # pull jobs from the job boards into data/raw_jobs.json
    python3 aijobs.py score       # rank them into data/jobs.csv
    python3 aijobs.py draft       # write a pitch for each top job into data/drafts/
    python3 aijobs.py dashboard   # rebuild dashboard.html
    python3 aijobs.py skills      # which skills the market wants most (study list)
    python3 aijobs.py mark <id> applied|interview|won|skip|paid

Only uses public job-board APIs/feeds built for this (Remote OK, Remotive,
We Work Remotely, Himalayas). Nothing is ever submitted for you: drafts are
written to files (and to Gmail drafts by the daily scheduled session).
"""
import csv, html, json, os, re, sys, time, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "raw_jobs.json")
JOBS = os.path.join(DATA, "jobs.csv")
STATUS = os.path.join(HERE, "status.json")   # committed, so the daily run remembers what you applied to
DRAFTS = os.path.join(DATA, "drafts")
UA = {"User-Agent": "Mozilla/5.0 (ai-jobs-agent; personal job search)"}

AI_WORDS = ["ai", "a.i.", "artificial intelligence", "llm", "gpt", "chatgpt", "claude", "openai",
            "anthropic", "prompt", "generative", "genai", "machine learning", "chatbot",
            "automation", "agent", "agentic", "data annotation", "ai trainer", "rlhf", "copilot"]
SCAM_WORDS = ["telegram", "whatsapp only", "send a check", "upfront fee", "pay for training",
              "crypto wallet", "easy money", "no experience $", "$5000/week", "work from phone"]
SKILL_TERMS = ["python", "javascript", "typescript", "sql", "react", "node", "zapier", "make.com", "n8n",
               "airtable", "hubspot", "salesforce", "crm", "excel", "google sheets", "notion",
               "prompt engineering", "rag", "langchain", "llamaindex", "vector", "api", "openai api",
               "claude", "chatgpt", "fine-tuning", "evaluation", "agents", "workflow", "no-code",
               "low-code", "customer success", "sales", "project management", "spanish", "bilingual",
               "figma", "adobe", "video", "content", "copywriting", "seo", "data analysis",
               "docker", "aws", "gcp", "azure", "machine learning", "pytorch", "voice ai", "twilio"]

def load_profile():
    with open(os.path.join(HERE, "profile.json")) as f:
        return json.load(f)

def get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

def is_ai(text):
    t = " " + text.lower() + " "
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t) for w in AI_WORDS)

# ---------- fetch ----------
def from_remoteok():
    out = []
    for j in json.loads(get("https://remoteok.com/api"))[1:]:
        out.append(dict(source="RemoteOK", id="rok-%s" % j.get("id"), title=j.get("position", ""),
                        company=j.get("company", ""), location=j.get("location") or "Remote",
                        url=j.get("url") or j.get("apply_url", ""), date=j.get("date", "")[:10],
                        salary_min=j.get("salary_min") or 0, salary_max=j.get("salary_max") or 0,
                        tags=", ".join(j.get("tags") or []), description=strip_html(j.get("description", ""))[:4000]))
    return out

def from_remotive():
    out, seen = [], set()
    for q in ["ai", "automation", "prompt", "llm", "chatbot", "machine learning", "customer success ai"]:
        try:
            data = json.loads(get("https://remotive.com/api/remote-jobs?" + urllib.parse.urlencode({"search": q, "limit": 100})))
        except Exception as e:
            print("  remotive %s: %s" % (q, e)); continue
        for j in data.get("jobs", []):
            if j["id"] in seen: continue
            seen.add(j["id"])
            lo, hi = parse_salary(j.get("salary", ""))
            out.append(dict(source="Remotive", id="rem-%s" % j["id"], title=j.get("title", ""),
                            company=j.get("company_name", ""), location=j.get("candidate_required_location") or "Remote",
                            url=j.get("url", ""), date=(j.get("publication_date") or "")[:10], salary_min=lo, salary_max=hi,
                            tags=", ".join(j.get("tags") or []) + (", " + j.get("job_type", "") if j.get("job_type") else ""),
                            description=strip_html(j.get("description", ""))[:4000]))
        time.sleep(1)
    return out

def from_wwr():
    out = []
    root = ET.fromstring(get("https://weworkremotely.com/remote-jobs.rss"))
    for it in root.iter("item"):
        title = it.findtext("title") or ""
        company, _, role = title.partition(":")
        if not role: company, role = "", title
        link = it.findtext("link") or ""
        pub = it.findtext("pubDate") or ""
        try: date = datetime.strptime(pub[:25].strip(), "%a, %d %b %Y %H:%M:%S").strftime("%Y-%m-%d")
        except Exception: date = ""
        out.append(dict(source="WeWorkRemotely", id="wwr-" + link.rstrip("/").split("/")[-1], title=role.strip(),
                        company=company.strip(), location=it.findtext("region") or "Remote", url=link, date=date,
                        salary_min=0, salary_max=0, tags=it.findtext("category") or "",
                        description=strip_html(it.findtext("description") or "")[:4000]))
    return out

def from_himalayas():
    out, offset = [], 0
    while offset < 400:
        try:
            data = json.loads(get("https://himalayas.app/jobs/api?limit=20&offset=%d" % offset))
        except Exception as e:
            print("  himalayas: %s" % e); break
        jobs = data.get("jobs") or []
        if not jobs: break
        for j in jobs:
            lo, hi = j.get("minSalary") or 0, j.get("maxSalary") or 0
            if (j.get("salaryPeriod") or "").lower().startswith("hour"):
                lo, hi = lo * 2080, hi * 2080
            ts = j.get("pubDate")
            date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if isinstance(ts, (int, float)) else str(ts or "")[:10]
            out.append(dict(source="Himalayas", id="him-" + str(j.get("guid", ""))[-40:], title=j.get("title", ""),
                            company=j.get("companyName", ""), location=", ".join(j.get("locationRestrictions") or []) or "Remote (worldwide)",
                            url=j.get("applicationLink") or j.get("guid", ""), date=date, salary_min=lo, salary_max=hi,
                            tags=", ".join((j.get("categories") or [])[:6]) + ", " + (j.get("employmentType") or ""),
                            description=strip_html(j.get("description") or j.get("excerpt", ""))[:4000]))
        offset += 20
        time.sleep(0.5)
    return out

def parse_salary(s):
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*\.?\d*", s or "")]
    k = "k" in (s or "").lower()
    nums = [n * 1000 if k and n < 1000 else n for n in nums]
    nums = [n * 2080 if n < 300 else n for n in nums if n > 5]
    if not nums: return 0, 0
    return min(nums), max(nums)

def cmd_fetch():
    os.makedirs(DATA, exist_ok=True)
    allj = []
    for name, fn in [("Remote OK", from_remoteok), ("Remotive", from_remotive),
                     ("We Work Remotely", from_wwr), ("Himalayas", from_himalayas)]:
        try:
            jobs = fn(); print("  %-17s %4d jobs" % (name, len(jobs))); allj += jobs
        except Exception as e:
            print("  %-17s failed: %s" % (name, e))
    extra = os.path.join(HERE, "extra_jobs.csv")   # paste jobs you found yourself here
    if os.path.exists(extra):
        with open(extra) as f:
            for i, r in enumerate(csv.DictReader(f)):
                r.setdefault("id", "manual-%d" % i); r["source"] = r.get("source") or "Manual"; allj.append(r)
    seen, uniq = set(), []
    for j in allj:
        key = (j["title"].lower().strip(), j["company"].lower().strip())
        if key in seen: continue
        seen.add(key); uniq.append(j)
    ai = [j for j in uniq if is_ai(j["title"] + " " + j.get("tags", "") + " " + j["description"][:1500])]
    with open(RAW, "w") as f: json.dump(ai, f)
    print("Fetched %d jobs, %d are AI-related -> %s" % (len(uniq), len(ai), os.path.relpath(RAW, HERE)))

# ---------- score ----------
def score_job(j, p):
    title, desc = j["title"].lower(), (j.get("tags", "") + " " + j["description"]).lower()
    text = title + " " + desc
    why = []
    if any(w in text for w in SCAM_WORDS): return 0, "possible scam"
    if any(a in title for a in p["avoid_titles"]): return 5, "needs deep engineering / credentials"
    # fit 40
    fit = 0
    hits = [t for t in p["target_titles"] if t in title]
    if hits: fit += 22; why.append("title fits (%s)" % hits[0])
    skills = p["skills"]["strong"] + p["skills"]["ai_built"]
    have = sorted({s for s in skills if re.search(r"(?<![a-z])" + re.escape(s) + r"(?![a-z])", text)})
    fit += min(18, 3 * len(have))
    if have: why.append("you have: " + ", ".join(have[:4]))
    if re.search(r"senior|sr\.|lead |staff|head of|director", title): fit -= 10; why.append("senior level")
    if re.search(r"\b(5|6|7|8|10)\+? years", desc): fit -= 6
    # pay 25
    hi = float(j.get("salary_max") or 0); lo = float(j.get("salary_min") or 0)
    top = hi or lo
    if top:
        if top >= p["min_salary_usd"] * 1.5: pay = 25
        elif top >= p["min_salary_usd"]: pay = 18
        else: pay = 6
        why.append("$%dk" % round(top / 1000))
    else:
        pay = 12
    # speed 20 (contract/freelance/part-time pays sooner)
    speed = 8
    if re.search(r"contract|freelance|part[- ]time|gig|hourly|project", text): speed = 20; why.append("contract/fast start")
    elif re.search(r"entry|junior|associate|no experience", text): speed = 15
    # location 15
    loc = (j.get("location") or "").lower()
    if not loc or "worldwide" in loc or "anywhere" in loc or "remote" == loc.strip(): where = 15
    elif re.search(r"usa|united states|us only|north america|americas|\bus\b", loc): where = 15
    elif any(c in loc for c in p["local_cities"]): where = 15; why.append("local")
    elif re.search(r"europe|uk|emea|india|apac|germany|latam|canada only", loc): where = 2; why.append("region: " + loc[:25])
    else: where = 8
    total = max(0, min(100, fit + pay + speed + where))
    return round(total), "; ".join(why)

def read_status():
    if os.path.exists(STATUS):
        with open(STATUS) as f: return json.load(f)
    return {}

def cmd_score():
    p = load_profile()
    with open(RAW) as f: jobs = json.load(f)
    status = read_status()
    rows = []
    for j in jobs:
        s, why = score_job(j, p)
        rows.append(dict(score=s, status=status.get(j["id"], {}).get("status", "new"), id=j["id"], title=j["title"],
                         company=j["company"], location=j["location"], salary_min=int(float(j.get("salary_min") or 0)),
                         salary_max=int(float(j.get("salary_max") or 0)), date=j.get("date", ""), source=j["source"],
                         why=why, url=j["url"]))
    rows.sort(key=lambda r: (r["score"], r["date"]), reverse=True)
    with open(JOBS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["score"]); w.writeheader(); w.writerows(rows)
    print("Scored %d jobs -> %s" % (len(rows), os.path.relpath(JOBS, HERE)))
    for r in rows[:10]:
        print("  %3d  %-45.45s %-22.22s %s" % (r["score"], r["title"], r["company"], r["why"][:50]))

def read_jobs():
    with open(JOBS) as f: return list(csv.DictReader(f))

# ---------- draft ----------
def draft_text(j, raw, p):
    desc = (raw or {}).get("description", "").lower()
    proof = []
    if re.search(r"automat|workflow|zapier|n8n|make\.com|agent|lead|outreach|crm", desc):
        proof.append("I built an AI deal-finder agent that pulls listings and public records, scores every lead, writes personal outreach emails and tracks it all in a dashboard (%s)." % p["portfolio"][0]["url"])
    if re.search(r"customer|client|support|success|account", desc):
        proof.append("I've run customer-facing operations for years: 2 locations, 50 staff, and a loyalty program that lifted repeat business 25%.")
    if re.search(r"operat|process|manag|team|train", desc):
        proof.append("As a regional manager I grew sales 15% and cut stock shortages 20% by putting new systems in place, so I know how to get a team to actually use a new tool.")
    if re.search(r"spanish|bilingual|latam", desc):
        proof.append("I'm fluent in English and Spanish.")
    if re.search(r"design|creative|content|video|adobe|figma|brand", desc):
        proof.append("I have a degree in Industrial Design and work daily in Adobe Creative Suite.")
    if not proof:
        proof.append("I build practical AI tools with Claude and Python, like my deal-finder agent (%s), and I bring years of operations and team leadership." % p["portfolio"][0]["url"])
    body = ("Hi %s team,\n\n"
            "I'm applying for the %s role. I'm an operations leader who builds AI automations that save teams real hours.\n\n"
            "%s\n\n"
            "I'd love to show you how I'd approach this role in a short call. My portfolio: %s\n\n"
            "Thanks,\nJohn Ruiz\n%s") % (j["company"] or "hiring", j["title"], "\n".join("- " + x for x in proof[:3]),
                                         p["portfolio"][0]["url"], p["location"])
    return body

def cmd_draft(n=10):
    p = load_profile()
    with open(RAW) as f: raw = {j["id"]: j for j in json.load(f)}
    os.makedirs(DRAFTS, exist_ok=True)
    top = [j for j in read_jobs() if j["status"] == "new" and int(j["score"]) >= 55][:n]
    for j in top:
        path = os.path.join(DRAFTS, re.sub(r"[^a-zA-Z0-9_-]", "_", j["id"]) + ".txt")
        with open(path, "w") as f:
            f.write("JOB: %s at %s\nAPPLY: %s\nSCORE: %s (%s)\n\n---\n%s\n" % (j["title"], j["company"], j["url"], j["score"], j["why"], draft_text(j, raw.get(j["id"]), p)))
    print("Wrote %d drafts -> %s" % (len(top), os.path.relpath(DRAFTS, HERE)))

# ---------- skills (study coach) ----------
def cmd_skills(quiet=False):
    with open(RAW) as f: jobs = json.load(f)
    p = load_profile()
    have = set(p["skills"]["strong"] + p["skills"]["ai_built"])
    counts = {}
    for j in jobs:
        t = (j["title"] + " " + j.get("tags", "") + " " + j["description"]).lower()
        for s in SKILL_TERMS:
            if re.search(r"(?<![a-z])" + re.escape(s) + r"(?![a-z])", t): counts[s] = counts.get(s, 0) + 1
    ranked = sorted(counts.items(), key=lambda x: -x[1])
    out = [dict(skill=s, jobs=c, pct=round(100 * c / max(1, len(jobs))), have=(s in have)) for s, c in ranked]
    with open(os.path.join(DATA, "skills.json"), "w") as f: json.dump(out, f, indent=1)
    if not quiet:
        print("Skills in %d AI jobs (top 20):" % len(jobs))
        for r in out[:20]: print("  %-20s %3d%%  %s" % (r["skill"], r["pct"], "have" if r["have"] else "LEARN"))
    return out

# ---------- dashboard ----------
def cmd_dashboard():
    jobs = read_jobs()
    skills = cmd_skills(quiet=True)
    tpl = open(os.path.join(HERE, "dashboard_template.html")).read()
    payload = json.dumps({"updated": datetime.now().strftime("%Y-%m-%d %H:%M"), "jobs": jobs[:300], "skills": skills[:25]})
    with open(os.path.join(HERE, "dashboard.html"), "w") as f:
        f.write(tpl.replace("/*DATA*/null", payload.replace("</", "<\\/")))
    print("Dashboard -> dashboard.html")

def cmd_mark(job_id, status):
    st = read_status(); st[job_id] = {"status": status, "at": datetime.now().isoformat(timespec="minutes")}
    os.makedirs(DATA, exist_ok=True)
    with open(STATUS, "w") as f: json.dump(st, f, indent=1)
    print("%s -> %s" % (job_id, status))

def main():
    a = sys.argv[1:] or ["run"]
    if a[0] == "fetch": cmd_fetch()
    elif a[0] == "score": cmd_score()
    elif a[0] == "draft": cmd_draft()
    elif a[0] == "skills": cmd_skills()
    elif a[0] == "dashboard": cmd_dashboard()
    elif a[0] == "mark" and len(a) == 3: cmd_mark(a[1], a[2])
    elif a[0] == "run":
        cmd_fetch(); cmd_score(); cmd_draft(); cmd_dashboard()
    else: print(__doc__)

if __name__ == "__main__":
    main()
