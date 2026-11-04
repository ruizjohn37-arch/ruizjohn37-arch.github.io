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

Standard library only. Python 3.9+.
"""
import argparse
import csv
import json
import os
import random
import smtplib
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "raw_listings.json")
DEALS = os.path.join(DATA, "deals.csv")
OUTBOX = os.path.join(DATA, "outbox")
SENT_LOG = os.path.join(DATA, "sent_log.csv")
DNC = os.path.join(DATA, "do_not_contact.txt")

DEAL_COLUMNS = [
    "status", "score", "address", "zip", "price", "sqft", "price_per_sqft",
    "area_median_ppsf", "discount_vs_area_pct", "price_cut_pct", "days_on_market",
    "beds", "baths", "property_type", "motivation_flags", "contact_name",
    "contact_email", "contact_phone", "contact_role", "listing_url", "notes",
]


# ---------------------------------------------------------------- helpers
def load_config():
    path = os.path.join(HERE, "config.json")
    if not os.path.exists(path):
        sys.exit("No config.json yet. Copy config.example.json to config.json and fill it in.")
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


def do_not_contact():
    if not os.path.exists(DNC):
        return set()
    with open(DNC) as f:
        return {line.strip().lower() for line in f if line.strip()}


def already_sent():
    if not os.path.exists(SENT_LOG):
        return set()
    with open(SENT_LOG, newline="") as f:
        return {(r["email"].lower(), r["address"].lower()) for r in csv.DictReader(f)}


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
            "notes": prev.get("notes", ""),
        })

    out.sort(key=lambda d: d["score"], reverse=True)
    if args.top:
        out = out[: args.top]
    os.makedirs(DATA, exist_ok=True)
    with open(DEALS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DEAL_COLUMNS)
        w.writeheader()
        w.writerows(out)

    print(f"Ranked {len(out)} properties -> {DEALS}\n")
    print(f"{'#':>3} {'score':>6} {'price':>10} {'$/sf':>5} {'vs area':>8} {'cut':>6} {'DOM':>4}  address")
    for i, d in enumerate(out[:15], 1):
        print(f"{i:>3} {d['score']:>6} {d['price']:>10,} {str(d['price_per_sqft']):>5} "
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

BODIES_AGENT = [
    "I came across your listing at {street} and wanted to reach out. I'm a real estate "
    "wholesaler here in the area, which means I work with a group of cash buyers and match "
    "them with properties. One of them is looking for something just like this.\n\n"
    "Would your seller be open to a cash offer with a quick, flexible close? If so I'd love to "
    "set up a time to walk the property, or just hear what number would make sense for them.",

    "I'm reaching out about {street}. To be upfront, I'm a wholesaler, not an end buyer: I "
    "put properties under contract and assign them to investors I work with. I've got a "
    "couple of cash buyers active around {zip} right now and this one caught my eye.\n\n"
    "Is the seller still flexible on price or terms? Happy to work through you on everything.",

    "Saw {street} has been on the market for a bit and thought I'd ask. I'm a real estate "
    "wholesaler and I've got buyers looking for {beds}-bed homes in this area who can close "
    "in cash, as-is.\n\n"
    "If your seller would consider an offer, what would it take to get something done?",
]

BODIES_OWNER = [
    "I'm reaching out about your property at {street}. I'm a real estate wholesaler, so I "
    "connect homeowners with cash buyers I work with. No pressure at all; I just wanted to see "
    "if you'd ever thought about selling.\n\n"
    "If the timing's right, we can usually close quickly and buy as-is, no repairs or "
    "showings. If not, no worries and I won't keep bugging you.",

    "I'll keep this short. I'm a local real estate wholesaler, meaning I match property "
    "owners with investors who buy for cash. Your place at {street} is in an area a few of my "
    "buyers are focused on.\n\n"
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
    footer = (f"\n\n--\n{cfg['your_company']} | {cfg['mailing_address']}\n"
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
        if email in dnc or (email, d["address"].lower()) in sent:
            print(f"  skip (do-not-contact or already emailed): {d['address']}")
            continue
        subject, body = build_email(d, cfg, rng)
        slug = "".join(c if c.isalnum() else "_" for c in d["address"])[:60]
        with open(os.path.join(OUTBOX, slug + ".json"), "w") as f:
            json.dump({"to": email, "address": d["address"], "subject": subject, "body": body}, f, indent=2)
        made += 1
        print(f"\n===== {d['address']}  ->  {email}\nSubject: {subject}\n\n{body}")
    print(f"\n{made} drafts written to {OUTBOX}. Edit any of them, then run `send` "
          "(dry run) and `send --confirm` when you're happy.")


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
        if msg["to"].lower() in dnc or (msg["to"].lower(), msg["address"].lower()) in sent:
            continue
        todo.append((name, msg))
    todo = todo[:limit]

    if not args.confirm:
        print(f"DRY RUN: would send {len(todo)} emails (limit {limit}/run):")
        for _, m in todo:
            print(f"  {m['to']:<35} {m['subject']}")
        print("\nRe-run with --confirm to actually send.")
        return

    server = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"])
    server.starttls()
    server.login(cfg["smtp_user"], cfg["smtp_app_password"])
    new_log = not os.path.exists(SENT_LOG)
    with open(SENT_LOG, "a", newline="") as logf:
        log = csv.writer(logf)
        if new_log:
            log.writerow(["sent_at", "email", "address", "subject"])
        for i, (name, m) in enumerate(todo):
            em = EmailMessage()
            em["From"] = f"{cfg['your_name']} <{cfg['smtp_user']}>"
            em["To"] = m["to"]
            em["Subject"] = m["subject"]
            em.set_content(m["body"])
            server.send_message(em)
            log.writerow([datetime.now().isoformat(timespec="seconds"), m["to"], m["address"], m["subject"]])
            logf.flush()
            os.rename(os.path.join(OUTBOX, name), os.path.join(OUTBOX, name + ".sent"))
            print(f"  sent -> {m['to']}  ({m['address']})")
            if i < len(todo) - 1:
                # spacing sends out keeps you out of spam filters and looks like a person
                time.sleep(random.uniform(cfg["min_seconds_between_emails"], cfg["max_seconds_between_emails"]))
    server.quit()
    print(f"Done. Log: {SENT_LOG}. Add anyone who replies 'remove' to {DNC}.")


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

    se = sub.add_parser("send", help="send drafts (dry run unless --confirm)")
    se.add_argument("--confirm", action="store_true")
    se.set_defaults(func=cmd_send)

    args = p.parse_args()
    if args.cmd == "fetch" and not args.zip and not (args.city and args.state):
        p.error("fetch needs --zip, or --city and --state")
    args.func(args)


if __name__ == "__main__":
    main()
