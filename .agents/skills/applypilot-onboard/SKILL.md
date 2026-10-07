---
name: applypilot-onboard
description: "Personalize the local ApplyPilot job agent from the user's background, job preferences and submission authorization. Use on first run or when the user changes their facts, direction or automatic-versus-review boundary."
---

# ApplyPilot personalization

Turn the user's natural-language input and selected background documents into
an editable local profile and explicit application policy. Do not ask the user
to fill JSON or repeat facts they already supplied.

From the repository root, read the chosen config and existing profile if present:

```text
uv run applypilot-agent --config configs/agent.yaml profile
uv run applypilot-agent --config configs/agent.yaml schema profile
uv run applypilot-agent --config configs/agent.yaml schema config
```

Treat “No profile” as onboarding, not a tool outage. For input structure only,
read [profile-example.json](references/profile-example.json). It is synthetic,
not user data; its records are deliberately unconfirmed.

## Personalize with few questions

Infer an initial draft from provided context, then ask only consequential gaps
in one compact batch. Reuse already explicit answers. Cover these distinct items:

- **Structured background:** put contact details in `personal`, work/visa permission
  in `work_authorization` (one record per country), then use `education`,
  `work_experience`, `projects`, `publications`, `competitions`, `skills` and
  `availability` with their typed fields. Do not store a flat `facts` list or
  duplicate a structured field in free-text notes. Read the actual schema; keep
  unknown values null and missing lists empty. Preserve date precision.
  Every entity has a globally unique stable `id` and `evidence` (source,
  confirmed, scope_job_ids). `confirmed: true` requires an explicit user
  statement, an already confirmed record, or authorization to treat the named
  source as factual. Extraction alone does not confirm information. Use
  `field_evidence` when a populated field has a different source, confirmation
  or job scope. Confirm only the exact fields supported by the source; never
  promote an inferred visa status or publication status alongside other fields.
  Foreign keys connect projects to work experiences and skills to background
  records; they do not infer additional qualifications or confirmation.
- **Direction and preferences:** role families, important working conditions,
  preferred and avoided terms, under `preferences`. Preferences are not claims
  about qualifications.
- **Hard constraints:** only requirements the user really treats as mandatory.
  Store in `preferences.constraints`; use `title`, `company`, `location` or
  documented job attributes. Operators are
  `equals`, `contains`, `excludes`, `one_of`; these are literal, case-insensitive
  comparisons, not semantic or numerical filters. Missing required attributes
  remain unknown. Do not turn a salary range into a string comparison and call
  it a valid numerical rule; evaluate such requirements explicitly in assessment.
- **Action scope:** which match categories can submit automatically, exact
  authorized destination hosts, companies requiring review, attempt limit and
  whether rewritten material requires review. Explain these as user choices,
  not implementation details.

Offer a few real job comparisons only when they would resolve ambiguous
preferences. Record a direct user choice as feedback; a non-click, generated
recruiter reaction or your own opinion is not user feedback.

## Persist what the user actually decided

The active SQLite profile is the only runtime source of truth. First-time
onboarding can start from [profile-template.json](references/profile-template.json).
For edits, always read/export the current database profile, rather than reuse an
old input file. Export to the configured private data directory, edit records
in place by stable ID, and save the complete validated result. A changed paper
status replaces that paper's status; do not append a second paper or a sentence
negating the previous value. Preserve unrelated records and their provenance.

```text
uv run applypilot-agent --config configs/agent.yaml profile-export data/local/profile.json
# Edit the exported JSON; use the profile_hash returned by that export.
uv run applypilot-agent --config configs/agent.yaml profile-set data/local/profile.json --expected-hash CURRENT_PROFILE_HASH
uv run applypilot-agent --config configs/agent.yaml profile-export data/local/profile.json
```

For a new profile, write the completed template and use `profile-set` without
`--expected-hash`. The JSON is an explicit draft/export, not a second live store;
file edits take effect only after successful `profile-set`. On a stale-hash
error, reread current state and reconcile the requested change; never retry
without the guard. Never patch the database. Old flat profiles require reviewed
conversion into typed records; do not invent structured fields from ambiguous
text or discard unresolved source material. See the profile contract in
`docs/agent/PROFILE.md`.

Save action authorization in the selected YAML's `policy`, independently of the
profile. Preserve unrelated values. An empty `auto_fit` keeps review as default.
For example, **only if the user requests these exact boundaries**, a policy can
use `auto_fit: [possible]` while strong matches remain review-only. A user may
instead authorize both categories. Never infer permission merely from a strong
match or a high daily target.

`allowed_domains` contains exact hosts, without scheme, path or wildcard, such
as `jobs.lever.co`. `review_companies` matches stored company identifiers; public
board discovery currently stores board tokens, so resolve a company name to its
token rather than assuming the display name will match. `daily_submission_limit`
counts attempts per UTC day. Keep `require_review_for_rewrites: true` unless the
user chooses otherwise; evidence IDs establish provenance, not semantic truth.

Source facts still must support a rewritten claim even when review is disabled.
External resume files require review before automatic submission; simply adding
their file hash cannot turn them into verified generated materials.

Record the user's policy instruction and date in a local onboarding note; do not
manufacture the instruction. Explain the saved scope in normal language. A
profile or policy change can invalidate old assessments or approvals; refresh
affected work from current `context` rather than rewriting old approval records.

Use an isolated data directory/config for each person. Never inspect a user's
other browser profiles, secrets, folders or old ApplyPilot database to fill gaps
without the task authorizing those sources. Unknown facts can stay unconfirmed
while discovery and other independent work continue.
