#!/usr/bin/env python3
"""
Deal Finder: find underpriced / motivated-seller houses, rank them,
let you review the list, then send honest, personal-sounding outreach emails.

Pipeline:
    fetch   -> pull active for-sale listings from the RentCast API (MLS data)
    import  -> or load a CSV export (PropStream, county list, a sheet you built, etc.)
    score   -> rank every property by how good a deal it looks
    draft   -> write emails for rows you marked "approved" in data/deals.csv
    send    -> send those drafts (dry run unless you pass --confirm)
    followup -> write a polite follow-up for deals with no reply after a few days
    blast   -> once a house is under contract, email the cash buyers who match it
    records -> off-market leads, cash buyers and mail letters from NJ property records
    setup   -> answer a few questions once to create config.json
    demo    -> a full practice deal on sample data:  python3 dealfinder.py demo --demo
    dashboard -> open a page in your browser to track every deal

Standard library only. Python 3.9+.
"""
import argparse
import os
import sys

if "--demo" in sys.argv:  # keep the practice run away from your real data
    sys.argv.remove("--demo")
    os.environ["DEALFINDER_DATA"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_demo")

import records  # noqa: E402  (reads DEALFINDER_DATA)
import csv
import contextlib
import getpass
import http.server
import io
import threading
import json
import os
import random
import smtplib
import statistics
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DEALFINDER_DATA") or os.path.join(HERE, "data")
RAW = os.path.join(DATA, "raw_listings.json")
DEALS = os.path.join(DATA, "deals.csv")
OUTBOX = os.path.join(DATA, "outbox")
SENT_LOG = os.path.join(DATA, "sent_log.csv")
DNC = os.path.join(DATA, "do_not_contact.txt")
BUYERS = os.path.join(DATA, "buyers.csv")
BUYER_COLUMNS = ["name", "email", "phone", "zips", "max_price", "property_types", "notes", "added"]
SENT_COLUMNS = ["sent_at", "email", "address", "subject", "kind"]

DEAL_COLUMNS = [
    "status", "score", "address", "zip", "price", "sqft", "price_per_sqft",
    "area_median_ppsf", "discount_vs_area_pct", "price_cut_pct", "days_on_market",
    "beds", "baths", "property_type", "motivation_flags", "contact_name",
    "contact_email", "contact_phone", "contact_role", "listing_url", "notes",
    "offer", "assignment_fee", "follow_up", "arv", "repairs", "updated",
]
# The order a deal moves through. The dashboard uses these as its tabs.
STAGES = ["pending", "approved", "emailed", "replied", "negotiating",
          "under_contract", "closed", "skip", "dead"]
# Columns you edit by hand (or in the dashboard); re-scoring keeps them.
USER_COLUMNS = ["status", "notes", "offer", "assignment_fee", "follow_up", "arv", "repairs", "updated"]


# ---------------------------------------------------------------- helpers
def is_set(cfg, key):
    """True when a secret in config.json has been filled in (not the example placeholder)."""
    val = cfg.get(key, "")
    return bool(val) and "PASTE" not in val and " " not in val


def load_config():
    path = os.path.join(HERE, "config.json")
    if not os.path.exists(path):
        sys.exit("Not set up yet. Run `python3 dealfinder.py setup` first.")
    with open(path) as f:
        return json.load(f)


def num(v):
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def save_raw(rows):
    os.makedirs(DATA, exist_ok=True)
    existing = load_raw()
    by_addr = {r["address"].lower(): r for r in existing}
    for r in rows:
        by_addr[r["address"].lower()] = r
    with open(RAW, "w") as f:
        json.dump(list(by_addr.values()), f, indent=2)
    print(f"Saved {len(rows)} listings ({len(by_addr)} total) -> {RAW}")


def load_raw():
    if not os.path.exists(RAW):
        return []
    with open(RAW) as f:
        return json.load(f)


def read_deals():
    if not os.path.exists(DEALS):
        sys.exit("No deals list yet. Run `fetch` or `import`, then `score`.")
    with open(DEALS, newline="") as f:
        return list(csv.DictReader(f))


def write_deals(deals):
    os.makedirs(DATA, exist_ok=True)
    tmp = DEALS + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DEAL_COLUMNS, restval="", extrasaction="ignore")
        w.writeheader()
        w.writerows(deals)
    os.replace(tmp, DEALS)


def do_not_contact():
    if not os.path.exists(DNC):
        return set()
    with open(DNC) as f:
        return {line.strip().lower() for line in f if line.strip()}


def read_sent():
    """Every email sent so far. `kind` is intro, followup1, followup2... or buyer."""
    if not os.path.exists(SENT_LOG):
        return []
    with open(SENT_LOG, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["kind"] = r.get("kind") or "intro"
    return rows


def already_sent():
    return {(r["email"].lower(), r["address"].lower(), r["kind"]) for r in read_sent()}


def append_sent(row):
    rows = read_sent()  # also upgrades older logs that had no `kind` column
    rows.append(row)
    os.makedirs(DATA, exist_ok=True)
    with open(SENT_LOG, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SENT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_buyers():
    if not os.path.exists(BUYERS):
        return []
    with open(BUYERS, newline="") as f:
        return list(csv.DictReader(f))


def write_buyers(buyers):
    os.makedirs(DATA, exist_ok=True)
    with open(BUYERS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BUYER_COLUMNS, restval="", extrasaction="ignore")
        w.writeheader()
        w.writerows(buyers)


def contract_price(deal):
    return num(deal.get("offer")) or num(deal.get("price")) or 0


def matching_buyers(deal, buyers):
    """Buyers whose ZIPs and max price fit this deal (blank means 'any')."""
    out = []
    for b in buyers:
        zips = [z.strip() for z in (b.get("zips") or "").replace(";", ",").split(",") if z.strip()]
        types = (b.get("property_types") or "").lower()
        if zips and str(deal.get("zip", ""))[:5] not in zips:
            continue
        if num(b.get("max_price")) and contract_price(deal) + (num(deal.get("assignment_fee")) or 0) > num(b["max_price"]):
            continue
        if types and deal.get("property_type") and deal["property_type"].lower() not in types:
            continue
        if b.get("email"):
            out.append(b)
    return out


def write_draft(kind, to, address, subject, body, suffix=""):
    os.makedirs(OUTBOX, exist_ok=True)
    slug = "".join(c if c.isalnum() else "_" for c in address)[:60]
    name = slug + (f"__{suffix}" if suffix else "") + ".json"
    with open(os.path.join(OUTBOX, name), "w") as f:
        json.dump({"kind": kind, "to": to, "address": address, "subject": subject, "body": body}, f, indent=2)


# ---------------------------------------------------------------- fetch
def cmd_fetch(args):
    """Active sale listings from RentCast (https://www.rentcast.io/api).
    Same MLS listings you see on Zillow, through an API that allows this use."""
    cfg = load_config()
    params = {"status": "Active", "limit": 500}
    if args.zip:
        params["zipCode"] = args.zip
    else:
        params["city"], params["state"] = args.city, args.state
    if args.property_type:
        params["propertyType"] = args.property_type

    rows, offset = [], 0
    while len(rows) < args.max_results:
        params["offset"] = offset
        url = "https://api.rentcast.io/v1/listings/sale?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"X-Api-Key": cfg["rentcast_api_key"],
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            batch = json.load(resp)
        if not batch:
            break
        for item in batch:
            rows.append(normalize_rentcast(item))
        if len(batch) < params["limit"]:
            break
        offset += params["limit"]

    if args.max_price:
        rows = [r for r in rows if r["price"] and r["price"] <= args.max_price]
    save_raw(rows[: args.max_results])


def normalize_rentcast(item):
    agent = item.get("listingAgent") or {}
    history = item.get("history") or {}
    prices = [h.get("price") for h in history.values() if isinstance(h, dict) and h.get("price")]
    return {
        "address": item.get("formattedAddress", ""),
        "zip": item.get("zipCode", ""),
        "price": item.get("price"),
        "original_price": max(prices) if prices else item.get("price"),
        "sqft": item.get("squareFootage"),
        "beds": item.get("bedrooms"),
        "baths": item.get("bathrooms"),
        "days_on_market": item.get("daysOnMarket"),
        "property_type": item.get("propertyType", ""),
        "description": "",  # RentCast does not return remarks; CSV imports can
        "contact_name": agent.get("name", ""),
        "contact_email": agent.get("email", ""),
        "contact_phone": agent.get("phone", ""),
        "contact_role": "listing agent",
        "listing_url": "",
    }


# ---------------------------------------------------------------- import
IMPORT_ALIASES = {
    "address": ["address", "property address", "full address", "street address"],
    "zip": ["zip", "zipcode", "zip code", "postal code"],
    "price": ["price", "list price", "asking price", "listprice"],
    "original_price": ["original price", "original list price", "orig price"],
    "sqft": ["sqft", "square feet", "living area", "building sqft", "squarefootage"],
    "beds": ["beds", "bedrooms", "bd"],
    "baths": ["baths", "bathrooms", "ba"],
    "days_on_market": ["days on market", "dom", "days_on_market", "days on zillow"],
    "property_type": ["property type", "type", "home type"],
    "description": ["description", "remarks", "public remarks", "notes"],
    "contact_name": ["contact name", "owner name", "owner", "agent name", "name"],
    "contact_email": ["email", "contact email", "owner email", "agent email", "email 1"],
    "contact_phone": ["phone", "contact phone", "owner phone", "agent phone", "phone 1"],
    "contact_role": ["contact role", "role"],
    "listing_url": ["url", "listing url", "link"],
}


def cmd_import(args):
    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = {h.lower().strip(): h for h in reader.fieldnames}
        colmap = {}
        for field, aliases in IMPORT_ALIASES.items():
            for a in aliases:
                if a in header:
                    colmap[field] = header[a]
                    break
        if "address" not in colmap or "price" not in colmap:
            sys.exit(f"CSV needs at least an address and price column. Found: {reader.fieldnames}")
        rows = []
        for line in reader:
            r = {k: line.get(v, "") for k, v in colmap.items()}
            for k in ("price", "original_price", "sqft", "beds", "baths", "days_on_market"):
                r[k] = num(r.get(k))
            r.setdefault("contact_role", args.role)
            r["contact_role"] = r["contact_role"] or args.role
            if not r.get("zip"):
                tail = r["address"].replace(",", " ").split()
                r["zip"] = tail[-1] if tail and tail[-1][:5].isdigit() else ""
            rows.append(r)
    print(f"Mapped columns: {colmap}")
    save_raw(rows)


# ---------------------------------------------------------------- score
def cmd_score(args):
    cfg = load_config()
    keywords = [k.lower() for k in cfg.get("motivated_keywords", [])]
    raw = load_raw()
    if not raw:
        sys.exit("Nothing to score. Run `fetch` or `import` first.")

    # median $/sqft per zip (fall back to whole dataset)
    by_zip, all_ppsf = {}, []
    for r in raw:
        p, s = num(r.get("price")), num(r.get("sqft"))
        if p and s:
            by_zip.setdefault(str(r.get("zip", ""))[:5], []).append(p / s)
            all_ppsf.append(p / s)
    overall = statistics.median(all_ppsf) if all_ppsf else None

    # keep the status/notes you already set when re-scoring
    previous = {}
    if os.path.exists(DEALS):
        with open(DEALS, newline="") as f:
            previous = {d["address"].lower(): d for d in csv.DictReader(f)}

    out = []
    for r in raw:
        price, sqft = num(r.get("price")), num(r.get("sqft"))
        if not price:
            continue
        z = str(r.get("zip", ""))[:5]
        zip_vals = by_zip.get(z, [])
        median = statistics.median(zip_vals) if len(zip_vals) >= 5 else overall
        ppsf = price / sqft if sqft else None
        discount = (1 - ppsf / median) * 100 if ppsf and median else 0.0
        orig = num(r.get("original_price")) or price
        cut = (1 - price / orig) * 100 if orig > price else 0.0
        dom = num(r.get("days_on_market")) or 0
        text = (r.get("description") or "").lower()
        flags = [k for k in keywords if k in text]

        score = (max(discount, 0) * 2.0       # cheap vs. neighbors matters most
                 + cut * 1.5                    # seller already dropping the price
                 + min(dom, 180) / 6            # sitting a long time = more flexible
                 + len(flags) * 8)              # motivated-seller wording

        if args.max_price and price > args.max_price:
            continue
        prev = previous.get(r["address"].lower(), {})
        out.append({
            "status": prev.get("status", "pending"),
            "score": round(score, 1),
            "address": r["address"],
            "zip": z,
            "price": int(price),
            "sqft": int(sqft) if sqft else "",
            "price_per_sqft": round(ppsf) if ppsf else "",
            "area_median_ppsf": round(median) if median else "",
            "discount_vs_area_pct": round(discount, 1),
            "price_cut_pct": round(cut, 1),
            "days_on_market": int(dom),
            "beds": r.get("beds") or "",
            "baths": r.get("baths") or "",
            "property_type": r.get("property_type", ""),
            "motivation_flags": "; ".join(flags),
            "contact_name": r.get("contact_name", ""),
            "contact_email": r.get("contact_email", ""),
            "contact_phone": r.get("contact_phone", ""),
            "contact_role": r.get("contact_role", ""),
            "listing_url": r.get("listing_url", ""),
        })
        for col in USER_COLUMNS:
            out[-1][col] = prev.get(col, out[-1].get(col, ""))
        out[-1]["status"] = out[-1]["status"] or "pending"

    out.sort(key=lambda d: d["score"], reverse=True)
    if args.top:
        out = out[: args.top]
    # keep deals that didn't come from listings (off-market leads you added)
    listed = {d["address"].lower() for d in out}
    out += [d for d in previous.values() if d["address"].lower() not in listed and d.get("contact_role") == "owner"
            and d["address"].lower() not in {r["address"].lower() for r in raw}]
    write_deals(out)

    print(f"Ranked {len(out)} properties -> {DEALS}\n")
    print(f"{'#':>3} {'score':>6} {'price':>10} {'$/sf':>5} {'vs area':>8} {'cut':>6} {'DOM':>4}  address")
    for i, d in enumerate(out[:15], 1):
        print(f"{i:>3} {d['score']:>6} {int(num(d['price']) or 0):>10,} {str(d['price_per_sqft']):>5} "
              f"{d['discount_vs_area_pct']:>7}% {d['price_cut_pct']:>5}% {d['days_on_market']:>4}  {d['address']}")
    print("\nNext: open data/deals.csv, change `status` to `approved` for the ones you want "
          "to reach out on (or `skip`), then run `draft`.")


# ---------------------------------------------------------------- emails
SUBJECTS = [
    "Question about {street}",
    "{street}",
    "Quick question on {street}",
    "Buyer interest in {street}",
]

OPENERS = [
    "Hi {first},",
    "Hey {first},",
    "Hi {first}, hope your week's going well.",
    "Good afternoon {first},",
]

# Keep these true for you. Don't claim buyers or deals you don't have.
BODIES_AGENT = [
    "I came across your listing at {street} and wanted to reach out. I'm a real estate "
    "wholesaler, which means I put properties under contract and connect them with cash "
    "investors who buy as-is.\n\n"
    "Would your seller be open to a cash offer with a quick, flexible close? If so I'd love to "
    "set up a time to see the property, or just hear what number would make sense for them.",

    "I'm reaching out about {street}. To be upfront, I'm a wholesaler, not an end buyer: I "
    "put properties under contract and assign them to investors. Homes like this one around "
    "{zip} are the kind of thing cash buyers look for.\n\n"
    "Is the seller flexible on price or terms? Happy to work through you on everything.",

    "Saw {street} has been on the market for a bit and thought I'd ask. I'm a real estate "
    "wholesaler and I work on finding {beds}-bed homes in this area for cash investors who "
    "buy as-is.\n\n"
    "If your seller would consider an offer, what would it take to get something done?",
]

BODIES_OWNER = [
    "I'm reaching out about your property at {street}. I'm a real estate wholesaler, so I "
    "connect homeowners with cash investors. No pressure at all; I just wanted to see "
    "if you'd ever thought about selling.\n\n"
    "If the timing's right, deals like this can usually close quickly and as-is, with no "
    "repairs or showings. If not, no worries and I won't keep bugging you.",

    "I'll keep this short. I'm a local real estate wholesaler, meaning I connect property "
    "owners with investors who buy for cash. I'm looking at homes on your street and "
    "wanted to ask about {street}.\n\n"
    "Would you be open to hearing an offer? Even if you're not selling now, happy to chat.",
]

CLOSERS = [
    "Thanks,\n{you}",
    "Appreciate your time,\n{you}",
    "Talk soon,\n{you}",
    "Thanks so much,\n{you}",
]


def build_email(deal, cfg, rng):
    street = deal["address"].split(",")[0].strip()
    first = (deal["contact_name"] or "").split()[0] if deal["contact_name"] else "there"
    fields = {
        "street": street, "first": first.title(), "zip": deal["zip"] or "the area",
        "beds": str(deal["beds"]).rstrip("0").rstrip(".") if deal["beds"] else "these",
        "you": cfg["your_name"],
    }
    is_owner = "owner" in (deal["contact_role"] or "").lower()
    body = "\n\n".join([
        rng.choice(OPENERS).format(**fields),
        rng.choice(BODIES_OWNER if is_owner else BODIES_AGENT).format(**fields),
        rng.choice(CLOSERS).format(**fields),
    ])
    signature = "\n".join(filter(None, [cfg.get("your_company"), cfg.get("your_phone"), cfg.get("your_email")]))
    sender = cfg.get("your_company") or cfg["your_name"]
    footer = (f"\n\n--\n{sender} | {cfg['mailing_address']}\n"
              "If you'd rather not hear from me, just reply \"remove\" and I won't contact you again.")
    return rng.choice(SUBJECTS).format(**fields), f"{body}\n{signature}{footer}"


def cmd_draft(args):
    cfg = load_config()
    deals = read_deals()
    dnc, sent = do_not_contact(), already_sent()
    os.makedirs(OUTBOX, exist_ok=True)
    rng = random.Random()
    made = 0
    for d in deals:
        if d["status"].strip().lower() != "approved":
            continue
        email = d["contact_email"].strip().lower()
        if not email:
            print(f"  skip (no email): {d['address']}")
            continue
        if email in dnc or (email, d["address"].lower(), "intro") in sent:
            print(f"  skip (do-not-contact or already emailed): {d['address']}")
            continue
        subject, body = build_email(d, cfg, rng)
        write_draft("intro", email, d["address"], subject, body)
        made += 1
        print(f"\n===== {d['address']}  ->  {email}\nSubject: {subject}\n\n{body}")
    print(f"\n{made} email(s) written. Review and edit them, then send "
          "(Send button in the dashboard, or `send` then `send --confirm`).")


FOLLOWUPS = [
    "Hi {first},\n\nJust following up on my note about {street}. Is the seller still open to "
    "offers? If now's not a good time, no worries.",
    "Hi {first},\n\nCircling back on {street}. If the seller would consider a cash offer that "
    "closes as-is on their timeline, I'd love to hear what number works for them.",
    "Hey {first},\n\nOne last check-in on {street}. If anything changes with the seller, I'm happy "
    "to take a look. Thanks either way!",
]


def cmd_followup(args):
    """Draft a short follow-up for deals still at 'Emailed' with no reply after N days."""
    cfg = load_config()
    days = args.days or cfg.get("followup_after_days", 5)
    max_n = cfg.get("max_followups", 2)
    deals, sent, dnc = read_deals(), read_sent(), do_not_contact()
    now, made = datetime.now(), 0
    for d in deals:
        if d["status"] != "emailed":
            continue
        email = d["contact_email"].strip().lower()
        mine = [r for r in sent if r["address"].lower() == d["address"].lower() and r["email"].lower() == email
                and (r["kind"] == "intro" or r["kind"].startswith("followup"))]
        if not mine or email in dnc:
            continue
        done = len([r for r in mine if r["kind"].startswith("followup")])
        last = max(datetime.fromisoformat(r["sent_at"]) for r in mine)
        if done >= max_n or (now - last).days < days:
            continue
        street = d["address"].split(",")[0].strip()
        first = (d["contact_name"] or "there").split()[0].title()
        kind = f"followup{done + 1}"
        body = FOLLOWUPS[min(done, len(FOLLOWUPS) - 1)].format(first=first, street=street)
        body += f"\n\n{cfg['your_name']}\n" + "\n".join(filter(None, [cfg.get("your_phone"), cfg.get("your_email")]))
        sender = cfg.get("your_company") or cfg["your_name"]
        body += (f"\n\n--\n{sender} | {cfg['mailing_address']}\n"
                 "If you'd rather not hear from me, just reply \"remove\" and I won't contact you again.")
        write_draft(kind, email, d["address"], f"Re: {street}", body, kind)
        made += 1
        print(f"  follow-up #{done + 1} -> {email}  ({d['address']})")
    print(f"\n{made} follow-up(s) written. Review them, then send.")


def cmd_blast(args):
    """Draft emails to matching cash buyers for a deal you have under contract."""
    cfg = load_config()
    deal = next((d for d in read_deals() if d["address"].lower() == args.address.lower()), None)
    if not deal:
        sys.exit(f"No deal with address {args.address!r}.")
    if deal["status"] != "under_contract":
        sys.exit("Only email buyers about a house you actually have under contract. "
                 "Set its stage to Under contract first.")
    buyers = matching_buyers(deal, read_buyers())
    if not buyers:
        sys.exit("No buyers match this deal yet. Add cash buyers (ZIPs and max price) in the dashboard.")
    sent, dnc = already_sent(), do_not_contact()
    street = deal["address"].split(",")[0].strip()
    price = contract_price(deal) + (num(deal.get("assignment_fee")) or 0)
    lines = [f"Price: ${price:,.0f} (assignment of contract)"]
    if num(deal.get("arv")):
        lines.append(f"Estimated ARV: ${num(deal['arv']):,.0f}")
    if num(deal.get("repairs")):
        lines.append(f"Estimated repairs: ${num(deal['repairs']):,.0f}")
    beds, baths = str(deal.get("beds", "")).replace(".0", ""), str(deal.get("baths", "")).replace(".0", "")
    lines.append(f"{beds or '?'} bed / {baths or '?'} bath, {deal.get('sqft') or '?'} sqft")
    made = 0
    for b in buyers:
        email = b["email"].strip().lower()
        if email in dnc or (email, deal["address"].lower(), "buyer") in sent:
            continue
        first = (b.get("name") or "there").split()[0].title()
        body = (f"Hi {first},\n\nI have {deal['address']} under contract and I'm looking to assign the "
                f"contract to a cash buyer. Here are the numbers:\n\n" + "\n".join("  - " + l for l in lines) +
                "\n\nTo be clear, I'm selling my contract rights, not listing the house. Please do your own "
                "walkthrough and numbers. Want to set up a time to see it?\n\n"
                f"{cfg['your_name']}\n" + "\n".join(filter(None, [cfg.get("your_phone"), cfg.get("your_email")])) +
                f"\n\n--\n{cfg.get('your_company') or cfg['your_name']} | {cfg['mailing_address']}\n"
                "Reply \"remove\" to stop getting deals from me.")
        write_draft("buyer", email, deal["address"], f"Off-market deal: {street}", body,
                    "buyer_" + "".join(c if c.isalnum() else "_" for c in email))
        made += 1
        print(f"  buyer email -> {email}")
    print(f"\n{made} buyer email(s) written for {deal['address']}. Review them, then send.")


def cmd_send(args):
    cfg = load_config()
    drafts = sorted(f for f in os.listdir(OUTBOX) if f.endswith(".json")) if os.path.isdir(OUTBOX) else []
    if not drafts:
        sys.exit("No drafts. Run `draft` first.")
    dnc, sent = do_not_contact(), already_sent()
    limit = cfg.get("max_emails_per_run", 20)
    todo = []
    for name in drafts:
        with open(os.path.join(OUTBOX, name)) as f:
            msg = json.load(f)
        msg.setdefault("kind", "intro")
        if msg["to"].lower() in dnc or (msg["to"].lower(), msg["address"].lower(), msg["kind"]) in sent:
            continue
        todo.append((name, msg))
    todo = todo[:limit]

    if not args.confirm:
        print(f"DRY RUN: would send {len(todo)} emails (limit {limit}/run):")
        for _, m in todo:
            print(f"  {m['to']:<35} {m['subject']}")
        print("\nRe-run with --confirm to actually send.")
        return

    if not is_set(cfg, "smtp_app_password"):
        sys.exit("Add your Gmail app password first: run `python3 dealfinder.py setup`.")
    server = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30)
    server.starttls()
    try:
        server.login(cfg["smtp_user"], cfg["smtp_app_password"])
    except smtplib.SMTPAuthenticationError:
        sys.exit("Gmail rejected the login. Check the Gmail address and app password with `setup`.")
    for i, (name, m) in enumerate(todo):
        em = EmailMessage()
        em["From"] = f"{cfg['your_name']} <{cfg['smtp_user']}>"
        em["To"] = m["to"]
        em["Subject"] = m["subject"]
        em.set_content(m["body"])
        server.send_message(em)
        append_sent({"sent_at": datetime.now().isoformat(timespec="seconds"), "email": m["to"],
                     "address": m["address"], "subject": m["subject"], "kind": m["kind"]})
        os.rename(os.path.join(OUTBOX, name), os.path.join(OUTBOX, name + ".sent"))
        print(f"  sent -> {m['to']}  ({m['address']})")
        if i < len(todo) - 1:
            # spacing sends out keeps you out of spam filters and looks like a person
            time.sleep(random.uniform(cfg["min_seconds_between_emails"], cfg["max_seconds_between_emails"]))
    server.quit()
    # move the deals you just emailed to the "emailed" stage
    sent_addrs = {m["address"].lower() for _, m in todo if m["kind"] == "intro"}
    deals = read_deals() if os.path.exists(DEALS) else []
    for d in deals:
        if d["address"].lower() in sent_addrs and d["status"] == "approved":
            d["status"], d["updated"] = "emailed", datetime.now().isoformat(timespec="seconds")
    if deals:
        write_deals(deals)
    print(f"Done. Log: {SENT_LOG}. Add anyone who replies 'remove' to {DNC}.")


# ---------------------------------------------------------------- setup
def cmd_setup(args):
    path = os.path.join(HERE, "config.json")
    with open(os.path.join(HERE, "config.example.json")) as f:
        cfg = json.load(f)
    if os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f))
    print("Let's set you up. Press Enter to keep what's in [brackets].\n")

    def ask(key, prompt, secret=False):
        cur = cfg.get(key, "")
        shown = ("saved" if is_set(cfg, key) else "") if secret else cur
        q = f"{prompt} [{shown}]: " if shown else f"{prompt}: "
        val = (getpass.getpass(q) if secret else input(q)).strip()
        if val:
            cfg[key] = val.replace(" ", "") if secret else val

    ask("your_name", "Your name")
    ask("your_company", "Company name")
    ask("your_phone", "Phone number")
    ask("your_email", "Email address")
    ask("mailing_address", "Mailing address (required by law in the email footer)")
    print("\nRentCast key (free at https://www.rentcast.io/api). Leave blank to add later.")
    ask("rentcast_api_key", "RentCast API key", secret=True)
    print("\nGmail app password: Google Account > Security > 2-Step Verification on > App passwords.")
    ask("smtp_user", "Gmail address to send from")
    ask("smtp_app_password", "Gmail app password (16 letters)", secret=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"\nSaved to {path}. You're ready: run the dashboard.")


# ---------------------------------------------------------------- dashboard
def dashboard_state():
    deals = read_deals() if os.path.exists(DEALS) else []
    sent = []
    sent = read_sent()
    drafts = []
    if os.path.isdir(OUTBOX):
        for n in sorted(os.listdir(OUTBOX)):
            if n.endswith(".json"):
                with open(os.path.join(OUTBOX, n)) as f:
                    drafts.append({"file": n, **json.load(f)})
    has_cfg = os.path.exists(os.path.join(HERE, "config.json"))
    cfg = load_config() if has_cfg else {}
    buyers = read_buyers()
    for d in deals:
        d["buyer_matches"] = len(matching_buyers(d, buyers))
    settings = {"mao_percent": cfg.get("mao_percent", 0.70), "target_fee": cfg.get("target_fee", 10000),
                "followup_after_days": cfg.get("followup_after_days", 5)}
    in_pipeline = {d["address"].lower() for d in deals}
    leads = [dict(l, in_pipeline=f"{l['address']}, {l['town']}".lower() in in_pipeline)
             for l in records.build_leads(150)] if os.path.exists(records.RECORDS) else []
    known = {(b.get("name") or "").lower() for b in buyers}
    buyer_leads = [dict(b, added=b["name"].lower() in known)
                   for b in records.find_buyers()[:60]] if os.path.exists(records.RECORDS) else []
    return {"deals": deals, "sent": sent, "drafts": drafts, "buyers": buyers, "settings": settings,
            "leads": leads, "buyer_leads": buyer_leads, "records_count": len(records.load_records()), "drafts_waiting": len(drafts),
            "do_not_contact": sorted(do_not_contact()), "stages": STAGES, "job": JOB,
            "setup": {"config": has_cfg,
                      "rentcast": is_set(cfg, "rentcast_api_key"),
                      "email": is_set(cfg, "smtp_app_password")}}


# One background job at a time (finding houses, writing or sending emails).
JOB = {"running": False, "name": "", "output": "", "ok": True}


def run_job(name, func, args):
    def work():
        buf = io.StringIO()
        ok = True
        try:
            with contextlib.redirect_stdout(buf):
                func(args)
        except SystemExit as e:
            ok = False
            buf.write(f"\n{e}")
        except Exception as e:  # show the problem on the page instead of crashing the server
            ok = False
            buf.write(f"\nError: {e}")
        JOB.update(running=False, output=buf.getvalue()[-4000:], ok=ok)
    JOB.update(running=True, name=name, output="", ok=True)
    threading.Thread(target=work, daemon=True).start()


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "dashboard.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path.startswith("/letters"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            kind = "buyer" if q.get("kind") == ["buyer"] else "seller"
            top = int((q.get("top") or ["50"])[0])
            items = records.build_leads() if kind == "seller" else records.find_buyers()
            items = [i for i in items if i.get("mail_street")][:top]
            return self._send(200, records.render_letters(items, kind, load_config()).encode(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self._send(200, dashboard_state())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # only accept requests from this page, not from other websites
        if self.headers.get("X-Dashboard") != "1":
            return self._send(403, {"error": "forbidden"})
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/deal":
            deals = read_deals() if os.path.exists(DEALS) else []
            for d in deals:
                if d["address"] == body.get("address"):
                    for col in USER_COLUMNS:
                        if col in body and col != "updated":
                            d[col] = str(body[col])
                    if d["status"] not in STAGES:
                        return self._send(400, {"error": "unknown status"})
                    d["updated"] = datetime.now().isoformat(timespec="seconds")
                    write_deals(deals)
                    return self._send(200, d)
            return self._send(404, {"error": "deal not found"})
        if self.path == "/api/run":
            if JOB["running"]:
                return self._send(409, {"error": f"Still busy: {JOB['name']}"})
            action = body.get("action")
            if action == "fetch":
                a = argparse.Namespace(city=body.get("city") or None, state=body.get("state") or None,
                                       zip=body.get("zip") or None, property_type=body.get("property_type") or None,
                                       max_price=num(body.get("max_price")), max_results=500)
                if not a.zip and not (a.city and a.state):
                    return self._send(400, {"error": "Enter a ZIP, or a city and state."})
                def fetch_and_score(x):
                    cmd_fetch(x)
                    cmd_score(argparse.Namespace(max_price=x.max_price, top=None))
                run_job("Finding houses", fetch_and_score, a)
            elif action == "import":
                path = os.path.join(DATA, "upload.csv")
                os.makedirs(DATA, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(body.get("csv", ""))
                def import_and_score(x):
                    cmd_import(x)
                    cmd_score(argparse.Namespace(max_price=None, top=None))
                run_job("Importing list", import_and_score,
                        argparse.Namespace(csv=path, role=body.get("role") or "listing agent"))
            elif action == "draft":
                run_job("Writing emails", cmd_draft, argparse.Namespace())
            elif action == "records_import":
                path = os.path.join(DATA, "records_upload.csv")
                os.makedirs(DATA, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(body.get("csv", ""))
                if body.get("tag"):
                    run_job("Tagging properties", records.cmd_flag, argparse.Namespace(csv=path, tag=body["tag"]))
                else:
                    run_job("Loading property records", records.cmd_import,
                            argparse.Namespace(csv=path, town=body.get("town"), county=body.get("county")))
            elif action == "records_pull":
                run_job("Downloading county records", records.cmd_pull,
                        argparse.Namespace(county=body.get("county") or "UNION", towns=body.get("towns") or None))
            elif action == "followup":
                run_job("Writing follow-ups", cmd_followup, argparse.Namespace(days=None))
            elif action == "blast":
                run_job("Writing buyer emails", cmd_blast, argparse.Namespace(address=body.get("address", "")))
            elif action == "send":
                run_job("Sending emails", cmd_send, argparse.Namespace(confirm=True))
            else:
                return self._send(400, {"error": "unknown action"})
            return self._send(200, JOB)
        if self.path == "/api/draft":
            name = os.path.basename(body.get("file", ""))
            path = os.path.join(OUTBOX, name)
            if not name.endswith(".json") or not os.path.exists(path):
                return self._send(404, {"error": "draft not found"})
            if body.get("delete"):
                os.remove(path)
                return self._send(200, {"ok": True})
            with open(path) as f:
                d = json.load(f)
            d["subject"], d["body"] = body.get("subject", d["subject"]), body.get("body", d["body"])
            with open(path, "w") as f:
                json.dump(d, f, indent=2)
            return self._send(200, d)
        if self.path == "/api/buyer":
            # a buyer is identified by email, or by name when you only have a phone number
            key = lambda b: (b.get("email") or "").strip().lower() or (b.get("name") or "").strip().lower()
            row = {k: str(body.get(k, "")).strip() for k in BUYER_COLUMNS}
            row["email"] = row["email"].lower()
            if not key(row):
                return self._send(400, {"error": "Buyer needs a name or email."})
            if row["email"] and "@" not in row["email"]:
                return self._send(400, {"error": "That email doesn't look right."})
            buyers = [b for b in read_buyers() if key(b) != key(row)]
            if not body.get("delete"):
                row["added"] = row["added"] or datetime.now().date().isoformat()
                buyers.append(row)
            write_buyers(buyers)
            return self._send(200, {"ok": True})
        if self.path == "/api/lead/add":
            lead = next((l for l in records.build_leads() if l["address"] == body.get("address")
                         and l["town"] == body.get("town")), None)
            if not lead:
                return self._send(404, {"error": "lead not found"})
            deals = read_deals() if os.path.exists(DEALS) else []
            full = f"{lead['address']}, {lead['town']}".strip(", ")
            if full.lower() not in {d["address"].lower() for d in deals}:
                deals.append({
                    "status": "pending", "score": lead["score"], "address": full,
                    "zip": str(lead.get("mail_zip", ""))[:5] if lead.get("mail_street", "").upper() == lead["address"].upper() else "",
                    "price": "", "beds": "", "baths": "", "sqft": "", "days_on_market": "",
                    "property_type": "Off-market", "motivation_flags": lead["flags"],
                    "contact_name": lead["owner"], "contact_role": "owner",
                    "notes": f"Mail to: {lead['mail_street']}, {lead['mail_city_state']} {lead['mail_zip']}",
                    "updated": datetime.now().isoformat(timespec="seconds"),
                })
                write_deals(deals)
            return self._send(200, {"ok": True})
        if self.path == "/api/buyerlead/add":
            b = next((x for x in records.find_buyers() if x["name"] == body.get("name")), None)
            if not b:
                return self._send(404, {"error": "not found"})
            buyers = [x for x in read_buyers() if (x.get("name") or "").lower() != b["name"].lower()]
            buyers.append({"name": b["name"], "email": "", "phone": "", "zips": "", "max_price": "",
                           "notes": f"From records: {b['purchases']} buys, {b['flips']} flips, "
                                    f"${b['min_price']:,}-${b['max_price']:,} in {b['towns']}. "
                                    f"Mail: {b['mail_street']}, {b['mail_city_state']} {b['mail_zip']}",
                           "added": datetime.now().date().isoformat()})
            write_buyers(buyers)
            return self._send(200, {"ok": True})
        if self.path == "/api/buyers/starter":
            have = {(b.get("name") or "").lower() for b in read_buyers()}
            with open(os.path.join(HERE, "starter_buyers.csv"), newline="") as f:
                new = [dict(r, added=datetime.now().date().isoformat()) for r in csv.DictReader(f)
                       if r["name"].lower() not in have]
            write_buyers(read_buyers() + new)
            return self._send(200, {"added": len(new)})
        if self.path == "/api/dnc":
            email = (body.get("email") or "").strip().lower()
            if "@" not in email:
                return self._send(400, {"error": "bad email"})
            if email not in do_not_contact():
                os.makedirs(DATA, exist_ok=True)
                with open(DNC, "a") as f:
                    f.write(email + "\n")
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


def cmd_dashboard(args):
    url = f"http://127.0.0.1:{args.port}/"
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    print(f"Dashboard running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


# ---------------------------------------------------------------- demo
def fake_send(when):
    """Stand-in for `send` in the demo: logs the drafts as sent on a given date, sends nothing."""
    n = 0
    for name in sorted(os.listdir(OUTBOX)) if os.path.isdir(OUTBOX) else []:
        if not name.endswith(".json"):
            continue
        with open(os.path.join(OUTBOX, name)) as f:
            m = json.load(f)
        append_sent({"sent_at": when.isoformat(timespec="seconds"), "email": m["to"], "address": m["address"],
                     "subject": m["subject"], "kind": m.get("kind", "intro")})
        os.rename(os.path.join(OUTBOX, name), os.path.join(OUTBOX, name + ".sent"))
        n += 1
    deals = read_deals()
    sent = {r["address"].lower() for r in read_sent() if r["kind"] == "intro"}
    for d in deals:
        if d["status"] == "approved" and d["address"].lower() in sent:
            d["status"] = "emailed"
    write_deals(deals)
    return n


def set_deal(address, **fields):
    deals = read_deals()
    for d in deals:
        if d["address"] == address:
            d.update({k: str(v) for k, v in fields.items()})
            d["updated"] = datetime.now().isoformat(timespec="seconds")
    write_deals(deals)


def cmd_demo(args):
    """A full wholesale deal, start to finish, on made-up sample data. Sends nothing."""
    import shutil
    from datetime import timedelta
    if os.path.basename(DATA) != "data_demo":
        sys.exit("Run it as: python3 dealfinder.py demo --demo")
    if os.path.isdir(DATA):
        shutil.rmtree(DATA)
    os.makedirs(DATA)
    with open(os.path.join(HERE, "config.example.json")) as f:
        cfg = json.load(f)
    if os.path.exists(os.path.join(HERE, "config.json")):
        with open(os.path.join(HERE, "config.json")) as f:
            cfg.update(json.load(f))
    global load_config
    real_load = load_config
    load_config = lambda: cfg  # noqa: E731  the demo never needs your keys
    records_cfg = cfg
    quiet = io.StringIO()
    sample = os.path.join(HERE, "sample_data")
    now = datetime.now()
    say = lambda t: print(t, flush=True)  # noqa: E731

    def step(n, title):
        say(f"\n{'=' * 64}\nSTEP {n}: {title}\n{'=' * 64}")

    try:
        step(1, "Find houses for sale and rank them")
        with contextlib.redirect_stdout(quiet):
            cmd_import(argparse.Namespace(csv=os.path.join(sample, "sample_listings.csv"), role="listing agent"))
            cmd_score(argparse.Namespace(max_price=None, top=None))
        for d in read_deals()[:3]:
            say(f"  score {d['score']:>6}  ${int(num(d['price'])):,}  {d['discount_vs_area_pct']}% under area, "
                f"{d['days_on_market']} days  {d['address']}  [{d['motivation_flags']}]")

        step(2, "Find off-market owners and cash buyers in public records")
        with contextlib.redirect_stdout(quiet):
            records.cmd_import(argparse.Namespace(csv=os.path.join(sample, "sample_tax_list.csv"), town=None, county=None))
            records.cmd_flag(argparse.Namespace(csv=os.path.join(sample, "sample_sheriff_sales.csv"), tag="pre-foreclosure"))
        leads, rbuyers = records.build_leads(), records.find_buyers()
        for l in leads[:3]:
            say(f"  lead  {l['score']:>5}  {l['address']}, {l['town']}  {l['owner']}  [{l['flags']}]")
        for b in rbuyers:
            say(f"  buyer {b['name']}: {b['purchases']} buys, {b['flips']} flips, ${b['min_price']:,}-${b['max_price']:,}")

        step(3, "Build the cash-buyer list")
        write_buyers([
            {"name": "Ana Investor (sample)", "email": "ana.example@example.com", "zips": "78210, 78211",
             "max_price": "190000", "notes": "flips; met at investor meetup", "added": now.date().isoformat()},
            {"name": "Bright Door Properties LLC", "email": "deals.example@example.com", "zips": "",
             "max_price": "200000", "notes": "found in records: repeat buyer + flip", "added": now.date().isoformat()},
        ])
        say("  2 buyers on the list (one from a meetup, one found in the records)")
        n = records.write_letters("seller", 25, records_cfg)
        say(f"  {n} letters printed for off-market owners -> {records.LETTERS}")

        step(4, "Approve the best houses and email the listing agents")
        top = read_deals()[:2]
        for d in top:
            set_deal(d["address"], status="approved")
        with contextlib.redirect_stdout(quiet):
            cmd_draft(argparse.Namespace())
        sent = fake_send(now - timedelta(days=6))
        say(f"  {sent} first emails sent (simulated, dated 6 days ago):")
        for r in read_sent():
            say(f"    -> {r['email']}  \"{r['subject']}\"")

        step(5, "No reply after 5 days: polite follow-ups")
        with contextlib.redirect_stdout(quiet):
            cmd_followup(argparse.Namespace(days=None))
        sent = fake_send(now - timedelta(days=1))
        say(f"  {sent} follow-up(s) sent (simulated)")

        deal = top[0]["address"]
        step(6, f"The agent for {deal.split(',')[0]} replies: seller wants out. Run the numbers")
        arv, repairs, fee = 265000, 40000, 10000
        mao = arv * cfg.get("mao_percent", 0.70) - repairs
        offer = int((mao - fee) // 1000 * 1000)
        set_deal(deal, status="negotiating", arv=arv, repairs=repairs, assignment_fee=fee, offer=offer,
                 notes="Agent: seller inherited it, lives out of state, wants a quick close")
        say(f"  After-repair value ${arv:,}, repairs ${repairs:,}")
        say(f"  Buyer's max (70% rule): ${arv:,} x 0.70 - ${repairs:,} = ${mao:,.0f}")
        say(f"  Your offer to the seller, keeping a ${fee:,} fee: ${offer:,} (asking ${int(num(top[0]['price'])):,})")

        step(7, "Seller accepts: house under contract. Email matching cash buyers")
        set_deal(deal, status="under_contract")
        d = next(x for x in read_deals() if x["address"] == deal)
        matches = matching_buyers(d, read_buyers())
        say(f"  {len(matches)} of {len(read_buyers())} buyers match (ZIP + max price): "
            + ", ".join(b["name"] for b in matches))
        with contextlib.redirect_stdout(quiet):
            cmd_blast(argparse.Namespace(address=deal))
        draft = next(json.load(open(os.path.join(OUTBOX, f))) for f in os.listdir(OUTBOX) if f.endswith(".json"))
        say("  Email to buyer:\n    " + draft["body"].split("\n\n--")[0].replace("\n", "\n    "))
        fake_send(now)

        step(8, "A buyer takes the contract. Close and get paid")
        set_deal(deal, status="closed", notes="Assigned to Ana Investor (sample). Closed with title company.")
        state = dashboard_state()
        earned = sum(num(x.get("assignment_fee")) or 0 for x in state["deals"] if x["status"] == "closed")
        say(f"  Contract price ${offer:,} + your fee ${fee:,} = buyer pays ${offer + fee:,}")
        say(f"  Buyer's max was ${mao:,.0f}, so the buyer still has ${mao - offer - fee:,.0f} of room.")

        step(9, "Scoreboard")
        stages = {}
        for x in state["deals"]:
            stages[x["status"]] = stages.get(x["status"], 0) + 1
        say(f"  Properties found:  {len(state['deals'])} listed + {len(leads)} off-market leads")
        say(f"  Emails sent:       {len(state['sent'])}  (first emails, follow-ups, buyer emails)")
        say(f"  Cash buyers:       {len(state['buyers'])}")
        say(f"  Pipeline:          " + ", ".join(f"{k} {v}" for k, v in stages.items()))
        say(f"  Fees earned:       ${earned:,.0f}")
        say("\nThis was a practice run on made-up data. Nothing was emailed or mailed.")
        say("See it in the dashboard:  python3 dealfinder.py dashboard --demo")
    finally:
        load_config = real_load


# ---------------------------------------------------------------- cli
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="pull active listings from RentCast")
    f.add_argument("--city")
    f.add_argument("--state")
    f.add_argument("--zip")
    f.add_argument("--property-type", help="e.g. 'Single Family'")
    f.add_argument("--max-price", type=float)
    f.add_argument("--max-results", type=int, default=500)
    f.set_defaults(func=cmd_fetch)

    i = sub.add_parser("import", help="load listings/leads from a CSV")
    i.add_argument("csv")
    i.add_argument("--role", default="listing agent", help="who the contacts are: 'listing agent' or 'owner'")
    i.set_defaults(func=cmd_import)

    s = sub.add_parser("score", help="rank everything into data/deals.csv")
    s.add_argument("--max-price", type=float)
    s.add_argument("--top", type=int, help="keep only the best N")
    s.set_defaults(func=cmd_score)

    sub.add_parser("draft", help="write emails for approved deals").set_defaults(func=cmd_draft)

    fu = sub.add_parser("followup", help="write follow-ups for emailed deals with no reply")
    fu.add_argument("--days", type=int, help="wait this many days after the last email (default 5)")
    fu.set_defaults(func=cmd_followup)

    bl = sub.add_parser("blast", help="write emails to matching cash buyers for a deal under contract")
    bl.add_argument("address", help="the deal's address, exactly as in deals.csv")
    bl.set_defaults(func=cmd_blast)

    rc = sub.add_parser("records", help="off-market leads and cash buyers from NJ property records")
    rsub = rc.add_subparsers(dest="rcmd", required=True)
    ri = rsub.add_parser("import", help="load a tax list or sales CSV")
    ri.add_argument("csv")
    ri.add_argument("--town")
    ri.add_argument("--county")
    ri.set_defaults(func=records.cmd_import)
    rp = rsub.add_parser("pull", help="download a county's tax list from NJ's public parcel service")
    rp.add_argument("--county", default="UNION")
    rp.add_argument("--towns", help="optional, comma separated, e.g. Elizabeth,Linden,Plainfield")
    rp.set_defaults(func=records.cmd_pull)
    rl = rsub.add_parser("leads", help="rank owners most likely to sell")
    rl.add_argument("--top", type=int)
    rl.add_argument("--town")
    rl.set_defaults(func=records.cmd_leads)
    rb = rsub.add_parser("buyers", help="find companies that keep buying (cash buyers)")
    rb.add_argument("--months", type=int, default=24)
    rb.add_argument("--min-buys", type=int, default=2)
    rb.set_defaults(func=records.cmd_buyers)
    rf = rsub.add_parser("flag", help="tag properties on a list, e.g. sheriff sales")
    rf.add_argument("csv")
    rf.add_argument("--tag", default="pre-foreclosure")
    rf.set_defaults(func=records.cmd_flag)
    rt = rsub.add_parser("letters", help="printable letters + mailing list")
    rt.add_argument("--kind", choices=["seller", "buyer"], default="seller")
    rt.add_argument("--top", type=int, default=50)
    rt.add_argument("--town")
    rt.set_defaults(func=lambda a: records.cmd_letters(a, load_config()))

    se = sub.add_parser("send", help="send drafts (dry run unless --confirm)")
    se.add_argument("--confirm", action="store_true")
    se.set_defaults(func=cmd_send)

    sub.add_parser("demo", help="practice run of a full deal on sample data (use with --demo)").set_defaults(func=cmd_demo)

    sub.add_parser("setup", help="answer a few questions to create config.json").set_defaults(func=cmd_setup)

    db = sub.add_parser("dashboard", help="open the tracking dashboard in your browser")
    db.add_argument("--port", type=int, default=8765)
    db.add_argument("--no-browser", action="store_true")
    db.set_defaults(func=cmd_dashboard)

    args = p.parse_args()
    if args.cmd == "fetch" and not args.zip and not (args.city and args.state):
        p.error("fetch needs --zip, or --city and --state")
    args.func(args)


if __name__ == "__main__":
    main()
