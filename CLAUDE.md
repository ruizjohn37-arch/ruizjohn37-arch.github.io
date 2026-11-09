# CLAUDE.md

Read this first. It replaces exploring the repo. Only open a file when the task touches it.

## Owner
John Ruiz runs two businesses from this site and often asks from his phone, sometimes driving.
- Answer short and in plain language: a few bullets, no long explanations unless asked.
- Do the work, then report. Don't ask questions you can answer with a sensible default.
- Batch work: one change set, one PR, then stop.

## Site map (GitHub Pages, plain HTML, no build step)
| Path | What it is |
|---|---|
| `index.html` | **Hands of Faith Construction Services**, Houston TX. Bilingual Spanish / English, black + orange (`#ff4500`). Phone 346-251-3550, email handsofffaith0629@gmail.com. |
| `sell/index.html` | **"Sell Your House As-Is for Cash"** landing page for John's cash home buying. Light/dark tokens on `:root`, green accent. Form sends by SMS or email (`PHONE` / `EMAIL` vars near the bottom). |
| `videos/` | Short 9:16 MP4 clips for both pages. See below. |
| `deal-finder/` | Python CLI for wholesaling (rank listings, owner records, outreach emails, dashboard). Has its own `README.md`; read that only for deal-finder work. |

## Adding a video (the common task)
1. Put the MP4 in `videos/` (keep it under ~20 MB).
2. Add `{ "file": "name.mp4", "caption": "..." }` to `videos/videos.json` under `construction` (home page) or `sell` (`/sell/`).
3. That's it. `videos/videos.js` renders them; a section stays hidden while its list is empty. Don't edit the HTML.

## Rules
- Keep each page's existing look. Construction page text stays bilingual (Spanish / English).
- Don't invent claims, prices, reviews or guarantees (for example "close in 7 days") on either page.
- Test page changes with Playwright from a local `python3 -m http.server`, then commit, push, open a PR.

## Video marketing plan (in progress)
- Making short promo videos for both businesses. Talking-head clips are filmed on a phone; AI clips come from free tools (Google Flow / Veo) first, Muapi only if needed.
- Scripts were written for 6 videos: 3 construction (before/after, intro, job-site b-roll) and 3 cash buying (pain point, intro, "three situations").
