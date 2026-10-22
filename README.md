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

## Weekly run

```bash
uv run pulse doctor                       # config, Groq key + models, MCP server + tools: all must print OK
uv run pulse run --week 2026-W42          # ingest → themes → quotes → compose → validate → Doc → Gmail draft
uv run pulse run --week 2026-W42 --dry-run   # stop after validate: writes runs/<id>/pulse.md, publishes nothing
```

Nothing is ever sent: the run creates or updates one Google Doc per ISO week and one Gmail **draft** to `delivery.recipient`. Results and every artifact are in `runs/<run_id>/` (`report.md` first).

**Resuming.** A run that stops can continue from the stage named in `report.md`:

| Report says | Run |
|---|---|
| `daily_token_budget: … --from classify` (the free Groq tier allows about 200K tokens a day; a first run classifies 12 weeks and takes about three days) | the printed command after 00:00 UTC, e.g. `uv run pulse run --week 2026-W42 --from classify --until aggregate` |
| `MCP tool call failed: … --from publish` (often expired Google auth) | re-authorise the MCP server (below), then `uv run pulse run --week 2026-W42 --from publish` |
| status `PUBLISHED_DOC_ONLY` (the draft failed) | `uv run pulse run --week 2026-W42 --from draft` |
| `VALIDATION_FAILED` | read the **Validation** section; nothing was published |

**Evals.** `uv run pulse eval --suite deterministic` is free and runs on every commit. `--suite pulses` and `--suite classification` spend Groq tokens; run them on a day with no backfill.

## Delivery setup (Google Docs and Gmail through MCP)

Google is reached only through an MCP server; this project never holds Google credentials. `config/mcp.yaml` points at the user's own hosted server, [gmail-gdocs-mcp on Render](https://github.com/Bhavya-Chadha-256/Milestone-3---MCP-Testing) (`https://milestone-3-mcp-testing.onrender.com/mcp`, Streamable HTTP). It holds the Google tokens itself.

That server can only **append to an existing Doc** and **send** mail. So `pulse` appends each week's pulse once to one running Doc, and makes **no Gmail draft** (there is no draft tool, and the send tool is never mapped). The run ends `PUBLISHED_DOC_ONLY`, and the report says why. This leaves the brief's "Gmail draft" item unmet: a known gap, chosen by the user on 2026-10-22.

1. **`.env`:** set `MCP_AUTH_TOKEN` to the server's bearer token (Render dashboard → the service → Environment → `MCP_AUTH_TOKEN`).
2. **The running Doc:** create a Google Doc by hand (for example "Groww Review Pulse"), and set `delivery.doc_id` in `config/pulse.yaml` to the ID in its URL (`docs.google.com/document/d/<ID>/edit`). The server's Google account must be able to edit it.
3. **`uv run pulse doctor`** until every line says `OK`. `mcp_tools` also asks the server whether its Google account is authorised (its refresh token expires every 7 days while the Google app is in Testing; re-run the server's `auth` then).

A re-run of the same week doesn't append a second copy; `--force` appends again (for example after the backfill improves the pulse). With no read tool, the Doc can't be read back: "verified" means the server confirmed the append.

**Other servers.** A server with create, read and draft tools (such as [workspace-mcp](https://github.com/taylorwilsdon/google_workspace_mcp)) maps `create_doc`, `update_doc`, `read_doc` and `create_draft` instead (architecture.md §6.2): one Doc per week in `delivery.drive_folder_id`, read back, plus a draft to `delivery.recipient`. Tools that send, share or delete are refused by design.

**Mode B (no MCP server):** run `uv run pulse run --week <week> --dry-run`, then ask a Claude Code session with the claude.ai Google Drive and Gmail connectors to publish `runs/<run_id>/pulse.md` as a Doc and create the draft.

## Where things live

| Path | In git? | What it is |
|---|---|---|
| `src/pulse/`, `tests/`, `config/` | yes | code, tests and settings (`config/pulse.yaml`, `config/mcp.yaml`) |
| `.env.example` | yes | template listing the secrets the project needs |
| `.env` | **no** | your secrets |
| `data/raw/*.csv` | **no** | raw review exports from `pulse fetch` |
| `data/pulse.db` | **no** | scrubbed, normalised reviews (SQLite), rebuilt by `pulse ingest` |
| `runs/<run_id>/` | **no** | per-run `report.md`, `report.json`, `manifest.json`, `taxonomy.json`, `aggregates.json`, `quotes.json`, `pulse_draft.json`, `pulse.md`, `validation.json`, `delivery.json` |
| `evals/gold/*.csv` | **no** | the human-labelled gold set (real, scrubbed review text); back it up privately |
| `evals/gold/*.meta.json` | yes | gold-set metadata (taxonomy version, κ, annotators) |

## Test data is made up

Everything under `tests/fixtures/` and `evals/redteam/` is synthetic. Names, phone numbers, PAN, Aadhaar, UPI and account numbers there are invented for the tests and belong to no one. Real reviews are only ever fetched into `data/`, which is never committed.
