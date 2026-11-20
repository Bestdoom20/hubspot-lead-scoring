# lead-scoring

Reads contacts, companies and deals from a HubSpot portal and does four jobs: scores every contact on fit and engagement (tiers A, B, C), works out the outreach stage and touch plan of every account, prints the touches due today, and tiers a CSV of new companies.

It comes from my own HubSpot, where I track outreach to games studios for a VR product. The portal has 283 contacts, 119 companies, and a 9-stage deal pipeline. I wanted one ranked list of who to contact first instead of eyeballing the board.

Python 3, standard library only, three modules: `score.py` (HubSpot reader, scoring, CLI, selftest), `cycle.py`, `prospects.py`. The default run is read-only: it makes GET requests to HubSpot and writes files to `out/`. Nothing in the CRM changes unless you pass `--write`.

## How the score works

A contact gets points for fit (who they are) and engagement (what has happened so far). The model is the `RULES` table at the top of `score.py`. Each row is one rule, and a contact's score is the sum of the rules that apply.

| Group | Rule | Points |
|---|---|---|
| fit | role is decision_maker | 30 |
| fit | role is champion | 20 |
| fit | role is influencer | 10 |
| fit | role is gatekeeper | 5 |
| fit | title is senior (chief, VP, head, director, founder, owner, partner) | 15 |
| fit | title is mid (lead, principal, senior, manager, producer) | 8 |
| engagement | outreach status is replied | 30 |
| engagement | outreach status is messaged | 15 |
| engagement | outreach status is requested | 8 |
| engagement | a conversation has been held | 10 |
| engagement | last touch within 7 days | 10 |
| engagement | last touch 8 to 21 days ago | 5 |
| engagement | company deal stage is Pilot | 25 |
| engagement | company deal stage is Call booked | 20 |
| engagement | company deal stage is Decision maker reached | 15 |
| engagement | company deal stage is Warming (replied) | 10 |
| engagement | company deal stage is Champion in | 8 |
| engagement | company deal stage is Connecting | 3 |
| engagement | someone else at the company has replied | 5 |

Tiers: **A** is 60 or more, **B** is 35 to 59, **C** is below 35. The next step is the first matching row of a second small table (`NEXT_STEPS`), for example "Reply and propose a call" for anyone who has replied.

The rules use only fields that carry data in this portal. Company type, industry, size and revenue are empty on every company, and the standard email-engagement fields (last reply, last send, opens) are empty on every contact, because the outreach happens on LinkedIn. So engagement comes from the custom `omvion_*` fields I fill in while doing outreach, and fit comes from the role label and job title.

HubSpot properties read:

- contacts: `firstname`, `lastname`, `jobtitle`, `associatedcompanyid`, `omvion_role`, `omvion_outreach_status`, `omvion_conversed`, `omvion_last_touch`
- companies: `name`, `omvion_people_replied`
- deals: `dealstage`, and the deal to company association. A contact takes the furthest stage among its company's deals.

## Commands

```bash
python3 score.py                       # rank contacts (the original tool)
python3 score.py cycle                 # stage, cycle day and touch plan per account, compared with the stage already in HubSpot
python3 score.py today                 # every touch due today, ranked, printed and written to out/today.md
python3 score.py prospects prospects-sample.csv   # tier a CSV of companies, check the mail domain, write out/prospects.csv
```

## Account cycle

My outreach runs per account, not per contact: one HubSpot company and one deal per studio, a map of people at the studio with a role (decision maker, champion, influencer, gatekeeper) and an outreach status (found, requested, connected, messaged, replied, call booked, declined). `cycle` rebuilds that map from live HubSpot (read-only GETs), then works out the stage and the touch plan.

What mirrors production (my own sync script, which writes the stage to HubSpot):

- The stage ladder and the rules that pick the stage are a port of production's `stage_of`, unchanged: researching, connecting, champion in, warming, decision maker reached, call booked.
- The map needs a decision maker plus two others (`people_target`), connection requests and email 1 on day 1, email 2 on day 7, a follow-up message on day 7.
- No accepts by day 7: add two more people to the map.
- A champion who replied: ask for an intro to the decision maker.
- A reply stops the cold touches. Only the reply is due.

The extended schedule, not yet in production. This is the design I want, written down so it can be tried: keep touching the account through every channel and through other employees until something happens, then stop.

- Day 14: phone call to the decision maker (or whoever on the map has a number).
- Day 21: last touch, an email and a message through another employee instead of the decision maker.
- Day 30: park the account with a re-entry date 90 days later. Only accounts where nothing has happened yet (researching, connecting) are parked. An account that has an accept or a reply keeps going.

Every row of the plan carries its source, `production` or `extended`, in `out/plan.csv` and in `out/today.md`. Rows that cannot be done yet (a message before the accept) are `waiting`. The cycle day comes from `omvion_cycle_start`. Touch history comes from `omvion_touch_log`, which records connection requests and messages but not emails or calls. An email or call only shows as done if a line like `2026-11-20 email sent` is added to the log.

`today` ranks accounts by the best contact score in the account plus points for its stage (`stage_points`), then orders each account's touches by `channel_order`.

## Prospects

`prospects` reads a CSV with `name` and `domain` and optional `country`, `employees`, `revenue`, tiers each company with a rules table at the top of `prospects.py` (revenue, team size, mail domain), and writes `out/prospects.csv` with score, tier, status and the rules that fired. The mail check is production's: `dig` for an MX record, then an A record, and a domain that cannot be checked is kept and marked `unchecked`. Status is `invalid` for a bad or mail-less domain and `excluded` for a listed country or a `.de` domain. `prospects-sample.csv` holds invented companies on reserved domains.

## Configuration

Cycle length, day offsets, people per account, stage points, channel order and the tier floors for both contacts and prospects live in `DEFAULTS` at the top of `score.py`, mirrored in `config.example.json`. Copy it to `config.json` (git-ignored) and change only the keys you want. An unknown key stops the run. The selftest fails if `config.example.json` and `DEFAULTS` drift apart.

## Run it

```bash
export HUBSPOT_TOKEN=...        # a HubSpot token with CRM read scopes
python3 score.py --selftest     # offline check, no token needed
python3 score.py                # dry run: prints the top 10, writes out/scores.csv and out/pipeline-report.md
python3 score.py --write        # also writes lead_score, lead_tier, lead_next_step to HubSpot
```

The token is read from the environment and is never printed or written to a file. Any HTTP failure stops the run with the status code and the start of the response body.

`--write` updates contacts in batches of 100. The three contact properties must already exist (`lead_score` as a number, `lead_tier` and `lead_next_step` as single-line text). The tool does not create them. If any are missing it stops before writing anything and names them. I have not run `--write` against the real portal, so that path is covered only by reading the code and by a run against a stubbed API.

## A real run

Run on 2026-11-20 against my portal. These are aggregate counts only. Names, companies and emails stay in `out/`, which is git-ignored.

```
$ python3 score.py
tiers: {'A': 25, 'B': 74, 'C': 184}
```

Score distribution, in bands of 10 points (maximum possible is 125, highest seen is 110):

| Score | 0-9 | 10-19 | 20-29 | 30-39 | 40-49 | 50-59 | 60-69 | 70-79 | 80-89 | 90-99 | 110-119 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Contacts | 111 | 53 | 7 | 36 | 39 | 12 | 17 | 3 | 3 | 1 | 1 |

Tiers by deal stage of the contact's company:

| Stage | A | B | C |
|---|---|---|---|
| Champion in | 0 | 13 | 1 |
| Connecting | 14 | 59 | 29 |
| Decision maker reached | 3 | 0 | 0 |
| Researching | 0 | 0 | 4 |
| Warming (replied) | 8 | 2 | 0 |
| no deal | 0 | 0 | 150 |

Suggested next steps: 168 research or park, 67 wait for a connection to be accepted, 21 stale connection request, 8 reply and propose a call, 8 send a connection request, 7 follow up, 4 wait for a reply.

What it showed: 150 of the 283 contacts have no company with a deal, so they can never score above C. That is a data gap in the CRM, not a scoring result, and it is the first thing to fix.

Sample output rows. These are invented, not from the portal:

| Tier | Score | Name | Company | Role | Status | Next step |
|---|---|---|---|---|---|---|
| A | 110 | Sample Person | Sample Studio | decision_maker | replied | Reply and propose a call |
| B | 41 | Example Producer | Example Games | champion | requested | Wait for the connection to be accepted |
| C | 0 | Placeholder Name | Placeholder Inc | unknown | | Research the person or park |

## The cycle on the live pipeline

Run on 2026-11-20, aggregate counts only.

- 100 accounts: 75 connecting, 9 champion in, 7 warming, 6 researching, 3 decision maker reached.
- Stage agreement with the stage production wrote to HubSpot: 100 of 100. The next-action text also matches on 100 of 100.
- 71 touches due today: 59 research (add two more people), 7 LinkedIn messages, 5 LinkedIn requests, 0 emails, 0 calls, 0 parks.
- Nothing is parked yet because every account that is past day 30 already has a decision maker in.

The 100 of 100 shows the port of `stage_of` is faithful and the HubSpot round trip is intact. It does not test how production gathers people from its own database, since HubSpot was written from that. No email or call touches are due because none of the cycle contacts has an email or phone in HubSpot.

## Selftest

```
$ python3 score.py --selftest
selftest ok: 49 checks passed
```

It also covers the stage rules, the touch schedule, the park rule, account ranking and prospect tiering. The scoring part scores invented contacts and asserts the exact fit, engagement and tier for each: a hot lead lands in A, an empty contact scores 0 in C, the recency cut-offs at 7, 8 and 21 days, a senior title is not counted twice, a stale connection request gets the stale next step, and a raw HubSpot record is turned into the right fields. It makes no network calls. Changing a point value in `RULES` makes it fail.

## Limits

- The weights are my judgment, not fitted to outcomes. 8 contacts have replied, which is too few to fit anything.
- Fit depends on the `omvion_role` label I assign by hand. The job title adds a second signal but only by keyword.
- Recency is measured from `omvion_last_touch`, which is set by hand or by my own scripts, not by HubSpot activity tracking.
- Contacts without a company on a deal get no deal-stage points.
- No retries on rate limits. At this portal's size a run is about 10 requests.
- The `--write` path is untested against the real API.
- `cycle` and `today` never write to HubSpot. There is no write path for them.
- The extended schedule is untested in practice. It is a design, not something production has run.
- Email and call history is not in HubSpot, so those touches stay due until logged in `omvion_touch_log`.
