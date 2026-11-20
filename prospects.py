"""Prospects: read a CSV of companies, tier them, check the mail domain, write a prospect list.
Standard library only. Run through score.py: `score.py prospects companies.csv`."""
import csv, os, re, subprocess, sys
from collections import Counter

from score import CFG

DOMAIN_RE = re.compile(r"^([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")   # production's EMAIL_RE, domain part only

# One row per rule: (what it checks, points, test on a normalised prospect). Floors and thresholds are in config.
RULES = [
    ("revenue at or above revenue_high",  30, lambda r: r["revenue"] >= CFG["revenue_high"]),
    ("revenue at or above revenue_mid",   15, lambda r: CFG["revenue_mid"] <= r["revenue"] < CFG["revenue_high"]),
    ("team size in the sweet spot",       20, lambda r: CFG["team_min"] <= r["employees"] <= CFG["team_max"]),
    ("team larger than the sweet spot",   10, lambda r: r["employees"] > CFG["team_max"]),
    ("team smaller than the sweet spot",   5, lambda r: 0 < r["employees"] < CFG["team_min"]),
    ("mail domain accepts mail",          20, lambda r: r["mail"] in ("ok", "unchecked")),
]


def has_mx(domain):
    """Same check as production: a domain with an MX or an A record can receive mail. None means dig failed, not checked."""
    # ponytail: `dig` beats a DNS dependency. A null MX ("0 .") counts as mail, as in production.
    for rr in ("MX", "A"):
        try:
            out = subprocess.run(["dig", "+short", rr, domain], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.strip():
            return True
    return False


def mail_of(domain, check=has_mx):
    if not DOMAIN_RE.match(domain):
        return "bad domain"
    ok = check(domain)
    return "unchecked" if ok is None else "ok" if ok else "no mail"


def tier(r):
    """r has name, domain, country, employees, revenue, mail. Returns r plus score, tier, status and why."""
    hit = [x for x in RULES if x[2](r)]
    total = sum(x[1] for x in hit)
    excluded = r["country"].lower() in {c.lower() for c in CFG["exclude_countries"]} or r["domain"].lower().endswith(".de")
    f = CFG["prospect_floors"]
    return {**r, "score": total, "tier": "A" if total >= f["A"] else "B" if total >= f["B"] else "C",
            "status": "excluded" if excluded else "invalid" if r["mail"] in ("no mail", "bad domain") else "new",
            "why": "; ".join(f"{x[0]} +{x[1]}" for x in hit)}


def read(path):
    """Read the input CSV. name and domain are required, country, employees and revenue are optional."""
    with open(path, newline="") as f:
        rd = csv.DictReader(f)
        if not {"name", "domain"} <= set(rd.fieldnames or []):
            sys.exit(f"{path}: the CSV needs name and domain columns, found {rd.fieldnames}")
        rows = []
        for i, d in enumerate(rd, 2):
            try:
                num = lambda k: int(float(d.get(k) or 0))
                rows.append({"name": d["name"].strip(), "domain": d["domain"].strip().lower(),
                             "country": (d.get("country") or "").strip(), "employees": num("employees"), "revenue": num("revenue")})
            except ValueError as e:
                sys.exit(f"{path} line {i}: {e}")
    return rows


def cmd_prospects(path, out):
    rows = [tier({**r, "mail": mail_of(r["domain"])}) for r in read(path)]
    rows.sort(key=lambda r: (-r["score"], r["name"]))
    os.makedirs(out, exist_ok=True)
    cols = ["name", "domain", "country", "employees", "revenue", "mail", "score", "tier", "status", "why"]
    with open(f"{out}/prospects.csv", "w", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} prospects, tiers {dict(sorted(Counter(r['tier'] for r in rows).items()))}, status {dict(Counter(r['status'] for r in rows))}")
    for r in rows[:10]:
        print(f"{r['tier']} {r['score']:>3}  {r['name']} ({r['domain']}, mail {r['mail']}, {r['status']})")
    print(f"wrote {out}/prospects.csv")


def selftest():
    """Invented fixtures, no network. Returns the number of checks."""
    base = {"name": "Sample Games", "domain": "example.com", "country": "", "employees": 0, "revenue": 0, "mail": "ok"}
    t = lambda **kw: tier({**base, **kw})
    assert (t()["score"], t()["tier"], t()["status"]) == (20, "C", "new")                      # mail alone
    top = t(revenue=2_000_000, employees=12)
    assert (top["score"], top["tier"]) == (70, "A"), top                                       # 30 + 20 + 20
    assert (t(revenue=100_000)["score"], t(revenue=99_999)["score"]) == (35, 20)                # mid-revenue boundary
    assert (t(revenue=999_999)["tier"], t(revenue=1_000_000)["tier"]) == ("B", "A")             # 35 is B, 50 is exactly the A floor
    assert (t(employees=4)["score"], t(employees=5)["score"]) == (25, 40)                       # sweet spot starts at 5
    assert (t(employees=50)["score"], t(employees=51)["score"]) == (40, 30)                     # and ends at 50
    assert t(mail="no mail")["status"] == "invalid" and t(mail="no mail")["score"] == 0
    assert t(mail="unchecked")["status"] == "new" and t(mail="unchecked")["score"] == 20        # dig down: not thrown away
    assert t(domain="studio.de")["status"] == "excluded"
    assert mail_of("not a domain") == "bad domain" and mail_of("a.example.com", lambda d: False) == "no mail"
    assert mail_of("example.org", lambda d: None) == "unchecked" and mail_of("example.org", lambda d: True) == "ok"
    return 11
