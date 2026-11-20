"""Account cycle: stage, cycle day and a multi-channel touch plan per account, and today's working list.
Read-only. Standard library only. Run through score.py: `score.py cycle`, `score.py today`."""
import csv, os, re
from collections import Counter
from datetime import date, timedelta

import score
from score import CFG

# Mirrors production (hubspot_sync.py): same stage ladder, same roles, same outreach statuses.
STAGES = ["researching", "connecting", "champion_in", "warming", "dm_reached", "call_booked", "pilot", "won", "parked"]
LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d) (\w+)")
LOG_CHANNEL = {"connect": "linkedin_request", "message": "linkedin_message", "email": "email", "call": "phone"}
ROLE_RANK = {"decision_maker": 0, "champion": 1, "influencer": 2, "gatekeeper": 3, "unknown": 4}
STOPPED = ("call_booked", "pilot", "won", "parked")   # outreach stops, nothing is due


def stage_of(people, day, booked=False):
    """Ported unchanged from production stage_of. people: dicts with name, role, out. Returns (stage, next_action)."""
    n = lambda *s: [p for p in people if p['out'] in s]
    dm = [p for p in people if p['role'] == 'decision_maker']
    dm_in = [p for p in dm if p['out'] in ('connected', 'messaged', 'replied', 'call_booked')]
    other_in = [p for p in n('connected', 'messaged', 'replied', 'call_booked') if p['role'] != 'decision_maker']
    replied = n('replied')
    if booked or n('call_booked'): return 'call_booked', 'Call booked: stop outreach, prep notes'
    if dm_in:
        p = dm_in[0]
        return 'dm_reached', f'Decision maker {p["name"]} is in: ' + {'replied': 'reply to them', 'messaged': 'message sent, wait for a reply (follow up day 7)'}.get(p['out'], 'send first message now (draft, you approve)')
    if replied:
        p = replied[0]
        return 'warming', f'{p["name"]} replied: draft follow-up, ask for an intro to the decision maker' + ('' if dm else '; no decision maker on the map yet, find one')
    if other_in:
        p = [x for x in other_in if x['out'] == 'connected'] or other_in
        p = p[0]
        extra = '' if any(d['out'] != 'found' for d in dm) else '; also request the decision maker'
        return 'champion_in', f'Send {p["name"]} the first message (accepted){extra}'
    if n('requested'):
        pend = len(n('requested'))
        if day >= 7: return 'connecting', f'Day {day}, {pend} requests, no accepts: add two more people to the map'
        return 'connecting', f'Wait for accepts ({pend} pending). Email 1 on day 1, email 2 on day 7'
    return 'researching', 'Map the studio: need a decision maker plus two others, then send requests'


def accounts(contacts, companies, stages, today):
    """One dict per company that has cycle contacts or a stage already written. Contacts without an outreach status are not cycle contacts."""
    by_co = {}
    for raw in contacts:
        p = raw["properties"]
        cid = p.get("associatedcompanyid")
        if not p.get("omvion_outreach_status") or cid not in companies:
            continue
        sc = score.score(score.normalise(raw, companies, stages, today))
        log = [(m[1], LOG_CHANNEL[m[2]]) for m in map(LOG_LINE.match, (p.get("omvion_touch_log") or "").split("\n"))
               if m and m[2] in LOG_CHANNEL]
        by_co.setdefault(cid, []).append({"name": sc["name"], "role": sc["role"], "out": p["omvion_outreach_status"],
                                          "last": p.get("omvion_last_touch") or "", "log": log, "score": sc["score"],
                                          "email": p.get("email") or "", "phone": p.get("phone") or ""})
    out = []
    for cid, co in companies.items():
        people = by_co.get(cid, [])
        if not people and not co.get("omvion_cycle_stage"):
            continue
        start = (co.get("omvion_cycle_start") or "")[:10]
        day = (today - date.fromisoformat(start)).days if start else 0
        stage, nxt = stage_of(people, day)
        best = max((p["score"] for p in people), default=0)
        out.append({"id": cid, "name": co.get("name") or "", "people": people, "start": start, "day": day,
                    "stage": stage, "next": nxt, "prod_stage": co.get("omvion_cycle_stage") or "",
                    "prod_next": co.get("omvion_next_action") or "", "best": best,
                    "priority": best + CFG["stage_points"].get(stage, 0)})
    return out


def plan(a):
    """The touch plan for one cycle: rows of day, channel, person, action, source, status.
    status is done, due (ready and its day has come), waiting (not possible yet) or later (day not reached).
    Rows marked 'production' follow hubspot_sync.py. Rows marked 'extended' are the extended schedule, not yet in production."""
    P = sorted((p for p in a["people"] if p["out"] != "declined"), key=lambda p: (ROLE_RANK.get(p["role"], 4), -p["score"]))
    n = lambda p, ch: sum(1 for _, c in p["log"] if c == ch)
    rows = []

    def row(day, ch, p, action, source, done=False, ready=True):
        status = "done" if done else "waiting" if not ready else "due" if day <= a["day"] else "later"
        rows.append({"day": day, "channel": ch, "person": p, "role": p["role"] if p else "account",
                     "action": action, "source": source, "status": status})

    if a["prod_stage"] in STOPPED or a["stage"] in STOPPED:
        return rows
    replied = [p for p in P if p["out"] == "replied"]
    if replied:   # something happened: reply to it, stop the cold touches
        for p in replied:
            row(1, "linkedin_message", p, f'Reply to {p["name"]}' + ("" if p["role"] == "decision_maker" else ", ask for an intro to the decision maker"), "production")
        return rows
    team = P[:CFG["people_target"]]
    accepted = [p for p in P if p["out"] in ("connected", "messaged") and p not in team]
    for p in team:
        row(1, "linkedin_request", p, f'Send {p["name"]} a connection request', "production",
            done=p["out"] != "found" or n(p, "linkedin_request") > 0)
    for p in team + accepted:
        row(1, "linkedin_message", p, f'Send {p["name"]} the first message', "production",
            done=p["out"] == "messaged" or n(p, "linkedin_message") > 0, ready=p["out"] in ("connected", "messaged"))
        row(CFG["followup_day"], "linkedin_message", p, f'Follow up with {p["name"]}', "production",
            done=n(p, "linkedin_message") > 1, ready=p["out"] == "messaged")
    em = next((p for p in P if p["email"]), None)   # email history is not in HubSpot, only the touch log says what was sent
    if em:
        for i, d in enumerate(CFG["email_days"]):
            row(d, "email", em, f'Email {i + 1} to {em["name"]}', "production", done=n(em, "email") > i)
    if a["stage"] == "researching":
        row(1, "research", None, a["next"], "production")
    row(CFG["add_people_day"], "research", None, "No accepts: add two more people to the map", "production",
        done=len(P) >= CFG["people_target"] + 2, ready=a["stage"] == "connecting")
    # extended schedule, not yet in production
    ph = next((p for p in P if p["phone"]), None)
    if ph:
        row(CFG["phone_day"], "phone", ph, f'Call {ph["name"]}', "extended", done=n(ph, "phone") > 0)
    if em:
        row(CFG["last_touch_day"], "email", em, f'Last-touch email to {em["name"]}', "extended", done=n(em, "email") > len(CFG["email_days"]))
    other = next((p for p in sorted(accepted + team, key=lambda p: p["out"] != "connected")
                  if p["role"] != "decision_maker" and p["out"] in ("connected", "messaged")), None)
    if other:
        end = (date.fromisoformat(a["start"]) + timedelta(days=CFG["last_touch_day"])).isoformat() if a["start"] else ""
        row(CFG["last_touch_day"], "linkedin_message", other, f'Last touch through another employee: message {other["name"]}', "extended",
            done=bool(end) and other["last"] >= end)
    reentry = (date.fromisoformat(a["start"]) + timedelta(days=CFG["cycle_days"] + CFG["reentry_days"])).isoformat() if a["start"] else "unknown"
    row(CFG["cycle_days"], "park", None, f"Park the account, nothing happened. Re-enter on {reentry}", "extended",
        ready=a["stage"] in CFG["park_stages"])
    return rows


def due_today(a):
    """Touches due today for one account. Past the cycle length with nothing happened, the only thing due is the park."""
    rows = plan(a)
    if a["day"] >= CFG["cycle_days"] and a["stage"] in CFG["park_stages"]:
        return [r for r in rows if r["channel"] == "park"]
    return [r for r in rows if r["status"] == "due" and r["channel"] != "park"]


def rank(accts):
    """Every due touch, best account first, then channel order, then person role."""
    order = {c: i for i, c in enumerate(CFG["channel_order"])}
    lines = [(a, r) for a in accts for r in due_today(a)]
    return sorted(lines, key=lambda ar: (-ar[0]["priority"], ar[0]["name"], order.get(ar[1]["channel"], 99), ROLE_RANK.get(ar[1]["role"], 5)))


def why(r):
    return f'{r["source"]} schedule, day {r["day"]}' + (" (extended schedule, not yet in production)" if r["source"] == "extended" else "")


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def cmd_cycle(out):
    accts = accounts(*score.load(), date.today())
    os.makedirs(out, exist_ok=True)
    cmp = [a for a in accts if a["prod_stage"]]
    agree = sum(a["stage"] == a["prod_stage"] for a in cmp)
    nxt = sum(a["next"] == a["prod_next"] for a in cmp)
    print(f"accounts: {len(accts)}, per stage: {dict(Counter(a['stage'] for a in accts))}")
    print(f"stage agreement with production: {agree} of {len(cmp)}; next action text agreement: {nxt} of {len(cmp)}")
    print("mismatches (production -> computed):", dict(Counter((a["prod_stage"], a["stage"]) for a in cmp if a["stage"] != a["prod_stage"])))
    write_csv(f"{out}/cycle.csv", ["account", "stage", "production_stage", "day", "start", "people", "priority", "next_action"],
              [[a["name"], a["stage"], a["prod_stage"], a["day"], a["start"], len(a["people"]), a["priority"], a["next"]] for a in accts])
    write_csv(f"{out}/plan.csv", ["account", "day", "channel", "role", "action", "source", "status"],
              [[a["name"], r["day"], r["channel"], r["role"], r["action"], r["source"], r["status"]] for a in accts for r in plan(a)])
    print(f"wrote {out}/cycle.csv and {out}/plan.csv")


def cmd_today(out):
    accts = accounts(*score.load(), date.today())
    lines = rank(accts)
    os.makedirs(out, exist_ok=True)
    md = [f"# Today, {date.today()}", "", f"{len(lines)} touches due across {len({a['id'] for a, _ in lines})} accounts.", "",
          "| # | Account | Role | Channel | Action | Why |", "|---|---|---|---|---|---|"]
    for i, (a, r) in enumerate(lines, 1):
        md.append(f"| {i} | {a['name']} | {r['role']} | {r['channel']} | {r['action']} | {why(r)} |".replace("\n", " "))
        print(f"{i:>3}. {a['name']} | {r['role']} | {r['channel']} | {r['action']} | {why(r)}")
    with open(f"{out}/today.md", "w") as f:
        f.write("\n".join(md) + "\n")
    print(f"{len(lines)} touches due today, per channel: {dict(Counter(r['channel'] for _, r in lines))}")
    print(f"wrote {out}/today.md")


def selftest():
    """Invented fixtures, no network. Returns the number of checks."""
    pp = lambda *a: [dict(name=n, role=r, out=o) for n, r, o in a]
    # stage rules, same cases as production plus the branches it leaves untested
    assert stage_of([], 0)[0] == "researching"
    assert stage_of(pp(("A", "decision_maker", "requested"), ("B", "champion", "requested")), 2) == ("connecting", "Wait for accepts (2 pending). Email 1 on day 1, email 2 on day 7")
    assert "add two more" in stage_of(pp(("A", "champion", "requested")), 8)[1]
    s, a = stage_of(pp(("A", "decision_maker", "requested"), ("B", "champion", "connected")), 3)
    assert s == "champion_in" and "B" in a and "also request the decision maker" not in a
    assert "also request the decision maker" in stage_of(pp(("B", "champion", "connected")), 3)[1]
    assert stage_of(pp(("A", "decision_maker", "connected"), ("B", "champion", "connected")), 4)[0] == "dm_reached"
    assert stage_of(pp(("A", "decision_maker", "messaged")), 4)[1].endswith("wait for a reply (follow up day 7)")
    s, a = stage_of(pp(("B", "champion", "replied")), 5)
    assert s == "warming" and "find one" in a and "find one" not in stage_of(pp(("B", "champion", "replied"), ("A", "decision_maker", "found")), 5)[1]
    assert stage_of(pp(("B", "champion", "replied")), 5, booked=True)[0] == "call_booked"
    assert stage_of(pp(("A", "decision_maker", "call_booked")), 5)[0] == "call_booked"
    # building accounts from raw HubSpot records, day from cycle start
    raw = lambda i, co, role, st, log="", last="", **kw: {"id": i, "properties": {
        "firstname": "P" + i, "associatedcompanyid": co, "omvion_role": role, "omvion_outreach_status": st,
        "omvion_touch_log": log, "omvion_last_touch": last, **kw}}
    today = date(2026, 11, 20)
    cos = {"1": {"name": "Alpha Co", "omvion_cycle_start": "2026-11-12", "omvion_cycle_stage": "connecting"},
           "2": {"name": "Beta Co", "omvion_cycle_start": "2026-10-10"}, "3": {"name": "Gamma Co"}}
    cts = [raw("a", "1", "decision_maker", "requested", "2026-11-12 connect done", "2026-11-12", email="a@example.com"),
           raw("b", "1", "champion", "requested", "2026-11-12 connect request\nreplied: hi"),
           raw("c", "2", "champion", "requested"), raw("d", "2", "influencer", "requested"),
           raw("e", "3", "champion", "replied", "", "2026-11-19", jobtitle="Head of Studio"), raw("x", "1", "", "")]
    accs = {a["name"]: a for a in accounts(cts, cos, {}, today)}
    assert set(accs) == {"Alpha Co", "Beta Co", "Gamma Co"} and len(accs["Alpha Co"]["people"]) == 2   # contact with no status is skipped
    assert accs["Alpha Co"]["day"] == 8 and accs["Alpha Co"]["people"][0]["log"] == [("2026-11-12", "linkedin_request")]
    assert accs["Gamma Co"]["day"] == 0 and accs["Gamma Co"]["stage"] == "warming"
    # the schedule: day 8 has email 1 and email 2 due, the request is done, add-people is due
    due = lambda a: {(r["channel"], r["action"].split(" to ")[0]) for r in due_today(a)}
    al = accs["Alpha Co"]
    assert ("email", "Email 1") in due(al) and ("email", "Email 2") in due(al) and not any(c == "linkedin_request" for c, _ in due(al))
    assert any(c == "research" for c, _ in due(al))
    at = lambda a, day, **kw: {**a, "day": day, **kw}
    assert ("email", "Email 2") not in due(at(al, 6)) and ("email", "Email 1") in due(at(al, 6))   # email 2 waits for day 7
    assert not any(r["source"] == "extended" for r in due_today(at(al, 20)))                   # extended steps not yet due
    # extended schedule: last-touch email on day 21, park on day 30 with a re-entry date, nothing else after
    assert ("email", "Last-touch email") in due(at(al, 21)) and not any(r["channel"] == "park" for r in due_today(at(al, 29)))
    pk = due_today(at(al, 30))
    assert [r["channel"] for r in pk] == ["park"] and "Re-enter on 2027-03-12" in pk[0]["action"]   # 11-12 + 30 + 90 days
    assert due_today(at(accs["Gamma Co"], 40))[0]["action"].startswith("Reply to")                # replied accounts are never parked
    ch = at(al, 40, stage="champion_in")
    assert not any(r["channel"] == "park" for r in due_today(ch))                                 # something happened: keep going
    # the last touch goes through another employee, once
    two = at(al, 22, people=[{**al["people"][0]}, {"name": "Z", "role": "champion", "out": "connected", "last": "2026-11-15", "log": [("2026-11-15", "linkedin_message")], "score": 5, "email": "", "phone": ""}])
    assert any("another employee" in r["action"] for r in due_today(two))
    done = {**two["people"][1], "last": "2026-12-03"}
    assert not any("another employee" in r["action"] for r in due_today({**two, "people": [two["people"][0], done]}))
    # stopped accounts have nothing due, replied accounts only reply
    assert due_today(at(al, 10, stage="call_booked")) == []
    rp = due_today(at(al, 10, people=[{**al["people"][0], "out": "replied"}]))
    assert [r["channel"] for r in rp] == ["linkedin_message"] and rp[0]["action"].startswith("Reply to")
    # ranking: best contact score plus stage points, highest first
    hi = {**al, "name": "High", "priority": 90}
    lo = {**al, "name": "Low", "priority": 20}
    assert [a["name"] for a, _ in rank([lo, hi])][0] == "High" and rank([lo, hi])[-1][0]["name"] == "Low"
    assert accs["Alpha Co"]["priority"] == al["best"] + CFG["stage_points"]["connecting"]
    return 27
