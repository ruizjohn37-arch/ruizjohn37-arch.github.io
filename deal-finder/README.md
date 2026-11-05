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

## See a full deal first (practice run)
```bash
python3 dealfinder.py demo --demo        # runs a whole deal on made-up data, sends nothing
python3 dealfinder.py dashboard --demo   # look at the result in the dashboard
```
It runs one deal from start to finish in a separate `data_demo/` folder, so your real data is never touched:
1. Find and rank listed houses.
2. Find off-market owners and cash buyers in property records, and print letters.
3. Build a buyers list.
4. Email the best listings, then follow up after 5 days.
5. Run the offer numbers when the agent replies.
6. Get the house under contract and email the matching buyers.
7. Close and collect the assignment fee.

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

## Offers to listing agents (high-volume offer method)

This is the "send lots of offers on listed houses" method. You make a fair cash offer to the
listing agent on many houses at once, and resell (assign) the few that say yes to a cash buyer.

1. **Get listings.** Open `listings_template.csv` in Excel or Google Sheets and paste in houses from
   Zillow, Redfin or Realtor.com (address, price, agent name and email; the agent's email is usually
   on their brokerage page). Older listings with price cuts and "as-is" or "investor" in the remarks
   are best. Upload it with **...or upload a list** (choose "Contacts are listing agents").
2. **Press "Send offers to agents".** It writes an offer email for your 25 best-ranked houses. The
   price comes from, in order:
   - the offer you typed on the house,
   - your ARV and repairs (70% rule minus your fee),
   - the highest max price of a cash buyer whose ZIPs fit the house, minus your fee,
   - otherwise 75% of asking (`offer_percent_of_list` in config.json).

   Never more than 97% of asking, rounded down to $1,000. Each house shows its suggested offer and why.
3. **Read every email, then Send.** Each one says you may assign the contract, which keeps you honest
   with the agent and seller (and in NJ you should). Follow-ups go out after 5 days with no reply.
4. **A "yes" or a counter:** move it to Negotiating, sign the agent's offer form with an assignment
   clause ("John Ruiz and/or assigns") and an inspection period, then email your buyers.

Expect most agents to say no or not reply; that's normal for this method. Only offer a price you
could actually get a buyer to pay, and put down only an earnest money deposit you can afford to lose
if no buyer closes. Change close days, inspection days and deposit in config.json.

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

## Off-market leads from NJ property records (free)
Wholesalers make most of their money on houses that **aren't listed**. New Jersey's property records
are public, so the tool can find the owners most likely to sell and the investors who buy for cash,
the same thing paid tools like PropStream charge for.

### What it finds
**Sellers**, scored and ranked:
* **Absentee owners**: the tax bill goes to a different address (landlords, inherited houses).
* **Out-of-state owners.**
* **Estates**: owner names like "ESTATE OF…" or "C/O" (inherited houses).
* **Long-time owners**: 15 or 30+ years, so lots of equity.
* **$0-$100 transfers**: family or inherited.
* **Anything you tag** from another list: sheriff sale (pre-foreclosure), tax lien, code violation, vacant.

Banks, towns, housing authorities and commercial properties are left out.

**Cash buyers**: companies (LLC, Inc, Holdings…), or, when the records leave out owner names (the
state's public parcel service does), the same mailing address that bought 2+ houses in the last 3 years, and anyone
who flipped a house (bought and resold within 12 months). These are your best buyers.

### Easiest: pull a whole county automatically
```bash
python3 dealfinder.py records pull --county UNION                      # every home in Union County (~130,000)
python3 dealfinder.py records pull --county UNION --towns Elizabeth,Linden,Plainfield
```
This downloads the county's tax list straight from New Jersey's public parcel service (the
"Parcels and MOD-IV Composite" on services2.arcgis.com) and loads it. It takes a few minutes for a
whole county. Run it again every month or two to pick up new sales.

### Or download the files yourself (about 2 minutes per county)
1. **Tax list (owners and mailing addresses):** search for **"Parcels and MOD-IV Composite of NJ"**
   on the NJ Geographic Information Network (NJGIN) open data site. Download your county
   (Union, Essex…) as **CSV / spreadsheet**. Your county tax board's website may also offer the tax list.
2. **Sheriff sales (pre-foreclosure):** search for "Union County sheriff sales" or "Essex County sheriff sales".
   Copy the upcoming sales list into a spreadsheet with an **Address** column and save it as CSV.
3. **Sales (to find more cash buyers and flips):** the tax list already includes each property's last sale.
   A sales export with more history (for example, from a county or NJ Division of Taxation sales search)
   makes buyer and flip detection better.

### Using it
In the dashboard, open **Off-market leads**:
1. **Load records** → pick the county CSV.
2. **Tag properties** → pick the sheriff-sale CSV and choose *pre-foreclosure*.
3. Review the ranked leads. Click **Add to pipeline** on the ones you want, then track them like any other deal.
4. Click **Print top 50 seller letters**. Records give mailing addresses, not emails, so off-market
   owners get a letter: one per page, already addressed, with an honest wholesaler disclosure.
   It also saves `data/mailing_list.csv` for envelopes, labels or a mail service.
5. Under **Cash buyers found in records**, click **Add to buyers**, then **Print buyer letters**
   to invite them onto your buyers list.

From the terminal:
```bash
python3 dealfinder.py records import union_modiv.csv
python3 dealfinder.py records flag union_sheriff_sales.csv --tag pre-foreclosure
python3 dealfinder.py records leads --top 200 --town Elizabeth
python3 dealfinder.py records buyers
python3 dealfinder.py records letters --top 50              # seller letters
python3 dealfinder.py records letters --kind buyer --top 50 # buyer letters
```
Try it first with the made-up samples: `sample_data/sample_tax_list.csv` and `sample_data/sample_sheriff_sales.csv`.

Mail tips: send to the same lead every 3-4 weeks. Most deals come from the 3rd to 5th letter.
Handwritten envelopes get opened more often. Honor every "don't contact me": add their address to your notes and stop.


## The factory view
The top of the dashboard shows your business as a production line:

**Records → Off-market leads → New → Approved → Contacted → Replied → Negotiating → Under contract → Cash buyers → Closed**

Each station shows how many deals are in it, with a status light:
* **green**: deals are moving
* **amber**: it needs you (approve, answer replies, follow-ups due, or a contract with no matching buyer)

Click a station to jump to its deals. Below it, the **Deal board** lets you drag a card to the
next stage, and the **Factory log** shows everything that happened, newest first.

## Open it on your phone

The app runs on your computer; your phone opens it over your home Wi-Fi.

1. On the computer, start it with `python3 dealfinder.py dashboard --phone`
   (Windows: `py dealfinder.py dashboard --phone`).
2. The black window prints an address like `http://192.168.1.23:8765/` and a 6-digit PIN.
3. On your phone (same Wi-Fi), type that address into Safari or Chrome and enter the PIN.
   Tip: use "Add to Home Screen" so it opens like an app.

The computer must stay on with the window open. You get a new PIN every start. Only use this on
your own home network, not coffee-shop or hotel Wi-Fi. If the phone can't connect, allow Python
through the computer's firewall when it asks.

## Texting (Google Voice or your phone)
Every agent, owner and buyer with a phone number gets a **Text** button. Clicking it opens your
texting app (your phone, or Google Voice if that's your default) with a short message already
written, and **you** press send. The tool doesn't send texts by itself, on purpose:
* Google Voice doesn't allow automated or bulk texting, and accounts that do it get suspended.
* Under federal law (the TCPA and the Do Not Call rules), texting homeowners' cell phones for
  marketing without their permission can cost $500-$1,500 per text. Many wholesalers get sued over this.
* **Safe:** texting listing agents at their business numbers, buyers who gave you their number, and
  anyone who replied to you.
* **Before texting a homeowner** from a skip-traced list, check the number against the
  National Do Not Call Registry (telemarketing.donotcall.gov). Never text anyone who said stop.
* If you outgrow texting by hand, use a provider built for business texting (one that registers
  your number for business messaging, called "10DLC") and only text people who opted in.
