# AI Jobs Agent — Full Plan

An agent that works for you every day: it **finds** AI jobs and gigs all over the market,
**studies** what those jobs ask for, **teaches** you the skills you're missing, and **does the
work** on the jobs you win. It acts on your behalf, but you stay the one who says "yes, send it".

```
find  ->  score  ->  (you approve)  ->  apply / pitch  ->  win  ->  agent drafts the work  ->  you check & deliver  ->  get paid
                \->  study what jobs ask for  ->  daily lessons  ->  portfolio projects  ->  better scores next time
```

---

## 1. The one rule: it acts *for* you, not *as* a hidden bot

| What the agent does on its own | What waits for your OK |
|---|---|
| Searches job boards, feeds and your email alerts | Sending any application or proposal |
| Scores and ranks jobs | Accepting a contract or a price |
| Drafts résumés, cover letters, proposals | Messaging a client for the first time |
| Drafts the actual work (code, prompts, data labels, writing) | Delivering the work |
| Builds your study plan and quizzes you | Anything that costs money |

Why: Upwork, Fiverr, LinkedIn and most job boards ban automated bidding and bot accounts. If a
bot submits for you, the account gets banned and you lose your reviews. Clients also pay *you*,
so the final work has to be checked by you. One tap of approval keeps you safe and still saves
~90% of the time.

---

## 2. What counts as an "AI job" (the targets)

Ranked from fastest to start → highest pay:

1. **AI training / data work** (start this week, no degree): rating and writing AI answers,
   data labeling, prompt writing. Sites: Outlier, DataAnnotation, Alignerr, Remotasks/Scale,
   Appen, TELUS AI. Usually $15–$40/hr.
2. **AI freelance gigs**: build chatbots, set up ChatGPT/Claude automations for small
   businesses, write prompts, make AI content. Sites: Upwork, Fiverr, Contra, Freelancer.
   $25–$100/hr. (Your deal-finder project in this repo is already a portfolio piece for this.)
3. **AI automation for local businesses**: go direct — realtors, wholesalers, contractors,
   shops. Lead finding + outreach, like `deal-finder/`. $500–$3,000 per setup + monthly fee.
4. **Full-time / contract roles**: AI Ops, Prompt Engineer, AI Automation Specialist,
   AI Solutions/Support, junior ML. Sites: LinkedIn, Indeed, Wellfound, Remote OK,
   We Work Remotely, YC Work at a Startup, company career pages.

---

## 3. How the agent is built (5 parts)

### Part A — Finder (runs every morning)
- Pulls jobs from **allowed sources only**: RSS/APIs (Remote OK, We Work Remotely, Greenhouse
  and Lever public job APIs, Indeed/LinkedIn **email alerts** read from your Gmail), plus a
  CSV you can paste into.
- Searches keywords: `AI`, `prompt`, `LLM`, `chatbot`, `automation`, `data annotation`,
  `AI trainer`, `GPT`, `Claude`, `n8n`, `Zapier AI`, `RAG`, `machine learning`.
- Removes duplicates and scams (asks for payment, Telegram-only contact, "check" deposits,
  pay that's way too high for the work).

### Part B — Scorer
Each job gets 0–100 based on:
- **Fit** (skills you have vs. skills listed) — 40%
- **Pay** per hour — 25%
- **Speed to money** (gig/trial task vs. long hiring process) — 20%
- **Remote / location / schedule** — 15%

Output: `jobs.csv` + a dashboard with Top 10 today, each with a one-line "why".

### Part C — Applier (drafts, you approve)
- Keeps one master profile (`profile.json`): your skills, projects, links, rates, availability.
- For each approved job it writes a **tailored** résumé, cover letter or proposal — short, plain,
  specific to that job, no copy-paste spam.
- Puts drafts in Gmail as **drafts** (or a ready-to-paste box for Upwork/Fiverr). You read,
  tap send. It logs where you applied and follows up after 5 days.

### Part D — Worker (does the job with you)
When you win a job, the agent:
1. Breaks the task into steps and a timeline.
2. Drafts the work (code, automations, prompts, labeled data, written content).
3. Self-checks against the client's requirements (checklist).
4. You review and deliver. It drafts the client update message.

> For AI-training platforms (Outlier, DataAnnotation, etc.) the work is *your own judgment*
> and those sites forbid using AI to answer tasks — the agent only helps you **study the
> guidelines**, not do the tasks for you. Breaking this gets the account closed and pay withheld.

### Part E — Study coach (learns from the market)
- Every week it counts which skills show up most in the jobs it found
  (e.g. "Python in 62%, n8n in 30%, RAG in 25%").
- Compares that to your profile → your **skill gaps**, ranked by how much money they unlock.
- Builds a daily 30–60 min lesson plan, free resources, and a quiz.
- Every 2 weeks: one small **portfolio project** that matches real job posts, pushed to this
  GitHub site so clients can see it.

---

## 4. Tools it uses (most you already have connected)

| Need | Tool |
|---|---|
| Brain / writing / coding | Claude (this session; Claude API for the script later) |
| Job alerts in, drafts out | Gmail (connected) |
| Interviews, deadlines, study time | Google Calendar (connected) |
| Résumé, portfolio docs | Google Drive (connected) |
| Daily automatic run | Scheduled task in Claude Code (e.g. every day 7:40am) |
| Portfolio website | This repo → `ruizjohn37-arch.github.io` |
| Tracker | `jobs.csv` + `dashboard.html` (same style as deal-finder) |

---

## 5. Build steps (roadmap)

**Week 1 — Get money moving + set up**
- [ ] Fill in `profile.json` (skills, experience, rate, hours/week, location)
- [ ] Sign up for 3 AI-training sites (Outlier, DataAnnotation, Alignerr); pass their tests
- [ ] Set up job-alert emails on LinkedIn, Indeed, Upwork for the keywords above
- [ ] Make/refresh Upwork + Fiverr profiles (agent drafts them)

**Week 2 — Finder + Scorer**
- [ ] `aijobs.py fetch` — pull from feeds + Gmail alerts
- [ ] `aijobs.py score` — rank into `jobs.csv`
- [ ] `dashboard.html` — Top 10 today, applied, interviews, won, paid

**Week 3 — Applier**
- [ ] `aijobs.py draft` — tailored résumé/cover/proposal per approved job
- [ ] Gmail drafts + follow-up reminders
- [ ] Goal: 5–10 quality applications a day

**Week 4 — Study coach + Worker**
- [ ] Weekly skill-gap report + daily lessons on Calendar
- [ ] First portfolio project on the site (e.g. "AI lead-finder for realtors" from deal-finder)
- [ ] Worker checklist template for client jobs

**Then — run it daily**
- [ ] Scheduled task every morning: fetch → score → draft → email you a summary
- [ ] Weekly review: what got replies, adjust scoring and pitch

---

## 6. Daily routine (once it's running, ~30–60 min of your time)

1. **Morning (5 min)** — read the agent's email: Top 10 jobs + drafts ready. Approve / skip.
2. **Send (10 min)** — tap send on approved drafts.
3. **Work (as needed)** — agent drafts client work, you check and deliver.
4. **Study (30 min)** — today's lesson + quiz.
5. **Training tasks** — log onto Outlier/DataAnnotation when tasks are available.

---

## 7. Goals to track

| Metric | Month 1 target | Month 3 target |
|---|---|---|
| Applications / pitches sent | 100 | 300 |
| Reply rate | 5% | 10%+ |
| Active AI-training accounts | 2 | 3 |
| Paid gigs won | 1–3 | 5–10 |
| Portfolio projects live | 1 | 4 |
| Monthly AI income | first $ | $1,500–$4,000 |

---

## 8. What I need from you to start building

1. Your skills and experience (or a current résumé)
2. What you want most: fast gig money, freelance clients, or a full-time job
3. Hourly rate you'd accept, hours per week, and your location / time zone
4. OK to read job-alert emails in your Gmail and save drafts there
5. What time each morning you want the daily summary
