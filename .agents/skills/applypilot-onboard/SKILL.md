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
not user data; its facts are deliberately unconfirmed.

## Personalize with few questions

Infer an initial draft from provided context, then ask only consequential gaps
in one compact batch. Reuse already explicit answers. Cover these distinct items:

- **Facts:** user-confirmed experience, skills, education, dates and contact or
  form answers. Give every fact a stable ID, source and optional unique `key`.
  `confirmed: true` requires an explicit user statement, a previously confirmed
  record, or the user's authorization to treat a designated source as factual.
  Merely finding a file or extracting a model's guess does not confirm it.
  Use a fact's optional typed `value` for a precisely confirmed form answer
  (including a boolean); retain readable source context in `text`. For an
  answer or consent valid only for particular jobs, set `scope_job_ids` to those
  actual IDs. Do not turn a job-specific agreement into reusable blanket consent.
- **Direction and preferences:** role families, important working conditions,
  preferred and avoided terms. Preferences are not claims about qualifications.
- **Hard constraints:** only requirements the user really treats as mandatory.
  Use `title`, `company`, `location` or documented job attributes. Operators are
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

Write the completed profile JSON under the configured `data_dir` (default
`data/local/profile.json`), keeping existing fact IDs stable when their meaning
is unchanged. Use `profile-set`; never patch the database.

```text
uv run applypilot-agent --config configs/agent.yaml profile-set data/local/profile.json
uv run applypilot-agent --config configs/agent.yaml profile
```

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
