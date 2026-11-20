# lead-scoring

Reads contacts, companies and deals from a HubSpot portal, scores every contact on fit and engagement with a written model, and ranks them into tiers A, B and C with a suggested next step.

It comes from my own HubSpot, where I track outreach to games studios for a VR product. The portal has 283 contacts, 119 companies, and a 9-stage deal pipeline. I wanted one ranked list of who to contact first instead of eyeballing the board.

Python 3, standard library only. The default run is read-only: it makes GET requests to HubSpot and writes files to `out/`. Nothing in the CRM changes unless you pass `--write`.

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

## Selftest

```
$ python3 score.py --selftest
selftest ok: 11 checks passed
```

It scores invented contacts and asserts the exact fit, engagement and tier for each: a hot lead lands in A, an empty contact scores 0 in C, the recency cut-offs at 7, 8 and 21 days, a senior title is not counted twice, a stale connection request gets the stale next step, and a raw HubSpot record is turned into the right fields. It makes no network calls. Changing a point value in `RULES` makes it fail.

## Limits

- The weights are my judgment, not fitted to outcomes. 8 contacts have replied, which is too few to fit anything.
- Fit depends on the `omvion_role` label I assign by hand. The job title adds a second signal but only by keyword.
- Recency is measured from `omvion_last_touch`, which is set by hand or by my own scripts, not by HubSpot activity tracking.
- Contacts without a company on a deal get no deal-stage points.
- No retries on rate limits. At this portal's size a run is about 10 requests.
- The `--write` path is untested against the real API.
