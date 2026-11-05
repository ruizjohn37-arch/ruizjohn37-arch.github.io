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

## Easiest way: double-click
1. Install Python from https://www.python.org/downloads/ (on Windows, tick **"Add python.exe to PATH"**).
2. Download this folder (on GitHub: **Code > Download ZIP**, then unzip it).
3. Double-click **`start-windows.bat`** (Windows) or **`start-mac.command`** (Mac; the first time,
   right-click > Open). The first run asks your name, company, phone, mailing address, RentCast key and
   Gmail app password. You can leave the keys blank and add them later with `python3 dealfinder.py setup`.
4. Your browser opens the **Deal Tracker** dashboard. Keep that window open while you use it.

### The dashboard
Everything works from the page, with no commands to type:
* **Find houses**: enter a city and state or a ZIP, plus a max price. It pulls listings and ranks them.
  You can also **upload a CSV** instead.
* **Track every property**: change its stage (New > Approved > Emailed > Replied > Negotiating >
  Under contract > Closed, or Skip / Dead), and enter your offer, assignment fee, a follow-up date and notes.
  It saves automatically to `data/deals.csv`.
* **Write emails** for the houses you approved, then **edit or remove each one** before you press **Send**.
  Sent houses move to *Emailed* on their own.
* The top row shows emails sent, reply rate, active deals, follow-ups due, fees earned and fees in
  the pipeline. Follow-ups that are due today or overdue are listed on the right.

* **Offer calculator**: enter a house's after-repair value (ARV) and repair cost. It shows the most a
  cash buyer will pay (70% of ARV minus repairs) and the most you can offer the seller while keeping your fee.
  It flags in red when the asking price is too high.
* **Follow-up emails**: deals still at *Emailed* with no reply after 5 days get a short, polite follow-up.
  There are at most 2 follow-ups, and you review them before they send.
* **Cash buyers**: keep your buyers list with the ZIPs they buy in and their max price. Once a house is
  **Under contract**, its card shows how many buyers match and an **Email matching buyers** button. Those
  emails say plainly that you're assigning a contract, not listing the house. The button only works on
  houses you actually have under contract, because marketing a house you don't control is where
  wholesalers get in trouble with licensing laws.

The dashboard only runs on your computer (`127.0.0.1`), so your leads aren't on the internet.

## Manual setup (one time)
1. Install Python 3.9+. Nothing else is needed.
2. Run `python3 dealfinder.py setup` (or copy `config.example.json` to `config.json`) and fill in your name, company, phone, and **mailing
   address**. The address goes in the email footer because the CAN-SPAM Act requires it.
3. Listings: sign up at https://www.rentcast.io/api (the free tier gives 50 requests a month)
   and paste the key into `config.json`.
4. Email: in your Google account, turn on 2-Step Verification, create an **App Password**
   (Security > App passwords), and paste it into `smtp_app_password`.

`config.json` and `data/` are git-ignored, so your keys and leads stay off GitHub. This repo is
public, so keep it that way.

## Daily use from the terminal (optional)
`python3 dealfinder.py dashboard` opens the tracker. The commands below do the same steps by hand.
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

# 6. A few days later: polite follow-ups to anyone who hasn't replied
python3 dealfinder.py followup

# 7. Got a house under contract? Email the cash buyers who match it
python3 dealfinder.py blast "905 Cedar Ln, San Antonio, TX 78210"
```

Settings you can change in `config.json`: `followup_after_days` (5), `max_followups` (2),
`mao_percent` (0.70) and `target_fee` (10000).

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

## Building your cash-buyer list
In the dashboard, click **Load starter list** under *Cash buyers*. It adds NJ cash-buyer companies
found in public directories (`starter_buyers.csv`). The numbers come from third-party directories,
so confirm each one when you call.

**Phone script (2 minutes):**
> "Hi, this is John. I'm a wholesaler in Union and Essex County, building my buyers list so I can
> send you off-market deals before anyone else. Do you buy in this area? What would you want to see?"

Ask every buyer, then fill in their row in the dashboard:
1. Which towns or ZIP codes do you buy in?
2. What's your max price, and what kind of house (single family, 2-4 family, rentals or flips)?
3. How much rehab is OK: cosmetic only, or full gut?
4. Do you buy contract assignments, and how fast can you close?
5. Can you show proof of funds (a bank letter or statement)?
6. What's the best email for deals?

A buyer without an email can't get deal emails, so always get one.

**More places to find buyers:**
* **Investor meetups:** a Union County investor group meets on the 2nd Wednesday of each month at
  The Westwood, 438 North Ave, Garwood (confirm the date on Meetup). The New Jersey REIA (free with RSVP
  on Meetup) and Metro REIA also hold meetings.
* **County records:** landlords and flippers who paid cash recently are the best buyers. In Union
  County and Essex County deed records, look for recent sales to LLCs, and for houses that sold twice in
  under a year (flips). Look up the owner's mailing address and send them a short letter.
* **Signs and ads:** call the "We Buy Houses" signs and Facebook Marketplace investors around Elizabeth and Newark.
