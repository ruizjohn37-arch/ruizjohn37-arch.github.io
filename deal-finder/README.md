# Deal Finder

A small command-line assistant for wholesaling. It finds houses that look cheap or motivated,
ranks them, lets you approve the ones worth pursuing, and then sends short, personal-sounding
outreach emails.

```
fetch / import  ->  score  ->  (you review deals.csv)  ->  draft  ->  send
```

## Why it doesn't scrape Zillow directly
Zillow's terms of use prohibit scraping, and it blocks bots quickly, so a scraper breaks
constantly and can get your IP or account banned. Zillow also never shows owner emails.
This tool gets the same MLS listings from **RentCast**, an API built for this kind of use,
and it can also load any CSV you have (PropStream, a county list, a skip-traced list, or a
sheet you typed up while browsing Zillow).

## Setup (one time)
1. Install Python 3.9+. Nothing else is needed.
2. `cp config.example.json config.json` and fill in your name, company, phone, and **mailing
   address**. The address goes in the email footer because the CAN-SPAM Act requires it.
3. Listings: sign up at https://www.rentcast.io/api (the free tier gives 50 requests a month)
   and paste the key into `config.json`.
4. Email: in your Google account, turn on 2-Step Verification, create an **App Password**
   (Security > App passwords), and paste it into `smtp_app_password`.

`config.json` and `data/` are git-ignored, so your keys and leads stay off GitHub. This repo is
public, so keep it that way.

## Daily use
```bash
# 1. Get listings (pick one)
python3 dealfinder.py fetch --city "San Antonio" --state TX --max-price 250000
python3 dealfinder.py fetch --zip 78210
python3 dealfinder.py import my_propstream_export.csv --role owner

# 2. Rank them
python3 dealfinder.py score --top 50

# 3. Open data/deals.csv in Excel or Google Sheets.
#    Set status to "approved" on the ones you want to email, or "skip".

# 4. Write the emails (printed on screen and saved in data/outbox/, edit them freely)
python3 dealfinder.py draft

# 5. Check what would go out, then send
python3 dealfinder.py send
python3 dealfinder.py send --confirm
```

Try it with no setup first:
`cp config.example.json config.json && python3 dealfinder.py import sample_data/sample_listings.csv && python3 dealfinder.py score`

## How "cheap" is scored
| Signal | Why it matters |
|---|---|
| $/sqft below the ZIP median | Priced under its neighbors (weighted most) |
| Price cut from the original list price | Seller already giving ground |
| Days on market | Stale listings are more negotiable |
| Words like *as-is, cash only, estate sale, investor special* | Motivated-seller language (edit the list in `config.json`) |

This is a first filter, not an appraisal. Check comps and condition before you put anything
under contract.

## Emails
* Each email picks at random from several subjects, openers, bodies, and sign-offs, so they read
  like you wrote them one by one. Edit the lists near the top of the email section in
  `dealfinder.py` so they sound like you.
* On-market listings go to the **listing agent**. Imported rows marked `--role owner` get the
  homeowner version.
* Every email says plainly that you're a wholesaler, and includes your mailing address and an
  easy "reply remove" opt-out.
* Safety rails: nothing sends without `--confirm`, at most 20 per run, 1 to 3 minutes apart,
  nobody gets the same property twice (`data/sent_log.csv`), and anyone in
  `data/do_not_contact.txt` is skipped. Add people there when they ask to be removed.

## Know your state's rules
Several states (for example Illinois, Oklahoma, Pennsylvania, Ohio, and South Carolina) now
require a license, or specific disclosures, to market or assign contracts as a wholesaler.
Check yours before you start sending.
