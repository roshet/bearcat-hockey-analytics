# Data sources

Findings verified against live sources on **2026-10-21**. Anything not verified is
marked as such. If you are picking this project up, read this before writing an
ingestion feature — most of the non-obvious decisions in the codebase are here.

## Primary source: ACHA via HockeyTech LeagueStat

The ACHA live-scores every game into HockeyTech's LeagueStat platform under a
[multi-year technology partnership](https://www.hockeytech.com/hockeytech-acha-partnership/),
[since extended](https://www.achahockey.org/acha-and-hockeytech-extend-partnership).
Every statistics page on `achahockey.org` is a client-side rendering of a JSON
feed, so we read the feed and never scrape the HTML.

```
Base URL     https://lscluster.hockeytech.com/feed/index.php
client_code  acha
key          (BCA_ACHA_API_KEY, the key the league's own pages send; not published)
```

The same platform backs the AHL, OHL, WHL, USHL and PWHL, so the response shapes
are documented publicly by third parties — the
[PWHL Data Reference](https://github.com/IsabelleLefebvre97/PWHL-Data-Reference)
and [`fastRhockey`](https://rdrr.io/cran/fastRhockey/src/R/hockeytech_helpers.R)
are the two most useful.

| | |
|---|---|
| **Seasons covered** | 7 men's regular seasons, 2020-21 → 2026-27 |
| **Update frequency** | Live during games; scorekeepers must submit by midnight local on game day ([process PDF](https://www.achahockey.org/wp-content/uploads/sites/2/2021/08/LeagueStats-Processes-New-scoring-and-stats-system-August-2021.pdf)) |
| **Format** | JSON (`fmt=json`) |
| **Authentication** | None beyond the key above, which is embedded in the league's own pages |
| **Rate limits** | None published. We self-impose one request per second. |

Endpoints are defined once, in `src/bca/ingest/endpoints.py`. `bca feed check`
exercises all of them and is the canary for the key rotating or a view changing.

### Men's regular-season ids

| Season | `season_id` |
|---|---|
| 2020-21 | 1 |
| 2021-22 | 10 |
| 2022-23 | 21 |
| 2023-24 | 34 |
| 2024-25 | 46 |
| 2025-26 | 60 |
| 2026-27 | 73 |

Tournament and women's seasons have their own ids and are out of scope.

### Endpoints that do not work

Probed and confirmed broken, so nobody re-tries them:

| Attempt | Result |
|---|---|
| `statviewfeed&view=standings` | `{"error": "InvalidView error: standings"}` |
| `statviewfeed&view=teams` | HTTP 200, empty payload |
| `modulekit&view=standingtype&type=conference` | Request metadata only, no rows |

Standings are computed from game rows instead. This is preferable anyway: the
aggregate becomes ours, deterministic, and testable.

**A parameter that silently does nothing:** `statviewfeed&view=players` (season
skater and goalie stats) takes **`season`**, not `season_id`. Given `season_id`
it ignores it and answers with the *current* season, whatever was asked for, with
HTTP 200 and no error. M0 only verified it against the current season, so it
looked fine; the M2 audit caught it when every season's archived lines came back
byte-identical. `modulekit` views do take `season_id`. The 938 entries fetched the
wrong way are kept, never loaded, in `data/cache/quarantine/`.

## Response quirks the client absorbs

All handled in `src/bca/ingest/client.py` so nothing downstream repeats them.

1. **`statviewfeed` wraps payloads in parentheses** — `([{...}])` — which is not
   valid JSON. `modulekit` does not.
2. **Errors arrive in three shapes**: a bare text body (`Invalid key.`), a JSON
   object (`{"error": ...}`), and an HTTP 200 with empty `sections` for a view
   that exists but has no rows.
3. **Numeric fields are inconsistently typed.** Team-level `shots` is an integer;
   period-level `homeShots` is a string. Coerce, never trust.
4. **`SiteKit.Roster` is heterogeneous** — a list of player objects with a
   *nested list* of coach objects appended as the last element.
5. **The API key may rotate** without notice, since it is public page furniture.
   Key rejection raises a dedicated `FeedAuthError`.

## What the data contains

Pinned as executable assertions in `tests/test_data_assumptions.py`. Read that
file alongside this table — if the league starts recording something new, those
tests fail and tell us.

### Recorded

- Team shots on goal, total **and by period**
- Complete goal event log: time, period, scorer, assists, power-play /
  short-handed / empty-net flags
- Complete penalty event log: time, period, player, team, minutes, infraction
  description, NHL rule number
- Goalie appearances: shots against, saves, goals against, minutes, in/out periods
- Skater season totals: GP, G, A, P, P/GP, PPG, SHG, GWG, PIM
- Team W/L and goals for/against (computed by us from game rows)
- Rosters with jersey, position, hometown, height, weight, handedness
- Coaching staff, venue, date/time, attendance (sporadic)

The box score has `referees`, `linesmen` and `scorekeepers` keys. All three were
**empty in every game checked.** No feature may depend on knowing who officiated
or scored a game — including attributing data quality to a named scorekeeper.

### Not recorded — the fields exist and are always zero

This is the decisive constraint on the whole product.

| Missing | Consequence |
|---|---|
| Individual skater shots | No shooting %, no individual shot volume |
| Faceoffs (team *and* player) | No faceoff analysis at all |
| Hits, blocked shots | No physical-play metrics |
| Skater time on ice | No per-60 rates, no deployment analysis |
| Plus/minus | Absent entirely from the season stats feed |
| On-ice players for goals | **No line-combination analysis** |
| Event coordinates | Fields exist, always `0`. **No shot maps.** |
| Shot / faceoff / zone events in play-by-play | Only `goal`, `penalty`, `goalie_change` exist |

**One known exception: the September 2023 trial.** The M1 backfill checked all
6,408 completed MD1 box scores from 2020-21 to 2026-27. In 27 games played between
2023-09-08 and 2023-09-19 (27 of the 46 completed in that window, spread across
the country), scorekeepers entered skater plus/minus and the on-ice "plus" players
for goals; 20 of those also have individual skater shots. The trial then stopped,
and nothing like it appears anywhere else. It is **unusable**: the "minus" side was
entered in only 2 of the 27 games, so plus/minus is almost all positive, and the
skater shots do not always add up to the team total (25 vs 30 in one game). Faceoffs,
hits, blocks and TOI are zero in every game, trial or not. The 27 game ids are
`SEPTEMBER_2023_TRIAL_GAMES` in `parsers/game_summary.py`; their findings are
recorded as `data` issues, and none of the values are loaded.

**Therefore Corsi, Fenwick, expected goals, zone entries and exits, possession
metrics, and line-combination performance are not computable from public data**,
and are permanently unrecoverable for games already played. Any feature implying
otherwise is a bug.

## Known data-integrity problems

Real, found in live data, and the reason a reconciliation suite is part of the
product rather than a nice-to-have.

1. **`powerPlayOpportunities` is incompletely and inconsistently entered.**
   Measured across all ten completed Cincinnati games of 2026-27, **8 of 10 are
   populated**:

   | Game | Venue | PP opportunities |
   |---|---|---|
   | Sep 18 home | Queen City | `0 / 0` — missing |
   | Sep 19 home | Queen City | `0 / 0` — missing, yet 1 PP goal recorded |
   | Sep 25 @ Adrian | Arrington | `5 / 5` |
   | Oct 2 @ Ohio | Bird | `4 / 6` |
   | Oct 10 @ Kentucky | Lexington | `5 / 3` |
   | Oct 11 home | Queen City | `7 / 7` |
   | Oct 16 @ Maryville | Maryville | `6 / 6` |

   This reads as an opening-weekend teething problem since fixed, not a systemic
   failure by any one scorekeeper. Note that Sep 19 is internally impossible — a
   power-play goal on zero opportunities.

   **Why we derive it anyway:** for one team in one season you could mostly read
   this field. Across 81 MD1 teams, seven seasons and dozens of volunteer
   scorekeepers, completeness varies per game, so any cross-team series built on it
   would be silently uneven. The case is *consistency*, not absence. Power-play
   **time** on ice is genuinely unpublished and can only be derived.
2. **`isPowerPlay` on penalty events is semantically wrong in places.** In game
   32130 a Game Misconduct is flagged `isPowerPlay: true`. Treat as a hint only.
3. **Goalie aggregates do not reconcile with game scores.** Summing Cincinnati's
   2026-27 goalie records gives 76 goals against; summing the 10 completed game
   rows gives 63. A goalie (#1, player 36407) appears in season stats with an
   implausible line and is absent from the roster feed.
4. **Players appear in season stats who are not on the roster feed** (29 skaters
   in stats vs. 26 roster entries for Cincinnati). Player records must be created
   from whichever source sees them first.
5. **Jersey numbers are not unique** — two Cincinnati goalies share #1.

## Ingestion (M1)

Two phases, deliberately separate:

```bash
bca ingest backfill --dry-run           # 1 request/season: confirm MD1 membership
bca ingest backfill [-s 73] [--refresh-current]   # network: archive only
bca db upgrade                          # Alembic migrations
bca ingest normalize                    # offline: archive -> Postgres
bca ingest coverage [--json data/exports/coverage.json]
```

`backfill` walks `teamsbyseason` → MD1 teams → each team's schedule and roster →
the union of completed game ids → each box score. It writes only to the archive
(`data/cache/`), so it resumes for free after an interruption.
`--refresh-current` re-reads the current season's teams, schedules and rosters;
when an upstream resource has changed, the previous envelope is kept under
`data/cache/history/`, so scorekeeper edits stay auditable.

`normalize` never touches the network. Every resource loads in its own
transaction; parse and load failures, duplicates, and canary findings go to
`ingest_issues` rather than aborting the run. It is idempotent.

Findings made while building it:

- **What "MD1" means changes in 2023-24.** Seasons 1, 10 and 21 (2020-21 to
  2022-23) have no `MD1 ...` divisions; Division 1 is called **`Men's Pool A`**
  (every team named `M1 ...`; 26/27, 66/67 and 69/69 of its members are MD1 in
  2023-24). The rule lives in `parsers/modulekit.py`. MD1 team counts per
  season: 27, 67, 69, 71, 73, 81, 81 — 2020-21 was COVID-shortened.
- **Event clocks are elapsed time in the period**, not time remaining:
  play-by-play pairs `time "0:23"` with `s: 23` for the same penalty.
- **Goalie appearances come from `goalieLog`, not `goalies`.** `goalies` lists
  every dressed goalie, including a backup who never played, with zeros.
- **Unentered is spelled `""`, `None` or `0`** depending on the field;
  attendance `0` is stored as NULL. `powerPlayOpportunities` is stored exactly as
  entered — interpreting a `0` is M2's job.
- **`shootout` on schedule rows is a code, not a flag:** `0` none, `1` home won
  the shootout, `2` visitor won (16/16 consistent in 2026-27; every non-zero row
  has status `Final SO`). The first live backfill failed 15 schedules on this
  until it was understood.
- **Score-only box scores exist.** 169 of the 6,408 completed MD1 games (2.6%;
  20 of 379 in 2026-27) have a box score holding only the score: each goal is anonymous (scorer id `0`, no time,
  no assists), the lineup is a single placeholder player `0` per team, and shots,
  penalties and goalies are absent. Their zeros mean *not entered*. They load
  with the score and per-period goals, `scorer_id` NULL, no phantom players, and
  a `data` issue naming them score-only. Shot, discipline and goalie metrics
  must exclude them.
- **"MD1 TBD" (team 534) is a placeholder opponent**, not a team: it appears in
  9 of those score-only games and in no `teamsbyseason` response. Opponent
  reports and standings must not treat it as a real side.
- **Rosters can list a player twice.** 28 roster responses repeat a player id;
  the first entry is kept and a `data` issue is recorded.
- **Birthdates are never modelled or stored.**
- **Every box score is checked against the always-zero fields** (skater shots,
  faceoffs, hits, blocks, TOI, plus/minus, on-ice players). A non-zero value is
  recorded as a `canary` issue: it would mean the league has started tracking
  real events. The only expected exceptions are the September 2023 trial's games
  and fields (above); any other field in those games is still a canary.

Backfill result (2026-10-22): 469 schedules and rosters plus 6,408 box scores
archived and normalized, 0 failures. MD1 completed games per season: 200, 1,017,
1,105, 1,153, 1,212, 1,342, 379. Per-season detail: `data/exports/coverage.json`.

## Audit (M2)

```bash
bca ingest backfill --upstream-stats    # + each MD1 team-season's league season lines
bca audit run                           # offline, idempotent
bca audit report [--json data/exports/audit.json]
```

`bca audit run` checks every completed MD1 box score against itself and against
the schedule, and every player-season against the league's own season lines. Each
finding carries a known cause or `unexplained`. A cause is named only after it was
seen in real cases, and it applies only when it accounts for the **whole**
difference. Score-only box scores are *not checkable*, never passes. Results:
`reconciliation_findings`, `audit_check_totals`, and per-game flags in
`game_quality`.

### Result (2026-10-23, 6,413 games, 12,826 team-games)

Game rows against themselves. All unexplained rates are under 1%:

| Check | Checked | Findings | Unexplained |
|---|---|---|---|
| Schedule score = box-score team goals | 12,826 | 15 | 5 (0.04%) |
| Goal events = score | 12,826 | 55 | 29 (0.23%) |
| Goalie GA = opponent goals | 12,486 | 826 | 79 (0.63%) |
| Period shots sum to team shots | 12,486 | 164 | 10 (0.08%) |
| Skater-line goals / assists = goal events | 12,486 | 23 / 311 | 8 / 23 |
| Penalty minutes = team PIM | 12,486 | 16 | 2 |
| PP goals ≤ PP opportunities | 12,486 | 955 | 31 (0.25%) |
| Box-score player on the team's roster | 13,070 | 764 | 3 |

The known causes, and how many findings each explains:

- **`empty_net` (707).** An empty-net goal is charged to no goalie. This is the
  1-goal gap in UC 31980.
- **`unentered_zero` (919).** PP opportunities read 0 although the opponent took
  minors or majors. This is the Sep 19 case (33170): a PP goal on "0 opportunities"
  against 5 UC minors.
- **`roster_feed_omission` (761).** The player is missing from the roster feed but
  is on the league's own season line for the team. The roster feed is the
  incomplete source, and its gaps are an argument for building rosters from box
  scores.
- **`goalie_assist` (273).** The goalie's assist sits in the goalie log, which has
  no skater line.
- **`shootout_winner_shot` (137).** Some scorekeeping setups add the deciding
  shootout goal to the winner's shots: 137 of 290 full shootout box scores, and
  never the loser.
- **`partial_box_score` (149 findings, 20 games).** The lineup was entered but
  only some goals were, usually with no goalies and part of the PIM. Every
  event-based number in such a game is incomplete. Flagged per game as
  `game_quality.goal_events = false`.
- **`unflagged_empty_net` (40).** Goals scored after the goalie's last stint
  ended but not flagged empty-net.
- **`major_penalty` (5).** Extra PP goals on a 4- or 5-minute penalty, which a
  goal does not end.

Unexplained and worth knowing:

- Five September 2023 games where the schedule score is **lower** than the box
  score: 14173 is 0 on the schedule but 7 in the box. The schedule row is
  normally the source of truth for results; here it is the one in error.
- **31 PP-goal counts exceed a non-zero opportunity count** with no major penalty
  to explain them. These are plain scorekeeping contradictions.

### The league's season lines disagree with its own box scores

The league's season lines are compared against box-score sums per player-season
(goalies 1,461, skaters 11,597). This is why convention 1 says never to use them:

- **UC 2026-27 goals against: 76 vs 63, explained.**
  - Box-score goalies: three goalies with 31, 20 and 11, totalling 62, all
    matching their upstream lines.
  - Plus 1 empty-net goal, which makes the 63 in the game rows.
  - The league adds **a goalie (2 GP, 14 GA)**, who is in **no box score**,
    on no roster and in no player row. 62 + 14 = 76.
- **`other_squad_games`.** About 4.6% of lines are filed under the school's
  *other* squad's division, and those lines count that squad's games too.
  Example: a Jamestown MD1 skater with 17 GP = 10 MD1 games + 7 for Jamestown's
  MD2 side.
- **`shootout_decider`.** The league counts the deciding shootout goal, for the
  scorer and against the losing goalie. Box scores have no event for it.
- **The current season drifts by the day.** The season lines update before an
  archived schedule does, so `--refresh-current` both together.
- **Still unexplained.** 0.7–1.5% of skater-seasons (goals, assists, PIM) and 8.7%
  of goalie-seasons (GA) disagree for no known reason. We do not chase these: no
  number of ours is computed from upstream.

### Scorekeeping completeness

League-wide share of team-games in which each field was entered (score-only box
scores count as not entered):

| Field | Entered | Compare across teams? |
|---|---|---|
| Lineup, goal events, goal times, shots, penalties, goalies | 96.9–97.3% | yes |
| Assists (excluding the 1,054 one-goal games where it cannot be told) | 96.8% | yes |
| **PP opportunities** | **79.1%** (71% in 2021-22 and in 2026-27 to date) | **no**: flag per game; derive in M3 |
| Attendance | 31.9% | no |
| Referees, linesmen, scorekeepers | **0 of 6,413** | never entered anywhere |

Completeness is falling slightly. 2020-21 had nearly every field at 100%; 2026-27
has 94–95%, driven by its score-only box scores (20 of 384). Per-season and
weakest-home-side breakdowns are in `data/exports/audit.json`.

## Special teams (M3)

```bash
bca special-teams run                   # simulate every full box score; offline, idempotent
bca special-teams validate [--json data/exports/special_teams.json]   # every rule set vs the flags
```

`analytics/penalty_state.py` rebuilds each game's strength state second by second
from its penalties and goals. Output:
- **`team_game_special_teams`:** per team, derived PP opportunities, PP and PK
  seconds, and PP/SH goals for and against.
- **`goal_strength`:** the strength each goal was scored at.
- **`penalty_outcomes`:** whether each penalty was cancelled or delayed, and how
  many seconds of advantage it gave.

It runs on 6,223 of 6,413 games, skipping the 170 score-only and 20 partly
entered box scores.

**What the penalty minutes mean.** Checked across all 68,914 penalties:

| Minutes | What it is | Man advantage |
|---|---|---|
| 2 | Minor or bench minor | 2:00 |
| 5 | Major | 5:00 |
| 10 | Misconduct or game misconduct | none |
| 12 | Minor + misconduct, in one event | 2:00 |
| 15 | Major + game misconduct or DQ, in one event | 5:00 |

There are no 4s. A double minor is two 2-minute events for the same player at the
same instant, served back to back, and a PP goal ends only the half being served.
A minor described as "Penalty Shot" was awarded as a shot instead. Overtime is
5:00: every shootout game's goalie log ends OT at exactly 300 s. 24 events in
about 14 games are timed later, up to 13:57 of OT, and are reported rather than
simulated.

**What the scorekeeper's "PP opportunities" field really is.** It is the count of
the opponent's penalties flagged `isPowerPlay`. That holds exactly in 96.4% of the
10,026 team-games where it is entered, and within one in 98.0%. So it is not an
independent count; it inherits every flagging slip. Examples:
- A game misconduct flagged as a power play: 202 misconducts are flagged.
- 324 pairs of coincidental minors flagged on both sides.
- About 1,300 plainly missed flags.

**Rules: calibrated, not assumed.** The ACHA's rulebook is not findable online,
so the uncertain rules are options (`Rules`), each compared against the
scorekeepers' penalty and goal flags across the league. Every combination agrees
to within 1–2%, which is less than the scorekeepers' own inconsistency. The
defaults were therefore chosen on principle among the near-best:
- **Coincidentals cancel like for like:** majors against majors, minors against
  minors. 3,001 of 3,432 instants with one minor each are unflagged on both sides.
- **Coincidental minors leave teams at full strength.** Either way it is even
  strength, never a PP.
- **An opportunity is a penalty that really gave the other side a man advantage.**
  A penalty that only ever made it 4-on-4 does not count, and each half of a
  double minor does, as the league flags it.
- **Overtime is labelled 5-on-5.** This is unverified, but it changes no
  advantage, only the label.
- **One player's penalties at one instant are served back to back, in one box
  place** (`same_player`, added 2026-10-23 during the M5 review): majors first,
  then minors, as double minors always were. Before this, a player's major and
  minor put his team two men down. And a major entered twice counts once: the
  same infraction as a 5 and a 15 ("Major-Grab Face Mask" and "...
  w/Disqualification DQ"), or a "Disqualification w/Major" beside its major.
  There are 90 pairs of majors on one player at one instant, and 277 pairs of a
  major and a minor; in game 33169 the duplicate tipped the coincidental majors
  of a fight and left UC a man short for 5:00. Two *different* majors (cross
  checking and fighting, game 7473) are both real: they count towards
  coincidentals, and the scorekeeper flags the one left over. Only 46 games
  change. On the 16 goals whose label changes, the scorekeepers agree with this
  rule on 12, with plain back-to-back on 9, and with the old rule on 6.

**Agreement with the scorekeepers** (default rules):

| Measure | Agreement |
|---|---|
| Penalties: per team per stoppage, team-games with PP data entered | 93.6% |
| Goal strength (PP / SH / EV) | 93.4% of 44,687 goals |
| Opportunity count vs the entered field | 77.4% exact, 92.4% within one |

The residuals were read case by case. Two were simulator bugs (fixed): a minor
cancelled out of one player's pair kept its credit for the advantage, and one
player's simultaneous penalties were boxed side by side (above). The rest
were the scorekeepers' slips listed above; disputed PP goals sit at the same
points in the advantage as agreed ones, so they are not a timing artifact.

**Game 32130 does not come out 5/5, and no rule set can make it.**
- **Home:** 3 real advantages (UC's minors at 19:48 of the 1st and 3:27 of the
  2nd, and a UC player's major). The entered 5 also counts the flagged game
  misconduct, which can never create a power play, and one more.
- **UC:** 7. A home player's minor and the Charging minor both put UC a man up for
  32 seconds (3:32–4:04 of the 3rd), and neither was flagged.

Seven real games are golden-tested against timelines computed by hand:
- 31418, 24759, 13015, 32953 and 31972 reproduce the entered counts exactly.
- 32130 gives 3 / 7, as worked through above.
- 33169's third-period fight: the majors cancel, and only UC's roughing minor stands.

**UC 2026-27, derived:**
- **PP:** 8 goals on 50 opportunities, 16.0%.
- **PK:** 70.8%, 14 PP goals against on 48 opportunities, with 79.5 minutes short
  (71.4%, 49 and 82.5 before the same-player rule removed 33169's phantom major).
- **The first two games:** their field reads "0/0"; derived it is 4/5 and 3/4.

## Rankings (M6)

The national tournament's field is chosen by the ACHA's M1 computer ranking, so M6
rebuilds it from game rows (`analytics/rankings.py`) and checks the rebuild against
the ACHA's own weekly PDFs (`analytics/acha_pdf.py`, `bca rankings fetch` / `check`).

**The method** (ACHA manual 2026-27, Men's D1, Art. IV §1 and Art. V §3):
- **Before Nov 1:** coaches' polls (rule A).
- **After Nov 1:** the order is the "Computerized Ranking Average" of two USHSHO
  ratings, with ties broken by Ranking B (rule B; [ushsho.com/acharank.htm](http://www.ushsho.com/acharank.htm)).
  - Ranking A caps each game's goal difference at 7.
  - Ranking B caps it at 1, so it counts only wins, losses and ties.
- **Which games count:** only games between two ACHA M1 teams (rule D). Overtime and
  shootout results count as ties (rule E).
- **Rule F:** docks points from a team with 3 or more home games over its away games. The
  amount isn't published.
- **The field:** 24 teams, chosen and seeded by the ranking (Art. V §1, §3–4).
  - Conference champions of the qualifying leagues (ACCHL, CHMA, ECHA, GL6, GLCHL, MCH,
    NECHL, WCHL, WHAC) get automatic bids. A champion ranked below 24th bumps the
    lowest team.
  - UC's conference has no automatic bid.
- **The rebuild:** a least-squares fit of `rating_home - rating_away = capped margin`,
  with the ratings summing to 0.

**What the calibration found:**
- **Forfeits:** recorded with status "Visiting Forfeit" or "Home Forfeit", scored 1-0
  (7 in 2024-25 and 2025-26). They are **left out**: that improves both PDFs that
  contain any (2024-25 #16: 70 of 73 within 2 instead of 68; the 2025-26 final: 78 of 81
  instead of 76), and changes nothing elsewhere.
- **Rule F was tested and not adopted.** On all four 2025-26 PDFs, the correlation
  between a team's home-minus-away games and its rank error is −0.13 to +0.23, so no
  penalty would close the gap.
- **The as-of date includes that day's games.** Rebuilding through the day before made
  every PDF worse.
- **The ACHA's M1 membership differs from the feed's:**
  - 2024-25 ranks Concordia Ann Arbor (62nd), which the feed lists only as an MD2
    program, and has no 2024-25 team-season for at all.
  - 2024-25 leaves out Buffalo, which the feed lists as MD1. The manual excludes
    teams that miss two reporting deadlines (Art. IV §4); whether that's why isn't
    known.
  - These teams are listed by the check, never patched.
- **The 2025-26 "final" is dated Mar 17,** after the national tournament (Mar 12–17).
  The feed keeps nationals as a separate season (68), which we don't hold, so our
  rebuild stops at Mar 7. The three remaining misses on that PDF (Adrian, Arizona
  State, Buffalo) are unexplained, and may come from nationals games.

**Gate results** (exact / within 2 / top 24):

| Official PDF | Exact | Within 2 | Top 24 |
|---|---|---|---|
| 2024-25 #16, as of 2025-02-23 (the selection ranking) | 42/73 | 70 (96%) | match |
| 2025-26 #5, as of 2025-11-02 (the first computer ranking) | 46/81 | 74 (91%) | **differs**: Missouri State 24th (rebuilt 28th), Buffalo 30th (rebuilt 23rd) |
| 2025-26 #9, as of 2025-12-07 | 64/81 | 81 (100%) | match |
| 2025-26 #13, as of 2026-02-01 | 59/81 | 76 (94%) | match |
| 2025-26 final, as of 2026-03-17 | 50/81 | 78 (96%) | match |

**The Nov 2, 2025 top-24 difference** (investigated 2026-10-24; unconfirmed):
- **Missouri State is not the cause.** Its A and B ranks are within 1–2 of the ACHA's (A
  28 vs 30, B 23 vs 24). It falls to 28th only because Buffalo jumps above it.
- **Buffalo is,** on Ranking B: 26th for the ACHA, 18th for us (5-2-1 vs MD1 through
  Nov 2).
- **Ruled out:**
  - No single removed game, of the 392, restores the top 24.
  - Rule F, measured on full-season schedules: home-heavy teams were ranked slightly
    *better* by the ACHA that week.
- **One result fits:** counting Buffalo's 3-2 win over Duquesne on 2025-10-04 (game
  26821) as a tie, which is how the ACHA scores an OT or shootout result, restores the top
  24 and lifts exact agreement from 46 to 50. No other single result change does both.
- **Our copy can't confirm it.** It was fetched Oct 2026 and shows a regulation win (the
  winner at 6:52 of the 3rd period, status "Final").
- **The likeliest reading:** the league's data held that game differently on Nov 2 and was
  corrected by Dec 7, when all 81 teams agree within 2 places. Not modelled, and not in
  `KNOWN_CAUSES`, since it is unconfirmed.
- **Next time:** refreshing the feed on the day each PDF is fetched would turn a case like
  this into an archived edit (`history/`) instead of a guess.

**Unexplained differences:** every miss of 3 or more places not listed above is
unexplained. Buffalo is off on three of the four 2025-26 PDFs, so its games are the
first place to look. The PDFs give ranks, not ratings, so a game-level cause can't be
read off them.

## Permission and politeness

The ACHA reviewed this project and approved it in November 2026, including publishing this
code. No league data is included here. The live site is private, behind a password.

These obligations are implemented in code, not left to convention:

- One request per second, exponential backoff, no concurrency
  (`request_delay_seconds`, `max_retries`)
- Aggressive caching, so a backfill is never repeated needlessly
- A `User-Agent` naming the project and a contact email; the client refuses to start
  without one
- No redistribution of raw league data: the site serves only derived pages
- Clear attribution to the ACHA and FloHockey in the UI

## Secondary sources

### LiveBarn — video for home games

Queen City Sportsplex, Cincinnati's home rink, is a
[LiveBarn venue](https://queencitysportsplex.com/watch). LiveBarn offers live
viewing, 30-day on-demand replay, and full game download. This is the **only**
viable path to the missing event data: manually tagging video yields shot
locations and on-ice personnel, and therefore Corsi, shot maps and line results.

Constraints: ~30-day retention (tag promptly), fixed camera angle, paid
subscription, and downloads are for internal analysis only — do not republish.

### FloHockey — video, partial

[ACHA on FloHockey](https://www.flohockey.tv/collections/12842643-acha) (absorbed
HockeyTV). Schedule rows carry `htv_game_id` / `flo_live_event_id` /
`flo_core_event_id`, but only 3 of Cincinnati's 28 games in 2026-27 have them
populated, all road games. **Home games are not streamed.** Subscription
required. Not a bulk data source.

### Not usable

| Source | Why not |
|---|---|
| [Elite Prospects](https://www.eliteprospects.com/team/10372/univ-of-cincinnati) | Returns **HTTP 403** to automated requests. Manual lookups only. |
| [hockeydb.com](https://www.hockeydb.com/stte/u.-of-cincinnati-12957.html) | Sparse, historical. |
| `bearcathockey.com` | The team's own site. Client-side rendered; **contents not yet inspected.** |

## Program reference

| | |
|---|---|
| ACHA `team_id` | **265** (stable across the MD2 → MD1 move) |
| 2026-27 division | ACHA Men's Division 1, conference **MD1 SECHL** (`division_id` 77) |
| Conference members | NC State (52), Alabama (408), **Cincinnati (265)**, Kentucky (346), Louisville (347), North Carolina (356) |
| Home rink | Queen City Sportsplex |
| Status | Club sport, player-funded; **not** NCAA, not under the Athletic Department |

Division history, same `team_id` throughout: Men's Division 2 (2021-22,
2022-23) → MD2 TSCHL (2023-24, 2024-25) → MD1 Independent (2025-26) → MD1 SECHL
(2026-27). Cincinnati does not appear in 2020-21.
