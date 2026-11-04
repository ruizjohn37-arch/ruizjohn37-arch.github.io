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
    setup   -> answer a few questions once to create config.json
    dashboard -> open a page in your browser to track every deal

Standard library only. Python 3.9+.
"""
import argparse
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
    "offer", "assignment_fee", "follow_up", "updated",
]
# The order a deal moves through. The dashboard uses these as its tabs.
STAGES = ["pending", "approved", "emailed", "replied", "negotiating",
          "under_contract", "closed", "skip", "dead"]
# Columns you edit by hand (or in the dashboard); re-scoring keeps them.
USER_COLUMNS = ["status", "notes", "offer", "assignment_fee", "follow_up", "updated"]


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
        })
        for col in USER_COLUMNS:
            out[-1][col] = prev.get(col, out[-1].get(col, ""))
        out[-1]["status"] = out[-1]["status"] or "pending"

    out.sort(key=lambda d: d["score"], reverse=True)
    if args.top:
        out = out[: args.top]
    write_deals(out)

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
        if email in dnc or (email, d["address"].lower()) in sent:
            print(f"  skip (do-not-contact or already emailed): {d['address']}")
            continue
        subject, body = build_email(d, cfg, rng)
        slug = "".join(c if c.isalnum() else "_" for c in d["address"])[:60]
        with open(os.path.join(OUTBOX, slug + ".json"), "w") as f:
            json.dump({"to": email, "address": d["address"], "subject": subject, "body": body}, f, indent=2)
        made += 1
        print(f"\n===== {d['address']}  ->  {email}\nSubject: {subject}\n\n{body}")
    print(f"\n{made} email(s) written. Review and edit them, then send "
          "(Send button in the dashboard, or `send` then `send --confirm`).")


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

    if not is_set(cfg, "smtp_app_password"):
        sys.exit("Add your Gmail app password first: run `python3 dealfinder.py setup`.")
    server = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30)
    server.starttls()
    try:
        server.login(cfg["smtp_user"], cfg["smtp_app_password"])
    except smtplib.SMTPAuthenticationError:
        sys.exit("Gmail rejected the login. Check the Gmail address and app password with `setup`.")
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
    # move the deals you just emailed to the "emailed" stage
    sent_addrs = {m["address"].lower() for _, m in todo}
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
    if os.path.exists(SENT_LOG):
        with open(SENT_LOG, newline="") as f:
            sent = list(csv.DictReader(f))
    drafts = []
    if os.path.isdir(OUTBOX):
        for n in sorted(os.listdir(OUTBOX)):
            if n.endswith(".json"):
                with open(os.path.join(OUTBOX, n)) as f:
                    drafts.append({"file": n, **json.load(f)})
    has_cfg = os.path.exists(os.path.join(HERE, "config.json"))
    cfg = load_config() if has_cfg else {}
    return {"deals": deals, "sent": sent, "drafts": drafts, "drafts_waiting": len(drafts),
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
