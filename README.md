# Groww Weekly Review Pulse

A Python CLI, `pulse`, that turns public Groww reviews from the App Store and Google Play into a weekly pulse of at most 250 words: the top 3 themes, 3 real quotes and 3 action ideas. It publishes the pulse to Google Docs and creates a Gmail draft, both through MCP.

Design and progress are recorded in five docs: [problemStatement.md](problemStatement.md), [architecture.md](architecture.md), [implementation.md](implementation.md) (its **Status** table and **Handoff** say where things stand), [edgecase.md](edgecase.md) and [evaluation.md](evaluation.md). Rulings are in [docs/decision-ledger.md](docs/decision-ledger.md).

## Set up on any machine

The repository holds everything except review data and secrets.

1. **Install `uv`.** Use `curl -LsSf https://astral.sh/uv/install.sh | sh`, `brew install uv`, or `pipx install uv`. `uv` installs Python 3.12 itself, as pinned in `.python-version`.
2. **Clone and install:**
   ```bash
   git clone https://github.com/Bhavya-Chadha-256/Milestone-3---Groww-Review-Analyser.git
   cd Milestone-3---Groww-Review-Analyser
   uv sync --extra dev --extra eval
   uv run pytest -q
   ```
3. **Add secrets.** Run `cp .env.example .env`, then paste your key after `GROQ_API_KEY=` in `.env`. The `.env` file is git-ignored and never committed. The CLI loads it automatically, and a real environment variable with the same name takes precedence.
4. **Rebuild the data.** Review data is never committed, because of the privacy rule in CLAUDE.md. Fetch it fresh:
   ```bash
   uv run pulse fetch play-store     # about 2 minutes; covers the 12-week window
   uv run pulse fetch app-store      # Apple's feed reaches back only about 4–8 weeks
   uv run pulse ingest               # writes data/pulse.db and runs/<id>/report.md
   ```

## Weekly run (Phase 3 onward)

```bash
uv run pulse run --week 2026-W42 --until aggregate   # themes for the week (needs GROQ_API_KEY in .env)
```

The free Groq tier allows about 200K tokens a day. A first run classifies the whole 12-week window, takes about three days, and stops cleanly each day with `daily_token_budget`. Each following day, continue with `uv run pulse run --week 2026-W42 --from classify --until aggregate`. Later weekly runs fit in one day. Results are in `runs/<run_id>/report.md`.

## Where things live

| Path | In git? | What it is |
|---|---|---|
| `src/pulse/`, `tests/`, `config/` | yes | code, tests and settings (`config/pulse.yaml`) |
| `.env.example` | yes | template listing the secrets the project needs |
| `.env` | **no** | your secrets |
| `data/raw/*.csv` | **no** | raw review exports from `pulse fetch` |
| `data/pulse.db` | **no** | scrubbed, normalised reviews (SQLite), rebuilt by `pulse ingest` |
| `runs/<run_id>/` | **no** | per-run `report.md`, `report.json`, `manifest.json`, `taxonomy.json`, `aggregates.json` |
| `evals/gold/*.csv` | **no** | the human-labelled gold set (real, scrubbed review text); back it up privately |
| `evals/gold/*.meta.json` | yes | gold-set metadata (taxonomy version, κ, annotators) |

## Test data is made up

Everything under `tests/fixtures/` and `evals/redteam/` is synthetic. Names, phone numbers, PAN, Aadhaar, UPI and account numbers there are invented for the tests and belong to no one. Real reviews are only ever fetched into `data/`, which is never committed.
