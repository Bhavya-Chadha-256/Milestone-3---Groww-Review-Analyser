# Deploying the dashboard on Render

The **Review Pulse** dashboard can run on Render's free tier as a **read-only** website, so others can open it with a link. The full pipeline stays on the laptop: it fetches reviews, labels them with Groq, writes the weekly pulse and appends it to the Google Doc. Render only shows the data.

**Decisions (user, 2026-10-22):**
- The data snapshot is committed to **this public repo** (`deploy/snapshot/`). It holds review text scrubbed of personal details, so anyone can download it from GitHub.
- The dashboard is open to **anyone with the link**. It has no password.

## How it works

1. **On the laptop:** `pulse snapshot` copies what the dashboard reads into `deploy/snapshot/`:
   - `pulse.db.gz`: the database, compressed (about 1.2 MB). It holds scrubbed reviews, labels, themes, run records and the delivery ledger.
   - `runs/<run_id>/`: each week's newest validated pulse (the JSON artifacts, `pulse.md` and the warning codes).

   Raw downloads (`data/raw/`) never leave the laptop.
2. **You push.** Render watches the branch named in `render.yaml` (`autoDeploy: true`). Every push redeploys, so new data shows up a few minutes later.
3. **On Render:** `pulse serve --public --snapshot deploy/snapshot` unpacks the database into a temporary folder and serves it read-only. A free Render service keeps no files across restarts, which is why the data comes from the repo on every start.

**Hosted mode differs from the laptop:**
- **No Generate, Publish or Draft buttons.** The server refuses run jobs (403) and never contacts Groq or the MCP server.
- It listens on all interfaces, but answers only to its own `*.onrender.com` name (`RENDER_EXTERNAL_HOSTNAME`). Any other `Host` header gets a 400.
- `/healthz` is the only path exempt from that check (Render's health probe). It returns no data.

## Environment variables

**You don't need to enter any.**

| Variable | Who sets it | Why |
|---|---|---|
| `PYTHON_VERSION` | `render.yaml` (`3.12.8`) | The project needs Python ≥ 3.12 |
| `PORT` | Render, automatically | The port the server binds |
| `RENDER_EXTERNAL_HOSTNAME` | Render, automatically | The public name the server answers to |

**Don't add these on Render.** The hosted dashboard doesn't use them, so adding them only adds risk:
- `GROQ_API_KEY`
- `MCP_AUTH_TOKEN`
- the `GOOGLE_*` variables

## First deploy (one time)

1. Make sure `main` is pushed: Render deploys `main` (`render.yaml`). All phases were merged into `main` on 2026-10-22 so Render's default branch has the dashboard.
2. In the [Render dashboard](https://dashboard.render.com): **New → Blueprint**, connect the GitHub repo `Milestone-3---Groww-Review-Analyser`, and pick `main`. Render reads `render.yaml` and creates the web service `groww-review-pulse` on the **Free** plan.
3. Wait for the build (`pip install -e ".[web]"`) and the health check (`/healthz`). Then open `https://groww-review-pulse.onrender.com`, or whatever name Render gave the service.

**Without a Blueprint:** choose **New → Web Service** and use the same values:
- Runtime: Python
- Build command: `pip install -e ".[web]"`
- Start command: `pulse serve --public --snapshot deploy/snapshot`
- Health check path: `/healthz`
- Environment variable: `PYTHON_VERSION=3.12.8`

## Updating the data

The labels arrive slowly while the Groq free-tier backfill runs (about 1,000 reviews per UTC day). The dashboard works with whatever is labelled so far. After each backfill or weekly run on the laptop:

```bash
uv run pulse snapshot                     # rewrites deploy/snapshot/
git add deploy/snapshot
git commit -m "data: refresh dashboard snapshot"
git push                                  # Render redeploys in a few minutes
```

An unchanged database produces a byte-identical `pulse.db.gz` (fixed gzip header), so there's nothing to commit when nothing changed. Each real update adds about 1.2 MB to the repo's history.

## What visitors see while the data is incomplete

The page is built for partial data:

| Situation | What the page shows |
|---|---|
| The server is asleep (free tier, after ~15 min idle) | The first visit takes ~30–60 s. If the page loads before the API, it shows "Can't reach the dashboard server" with **Try again** |
| No reviews in the snapshot | "No review data yet" |
| Only some reviews labelled (backfill running) | An amber **Labelling in progress** notice: "X of Y reviews in this window have themes so far". Theme figures cover only those |
| No themes yet | "Themes appear here once reviews have been labelled" |
| Filters match nothing | "No reviews match these active filters" with **Clear all filters** |
| A week with no pulse yet | "This week's pulse isn't available yet. It appears here after the next data update" (no Generate button) |
| The newest data is older than last week | The default week is the latest one the data covers, not an empty current week |

The header shows how fresh the data is: "Reviews to 18 Oct · updated 22 Oct".

## Limits of the free tier

- **Sleep:** the service sleeps after about 15 minutes without visitors, and the next visit wakes it (~30–60 s).
- **No persistent disk:** this design doesn't need one.
- **Monthly hours:** the free instance hours cover one always-on-ish small service. Check Render's current free-tier terms.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Deploy fails at the health check | Check the logs for `Review Pulse dashboard: http://<name>.onrender.com`. If it says "cannot serve publicly … public_hosts", `RENDER_EXTERNAL_HOSTNAME` is missing; pass `--allowed-host <name>.onrender.com` in the start command |
| Every page returns `{"error": "host not allowed"}` | A custom domain is in use. Add `--allowed-host your.domain` to the start command |
| Old numbers after a push | Render is still deploying. Check that the push went to `main`, and that `deploy/snapshot/pulse.db.gz` changed in the commit |
| `No such command 'serve'` | Render built an old commit or another branch (the log installs `anthropic` and no `fastapi`). Set **Settings → Build & Deploy → Branch** to `main` and the build command to `pip install -e ".[web]"`, then **Manual Deploy → Deploy latest commit** |
| The page has no styles or charts | The browser couldn't reach the Tailwind or cdnjs CDNs (pinned versions; see the CSP in `src/pulse/web/app.py`) |
