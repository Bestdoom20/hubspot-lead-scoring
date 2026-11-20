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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()
    contacts, companies, stages = load()
    print(f"{len(contacts)} contacts, {len(companies)} companies, {len(stages)} companies with a deal")


if __name__ == "__main__":
    main()
