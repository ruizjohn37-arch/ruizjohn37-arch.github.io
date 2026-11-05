"""
Off-market leads and cash buyers from New Jersey public property records.

Works with the NJ "Parcels and MOD-IV" tax list (every property, owner, where the tax bill is
mailed, assessed value, last sale) and with sales files, exported as CSV. Column names are matched
loosely, so a county tax-board export or a spreadsheet you made also works.

    records import union_modiv.csv     load a county's tax list (repeat for each county / sales file)
    records leads --top 200            rank owners most likely to sell -> data/leads.csv
    records buyers                     find LLCs that keep buying -> data/buyer_leads.csv
    records flag sheriff_sales.csv     mark properties on a sheriff-sale / foreclosure list
    records letters --top 50           printable letters + mailing list for the best leads
"""
import csv
import html
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DEALFINDER_DATA") or os.path.join(HERE, "data")
RECORDS = os.path.join(DATA, "records.json")
LEADS = os.path.join(DATA, "leads.csv")
BUYER_LEADS = os.path.join(DATA, "buyer_leads.csv")
LETTERS = os.path.join(DATA, "letters.html")
MAILING = os.path.join(DATA, "mailing_list.csv")

ALIASES = {
    "address": ["prop_loc", "property location", "property address", "location", "site address", "address"],
    "town": ["mun_name", "municipality", "town", "city", "property city"],
    "county": ["county", "county_name"],
    "block": ["block"],
    "lot": ["lot"],
    "prop_class": ["prop_class", "property class", "class"],
    "owner": ["owner_name", "owner", "owner name", "grantee", "buyer", "grantee name"],
    "mail_street": ["st_address", "owner address", "mailing address", "owner street", "mail address"],
    "mail_city_state": ["city_state", "owner city state", "mailing city state", "owner city/state"],
    "mail_zip": ["zip_code", "owner zip", "mailing zip", "zip"],
    "assessed": ["net_value", "assessed value", "total assessment", "assessment", "net value"],
    "sale_date": ["sale_date", "deed date", "sale date", "recorded date", "date of sale"],
    "sale_price": ["sale_price", "sale price", "consideration", "price"],
    "year_built": ["yr_constr", "year built", "year_built"],
    "building": ["bldg_desc", "building description", "building"],
}

# Owner names that look like a company or investor rather than a person.
ENTITY = re.compile(r"\b(LLC|L\.L\.C|INC|CORP|CORPORATION|COMPANY|CO\b|HOLDINGS?|PROPERTIES|PROPERTY|REALTY|"
                    r"INVESTMENTS?|INVESTORS?|CAPITAL|GROUP|PARTNERS|LP|LLP|VENTURES|DEVELOPMENT|ENTERPRISES?|"
                    r"MANAGEMENT|HOMES|ASSETS|EQUITIES|REAL ESTATE)\b")
GOVT = re.compile(r"\b(CITY OF|TOWN OF|TOWNSHIP|BOROUGH|COUNTY OF|STATE OF|HOUSING AUTHORITY|BOARD OF|"
                  r"UNITED STATES|U S A|SECRETARY OF|FANNIE|FREDDIE|FEDERAL NATIONAL|FEDERAL HOME|HUD|CHURCH)\b")
BANK = re.compile(r"\b(BANK|MORTGAGE|LOAN|TRUST COMPANY|FINANCIAL|SAVINGS|CREDIT UNION|SERVICING|N\.?A\.?)\b")
ESTATE = re.compile(r"\b(ESTATE OF|EST OF|EXECUT|ADMINISTRAT|HEIRS|DECEASED|C/O)\b")
RESIDENTIAL = {"2", "1", "4C"}  # 2 = residential, 1 = vacant land, 4C = apartments


# ---------------------------------------------------------------- helpers
def num(v):
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def parse_date(v):
    v = str(v or "").strip()
    for cand in (v, v[:10], v.split(" ")[0]):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y%m%d", "%m%d%y", "%m%d%Y"):
            try:
                return datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
    return None


def norm_street(s):
    s = re.sub(r"[^A-Z0-9 ]", " ", str(s or "").upper())
    s = re.sub(r"\b(STREET)\b", "ST", s)
    s = re.sub(r"\b(AVENUE)\b", "AVE", s)
    s = re.sub(r"\b(ROAD)\b", "RD", s)
    s = re.sub(r"\b(DRIVE)\b", "DR", s)
    s = re.sub(r"\b(PLACE)\b", "PL", s)
    s = re.sub(r"\b(COURT)\b", "CT", s)
    s = re.sub(r"\b(TERRACE)\b", "TER", s)
    s = re.sub(r"\b(EAST)\b", "E", s)
    s = re.sub(r"\b(WEST)\b", "W", s)
    s = re.sub(r"\b(NORTH)\b", "N", s)
    s = re.sub(r"\b(SOUTH)\b", "S", s)
    return " ".join(s.split())


def tidy_town(t):
    """'ELIZABETH CITY' -> 'Elizabeth', 'ROSELLE BORO' -> 'Roselle'."""
    t = re.sub(r"\s+(CITY|BORO|BOROUGH|TWP|TOWNSHIP|TOWN|VILLAGE)$", "", str(t or "").upper().strip())
    return t.title()


def mail_state(city_state):
    m = re.search(r"\b([A-Z]{2})\s*\d{0,5}\s*$", str(city_state or "").upper().strip())
    return m.group(1) if m else ""


def load_records():
    if not os.path.exists(RECORDS):
        return []
    with open(RECORDS) as f:
        return json.load(f)


def property_key(r):
    return (norm_street(r["address"]), (r.get("town") or "").upper().strip())


# ---------------------------------------------------------------- import
def cmd_import(args):
    with open(args.csv, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        header = {h.lower().strip(): h for h in (reader.fieldnames or [])}
        colmap = {}
        for field, names in ALIASES.items():
            for n in names:
                if n in header:
                    colmap[field] = header[n]
                    break
        if "address" not in colmap or "owner" not in colmap:
            sys.exit(f"Need at least a property address and an owner column. Found: {reader.fieldnames}")
        rows = []
        for line in reader:
            r = {k: (line.get(v) or "").strip() for k, v in colmap.items()}
            if not r["address"]:
                continue
            if args.town and not r.get("town"):
                r["town"] = args.town
            r["town"] = tidy_town(r.get("town"))
            if args.county and not r.get("county"):
                r["county"] = args.county
            sd = parse_date(r.get("sale_date"))
            r["sale_date"] = sd.isoformat() if sd else ""
            r["sale_price"] = num(r.get("sale_price"))
            r["assessed"] = num(r.get("assessed"))
            r["source"] = os.path.basename(args.csv)
            rows.append(r)

    existing = load_records()
    seen = {(property_key(r), r.get("sale_date"), r.get("owner", "").upper()) for r in existing}
    added = [r for r in rows if (property_key(r), r.get("sale_date"), r.get("owner", "").upper()) not in seen]
    os.makedirs(DATA, exist_ok=True)
    with open(RECORDS, "w") as f:
        json.dump(existing + added, f)
    print(f"Mapped columns: {colmap}")
    print(f"Loaded {len(added)} new records ({len(existing) + len(added)} total).")


def cmd_flag(args):
    """Mark properties that appear on another list (sheriff sale, tax lien, code violations...)."""
    with open(args.csv, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        header = {h.lower().strip(): h for h in (reader.fieldnames or [])}
        col = next((header[n] for n in ALIASES["address"] if n in header), None)
        if not col:
            sys.exit(f"Need an address column. Found: {reader.fieldnames}")
        wanted = {norm_street(line[col]) for line in reader if line.get(col)}
    recs = load_records()
    hits = 0
    for r in recs:
        if norm_street(r["address"]) in wanted:
            tags = set(filter(None, (r.get("tags") or "").split(";")))
            if args.tag not in tags:
                tags.add(args.tag)
                hits += 1
            r["tags"] = ";".join(sorted(tags))
    with open(RECORDS, "w") as f:
        json.dump(recs, f)
    print(f"Tagged {hits} properties as '{args.tag}' ({len(wanted)} addresses on the list).")


# ---------------------------------------------------------------- leads
def latest_by_property(recs):
    by = {}
    for r in recs:
        k = property_key(r)
        cur = by.get(k)
        if not cur or (r.get("sale_date") or "") >= (cur.get("sale_date") or ""):
            merged = dict(cur or {}, **{kk: vv for kk, vv in r.items() if vv not in ("", 0, 0.0, None)})
            merged["tags"] = ";".join(sorted(set(filter(None, ((cur or {}).get("tags", "") + ";" +
                                                                r.get("tags", "")).split(";")))))
            by[k] = merged
        else:
            cur["tags"] = ";".join(sorted(set(filter(None, (cur.get("tags", "") + ";" + r.get("tags", "")).split(";")))))
    return by


def score_lead(r, today=None):
    today = today or date.today()
    flags, score = [], 0.0
    owner = (r.get("owner") or "").upper()
    if GOVT.search(owner) or BANK.search(owner):
        return None  # banks, towns and housing authorities don't sell to wholesalers by letter
    cls = (r.get("prop_class") or "2").upper().strip()
    if cls not in RESIDENTIAL:
        return None
    prop_st, mail_st = norm_street(r["address"]), norm_street(r.get("mail_street"))
    absentee = bool(mail_st) and mail_st != prop_st and not mail_st.startswith("PO BOX " + prop_st)
    state = mail_state(r.get("mail_city_state"))
    if absentee:
        flags.append("absentee owner")
        score += 25
    if state and state != "NJ":
        flags.append(f"owner lives in {state}")
        score += 15
    if ESTATE.search(owner):
        flags.append("estate / inherited")
        score += 30
    sd = parse_date(r.get("sale_date"))
    years = (today - sd).days / 365.25 if sd else None
    if years is not None:
        if years >= 30:
            flags.append(f"owned {int(years)} yrs")
            score += 25
        elif years >= 15:
            flags.append(f"owned {int(years)} yrs")
            score += 15
    elif r.get("sale_date") == "":
        score += 5  # no recorded sale in the data usually means owned a very long time
    if sd and num(r.get("sale_price")) <= 100 and years is not None:
        flags.append("$0-$100 transfer (family / inherited)")
        score += 10
    if ENTITY.search(owner) and absentee:
        flags.append("landlord / LLC")
        score += 5  # tired landlords sell, but they negotiate harder
    if cls == "1":
        flags.append("vacant land")
    tags = [t for t in (r.get("tags") or "").split(";") if t]
    for t in tags:
        flags.append(t)
        score += 35
    if not flags:
        return None
    return round(score, 1), flags, years


LEAD_COLUMNS = ["score", "address", "town", "county", "owner", "mail_street", "mail_city_state", "mail_zip",
                "flags", "years_owned", "last_sale_date", "last_sale_price", "assessed", "prop_class",
                "year_built", "block", "lot"]


def build_leads(top=None, town=None):
    out = []
    for r in latest_by_property(load_records()).values():
        if town and town.upper() not in (r.get("town") or "").upper():
            continue
        s = score_lead(r)
        if not s:
            continue
        score, flags, years = s
        out.append({
            "score": score, "address": r["address"], "town": r.get("town", ""), "county": r.get("county", ""),
            "owner": r.get("owner", ""), "mail_street": r.get("mail_street", ""),
            "mail_city_state": r.get("mail_city_state", ""), "mail_zip": r.get("mail_zip", ""),
            "flags": "; ".join(flags), "years_owned": round(years, 1) if years is not None else "",
            "last_sale_date": r.get("sale_date", ""), "last_sale_price": int(num(r.get("sale_price"))) or "",
            "assessed": int(num(r.get("assessed"))) or "", "prop_class": r.get("prop_class", ""),
            "year_built": r.get("year_built", ""), "block": r.get("block", ""), "lot": r.get("lot", ""),
        })
    out.sort(key=lambda d: d["score"], reverse=True)
    return out[:top] if top else out


def cmd_leads(args):
    leads = build_leads(args.top, args.town)
    if not leads:
        sys.exit("No leads yet. Import a county tax list first: records import <file.csv>")
    os.makedirs(DATA, exist_ok=True)
    with open(LEADS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEAD_COLUMNS)
        w.writeheader()
        w.writerows(leads)
    print(f"{len(leads)} off-market leads -> {LEADS}\n")
    for i, d in enumerate(leads[:15], 1):
        print(f"{i:>3} {d['score']:>5}  {d['address']}, {d['town']}  |  {d['owner']}  |  {d['flags']}")


# ---------------------------------------------------------------- cash buyers
def find_buyers(months=24, min_buys=2):
    """Companies that bought several properties recently = active cash investors."""
    cutoff = date.today().toordinal() - int(months * 30.4)
    buys = defaultdict(list)
    for r in load_records():
        owner = (r.get("owner") or "").upper().strip()
        sd = parse_date(r.get("sale_date"))
        if not owner or not sd or sd.toordinal() < cutoff or num(r.get("sale_price")) <= 100:
            continue
        if not ENTITY.search(owner) or GOVT.search(owner) or BANK.search(owner):
            continue
        buys[re.sub(r"[^A-Z0-9 ]", "", owner)].append(r)
    # flips: same property sold twice within 12 months
    by_prop = defaultdict(list)
    for r in load_records():
        sd = parse_date(r.get("sale_date"))
        if sd and num(r.get("sale_price")) > 100:
            by_prop[property_key(r)].append((sd, r))
    flippers = defaultdict(int)
    for sales in by_prop.values():
        sales.sort(key=lambda x: x[0])
        for (d1, r1), (d2, _) in zip(sales, sales[1:]):
            if (d2 - d1).days <= 365:
                flippers[re.sub(r"[^A-Z0-9 ]", "", (r1.get("owner") or "").upper().strip())] += 1
    out = []
    for name, rows in buys.items():
        if len(rows) < min_buys and not flippers.get(name):
            continue
        rows.sort(key=lambda r: r.get("sale_date", ""), reverse=True)
        prices = [num(r["sale_price"]) for r in rows]
        out.append({
            "name": rows[0]["owner"], "purchases": len(rows), "flips": flippers.get(name, 0),
            "towns": ", ".join(sorted({tidy_town(r.get("town")) for r in rows if r.get("town")})),
            "min_price": int(min(prices)), "max_price": int(max(prices)),
            "last_purchase": rows[0].get("sale_date", ""),
            "mail_street": rows[0].get("mail_street", ""), "mail_city_state": rows[0].get("mail_city_state", ""),
            "mail_zip": rows[0].get("mail_zip", ""),
            "example": f"{rows[0]['address'].title()}, {tidy_town(rows[0].get('town'))}",
        })
    out.sort(key=lambda b: (b["purchases"] + 2 * b["flips"]), reverse=True)
    return out


def cmd_buyers(args):
    buyers = find_buyers(args.months, args.min_buys)
    if not buyers:
        sys.exit("No repeat buyers found. Import sales records (with sale dates and prices) for your area.")
    with open(BUYER_LEADS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(buyers[0].keys()))
        w.writeheader()
        w.writerows(buyers)
    print(f"{len(buyers)} likely cash buyers -> {BUYER_LEADS}\n")
    for b in buyers[:20]:
        print(f"  {b['name']:<40} {b['purchases']} buys, {b['flips']} flips, ${b['min_price']:,}-${b['max_price']:,}  {b['towns']}")


# ---------------------------------------------------------------- letters
SELLER_LETTER = """<p>Hi {first},</p>
<p>My name is {you}. I'm a local real estate investor, and I'm reaching out about your property at
<b>{street}</b> in {town}.</p>
<p>If you've ever thought about selling, I may be able to help. I work with cash buyers, so the
house can be sold <b>as-is</b>: no repairs, no cleaning, no showings, and no agent commissions. You pick the
closing date.</p>
<p>To be upfront: I'm a wholesaler. I put a property under contract and then either buy it or
assign the contract to one of the investors I work with. Either way, you get a cash offer and you're never
obligated to accept it.</p>
<p>If you're curious what we could offer, call or text me at <b>{phone}</b> or email
<b>{email}</b>. If you're not interested, no worries. Just let me know and I won't contact you again.</p>
<p>Thanks,<br>{you}</p>"""

BUYER_LETTER = """<p>Hi,</p>
<p>My name is {you}. I find off-market houses in {towns} and sell the contracts to cash investors.
I saw that {name} has been buying in the area, and I'd like to send you deals before anyone else
sees them.</p>
<p>If you'd like to be on my buyers list, text or call me at <b>{phone}</b> or email <b>{email}</b>
with the towns you buy in, your price range, and how much rehab you'll take on.</p>
<p>Thanks,<br>{you}</p>"""


def company_name(n):
    t = str(n or "").title()
    return re.sub(r"\b(Llc|Lp|Llp|Inc|Na)\b", lambda m: m.group(1).upper() if m.group(1) != "Inc" else "Inc", t)


def first_name(owner):
    o = (owner or "").upper()
    if ENTITY.search(o) or ESTATE.search(o) or "&" in o and len(o) > 30:
        return "there"
    parts = re.split(r"[ ,&]+", o.strip())
    # NJ tax lists usually write LAST, FIRST
    if "," in o and len(parts) > 1:
        return parts[1].title()
    return parts[0].title() if parts and parts[0] else "there"


def render_letters(items, kind, cfg):
    you = cfg.get("your_name", "")
    ret = f"{you}<br>{html.escape(cfg.get('mailing_address', ''))}"
    pages = []
    for it in items:
        to = "<br>".join(html.escape(x) for x in [
            it.get("owner") or it.get("name"), it.get("mail_street"),
            f"{it.get('mail_city_state', '')} {it.get('mail_zip', '')}".strip()] if x)
        if kind == "seller":
            body = SELLER_LETTER.format(first=html.escape(first_name(it.get("owner"))), you=html.escape(you),
                                        street=html.escape(it["address"].title()),
                                        town=html.escape(tidy_town(it.get("town")) or "your town"),
                                        phone=html.escape(cfg.get("your_phone") or "[your phone]"),
                                        email=html.escape(cfg.get("your_email", "")))
        else:
            body = BUYER_LETTER.format(you=html.escape(you), name=html.escape(company_name(it["name"])),
                                       towns=html.escape(it.get("towns") or "your area"),
                                       phone=html.escape(cfg.get("your_phone") or "[your phone]"),
                                       email=html.escape(cfg.get("your_email", "")))
        pages.append(f'<section class="page"><div class="ret">{ret}</div><div class="to">{to}</div>'
                     f'<div class="date">{date.today().strftime("%B %d, %Y").replace(" 0", " ")}</div>{body}</section>')
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Letters</title><style>"
            "body{margin:0;font:12pt/1.5 Georgia,serif;color:#111;background:#fff}"
            ".page{width:7.5in;min-height:9.5in;margin:0 auto;padding:.5in;page-break-after:always;box-sizing:border-box}"
            ".ret{font-size:10pt}.to{margin:.6in 0 .3in;font-size:11pt}.date{margin-bottom:.25in}"
            "@media screen{.page{border-bottom:1px dashed #ccc}}"
            "</style></head><body>" + "".join(pages) + "</body></html>")


def write_letters(kind, top, cfg, town=None):
    items = build_leads(None, town) if kind == "seller" else find_buyers()
    items = [i for i in items if i.get("mail_street")][:top]
    if not items:
        sys.exit("Nobody to mail yet. Import records first.")
    os.makedirs(DATA, exist_ok=True)
    with open(LETTERS, "w") as f:
        f.write(render_letters(items, kind, cfg))
    with open(MAILING, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "address", "city_state", "zip", "property"])
        for it in items:
            w.writerow([it.get("owner") or it.get("name"), it.get("mail_street"), it.get("mail_city_state"),
                        it.get("mail_zip"), it.get("address", it.get("example", ""))])
    return len(items)


def cmd_letters(args, cfg):
    n = write_letters(args.kind, args.top, cfg, args.town)
    print(f"{n} letters -> {LETTERS} (open it and print; one letter per page)")
    print(f"Mailing list -> {MAILING} (for envelopes/labels, or upload to a mail service like Click2Mail)")
