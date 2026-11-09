# AI Jobs Agent

Finds AI jobs every day, ranks them for John, writes a pitch for the best ones, and tells you
which skills the market wants most. See [PLAN.md](PLAN.md) for the full plan.

```
fetch (4 job boards) -> keep AI jobs -> score 0-100 -> draft pitches -> dashboard + daily email
```

## Run it
```bash
python3 aijobs.py run          # everything: fetch, score, draft, dashboard
python3 aijobs.py skills       # what to study next
python3 aijobs.py mark rok-12345 applied   # track a job: applied|interview|won|paid|skip
```
No installs needed (plain Python 3). Open `dashboard.html` to see the board.

## Where jobs come from
Public APIs/feeds built for this: Remote OK, Remotive, We Work Remotely, Himalayas.
Paste jobs you find yourself (LinkedIn, Indeed, Upwork) into `extra_jobs.csv` with columns
`title,company,location,url,description` and they get scored too.

## How a job is scored
| Part | Points | What counts |
|---|---|---|
| Fit | 40 | Title matches a target role; skills in the post that are in `profile.json` |
| Pay | 25 | Above your minimum ($65k/yr or $35/hr) |
| Speed | 20 | Contract / freelance / part-time starts paying sooner |
| Location | 15 | Remote US/worldwide or NJ/NY |

Senior roles, deep-engineering roles and scam signs score low.

## Daily run (10am Eastern)
A scheduled Claude session runs `aijobs.py run` every morning. Then it:
1. Writes a tailored pitch for each of the top new jobs and saves it as a **Gmail draft**
2. Emails John a summary: top jobs, drafts ready, and today's 30-minute lesson
3. Saves `status.json` so the same job is never pitched twice

Nothing is submitted for you. Job sites ban bots that apply automatically, so you open the
link, paste the draft, and hit apply.

## Files
- `profile.json`: your skills, targets, minimum pay (edit to change what it looks for)
- `status.json`: what you've applied to / won
- `dashboard.html`: the board (built from `dashboard_template.html`)
- `data/`: raw jobs, scores and drafts (rebuilt every run, not committed)
