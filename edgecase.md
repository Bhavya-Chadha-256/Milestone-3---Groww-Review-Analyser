# Edge Cases — Groww Weekly Review Pulse

> This catalog covers the inputs and conditions the pipeline will meet in practice: what should happen, where it's handled, and how it's verified. Sources: [architecture.md](architecture.md) (design, §n), [implementation.md](implementation.md) (Tasks *phase.n*) and [evaluation.md](evaluation.md) (metrics `E-*`, monitors `M-*`).

## How to read this

| Column | Meaning |
|---|---|
| **ID** | Stable reference, `AREA-n`. Use it in tests, commits and reviews. |
| **Sev** | **H**: wrong, unsafe or PII-leaking output, or a silently broken pulse. **M**: the run fails or degrades loudly. **L**: cosmetic, or a cost-only effect. |
| **Status** | ✅ pinned by a named test in implementation.md · 📏 measured by an eval or monitor rather than blocked at runtime · 🟦 accepted residual risk (see §10) |
| **Owner / verified by** | Task and test name, or the metric ID |

The guiding rule comes from the architecture: **when in doubt, fail loudly and deliver nothing.** A missing pulse is recoverable. A pulse with invented quotes or leaked PII is not.

---

## 1. Resolved gaps

The first draft of this catalog found 12 gaps. Each now has an owning task and a test in implementation.md, and the config keys they needed are in architecture §10.

| # | ID | Resolution | Task | Test |
|---|---|---|---|---|
| 1 | REN-1 | Theme names are capped at 4 words during discovery | 3.2 | `test_discover_reasks_when_theme_name_too_long` |
| 2 | LLM-6 | Discovery requires 3–5 themes | 3.2 | `test_discover_reasks_when_fewer_than_three_themes` |
| 3 | OPS-8 | `today` is the IST date | 1.1, 1.3, 2.4 | `test_today_in_tz_uses_ist`, `test_fetch_uses_ist_today`, `test_cli_today_uses_ist_date` |
| 4 | ING-5 | Edited reviews replace the stored row and invalidate their classification | 2.1 | `test_upsert_replaces_edited_review_and_invalidates_classification`, 2.3 `test_newest_file_wins_for_the_same_review` |
| 5 | OPS-6 | Interrupted classification resumes from the cache | 3.3 | `test_interrupted_classification_resumes_from_cache` |
| 6 | DEL-5 | A Doc deleted by hand is recreated | 5.2 | `test_update_on_missing_doc_recreates` |
| 7 | DEL-11 | One run per ISO week at a time (file lock) | 2.4 | `test_concurrent_run_same_week_refused` |
| 8 | LLM-5 | Prompt-injection red-team suite | 6.2 | E-INJ-1 |
| 9 | AGG-6 | ISO week 53 and year boundaries | 1.1, 4.3 | `test_iso_week_53_window`, `test_meta_line_shows_both_years_across_year_boundary` |
| 10 | DEL-10 | `pulse doctor` fails on placeholder config | 5.3 | `test_doctor_fails_on_placeholder_config` |
| 11 | DEL-13 | A create call with no extractable ID fails | 5.1 | `test_create_without_extractable_id_fails` |
| 12 | ING-6 | Too few reviews stops the run before discovery | 3.5 | `test_too_few_reviews_stops_before_discovery` |

---

## 2. Ingest and sources

| ID | Case | Expected behavior | Sev | Status | Owner / verified by |
|---|---|---|---|---|---|
| ING-1 | CSV with UTF-8 BOM, CRLF line endings and multi-line quoted text | Parses correctly. Newlines inside text are kept. | M | ✅ | 2.3 `test_file_import_handles_messy_csv` |
| ING-2 | Ratings such as `"4 stars"`, `"4.0"`, `"abc"`, blank | The first two parse to 4. The others are skipped as `invalid_rating`. | M | ✅ | 2.3 (same test) |
| ING-3 | Rating-only review (empty or whitespace text) | Skipped as `empty_text` | L | ✅ | 2.3 (same test) |
| ING-4 | Rows outside the window, including future-dated rows | Skipped as `out_of_window` | L | ✅ | 2.3 (same test) |
| ING-5 | The same `native_id` arrives with edited text or rating | Row replaced, its classification deleted, counted as `updated` | H | ✅ | 2.1 `test_upsert_replaces_edited_review_and_invalidates_classification` |
| ING-6 | Window has fewer than `themes.min_reviews` (100) eligible reviews | `INSUFFICIENT_DATA` before discovery, with zero LLM calls | M | ✅ | 3.5 `test_too_few_reviews_stops_before_discovery` |
| ING-7 | File with no mapping prefix, or naming a missing mapping | Skipped with warning `unmapped_file:<name>`. Other files still import. | M | ✅ | 2.3 `test_file_without_mapping_is_skipped_with_warning` |
| ING-8 | Unparseable date | Skipped as `invalid_date` | M | ✅ | 2.3 `test_file_import_handles_messy_csv` |
| ING-9 | Naive timestamps | Read in the mapping's `date_timezone` | M | ✅ | 2.3 `test_title_is_scrubbed_and_naive_dates_use_mapping_tz` |
| ING-10 | The same reviews in two export files | Imported once | M | ✅ | 2.3 `test_overlapping_files_import_once` |
| ING-11 | Very long review | Classified normally. Never a quote candidate (60-word cap). | L | ✅ | 4.1 `test_candidate_pool_filters` |
| ING-12 | Emoji-only or number-only review | `language="other"`; stored and counted, but excluded from themes as `unsupported_language` | L | ✅ | 2.3 `test_detect_language`, `test_mark_exclusions_flags_short_and_unsupported_language` |
| ING-13 | Devanagari (Hindi) review | Stored and counted, but excluded from themes and quotes as `unsupported_language` (decision 2026-10-21: only `en` and Latin-script `hinglish` are themed, `ingest.languages`) | M | ✅ | 2.3 `test_detect_language`, `test_mark_exclusions_flags_short_and_unsupported_language` |
| ING-14 | English text with stray Hinglish marker tokens | Needs ≥2 markers to become `hinglish`. A misread is harmless. | L | ✅ | 2.3 `test_detect_language` |
| ING-15 | App Store feed: an entry with no rating (older feeds put app metadata first; as of 2026-10-21 the first entry is a review) | Skipped (no `im:rating`) | M | ✅ | 1.2 `test_parses_entries_and_skips_metadata` |
| ING-16 | App Store feed hits its 10-page (~500 review) cap with everything still in the window (≈8 weeks at Groww's volume) | Warning `app_store_feed_cap_reached`. History builds up because each weekly fetch is kept as its own raw file, deduplicated at normalise time. | M | ✅ | 1.2 `test_warns_when_feed_cap_reached` |
| ING-17 | One source fails while others succeed | Warning `source_failed:<name>` and the run continues. `FAILED` only if every source fails. | M | ✅ | 2.4 `test_source_failure_is_isolated`, 1.2 `test_http_errors_are_retried_then_page_skipped` |
| ING-18 | Wrong iOS app ID (another app's reviews) | ID `1404871703` verified on 2026-10-21 through Apple's iTunes Search API (trackName "Groww Stocks, Mutual Fund, IPO", seller Billionbrains Garage Ventures). Per-source counts and date range in `report.md` still let a human spot drift. | M | ✅ | Verified; human check H8 (evaluation §6) |
| ING-19 | Apple's feed returns empty pages intermittently (HTTP 200, no `entry`); on 2026-10-21 most pages were empty (live: 8 of 10 pages empty after 16 retries) | Each page is retried with backoff, then skipped. Paging continues, because later pages often have data. Empty pages are counted and warned. | H | ✅ | 1.2 `test_empty_page_is_retried_then_skipped` |
| ING-20 | Feed timestamps carry a `-07:00` offset | Converted to UTC at fetch time. Weeks are derived in IST later. | M | ✅ | 1.2 `test_feed_timestamps_converted_to_utc` |
| ING-21 | A Play export saved as UTF-16 (the Play Console format, and some tools) | Detected by its BOM and decoded | M | ✅ | 2.3 `test_utf16_export_is_decoded` |
| ING-22 | `pulse fetch app-store` run with the placeholder app ID | Refused with exit code 2; no file written | M | ✅ | 1.3 `test_fetch_refuses_placeholder_app_id` |
| ING-23 | A file in `data/raw/` that matches no mapping (e.g. a third-party export saved without the `generic__` prefix) | `pulse raw status` flags it and exits 1 before normalising | M | ✅ | 1.3 `test_raw_status_flags_unmapped_files` |
| ING-24 | Review whose scrubbed body has fewer than `ingest.min_words` (8) words, e.g. "nice app" (59 of the first 100 live App Store reviews) | Stored and counted toward volume and rating, but excluded from themes as `too_short`; counted in the report's Import section (about 10,450 of 13,057 live Play reviews) | M | ✅ | 2.3 `test_mark_exclusions_flags_short_and_unsupported_language`, 2.4 `test_ingest_stores_all_reviews_and_flags_exclusions` |
| ING-25 | Text in a non-Latin, non-Devanagari script (Tamil, Bengali, Gujarati…) with a few English words | Latin letters are under half of all letters, so `language="other"`, then excluded from themes | M | ✅ | 2.3 `test_detect_language` |
| ING-27 | Play fetch reaches `sources.play_store.max_reviews` before the window start | Stops, keeps what it has, and warns `play_store_cap_reached` | M | ✅ | 1.4 `test_warns_when_max_reviews_reached` |
| ING-28 | `google-play-scraper` returns naive datetimes in the machine's local time | Converted to UTC with the local zone; aware values are converted directly | H | ✅ | 1.4 `test_naive_local_timestamps_are_converted_to_utc` |
| ING-29 | Play request fails (network error, Google changes the endpoint); the library swallows the error and returns an empty page | Each page retried with backoff; if it still fails, the fetch stops, keeps earlier pages and warns `play_store_fetch_failed`; no file is written if nothing came back | M | ✅ | 1.4 `test_page_errors_are_retried_then_fetch_stops_with_warning`, `test_swallowed_library_error_is_retried_with_the_previous_token` |
| ING-26 | A stored review is edited so that it now fails the filter (shortened, or rewritten in another language) | Re-marked as excluded on the next ingest; its classifications are dropped because its text changed | M | ✅ | 2.1 `test_reviews_between_can_skip_excluded`, 2.4 `test_ingest_reflags_stored_review_that_now_fails_filter` |

---

## 3. PII and privacy

| ID | Case | Expected behavior | Sev | Status | Owner / verified by |
|---|---|---|---|---|---|
| PII-1 | Email, phone, PAN, Aadhaar, UPI, IFSC, account number, URL in text | Replaced with typed placeholders | H | ✅ | 2.2 `test_scrub_redacts_each_kind`, `test_currency_guard_needs_a_currency_word`, `test_common_pii_spellings_are_redacted` |
| PII-2 | Rupee amounts: `₹1,00,000`, `Rs 50000`, `₹ 250000000`, `INR 1234567890` | Not redacted (currency guard) | M | ✅ | 2.2 `test_currency_amounts_are_not_redacted` |
| PII-3 | Author, avatar, reviewer ID or device fields in an export | Never read into memory | H | ✅ | 2.3 `test_author_fields_never_reach_review` |
| PII-4 | PII in the title field | Scrubbed, the same as the body | H | ✅ | 2.3 `test_title_is_scrubbed_and_naive_dates_use_mapping_tz` |
| PII-5 | Names in free text ("I am Priya Sharma", "Rahul from support") | No regex catches these. The quote prompt prefers spans without names; the judge and a human check the output. | H | 🟦 📏 | E-PII-2 (6.1), H5 |
| PII-6 | Obfuscated PII: `ABCDE 1234 F`, `rahul [at] gmail [dot] com` | Redacted. Spelled-out digits remain a residual risk (§10). | H | ✅ | 2.2 `test_scrub_redacts_each_kind` |
| PII-7 | Groww client codes, order IDs, ticket numbers | Redacted as `[id]` through `privacy.extra_patterns` | H | ✅ | 2.2 `test_extra_patterns_redact_as_id`; patterns filled at the Phase 5 checkpoint |
| PII-8 | 12-digit UPI transaction references (UTR) | Redacted as `[aadhaar]` or `[account]`. Wrong label but safe, and never quoted. | L | 🟦 | — |
| PII-9 | A user literally typed `[email]` | Treated as a placeholder, so never quoted | L | ✅ | 2.2 `test_placeholder_regex`, 4.1 pool filter |
| PII-10 | Row content leaking through skip reasons, logs or exceptions | Logs and `ImportReport` hold counts and reasons only | H | ✅ | 2.3 `test_skip_reasons_never_log_row_content` |
| PII-11 | Raw exports kept past retention | `purge_raw` runs on every ingest where every source succeeded, and deletes only files that were actually read; unmapped or unreadable files are kept | M | ✅ | 2.3 `test_purge_raw_deletes_files_older_than_retention`, `test_purge_raw_keeps_files_that_were_not_read`, 2.4 `test_unread_raw_files_are_never_purged` |
| PII-12 | PII in the rendered pulse (e.g. added by the model) | V6 blocks publishing | H | ✅ | 4.3 `test_each_gate_fails[summary_with_email]` |
| PII-13 | Seeded red-team PII across the whole ingest path | 100% of seeded values absent; rupee negatives untouched | H | ✅ | 2.4 `test_pii_redteam_recall` (E-PII-3) |

---

## 4. LLM stages

| ID | Case | Expected behavior | Sev | Status | Owner / verified by |
|---|---|---|---|---|---|
| LLM-1 | `finish_reason == "content_filter"` (refusal) | `LLMRefusal`; the chunk is retried once, split in half; reviews still refused become `unclassified` | M | ✅ | 3.1 `test_content_filter_raises_refusal`, 3.3 `test_refused_chunk_is_retried_once` |
| LLM-2 | `stop_reason == "max_tokens"` | Chunk split in half recursively; a single review that still truncates is `unclassified` | M | ✅ | 3.3 `test_truncation_splits_chunk` |
| LLM-3 | Output that doesn't match the schema | Re-asked once, then `LLMSchemaError` | M | ✅ | 3.1 `test_schema_invalid_reasks_once_then_raises` |
| LLM-4 | Hallucinated or missing review IDs | Unknown dropped; missing re-queued once, then `unclassified` | M | ✅ | 3.3 (two tests) |
| LLM-5 | Prompt injection in review text | Can't trigger any action (no tools; delivery is code-driven). Must not change other reviews' labels or appear in summaries or actions. | H | 📏 | E-INJ-1 (6.2) |
| LLM-6 | Discovery returns 1–2 themes | Re-ask, then `TaxonomyError` | H | ✅ | 3.2 `test_discover_reasks_when_fewer_than_three_themes` |
| LLM-7 | Overlapping themes (e.g. Payments and Withdrawals) | Not blocked at runtime. Shows up as a flagged pair and as low per-class F1. | M | 📏 | E-TAX-2 (6.1), E-CLS-2 (3.6) |
| LLM-8 | Rediscovery runs, and the actionable `unthemed` share is still above 20% | No second rediscovery; warning `unthemed_high_after_rediscovery` | M | ✅ | 3.5 `test_second_high_unthemed_share_warns_not_loops` |
| LLM-9 | 429 or 5xx during sync calls | SDK retries; then the stage fails, and the run can resume with `--from` | M | ✅ | 3.1 (SDK behavior); resume in 2.4 and 4.4 |
| LLM-10 | Rate limit hit (429) or the per-minute token limit would be exceeded | `RateLimiter` paces calls to stay under `llm.limits.tpm`; on 429 it waits for `retry-after` and retries | M | ✅ | 3.1 `test_rate_limiter_waits_to_stay_under_tpm`, `test_rate_limiter_honours_retry_after_on_429` |
| LLM-11 | A request served by a model other than the one requested | Serving model recorded per stage and shown in the report; eval rows flagged `unexpected_model` | M | ✅ | 3.1 `test_usage_records_served_model`, 3.5 `test_served_models_in_report`, 3.6 runner test, M-LLM-1 |
| LLM-12 | A single request would exceed the per-minute token limit (e.g. a 400-review discovery prompt is ~21K tokens) | Discovery runs in 100-review passes plus a merge call; stage `max_tokens` must stay below `limits.tpm` (load error) | M | ✅ | 3.1 `test_request_larger_than_tpm_is_rejected`, `test_stage_max_tokens_must_fit_the_per_minute_limit`, 3.2 `test_discovery_runs_in_passes_then_merges` |
| LLM-13 | Reclassifying a large history (first backfill, or after a version bump) exceeds the daily token budget | Tokens estimated first; classification stops cleanly at the budget (keeping the reserve) and resumes from the cache with `--from classify` the next day | M | ✅ | 3.1 `test_daily_budget_exhausted_is_raised_before_sending`, 3.3 `test_classification_stops_at_daily_budget_and_resumes`, 3.5 `test_budget_exhaustion_fails_run_with_resume_hint` |
| LLM-14 | Model retired or renamed | `pulse doctor` checks `models.list()` for the pipeline and judge models before any call | M | ✅ | 5.3 `test_doctor_fails_without_groq_key` |
| LLM-15 | App Store reviews are about 2% of eligible ones; a sample balanced by source would take all of them | Discovery strata are `(rating, week)`, so source stays proportional and the gold set keeps App Store reviews | M | ✅ | 3.2 `test_stratified_sample_keeps_source_proportional` |
| LLM-16 | Generic praise among eligible reviews lands in `unthemed` (about 27% mention no product issue) | Rediscovery and E-TAX-1 use the actionable `unthemed` share (negative or mixed sentiment only); the raw share is still reported | H | ✅ | 3.4 `test_actionable_unthemed_share_ignores_positive_reviews`, 3.5 `test_rediscovery_uses_actionable_unthemed_share` |

---

## 5. Aggregation and time

| ID | Case | Expected behavior | Sev | Status | Owner / verified by |
|---|---|---|---|---|---|
| AGG-1 | Review at 00:30 IST on Monday (Sunday 19:00 UTC) | Belongs to the new ISO week | H | ✅ | 1.1 `test_iso_week_uses_ist_boundary` |
| AGG-2 | Run on a Sunday | Uses the previous complete week | M | ✅ | 1.1 `test_resolve_window_on_sunday_uses_previous_complete_week` |
| AGG-3 | Fewer than 3 themes have reviews | `INSUFFICIENT_DATA`; nothing delivered | H | ✅ | 3.4 `test_rank_top_raises_when_fewer_than_three_populated_themes`, 3.5 `test_insufficient_data_stops_run`, 4.5 `thin` dataset |
| AGG-4 | Latest week has no classified reviews | `trend_pts = None`; trend clause omitted | M | ✅ | 3.4 `test_trend_is_none_when_latest_week_empty` |
| AGG-5 | Two themes tie on count | Tie broken by 1–2★ share, then by ID | L | ✅ | 3.4 `test_rank_top_breaks_ties_by_low_rating_share` |
| AGG-6 | ISO week 53 and year boundaries: a run on Mon 4 Jan 2027 gives `2026-W53`, window 12 Oct 2026 – 3 Jan 2027; 29 Dec 2025 is in `2026-W01` | Week label and window are correct. The metadata line shows both years when they differ. | M | ✅ | 1.1 `test_iso_week_53_window`, `test_iso_year_differs_from_calendar_year`; 4.3 `test_meta_line_shows_both_years_across_year_boundary` |
| AGG-7 | A top theme with fewer than 10 reviews | Still ranked, but flagged `low_sample` and warned in the report | M | ✅ 📏 | 3.4 `test_low_sample_flag`, M-DATA-4 (6.3) |
| AGG-8 | Review bombing: hundreds of identical 1★ reviews | Duplicates still count but are never quoted. `duplicate_share` reported and warned above 0.10. | H | ✅ 📏 | 3.4 `test_duplicate_share_reported`, 4.5 `burst` dataset, M-DATA-3 (6.3) |
| AGG-9 | Shares that round to `0%`, or that sum past 100% | Display only; shares are per theme | L | 🟦 | — |
| AGG-10 | Reviews still unclassified after retries | Excluded from `total`; count reported | M | ✅ 📏 | 3.4 `test_unclassified_reviews_are_excluded_from_total`, M-LLM-2 |
| AGG-11 | Old → new theme mapping after a taxonomy change | Majority mapping in the report; trends always within one taxonomy version | M | ✅ | 3.4 `test_theme_mapping_uses_majority` |
| AGG-12 | Daylight saving time | N/A (IST has no DST). The schedule assumes the Mac is set to IST. | L | 🟦 | 6.4 |
| AGG-13 | The latest week is small (W42: 135 eligible reviews; one review = 0.74 pts) | Trend arrow only when the change is ≥ `themes.trend_z` standard errors; otherwise `→` | M | ✅ | 3.4 `test_trend_needs_two_standard_errors` |
| AGG-14 | Eligible reviews rate far lower than all reviews (3.0★ vs 4.4★; short reviews are 85% 5★) | Header uses window totals over all reviews; theme stats use eligible reviews; the report labels both bases | H | ✅ | 3.4 `test_window_totals_include_excluded_reviews`, 3.5 `test_themes_use_only_eligible_reviews` |
| AGG-15 | A catch-all theme (e.g. "Trading" matching a third of reviews) | Warning `broad_theme:<id>` above `monitoring.broad_theme_warn` (0.40); revise and rediscover before the gold set | M | ✅ 📏 | 3.4 `test_broad_theme_is_warned`, M-DATA-6 |

---

## 6. Quote selection

| ID | Case | Expected behavior | Sev | Status | Owner / verified by |
|---|---|---|---|---|---|
| QUO-1 | Model paraphrases or alters a word | Verification fails, then a re-ask, then the first-sentence fallback | H | ✅ | 4.1 `test_verify_quote`, `test_invalid_picks_retry_then_fall_back_to_first_sentence` |
| QUO-2 | Whitespace differs between quote and review | Normalised, so it passes | M | ✅ | 4.1 `test_verify_quote` |
| QUO-3 | No quotable candidate for a top theme | `QuoteUnavailable` → `VALIDATION_FAILED` | H | ✅ | 4.1 `test_empty_pool_raises_quote_unavailable`, 4.4 `test_quote_unavailable_fails_validation` |
| QUO-4 | Model picks a review outside the pool, or from another theme | Rejected and re-asked; V3 also checks the theme | H | ✅ | 4.1 `test_pick_outside_pool_is_rejected`, 4.3 V3 |
| QUO-5 | The same text in several reviews | Duplicates excluded; the 3 quotes always come from 3 different reviews | M | ✅ | 4.1 pool filter, 4.3 `duplicate_quote_review` |
| QUO-6 | A verbatim quote that misleads out of context (sarcasm) | Passes the verbatim check; caught by the judge and human review | M | 📏 | E-QUO-2 `in_context` (6.1), H4 |
| QUO-7 | Profanity in the best quote | The prompt says `Avoid profanity and personal attacks.`; measured by the judge | M | ✅ 📏 | 4.1 `test_quotes_prompt_has_rules`, E-QUO-3 (6.1) |
| QUO-8 | Curly vs straight apostrophes inside the quote | Normalised on both sides before comparing | M | ✅ | 4.1 `test_verify_quote_normalises_curly_apostrophes` |
| QUO-9 | A quote spanning a line break | Whitespace normalisation lets it pass | L | ✅ | 4.1 `test_verify_quote` |
| QUO-10 | Quote month near a month boundary | Computed in IST | L | ✅ | 4.1 `test_quote_month_uses_ist` |

---

## 7. Compose, render and validate

| ID | Case | Expected behavior | Sev | Status | Owner / verified by |
|---|---|---|---|---|---|
| REN-1 | Long theme names push the pulse past 250 words | Prevented at discovery (names ≤4 words) | H | ✅ | 3.2 `test_discover_reasks_when_theme_name_too_long` |
| REN-2 | Summary or action over its word limit | V5, then compose re-runs with feedback (up to 2 retries) | M | ✅ | 4.3 `test_each_gate_fails[summary_19_words]`, 4.4 `test_validation_failure_keeps_artifacts_and_stops` |
| REN-3 | Model writes a statistic ("30% of users") | V7, then compose retries | H | ✅ | 4.3 `test_each_gate_fails[summary_with_percentage]` |
| REN-4 | V7 false positive ("reply within 24 hours") | Strict by design; frequent hits show in the retry monitor | M | 🟦 📏 | M-RUN-2 (6.3) |
| REN-5 | Tokens like `2FA` and `T+1` | Allowed by V7 | M | ✅ | 4.3 `test_v7_allows_alphanumeric_tokens` |
| REN-6 | Quote contains Markdown syntax | Escaped in the Markdown render. `Quote.text` stays verbatim, and V3 still passes. | M | ✅ | 4.3 `test_markdown_special_chars_in_quote_are_escaped` |
| REN-7 | Quote contains HTML | Escaped in the HTML email | H | ✅ | 4.3 `test_render_text_has_no_markdown_and_html_escapes_review_text` |
| REN-8 | All 3 actions address the same theme | The compose prompt asks for ≥2 themes; checked in code by E-ACT-5 | L | 📏 | 4.2 prompt rule, E-ACT-5 (4.5) |
| REN-9 | Two actions say the same thing | Not gated; measured by the judge | M | 📏 | E-ACT-4 `overlap` (6.1) |
| REN-10 | Themes in the wrong order, or a non-top theme | V2, then compose retries | M | ✅ | 4.3 `test_each_gate_fails[swap_theme_order]` |
| REN-11 | Only one source enabled | The metadata line names that source only | L | ✅ | 4.3 `test_golden_render_matches_and_fits_budget` |

---

## 8. Delivery via MCP

| ID | Case | Expected behavior | Sev | Status | Owner / verified by |
|---|---|---|---|---|---|
| DEL-1 | MCP server unreachable, or a tool missing, at start | `FAILED` before any LLM call | M | ✅ | 5.3 `test_missing_mcp_tool_fails_before_llm_spend` |
| DEL-2 | A mapped tool is missing on the server | `MissingTool`, naming it | M | ✅ | 5.1 `test_missing_tool_is_reported` |
| DEL-3 | Config maps an operation to a send, share or delete tool | `ForbiddenTool` before any call | H | ✅ | 5.1 `test_forbidden_tool_refused_before_any_call` |
| DEL-4 | Auth expires mid-run | `ToolCallFailed`; the report names the re-authorise step and the `--from` command | M | ✅ | 5.3 `test_tool_failure_message_reaches_report` |
| DEL-5 | The week's Doc was deleted by hand | Recreated; ledger updated; warning `doc_recreated` | M | ✅ | 5.2 `test_update_on_missing_doc_recreates` |
| DEL-6 | Doc read-back doesn't match | One retry, then `DELIVERY_UNVERIFIED`; the draft is still created | M | ✅ 📏 | 5.2 `test_read_back_mismatch_retries_once_then_unverified`, M-DEL-1 |
| DEL-7 | Re-run after the draft already exists | Skipped unless `--force` | M | ✅ | 5.2 `test_draft_is_idempotent_unless_forced` |
| DEL-8 | Draft fails after the Doc succeeded | `PUBLISHED_DOC_ONLY`; `--from draft` resumes with no second Doc | M | ✅ | 5.3 `test_resume_from_draft_after_draft_failure` |
| DEL-9 | Crash between a create call returning and the ledger saving | A re-run may duplicate the Doc. Kept small by saving the ledger right after each create. | L | 🟦 | 5.2 ordering |
| DEL-10 | `recipient` or `drive_folder_id` still a placeholder | `pulse doctor` fails | M | ✅ | 5.3 `test_doctor_fails_on_placeholder_config` |
| DEL-11 | Concurrent runs for the same week | The second run exits with `run_in_progress` (exit code 3) | M | ✅ | 2.4 `test_concurrent_run_same_week_refused`, `test_run_in_progress_exit_code` |
| DEL-12 | Server's draft tool has no HTML argument | Optional argument omitted | L | ✅ | 5.1 `test_unmapped_optional_field_is_not_sent` |
| DEL-13 | Create result has no ID the regex can find | `ToolCallFailed`; never stores `None` | H | ✅ | 5.1 `test_create_without_extractable_id_fails` |
| DEL-14 | Review text tries to steer delivery ("email this to …") | Impossible by construction: recipient, tools and arguments come from config | H | ✅ | 6.2 `test_injection_delivery_uses_only_config_recipient` |

---

## 9. Pipeline and operations

| ID | Case | Expected behavior | Sev | Status | Owner / verified by |
|---|---|---|---|---|---|
| OPS-1 | `--from <stage>` when earlier artifacts are missing | Clear `MissingArtifact` error naming the file | M | ✅ | 2.4 `test_load_artifact_missing_errors_clearly`, 4.4 `test_resume_from_compose_with_missing_quotes_errors_clearly` |
| OPS-2 | `--week 2026-42`, or a week that hasn't finished | `ValueError` for a malformed week; `WeekNotComplete` for a current or future one | M | ✅ | 1.1 `test_window_for_week_rejects_malformed`, 2.4 `test_run_refuses_incomplete_week` |
| OPS-3 | `--week` for an old week whose raw files were purged | Works: scrubbed reviews persist in SQLite | L | ✅ | By design (architecture §7) |
| OPS-4 | Validation fails on every attempt | `VALIDATION_FAILED`; artifacts kept; nothing delivered | H | ✅ | 4.4 `test_validation_failure_keeps_artifacts_and_stops`, 5.3 `test_validation_failure_delivers_nothing` |
| OPS-5 | Mac asleep at Monday 09:00 | launchd fires the missed job on wake | L | 🟦 | 6.4 |
| OPS-6 | Process dies during classification | Results are saved after every chunk and daily token usage is persisted; the next run sends only pending reviews | M | ✅ | 3.3 `test_interrupted_classification_resumes_from_cache`, 3.1 `test_daily_usage_persists_across_clients` |
| OPS-7 | Prompt or config change between runs | Cache keyed by `prompt_version`, so reclassification happens and its cost is visible | L | ✅ | 2.1 `test_classification_cache_is_keyed_by_versions` |
| OPS-8 | `today` computed in UTC (01:00 IST Monday is still Sunday in UTC) | The CLI uses `today_in_tz(Asia/Kolkata)` | H | ✅ | 1.1 `test_today_in_tz_uses_ist`, 1.3 `test_fetch_uses_ist_today`, 2.4 `test_cli_today_uses_ist_date` |
| OPS-9 | First run: no taxonomy and no cache | Full backfill, paced and spread over two days by the daily token budget | M | ✅ | 5.3 `test_full_run_delivers` |
| OPS-10 | Groq key missing (`GROQ_API_KEY` not in the environment or `.env`) | `pulse doctor` fails its auth check | M | ✅ | 5.3 `test_doctor_fails_without_groq_key` |
| OPS-11 | `--from classify` rebuilds `report.md` from only the stages it ran | Report state is saved to `report.json` and reloaded on resume, so the Import and Window sections survive | M | ✅ | 3.5 `test_resume_keeps_earlier_report_sections` |

---

## 10. Accepted residual risks

The design accepts these rather than eliminating them. Each is watched by a human check or a metric.

| ID | Risk | Why it's accepted | Where it's watched |
|---|---|---|---|
| PII-5 | Names in free text | Regex can't catch them without heavy false positives | E-PII-2 judge, H5 |
| PII-6 (part) | PII spelled out in words ("nine eight seven…") | Rare, and impractical to pattern-match | E-PII-2 judge, H5 |
| PII-8 | UTRs labelled as Aadhaar | The outcome is safe (redacted, never quoted) | — |
| ING-16, ING-19 | App Store coverage below 12 weeks at first (~500-review cap, flaky feed) | Apple offers no other public route. History builds up across weekly fetches. | M-DATA-1, `pulse raw status` date ranges |
| Play source | Play reviews come from `google-play-scraper`, an unofficial library; Google can change the endpoint and break it | No official public API covers 12 weeks; the fetch is kept light (pauses, window stop, cap) | ING-29 warning; `pulse raw status`; per-source counts in the run report |
| ING-25 (part) | A non-English review in Latin script (e.g. Spanish) passes as `en` | Rare on the India storefront; a language-ID model would be a new dependency for little gain | Classification lands it in `unthemed`; M-DATA-2 |
| REN-4 | V7 strictness on numbers in actions | A false fail beats a hallucinated statistic | M-RUN-2 |
| DEL-9 | Crash between create and ledger save | Only a server-side search could close it | Manual cleanup; rare |
| Free-tier limits | Groq can change free-plan limits or retire a model | The project needs only a key; limits and models are config values | `pulse doctor` (LLM-14); M-COST-2 daily-budget warning |
| AGG-9, AGG-12, OPS-5 | Display rounding; DST; a sleeping Mac | Cosmetic, not applicable, or self-healing | — |
| Judge reliability | The judge (`qwen/qwen3.8-27b`) is a mid-size open model (evaluation §7.2) | Free, a different family from the pipeline model, its own quota | Calibration against human labels (evaluation §7.3) |
