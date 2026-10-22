# Problem Statement — Groww Weekly Review Pulse

> Source: [Context Setup/Problem Statement](Context%20Setup/Problem%20Statement). This file restructures that brief. Sections 1–8 restate the source only. Section 9 lists gaps that the source leaves open.

## 1. Overview

Build an AI workflow for the **Groww** app (Stocks, Mutual Funds, IPO). It turns raw mobile app-store feedback into a **weekly pulse** that the team can read in a few minutes. The pulse answers three questions:

- What do users care about?
- What did they actually say?
- What should we do next?

The reviews are already public. The job is to **aggregate, theme, summarize, and deliver** the insight through surfaces the team already uses:

- **Google Docs** holds the written pulse.
- **Gmail** holds a draft email to yourself.

Both are reached through MCP, so the project does not handle credentials or REST wiring itself.

- **Product:** Groww: Stocks, Mutual Fund, IPO
- **Play Store listing:** https://play.google.com/store/apps/details?id=com.nextbillion.groww&hl=en_IN
- **Course context:** AI Workflows, Agents and Evals

## 2. End-to-End Flow (what "done" looks like)

1. **Ingest:** Pull recent App Store and Play Store reviews for Groww, within the constraints in §6.
2. **Cluster:** Group the reviews into a small set of themes.
3. **Summarize:** Distill a one-page weekly note.
4. **Publish:** Put the note in Google Docs, where stakeholders can read it.
5. **Notify:** Create a draft email to yourself (or an alias) in Gmail. The email contains the pulse or links to it.

## 3. Who This Helps

| Audience | Why |
|---|---|
| Product / Growth | Prioritize fixes and improvements based on real signals |
| Support | Align messaging with what users are actually saying |
| Leadership | Get a one-page health check without reading raw reviews |

## 4. What Must Be Built

| # | Requirement | Detail |
|---|---|---|
| 1 | Review import | Reviews from roughly the **last 8–12 weeks**. Fields include rating, title, text, and date (whatever the export provides). |
| 2 | Theme clustering | **At most 5 themes**, chosen to fit the product. Examples from the brief: onboarding, KYC, payments, statements, withdrawals. |
| 3 | Weekly one-page note | **Top 3 themes**, **3 user quotes**, and **3 action ideas** |
| 4 | Email draft | A Gmail draft to yourself or an alias that contains the note or a clear pointer to it |

### Weekly pulse contents

- **Top themes:** what people talk about most (the top 3 of at most 5).
- **Real user quotes:** verbatim snippets from reviews. Never invent or reword them.
- **Three action ideas:** concrete next steps grounded in the themes.

## 5. Integrations: Google Docs & Gmail via MCP

- Use **MCP (Model Context Protocol) servers** for Google Docs and Gmail. For example, create or update the pulse document, and create the draft message.
- **Do not** integrate Google APIs directly. The primary integration path must not be a custom OAuth client plus REST client code.
- Use whichever MCP servers or connectors the environment provides for Docs and Gmail. The requirement is **MCP-first**, not manual calls to Google APIs.
- Why: Docs and Gmail stay consistent with the course tooling, and the project doesn't duplicate auth and HTTP plumbing.

## 6. Key Constraints

| Area | Constraint |
|---|---|
| Reviews | **Public review exports only.** No scraping behind store logins, and no automation that violates the stores' terms of service. |
| Themes | **Maximum 5** themes when clustering. The written pulse highlights the **top 3**. |
| Length | Keep the note scannable: **≤250 words** where applicable. |
| Privacy | **No PII** in any artifact: no usernames, emails, device IDs, or other data that identifies a reviewer. Quotes must be anonymous, with identifying details stripped as needed. |

## 7. Deliverables Checklist

- [ ] Reviews from the last 8–12 weeks imported from public sources
- [ ] Reviews clustered into ≤5 themes
- [ ] One-page weekly note (≤250 words) with the top 3 themes, 3 verbatim quotes, and 3 action ideas
- [ ] Note published to Google Docs via MCP
- [ ] Gmail draft to self or an alias, created via MCP, that contains or links to the note
- [ ] No PII anywhere in the outputs

## 8. Acceptance Criteria (derived from the brief)

- Every quote matches a real review word for word, apart from removed PII.
- Each action idea traces back to at least one of the top 3 themes.
- The theme count is ≤5. The pulse shows exactly 3 themes, 3 quotes, and 3 actions.
- The note is ≤250 words.
- Google Docs and Gmail are reached only through MCP tools.

## 9. Open Questions (not answered by the source)

- **App Store source:** The brief asks for App Store *and* Play Store reviews but links only the Play Store listing. Which iOS source should we use, and is it in scope?
  - *Resolved 2026-10-21:* in scope. Groww's iOS app ID is `1404871703`, and reviews come from Apple's public customer-reviews feed (architecture §3.1).
- **Review export method:** Which public export or dataset is the approved way to get reviews without breaking the stores' terms of service?
  - *Resolved 2026-10-21:* App Store reviews come from Apple's public feed (`pulse fetch app-store`). Play Store reviews come from the `google-play-scraper` library (`pulse fetch play-store`). Third-party CSV exports are also accepted (architecture §3.1).
- **Recipient:** Should the draft go to the user's own address or to a team alias?
- **Cadence:** "Weekly" is stated. Is the run triggered manually or scheduled?
