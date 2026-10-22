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
```
