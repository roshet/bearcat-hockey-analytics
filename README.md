# Bearcat Hockey Analytics

Opponent scouting, special-teams analysis and a rebuilt national ranking for the University
of Cincinnati's club hockey team, built from the ACHA's public league data. The pages
update and deploy themselves every morning.

An independent student project. **Not affiliated with, endorsed by, or an official product
of the University of Cincinnati or the American Collegiate Hockey Association.** The ACHA
reviewed the project and approved publishing this code; no league data is included here.
Data courtesy of the ACHA and FloHockey.

**Stack:** Python 3.11 (Pydantic, SQLAlchemy, Alembic, Typer, FastAPI, pypdf), PostgreSQL,
React 19 + TypeScript + Vite + Tailwind + Recharts, Vercel, pytest, vitest, ruff, mypy.

---

## What it does

1. **Opponent scouting reports.** One printable, two-page report for each of the 81 Division 1
   teams: results, scoring, goaltending, special teams, penalty tendencies, and a
   strengths-and-weaknesses chart against the league. Every section states its sample size.
2. **Special teams and discipline.** Power-play and penalty-kill numbers derived by replaying
   every penalty, including the power-play *time* the league never publishes.
3. **"Where we stand."** A rebuild of the ACHA's computer ranking, which alone picks and
   seeds the 24-team national tournament, shown beside the official ranking.

The live site is private, behind a password; I'm glad to share it on request.

## The interesting parts

**Working within what the data can't say.** The league records no individual shots, no
faceoffs, no ice time and no on-ice players. The fields exist in the feed and are always
zero, which is a trap. So Corsi, expected goals and line combinations are impossible here,
and the project says so instead of faking them. `tests/test_data_assumptions.py` pins what
the data contains. Every morning a canary check halts the deploy if the league ever starts
recording one of those fields.

**A penalty simulator.** `analytics/penalty_state.py` replays each game's penalties: majors,
coincidental minors, misconducts, one player's penalties served back to back, and power-play
goals ending a minor. From that it derives the strength on the ice second by second.
- It agrees with the scorekeepers' own power-play flags on 93.6% of penalties and 93.4% of
  goals, across 6,223 games.
- The disagreements were read case by case: two were simulator bugs (fixed); the rest were
  scorekeeping slips.
- Seven games are golden-tested against timelines worked out by hand.

**Rebuilding the national ranking.** The ACHA's ranking is a least-squares rating, averaged
over two variants: one caps each margin at 7 goals, the other at 1. `analytics/rankings.py`
rebuilds it from game results, and `analytics/acha_pdf.py` checks it against the official
weekly PDFs.
- On the first official ranking of 2026-27, 71 of 81 teams match exactly and all 81 are within
  two places.
- Both past seasons' final rankings match on the 24-team national field.

**Finding the league's data errors.** Game rows are the source of truth: every record,
standing and total is computed from game results, never read from the league's season
summaries. Those summaries don't reconcile (one team's goalie lines sum to 76 goals against,
its games to 63). The 18-check audit (`audit/`) explains each mismatch with a known cause or
marks it unexplained. I sent the league a list of its own data-entry problems, with game ids.

**A pipeline that runs itself.** Every morning, `bca nightly` runs the whole chain, recording
each run in the database. A failed step never deploys, so the site keeps the previous day's
pages:
1. fetches the current season;
2. reloads only the changed files;
3. re-audits;
4. re-derives special teams;
5. rebuilds all pages;
6. runs the canary;
7. exports static JSON;
8. deploys to Vercel behind HTTP Basic auth (`web/middleware.ts`, which fails closed).

**A polite guest.** The feed is a volunteer league's server.
- One request per second, with retries and backoff and no concurrency.
- Every raw response is archived before parsing, so nothing is fetched twice.
- The client refuses to start without a real contact email, which goes in the
  `User-Agent`.

## Running it

Requires Python 3.11+, Node 20+ and Docker.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"          # Windows
# source .venv/bin/activate && pip install -e ".[dev]"   # macOS / Linux
cp .env.example .env    # set BCA_CONTACT_EMAIL and BCA_ACHA_API_KEY
docker compose up -d    # Postgres on port 5433
```

```bash
bca feed check                        # the nine feed endpoints, checked live
bca db upgrade                        # migrations
bca ingest backfill --upstream-stats  # network: archive every Division 1 resource (resumable)
bca ingest normalize                  # archive -> database, offline
bca audit run                         # reconciliation and completeness
bca special-teams run                 # the penalty simulator
bca scouting build                    # scouting, special-teams and ranking pages
bca export static                     # pages -> web/public/data
cd web && npm install && npm run dev  # http://127.0.0.1:5173
```

### Tests

```bash
pytest                  # Python
ruff check src tests && mypy
cd web && npm test && npm run typecheck
```

Most tests run against recorded league data: real box scores, including hand-verified
games, and official ranking PDFs. That data isn't redistributed, so in this copy those tests
skip with the reason. See [`tests/fixtures/README.md`](tests/fixtures/README.md).

## Layout

```
src/bca/
  ingest/      feed client (rate limit, retries, archive), endpoints, parsers
  audit/       reconciliation and completeness checks
  analytics/   penalty simulator, special teams, scouting, rankings, PDF parsing
  api/         read-only FastAPI layer (for debugging)
  db/          SQLAlchemy models; migrations in alembic/
  nightly.py   the morning pipeline
  export.py    stored pages -> static JSON
web/           React app: print-first, phone-friendly; Basic-auth middleware
tests/         pytest; test_data_assumptions.py pins what the feed contains
docs/data-sources.md   what the data does and doesn't contain, and why
```
