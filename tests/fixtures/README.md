# Recorded test data (not published)

The tests read recorded ACHA feed responses (`live/`), official ranking PDFs (`rankings/`)
and pages built from them (`web/src/__fixtures__/`). They are the league's data, so they
are not redistributed here. Tests that need them skip, with the reason.

To record your own: set `BCA_CONTACT_EMAIL` and `BCA_ACHA_API_KEY` in `.env`, then
`bca feed check --out-dir tests/fixtures/live`.
