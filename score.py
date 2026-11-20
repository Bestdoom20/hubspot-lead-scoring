#!/usr/bin/env python3
"""Score HubSpot contacts for fit and engagement. Dry run by default. Standard library only."""
import argparse, csv, json, os, re, sys, urllib.error, urllib.request
from collections import Counter
from datetime import date, datetime

API = "https://api.hubapi.com"
CONTACT_PROPS = ["firstname", "lastname", "jobtitle", "associatedcompanyid", "omvion_role",
                 "omvion_outreach_status", "omvion_conversed", "omvion_last_touch"]
COMPANY_PROPS = ["name", "omvion_people_replied"]


def api(method, path, body=None):
    """One HubSpot call. Any HTTP or network failure stops the run with the status and body."""
    req = urllib.request.Request(API + path, method=method, headers={
        "Authorization": "Bearer " + os.environ["HUBSPOT_TOKEN"], "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HubSpot {method} {path.split('?')[0]} failed: HTTP {e.code} {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"HubSpot {method} {path.split('?')[0]} failed: {e.reason}")


def fetch_all(obj, props, extra=""):
    """Page through /crm/v3/objects/<obj> (read-only GET, 100 per page)."""
    out, after = [], None
    while True:
        d = api("GET", f"/crm/v3/objects/{obj}?limit=100&properties={','.join(props)}{extra}"
                       + (f"&after={after}" if after else ""))
        out += d["results"]
        after = d.get("paging", {}).get("next", {}).get("after")
        if not after:
            return out


def load():
    """Return (contacts, companies by id, best deal stage label per company id)."""
    contacts = fetch_all("contacts", CONTACT_PROPS)
    companies = {c["id"]: c["properties"] for c in fetch_all("companies", COMPANY_PROPS)}
    stages = {s["id"]: (s["displayOrder"], s["label"])
              for p in api("GET", "/crm/v3/pipelines/deals")["results"] for s in p["stages"]}
    best = {}  # company id -> (order, label) of its furthest deal
    for d in fetch_all("deals", ["dealstage"], "&associations=companies"):
        for a in d.get("associations", {}).get("companies", {}).get("results", []):
            s = stages.get(d["properties"]["dealstage"])
            if s and s > best.get(a["id"], (-1, "")):
                best[a["id"]] = s
    return contacts, companies, {k: v[1] for k, v in best.items()}


SENIOR = re.compile(r"\b(chief|ceo|cto|coo|cpo|vp|vice president|president|head|director|founder|owner|partner)\b", re.I)
MID = re.compile(r"\b(lead|principal|senior|sr|manager|producer)\b", re.I)

# The scoring model. One row per rule: (group, what it checks, points, test on a normalised contact).
# Only fields that carry data in the portal are used. Company type, industry and size are empty there.
RULES = [
    ("fit", "role: decision_maker",             30, lambda c: c["role"] == "decision_maker"),
    ("fit", "role: champion",                   20, lambda c: c["role"] == "champion"),
    ("fit", "role: influencer",                 10, lambda c: c["role"] == "influencer"),
    ("fit", "role: gatekeeper",                  5, lambda c: c["role"] == "gatekeeper"),
    ("fit", "title is senior (chief, VP, head, director, founder)", 15, lambda c: bool(SENIOR.search(c["title"]))),
    ("fit", "title is mid (lead, principal, senior, manager)",       8, lambda c: not SENIOR.search(c["title"]) and bool(MID.search(c["title"]))),
    ("engagement", "outreach: replied",          30, lambda c: c["status"] == "replied"),
    ("engagement", "outreach: messaged",         15, lambda c: c["status"] == "messaged"),
    ("engagement", "outreach: requested",         8, lambda c: c["status"] == "requested"),
    ("engagement", "conversation held",          10, lambda c: c["conversed"]),
    ("engagement", "touched in last 7 days",     10, lambda c: c["days"] is not None and c["days"] <= 7),
    ("engagement", "touched 8 to 21 days ago",    5, lambda c: c["days"] is not None and 8 <= c["days"] <= 21),
    ("engagement", "deal stage: Pilot",          25, lambda c: c["stage"] == "Pilot"),
    ("engagement", "deal stage: Call booked",    20, lambda c: c["stage"] == "Call booked"),
    ("engagement", "deal stage: Decision maker reached", 15, lambda c: c["stage"] == "Decision maker reached"),
    ("engagement", "deal stage: Warming (replied)",      10, lambda c: c["stage"] == "Warming (replied)"),
    ("engagement", "deal stage: Champion in",     8, lambda c: c["stage"] == "Champion in"),
    ("engagement", "deal stage: Connecting",      3, lambda c: c["stage"] == "Connecting"),
    ("engagement", "someone else at the company replied", 5, lambda c: c["colleague_replied"]),
]
TIERS = [("A", 60), ("B", 35), ("C", 0)]  # minimum score for each tier, first match wins

# First matching row gives the suggested next step.
NEXT_STEPS = [
    (lambda c: c["status"] == "replied",                       "Reply and propose a call"),
    (lambda c: c["status"] == "messaged" and (c["days"] or 99) > 14, "Send a follow-up, the last message is over 2 weeks old"),
    (lambda c: c["status"] == "messaged",                      "Wait for a reply, follow up in 7 days"),
    (lambda c: c["status"] == "requested" and (c["days"] or 99) > 21, "Connection request is stale, re-send or find a second contact"),
    (lambda c: c["status"] == "requested",                     "Wait for the connection to be accepted"),
    (lambda c: c["role"] in ("decision_maker", "champion"),    "Send a connection request"),
    (lambda c: True,                                           "Research the person or park"),
]


def normalise(raw, companies, stages, today):
    """HubSpot contact record -> the flat dict the rules read."""
    p = raw["properties"]
    co = companies.get(p.get("associatedcompanyid") or "", {})
    touch = p.get("omvion_last_touch")
    return {
        "id": raw["id"], "name": f"{p.get('firstname') or ''} {p.get('lastname') or ''}".strip(),
        "company": co.get("name") or "", "title": p.get("jobtitle") or "",
        "role": p.get("omvion_role") or "unknown", "status": p.get("omvion_outreach_status") or "",
        "conversed": p.get("omvion_conversed") == "true",
        "days": (today - datetime.strptime(touch, "%Y-%m-%d").date()).days if touch else None,
        "stage": stages.get(p.get("associatedcompanyid") or ""),
        "colleague_replied": int(co.get("omvion_people_replied") or 0) > 0,
    }


def score(c):
    """Return the contact with fit, engagement, score, tier, next step and the rules that fired."""
    hit = [r for r in RULES if r[3](c)]
    fit = sum(r[2] for r in hit if r[0] == "fit")
    eng = sum(r[2] for r in hit if r[0] == "engagement")
    total = fit + eng
    return {**c, "fit": fit, "engagement": eng, "score": total,
            "tier": next(t for t, floor in TIERS if total >= floor),
            "next_step": next(text for test, text in NEXT_STEPS if test(c)),
            "why": "; ".join(f"{r[1]} +{r[2]}" for r in hit)}


def selftest():
    """Invented fixtures, no network. Fails if the rules, tiers or next steps break."""
    base = dict(id="0", name="Sample Person", company="Sample Co", title="", role="unknown",
                status="", conversed=False, days=None, stage=None, colleague_replied=False)
    hot = score({**base, "title": "VP of Publishing", "role": "decision_maker", "status": "replied",
                 "conversed": True, "days": 2, "stage": "Decision maker reached"})
    assert (hot["fit"], hot["engagement"], hot["tier"]) == (45, 65, "A"), hot
    assert hot["next_step"] == "Reply and propose a call"
    cold = score(base)
    assert (cold["score"], cold["tier"]) == (0, "C"), cold
    assert cold["next_step"] == "Research the person or park"
    mid = score({**base, "title": "Senior Producer", "role": "champion", "status": "requested", "days": 10})
    assert (mid["score"], mid["tier"]) == (41, "B"), mid          # 20 + 8 + 8 + 5
    assert score({**base, "title": "Head of Studio Lead"})["fit"] == 15   # senior wins, not both
    assert score({**base, "days": 7})["engagement"] == 10 and score({**base, "days": 8})["engagement"] == 5
    assert score({**base, "days": 22})["engagement"] == 0              # recency boundaries
    assert score({**base, "status": "requested", "days": 30})["next_step"].startswith("Connection request is stale")
    assert score({**base, "role": "gatekeeper", "colleague_replied": True})["score"] == 10
    raw = {"id": "9", "properties": {"firstname": "A", "associatedcompanyid": "5", "omvion_conversed": "true",
                                     "omvion_last_touch": "2026-11-10", "omvion_role": "champion"}}
    n = normalise(raw, {"5": {"name": "X", "omvion_people_replied": "1"}}, {"5": "Pilot"}, date(2026, 11, 20))
    assert (n["days"], n["stage"], n["conversed"], n["colleague_replied"]) == (10, "Pilot", True, True), n
    print("selftest ok: 11 checks passed")


WRITE_PROPS = {"lead_score": "score", "lead_tier": "tier", "lead_next_step": "next_step"}


def write_back(rows):
    """Push score, tier and next step to contact properties. The properties must already exist."""
    have = {p["name"] for p in api("GET", "/crm/v3/properties/contacts")["results"]}
    missing = sorted(set(WRITE_PROPS) - have)
    if missing:
        sys.exit(f"--write needs these contact properties to exist first (this tool never creates them): {', '.join(missing)}")
    for i in range(0, len(rows), 100):  # batch update takes at most 100 records
        api("POST", "/crm/v3/objects/contacts/batch/update", {"inputs": [
            {"id": r["id"], "properties": {p: str(r[k]) for p, k in WRITE_PROPS.items()}} for r in rows[i:i + 100]]})
    print(f"wrote {', '.join(WRITE_PROPS)} on {len(rows)} contacts")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true", help="run the offline self-check and exit")
    ap.add_argument("--write", action="store_true", help="also write score, tier and next step to HubSpot (default is a dry run)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    contacts, companies, stages = load()
    rows = sorted((score(normalise(r, companies, stages, date.today())) for r in contacts),
                  key=lambda r: -r["score"])
    print("tiers:", dict(sorted(Counter(r["tier"] for r in rows).items())))
    for r in rows[:10]:
        print(f"{r['tier']} {r['score']:>3}  {r['name']} ({r['company']})  ->  {r['next_step']}")
    report(rows, args.out)
    if args.write:
        write_back(rows)


def report(rows, out):
    """Write the full ranking as CSV and a short pipeline summary as markdown."""
    os.makedirs(out, exist_ok=True)
    cols = ["id", "name", "company", "title", "role", "status", "stage", "fit", "engagement", "score", "tier", "next_step", "why"]
    with open(f"{out}/scores.csv", "w", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tiers = Counter(r["tier"] for r in rows)
    by_stage = Counter((r["stage"] or "no deal", r["tier"]) for r in rows)
    lines = [f"# Pipeline report, {date.today()}", "", f"{len(rows)} contacts scored.", "",
             "| Tier | Contacts |", "|---|---|"] + [f"| {t} | {tiers[t]} |" for t, _ in TIERS]
    lines += ["", "## By deal stage", "", "| Stage | A | B | C |", "|---|---|---|---|"]
    for s in sorted({k[0] for k in by_stage}):
        lines.append(f"| {s} | " + " | ".join(str(by_stage[(s, t)]) for t, _ in TIERS) + " |")
    lines += ["", "## Next steps", "", "| Step | Contacts |", "|---|---|"]
    lines += [f"| {s} | {n} |" for s, n in Counter(r["next_step"] for r in rows).most_common()]
    lines += ["", "## Top 10", "", "| Tier | Score | Name | Company | Next step |", "|---|---|---|---|---|"]
    lines += [f"| {r['tier']} | {r['score']} | {r['name']} | {r['company']} | {r['next_step']} |" for r in rows[:10]]
    with open(f"{out}/pipeline-report.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {out}/scores.csv and {out}/pipeline-report.md")


if __name__ == "__main__":
    main()
