# Decision ledger (snapshot)

The executor's ledger of rulings, task completions and deferred minors, copied from the git-ignored `.superpowers/sdd/implementation/progress.md` so it survives a machine change. Refresh this copy at each phase checkpoint.

```text
# SDD ledger — plan: implementation.md
Scope: Phase 0 only (Tasks 0.1, 0.2), per user request "Implement phase 0".
Spec: problemStatement.md, architecture.md, edgecase.md, evaluation.md (all present).
Pre-flight: Task 0.2 consumes load_settings/Settings from Task 0.1 (Produces block) — consistent; 0.2 tmp_settings copies config/mappings/ "when that exists" (created in Task 1.3) — consistent.
Ruling: brew install uv failed (/opt/homebrew owned by another user); installed uv via `python3 -m pip install --user uv` instead of curl|sh — avoids sudo/chown and remote script — cost if wrong: uv lives in ~/Library/Python/3.9/bin (user must add to PATH).
Ruling: no git identity configured; set repo-local identity Bhavya Chadha <bhavyachadha256@gmail.com> (GitHub: Bhavya-Chadha-256); amended the unpushed docs commit — cost if wrong: author string change via git config + amend.
Ruling: plan said "Phase 1 — Foundation & ingest" with setup as Task 1.1; user calls setup "Phase 0" — split into Phase 0 (0.1, 0.2) and renumbered Phase 1 tasks 1.1–1.4 across all docs, consistency check clean — cost if wrong: renumber back.
Task 0.1: complete (commits b90f2b9..bbe136d, tests: uv run pytest tests/test_config.py -v → ============================== 6 passed in 0.17s ===============================)
Task 0.2: Ruling: added [tool.ruff.format] exclude *.md (not in plan) — ruff format would rewrite docs' code samples — cost if wrong: one config line
Task 0.2: Ruling: added test_fixtures_dir_is_under_repo_root and Task 0.1 test_unmodified_roundtrip_loads (control) beyond the plan's tests — strengthens fixture/negative-test validity — cost if wrong: two extra tests
Task 0.2: complete (commits bbe136d..c6d370d, tests: uv run pytest -v → ============================== 10 passed in 0.11s ==============================)
Phase 0 checkpoint: pytest 10/10, ruff clean; Anthropic credential: ant not installed, ANTHROPIC_API_KEY not set (needed from Phase 2)
Final review: fresh reviewer (opus) — With fixes; 2 Important, 11 Minor.
Final: fixed llm.stages open dict — test_missing_llm_stage_is_rejected, test_unknown_llm_stage_is_rejected RED→GREEN, suite 28/28
Final: fixed missing numeric range checks — test_out_of_range_tunables_are_rejected, test_out_of_range_pricing_is_rejected RED→GREEN, suite 28/28
Final: Ruling: re-graded Minor #8 (numeric app_store_id rejected) to Important — Phase 1 sets the real numeric ID 1404871703 — fixed, test_numeric_app_store_id_loads_as_string RED→GREEN
Final: fixed (cheap, alongside) themes min<=max test, extra=forbid test, UTF-8 read, CLAUDE.md inaccuracies
Final: minor (deferred): shallow-copy test can't detect shallow copy; google-dep test ignores extras; spec hard limits (max themes ≤5, 3/3/3, ≤250) not bounded in config; late-failing settings (quote_min>pool_max, doc_title placeholders, invalid regex, tz typo); fallbacks accepts null; validate_assignment off
Final: Ruling: user rejected tests/.craft-rules.yml — refactored parametrized tests to a single case tuple instead
Ruling: user delegated decisions ("You make the decision based on our context... portfolio") — merged phase-0-setup into main (ff) and tagged phase-0 — cost if wrong: git reset main / delete tag (local only).
Ruling: Phase 1 redefined as Acquire (user: "Implement Phase 1 and next we will normalise the data"); old Phase 1 → Phase 2 Normalise; later phases +1 — cost if wrong: renumber docs back.
Ruling: Play source = third-party CSV export at first — cost if wrong: one adapter to add later (added as Task 1.4).
Task 1.1: Ruling: tz → timezone (Window field, params) — craftsman hook forbids ≤2-char names — cost if wrong: rename
Task 1.1: complete (commits 5566476..1065cad, tests: uv run pytest -q → 49 passed in 1.27s)
Task 1.2: complete (commits 1065cad..12620c0, tests: uv run pytest -q → 60 passed in 0.35s)
Task 1.3: Ruling: Mapping.format dropped (extension decides); read_rows/parse_timestamp/detect_encoding live in ingest/mapping.py for Phase 2 reuse; raw_status takes timezone — cost if wrong: small refactor
Task 1.3: complete (commits 12620c0..e9b0963, tests: uv run pytest -q → 84 passed in 0.61s)
Phase 1 final review: fresh reviewer (opus) — With fixes; 1 Important, 8 Minor.
Final: fixed raw status passing unparseable dates — test_raw_status_flags_file_whose_dates_never_parse, test_raw_status_counts_partially_bad_dates RED→GREEN, suite 93/93
Final: Ruling: re-graded Minor #2 (odd feed shapes crash fetch), #5 (tz typo / JSON non-objects crash raw status), #6 (optional columns required) to Important — each breaks on realistic input — fixed with RED→GREEN tests, suite 93/93
Final: minor (deferred): page with only invalid entries counted as entries (false cap warning); 404/wrong-ID looks like flakiness; semicolon CSV / BOM-less UTF-16 misleading message; CWD-relative config/paths; --weeks unbounded; UTC vs IST dates in fetch summary; refusals on stdout; CSV formula-injection; same-second filename overwrite
Phase 2 start (2026-10-21): branch phase-2-normalise from main (phase-1).
Ruling: user asked to drop reviews <8 words and "other language" reviews — added ingest content filter (ingest.min_words=8, ingest.languages=[en, hinglish]) at the ingest stage after scrubbing; dropped reviews never stored, counted as too_short/unsupported_language separately from skipped rows; Hinglish kept because Latin-script and quotable — cost if wrong: set languages: [en] in config.
Ruling: detect_language tightened — en/hinglish need Latin letters ≥ half of letters (ING-25) — cost if wrong: one threshold.
Ruling: stored reviews that later fail the filter are deleted with classifications (Store.delete_reviews, ING-26) — cost if wrong: one method + one stage call.
Ruling: Play reviews fetched with google-play-scraper (pulse fetch play-store), added as Task 1.4 on phase-2-normalise (Phase 1 already merged); recorded in CLAUDE.md, architecture §3.1, problemStatement — cost if wrong: revert to CSV exports.
Pre-flight (Phase 2): 2.1 Review/Store → 2.3 consumes Review (ok); 2.2 Scrubber → 2.3 normalize (ok); 2.3 FileImportSource/normalize/filter_reviews → 2.4 ingest stage (ok); 1.4 PlaySourceSettings adds sources.play_store → 2.4 build_dependencies uses only FileImportSource over data/raw (Play arrives as raw files; ok).
Task 1.4: Ruling: FetchReport reused for Play; empty page ends paging; failed page stops fetch (token needed) — cost if wrong: small client change
Task 1.4: complete (commits 75b6f37..75535c5, tests: uv run pytest -q → 109 passed in 1.06s)
Ruling: user changed the content filter to "Store all, theme the long ones" (AskUserQuestion) — supersedes the drop-at-ingest ruling: Review.excluded_reason + reviews_between(eligible_only); filter_reviews→mark_exclusions; delete_reviews removed; ING-26 = re-mark on re-ingest — cost if wrong: flip back to dropping in the ingest stage.
Task 2.1: Ruling: rating-only edits count as updated but keep classifications (only text_hash change invalidates) — ING-5 spec says "edited text or rating → row replaced"; classification depends on text — cost if wrong: one condition.
Task 2.1: complete (commits 75535c5..0ee1a58, tests: uv run pytest -q → 121 passed in 1.44s)
Task 2.2: Ruling: scrub via same-length masking instead of literal rescans of redacted text — identical results, offsets stay on the original text for find_pii — cost if wrong: switch to sequential re.sub
Task 2.2: complete (commits 0ee1a58..801b543, tests: uv run pytest -q → 141 passed in 0.84s)
Task 2.3: Ruling: ImportReport gains `duplicates` (not a skip reason) — overlapping weekly fetches would otherwise trip M-DATA-5 — cost if wrong: fold into skipped.
Task 2.3: Ruling: fixture rows r4/r6/r8 given text+rating so each trips exactly one reason (plan table left them blank, which would make r6 invalid_rating not out_of_window) — cost if wrong: none.
Task 2.3: Ruling: detect_language counts Unicode L+M categories as letters (Tamil vowel signs aren't isalpha) — makes ING-25 robust — cost if wrong: threshold tweak.
Task 2.3: complete (commits 801b543..8849294, tests: uv run pytest -q → 171 passed in 0.82s)
Task 2.4: Ruling: RunOptions gains started_at (names the run; CLI passes now_utc()) — the plan's run_id needs a clock and pipeline had none; makes tests deterministic — cost if wrong: one field.
Task 2.4: Ruling: ingest stage lives in src/pulse/stages/ingest.py, imported lazily by run_pipeline (STAGE_MODULES) — avoids a pipeline↔stage import cycle; later phases add their modules to STAGE_MODULES — cost if wrong: move one function.
Task 2.4: Ruling: purge_raw is skipped when any source failed — otherwise unread raw files could be deleted — cost if wrong: none.
Task 2.4: Ruling: stage exceptions mark the run FAILED, still write report.md and re-raise; FAILED/INSUFFICIENT_DATA/VALIDATION_FAILED stop later stages and make the CLI exit 1 — cost if wrong: small.
Task 2.4: complete (commits 8849294..e35d1ac, tests: uv run pytest -q → 189 passed in 0.99s)
Phase 2 final review: fresh reviewer (opus) — With fixes; 1 Critical, 4 Important, 7 Minor.
Final: fixed currency guard word-boundary leak — test_currency_guard_needs_a_currency_word RED→GREEN, suite 210/210
Final: fixed scrubber recall (UPI+period, unspaced any-case PAN, phone groupings) — test_common_pii_spellings_are_redacted RED→GREEN, suite 210/210
Final: Ruling: PAN case-insensitivity limited to the unspaced form — spaced + case-insensitive redacts prose like "since 2023 I" — cost if wrong: spaced lowercase PAN ("abcde 1234 f") still leaks.
Final: fixed purge deleting unread files — test_purge_raw_keeps_files_that_were_not_read, test_unread_raw_files_are_never_purged, test_nothing_is_purged_without_a_file_source RED→GREEN, suite 210/210
Final: fixed oldest-copy-wins across overlapping exports — test_newest_file_wins_for_the_same_review RED→GREEN, suite 210/210
Final: fixed Play retries dead against real library (empty page + wrapped None token) — test_empty_page_is_retried_then_fetch_stops_with_warning, test_swallowed_library_error_is_retried_with_the_previous_token, test_wrapped_none_token_ends_paging RED→GREEN (last one hung before), suite 210/210
Final: minor (deferred): invalid --week gives traceback not exit 2
Final: minor (deferred): NaN/Infinity JSON rating fails the whole file source
Final: minor (deferred): a source failing mid-read still contributes earlier rows
Final: minor (deferred): invalid privacy.extra_patterns regex fails at ingest, not load
Final: minor (deferred): obfuscated-email pattern over-redacts prose ("at home dot com")
Final: minor (deferred): lowercase IFSC missed
Final: minor (deferred): report.md rebuilt on resume drops earlier sections (noted for Phase 3)
Phase 3 plan revision (2026-10-21): analysed data/pulse.db (aggregates only) → findings D1–D8 added to implementation.md Phase 3; Tasks 3.2–3.6 + checkpoint + 4.3 header + 6.3 monitors updated; new edge cases LLM-15, LLM-16, AGG-13..15, OPS-11; E-TAX-1/M-DATA-2 now actionable unthemed share; M-DATA-6 added. Docs-only; merged to main.
Ruling: rediscovery/E-TAX-1 use actionable unthemed share (negative/mixed) — ~27% of eligible reviews are issue-free praise and would trip the 0.20 trigger every run — cost if wrong: revert to raw share.
Ruling: trend arrow gated at 2 standard errors — W42 has 135 eligible reviews, ±2 pts is noise — cost if wrong: one threshold.
Ruling: discovery strata (rating, week), not source — 41 App Store reviews would all be sampled, leaving none for the gold set — cost if wrong: re-add source.
Ruling: owner chose Groq free tier (no Anthropic key) and asked for an env file — added .env.example (committed), .env (ignored), python-dotenv + pulse.config.load_env called from the CLI; Phase 3 Task 3.1 must be re-planned for Groq limits (8K TPM / 200K TPD) before Phase 3 — cost if wrong: rename GROQ_API_KEY (e.g. xAI Grok → XAI_API_KEY).
Ruling: portability — README "Set up on any machine", docs/decision-ledger.md snapshot of this ledger committed; data stays out of git and is rebuilt by fetch + ingest — cost if wrong: App Store history beyond the feed window is lost on a new machine.
Ruling (2026-10-22): Phase 3 re-planned for the Groq free tier — openai/gpt-oss-120b for all stages, qwen/qwen3.8-27b for judges (different family, own quota); strict json_schema; RateLimiter + persisted daily token budget replace the Batch API and the $15 cost guard; discovery in 100-review passes + merge; short keys in prompts — cost if wrong: swap provider in Task 3.1 (config-driven).
Docs (2026-10-22): Play source described neutrally as google-play-scraper; synthetic test data labelled (tests/fixtures/README.md, evals/redteam/README.md, README).
Phase 3 start (2026-10-22): branch phase-3-themes; Groq key verified live (gpt-oss-120b + qwen3.8-27b, tpm 8000 / rpd 1000 from headers).
Task 3.1: Ruling: RateLimiter rejects a single request > tpm with LLMError (it could never be sent) and enforces rpd too — cost if wrong: none.
Task 3.1: Ruling: request_tokens counts prompt + schema estimate + max_completion_tokens, because Groq deducts the allowance up front (observed) — cost if wrong: slightly slower pacing.
Task 3.1: complete (commits 9da3352..e66f06e, tests: uv run pytest -q → 239 passed in 1.54s)
Task 3.2: Ruling: discovery passes sized by token estimate (≤90% tpm), discovery_batch is a cap — a fixed 100-review pass + 3000 output allowance is ~8.5K > 8K tpm — cost if wrong: one more pass.
Task 3.2: Ruling: discover_taxonomy takes settings= (LLMSettings); themes return example_keys (short keys), not review IDs; merge uses its own prompt discover_merge.md; a single-batch sample skips the merge — cost if wrong: small refactor.
Task 3.2: complete (commits 118bd1d..c20e7a5, tests: uv run pytest -q → 255 passed in 1.73s)
Task 3.3: Ruling: refused/truncated/invalid reviews are not re-queued (only keys the model skipped are) — re-queueing refusals doubled the calls for nothing — cost if wrong: one retry path.
Task 3.3: complete (commits c20e7a5..2f29bcc, tests: uv run pytest -q → 268 passed in 1.91s)
Task 3.4: complete (commits 2f29bcc..b25ca66, tests: uv run pytest -q → 288 passed in 2.44s)
Task 3.5: Ruling: Groq client built only for runs reaching discover (pulse ingest needs no key); missing key → exit 2 — cost if wrong: none.
Task 3.5: Ruling: rediscovery test asserts store + run summary taxonomy_version, not manifest.json (ingest writes the manifest before discovery) — cost if wrong: add a manifest field later.
Task 3.5: Ruling: daily budget during discovery/rediscovery also returns FAILED with a --from resume hint — cost if wrong: none.
Task 3.5: complete (commits b25ca66..2b0f74e, tests: uv run pytest -q → 301 passed in 2.56s)
Task 3.6: Ruling: class_labels() not CLASSES(); classes keyword-only in paired_bootstrap_ci; load_gold(path, *, current_version) — Python signature constraints / the check needs the version — cost if wrong: rename.
Task 3.6: Ruling: evals/gold/*.csv git-ignored (real scrubbed review text; public repo); meta committed — cost if wrong: labels must be backed up privately.
Task 3.6: Ruling: compare() recomputes a paired bootstrap for E-CLS-1 from cases.jsonl; other metrics use CI overlap — cost if wrong: none.
Task 3.6: complete (commits 2b0f74e..45d3016, tests: uv run pytest -q → 315 passed in 3.30s)
Phase 3 status (2026-10-22): Tasks 3.1–3.6 complete (315 tests). Live checkpoint day 1 started (run 2026-W42-20261022T090923): taxonomy v1 = 5 themes (support_account, app_performance, charges_fees, trading_experience, mutual_funds_sip); 250/2,177 classified at last check (~84K tokens). Observed ~180 tokens/classified review → backfill ≈3 days. Final review dispatched; act on it (or re-run) before merging.
Phase 3 final review: fresh reviewer (opus) — With fixes; 1 Critical, 6 Important, 10 Minor. Fix pass NOT yet done (session handed over 2026-10-22). Re-graded; fix list:
Final: TODO Critical C1 — rediscover-once guard is in-memory (themes.py `created_taxonomy`); a --from classify resume forgets the taxonomy was created this run → repeated rediscovery, backfill never finishes. Fix: created-in-run = taxonomy.created_from_run == context.run_id, plus a persisted "rediscovered" marker in report.counts; resume test. MUST be fixed before resuming the live backfill.
Final: TODO Important I1 — 429 handling: retry-after uncapped (hours on a per-day 429), sleeps after the last attempt, ends as generic LLMError (no resume hint); 400 json_validate_failed tokens and SDK internal retries go unrecorded. Fix: retry-after > ~60 s or "per day" body → DailyBudgetExhausted; no sleep after last attempt; Groq(max_retries=0) for 429s.
Final: TODO Important I2 — classify chunks sized by count; a 50-review chunk over tpm raises LLMError forever (same chunk on every resume). Fix: split on that error like truncation, or token-sized chunks as in discovery.
Final: TODO Important I3 — eval suite scores infra failures (budget stop, refusals, schema) as wrong answers. Fix: abort on stopped_for_budget; return failed IDs with a failure class and keep them out of `truth`.
Final: TODO Important I4 — classify PROMPT_VERSION hard-coded "classify-v1"; bumping classify.md doesn't invalidate the cache. Fix: derive from load_prompt("classify").version; test.
Final: TODO Important I5 — eval results dir <date>-<suite> is overwritten by a same-day rerun, so --compare compares a run with itself. Fix: time suffix or refuse to overwrite; update evaluation §11.
Final: TODO Important I6 — load_gold accepts gold_theme values that aren't classes, and gold IDs missing from the store are counted as unclassified. Fix: GoldMetaError listing invalid labels/IDs.
Final: TODO should-fix (re-graded from Minor, cheap and user-visible) — resume hint omits --week and --until aggregate; report.json carries stale warnings (daily_token_budget, stage_failed, duplicate broad_theme) into a resumed report; style: 2-char names `f1` (metrics.py) and `ci` (runner.py, suites/classification.py) break the CLAUDE.md naming rule.
Final: minor (deferred): unexpected_model is all-or-nothing per eval run; summary.json lacks taxonomy_version/prompt_versions/model/dataset hash/truncated/refusals (evaluation §11); parse_response empty choices / None message → IndexError not LLMError; budget stop mid-chunk discards that chunk's partial labels; LLMError/TaxonomyError/APIError/GoldMetaError surface as tracebacks; one refusing review leaves its 25-review half unclassified each run; prompts hard-code 3–5 / 4 words / 40% instead of config; --force help text mentions the removed cost guard; estimate_classify_tokens ~80 vs ~180 observed tokens/review.
Phase 3 live day 1 finished: FAILED (daily_token_budget, expected); 800/2,177 classified, 0 unclassified; 178,331 tokens / 33 calls; 1,377 remain (~2 resumes).
Final: fixed C1 (rediscover-once guard in memory) — test_resume_does_not_rediscover_taxonomy_created_in_run + test_resume_after_rediscovery_does_not_rediscover_again RED→GREEN, suite 319/319
Final: fixed should-fix resume hint — test_budget_exhaustion_fails_run_with_resume_hint (now asserts `pulse run --week 2026-W42 --from classify --until aggregate`) RED→GREEN, suite 319/319
Final: fixed should-fix stale warnings — test_resume_drops_warnings_from_rerun_stages + test_resume_drops_budget_warning_from_older_report RED→GREEN, suite 319/319
Final: Ruling: warnings are tagged by stage (report.json `warning_stages`); untagged warnings from older reports are dropped on resume only when they start with daily_token_budget or stage_failed: — keeps the live W42 run's day-1 report clean without hand-editing it — cost if wrong: an untagged legacy warning of another kind stays once.
Final: fixed I1 (429 handling) — test_daily_rate_limit_stops_for_the_day_without_waiting, test_rate_limit_gives_up_without_a_final_sleep, test_default_client_leaves_retries_to_the_rate_limiter, test_json_validate_failure_counts_toward_daily_usage, test_server_error_is_retried RED→GREEN, suite 324/324
Final: Ruling: with the SDK's retries off (max_retries=0), 5xx and connection errors are retried by GroqLLM (3 attempts, 10 s apart) — otherwise turning SDK retries off would have removed 5xx resilience — cost if wrong: one extra wait on a real outage.
Final: Ruling: a json_validate_failed rejection records the request's token estimate (prompt + output allowance), since Groq's error carries no usage — conservative for the budget guard — cost if wrong: up to ~2.5K tokens over-counted per rejection (rare).
Final: fixed I2 (chunk over tpm fails forever) — test_chunk_larger_than_tpm_is_split + test_request_larger_than_tpm_is_rejected (now RequestTooLarge) RED→GREEN, suite 326/326
Final: fixed I4 (hard-coded PROMPT_VERSION) — test_prompt_version_comes_from_prompt_file RED→GREEN, suite 326/326. PROMPT_VERSION is replaced by prompt_version(); classify.md still says classify-v1, so the live run's cache stays valid.
Final: fixed I3 (eval scores infra failures) — test_suite_aborts_when_daily_budget_runs_out, test_infrastructure_failures_are_not_scored, test_eval_suite_aborted_exits_with_message RED→GREEN, suite 329/329. ClassifyResult gained `failures` (review_id → refused | truncated | schema | missing).
Final: fixed I5 (same-day eval rerun overwrites) — test_same_day_reruns_keep_separate_results RED→GREEN, suite 330/330. EvalRun(today=) became EvalRun(started=).
Final: fixed I6 (load_gold accepts invalid labels/IDs) — test_load_gold_rejects_labels_outside_taxonomy, test_suite_refuses_gold_ids_missing_from_store, test_eval_refuses_unusable_gold_set_with_message RED→GREEN, suite 333/333. load_gold(path, current_version=) became load_gold(path, taxonomy=).
Final: fixed should-fix style (f1, ci) — rename only, suite 333/333 before and after; EvalRun.metric(ci=) became ci95=.
Phase 3 fix pass complete (2026-10-22): C1, I1–I6 and the 3 should-fix items fixed, suite 333/333, ruff clean; docs updated (implementation Status/Handoff + fix-pass table, edgecase LLM-2/3/8/10/12/13, OPS-6/11, evaluation §11). Next: resume the backfill after 00:00 UTC 2026-10-23.
Ruling: classify pending reviews newest first (user chose to build on partial labels while the backfill continues; day 1's 800 labels were the oldest, 27 Jul–26 Aug, and the report week had 0) — test_newest_reviews_are_classified_first RED→GREEN, suite 334/334 — cost if wrong: none for a full backfill; order only matters when it stops early.
Phase 4 start (2026-10-22): user decisions — (a) build on with the labels available while the W42 backfill continues in the background (daily cron 05:37 + 17:47 IST, session-only); (b) no human annotators for now, so the gold-set check (Phase 3 checkpoint) stays open; (c) Phase 5 delivery = self-hosted Google Workspace MCP server (architecture Option A); (d) recipient bhavyachadha256@gmail.com.
Ruling: Phase 4 branches from phase-3-themes as phase-4-pulse (Phase 3 not merged: its checkpoint waits on the gold set) — the user chose to build ahead of the gold set — cost if wrong: phase-3 and phase-4 merge into main together.
Task 4.1: Ruling: QuotePick carries the short key k instead of review_id — consistent with classify's reviews_json keys; models copy small ints more reliably than 16-hex IDs — cost if wrong: none (internal wire schema).
Task 4.1: complete (commit e88349b, tests: uv run pytest tests/test_quotes.py → 15 passed; suite 349 passed)
Task 4.2: complete (commit 37253b4, tests: uv run pytest tests/test_compose.py → 3 passed; suite 352 passed)
Task 4.3: complete (commit 83fe379, tests: uv run pytest tests/test_render.py tests/test_validate.py → 30 passed; suite 382 passed). Golden render read in full before committing (156 words).
Task 4.4: Ruling: plan test test_v3_failure_reruns_quotes renamed test_quote_routed_failure_reruns_quotes and driven by V6 — V3 cannot occur through the pipeline because select_quotes verifies every quote; V6 takes the same "quotes" route — cost if wrong: none.
Task 4.4: Ruling: report.md's Pulse section shows the pulse only when validation passed — CLAUDE.md allows only validated pulse content in report.md; a V6 failure could otherwise copy personal data there — cost if wrong: one more file to open when debugging.
Task 4.4: complete (commit 57b6efe, tests: uv run pytest tests/test_pipeline_pulse.py → 6 passed; suite 388 passed)
Task 4.5: Ruling: generate.py was written before its tests (out of TDD order); compensated by a mutation check (unseeded rng → test_generator_is_deterministic fails, restored → passes) — cost if wrong: none observed.
Task 4.5: Ruling: oracle fixtures + gate mutations moved into src/pulse/evals/oracle.py so the deterministic suite can run them (§5 rule 7) — cost if wrong: test-only code ships in the package (small, deterministic, no data).
Task 4.5: Ruling: E-PII-1 skips 16-char hex tokens that contain a hex letter or are stored review IDs — the first real run flagged 30 review-ID hashes (all false positives); an unknown all-digit 16-char value is still flagged — cost if wrong: a real 16-char hex-with-letters secret would be missed (not a PII pattern we scrub).
Task 4.5: complete (commit 7b325de, tests: uv run pytest tests/test_evals_pulse.py → 8 passed; suite 397 passed; pulse eval --suite deterministic on real data → all pass, E-QUO-1 n/a)
Phase 5 start (2026-10-22): branch phase-5-deliver from phase-4-pulse (Phase 4 review running in parallel; its fixes land on phase-4-pulse, then phase-5 rebases).
Task 5.1: Ruling: mcp 2.3 is installed (plan assumed 1.x): MCPServer replaces FastMCP, mcp.Client replaces ClientSession wiring and the in-memory helper — gateway wraps an entered Client — cost if wrong: none (tests pin the behavior).
Task 5.1: Ruling: ServerSpec gains `env` (names of variables passed to the stdio server) — stdio servers inherit only safe vars, so workspace-mcp would not see GOOGLE_OAUTH_CLIENT_ID/SECRET — cost if wrong: one config key.
Task 5.1: Ruling: ToolResult.id → resource_id and the `to` field → `recipient` (two-character naming rule) — cost if wrong: renames.
Task 5.1: Ruling: chosen server workspace-mcp (taylorwilsdon/google_workspace_mcp), stdio, --single-user --tools docs gmail — most widely used self-hosted Workspace MCP server; user chose "self-hosted" — cost if wrong: remap config/mcp.yaml to another server.
Task 5.1: complete (commit 7d2aa88, tests: uv run pytest tests/test_mcp_gateway.py → 9 passed; suite 406 passed)
Task 5.2: complete (commit 03359e1, tests: uv run pytest tests/test_delivery.py → 6 passed; suite 412 passed)
Phase 4 final review: fresh reviewer (opus) — With fixes; 0 Critical, 6 Important, 8 Minor.
Final(P4): Ruling: re-graded Minor "quote keeps raw whitespace" to Important — a quote spanning a paragraph break ends the Markdown blockquote in the published Doc — fixed in the pass.
Final(P4): Ruling: Important #5 (live `thin` cannot reliably end INSUFFICIENT_DATA: discovery must emit ≥3 themes and a live classifier will use the third) — E-PUL-1 is now scored on normal+burst (blocking); thin is recorded as tracking metric `thin-insufficient-data` — the low_sample flag exists precisely so small themes publish — cost if wrong: a 2-theme week could publish a pulse whose third theme has very few reviews (flagged "low sample").
Final(P4): minor (deferred): NULL-validators ignores each mutation's expected gate code.
Final(P4): minor (deferred): V2 ">5 themes" and V1 quote-count issues route to compose, which can't fix them (wasted retries).
Final(P4): minor (deferred): every V6 issue routes to quotes, even one from a draft field (3 extra quote calls).
Final(P4): minor (deferred): model-written summaries/actions/theme names aren't escape_md'd in the Markdown render.
Final(P4): minor (deferred): V4 message echoes model theme_ids into validation.json/report.md.
Final(P4): minor (deferred): pulses suite loses finished datasets if an LLMError is raised mid-suite.
Final(P4): minor (deferred): deterministic suite creates data/pulse.db on a fresh machine.
Final(P4): minor (deferred): strip(SURROUNDING_QUOTES) also strips a closing quote/apostrophe that belongs to the review.
Final(P4): fixed #1 floats in E-PII-1 scan — test_artifact_scan_ignores_decimal_fractions RED→GREEN, suite 406/406
Final(P4): fixed #3 quote word-count mismatch — test_verify_quote_counts_words_like_the_validator + test_overlong_quote_routes_to_quotes RED (checked against the old quotes.py via stash)→GREEN, suite 406/406
Final(P4): fixed #4 mid-word spans — test_verify_quote_requires_word_boundaries RED→GREEN; NULL-quotes gains a span cut mid-word, suite 406/406
Final(P4): fixed (re-graded) quote whitespace — test_quote_whitespace_is_collapsed RED→GREEN, suite 406/406
Final(P4): fixed #2 LLM/budget errors in pulse stages — test_daily_budget_in_compose_fails_with_resume_hint, test_compose_error_is_retried_as_a_failed_attempt, test_compose_that_never_answers_fails_validation, test_request_too_large_counts_as_a_failed_pick RED→GREEN, suite 410/410
Final(P4): fixed #5 (ruling above) — test_pulses_suite_records_first_attempt_pass asserts thin-insufficient-data tracking, RED (KeyError)→GREEN, suite 410/410
Final(P4): fixed #6 Status table + Handoff rewritten (Phases 3–5 in flight); edgecase/evaluation updated; cross-check 138 cited tests all present.
Task 5.3: Ruling: the Doc body is render_text (plain), not Markdown — a Doc shows Markdown syntax literally and escaped quotes would never pass read-back — cost if wrong: switch the body to render_markdown if the chosen server converts Markdown.
Task 5.3: Ruling: publish refuses unless the saved validation.json passed — `--from publish` must never deliver a pulse that failed its gates — cost if wrong: none.
Task 5.3: Ruling: run_with_gateway/gateway_problem unwrap the mcp client's task-group ExceptionGroup to the first real error — otherwise ToolCallFailed/MissingTool lose their messages — cost if wrong: a multi-error group reports only its first error.
Phase 5 paused (2026-10-22) at the user's request ("stop after stage 5 is implemented"): Tasks 5.1–5.3 built; Phase 5 review not yet run; checkpoint needs the user's Google setup.
Task 5.3: complete (commit 7dd41ee, tests: uv run pytest tests/test_pipeline_deliver.py tests/test_doctor.py → 11 passed; suite 437 passed)
Docs refresh (2026-10-22, before a context switch): architecture.md (MCP §6 as built, §3.4/3.6/3.9/5.4 updates, §14 new decisions, §15 Q3/Q5 resolved, judge line fixed, layout), CLAUDE.md (commands, mcp 2.x + workspace-mcp, where-we-are), implementation.md (new-session start steps, stale plan lines annotated). No code change.
Docs refresh 2 (2026-10-22 17:30 IST, before a window change): Handoff gained user decisions, session notes (backfill state, how to recreate the schedule, laptop requirements, extra-key answer, token breakdown, tool-listing script), waiting-on-user (Phase 5 review go-ahead, push); Task 6.4 + architecture §12 record the plan gap (weekly job must fetch before run); architecture §5.5 token breakdown; README points to the tool-listing script.
```
