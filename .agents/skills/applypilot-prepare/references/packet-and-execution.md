# Packet and execution reference

Run commands from the repository root using the active configuration. Paths in
these examples assume `configs/agent.yaml`; replace with resolved configured
locations when using a different configuration. `JOB_ID`, `FACT_ID` and
`PACKET_HASH` below are values returned by the CLI, not strings to copy literally.

## Gather the actual inputs

```text
uv run applypilot-agent --config configs/agent.yaml context JOB_ID
uv run applypilot-agent --config configs/agent.yaml inspect JOB_ID
uv run applypilot-agent --config configs/agent.yaml schema browser
```

`inspect` launches an isolated browser and returns observed controls, labels,
options, required flags and selectors. It does not infer field answers or expose
a generic submission recipe. Use actual DOM evidence or an existing verified
adapter to establish submit and receipt selectors. Do not probe a real form by
submitting fake applicants. If a receipt contract is unavailable, preparation
can continue without pretending submission is supported.

## Build the material and packet

```text
uv run applypilot-agent --config configs/agent.yaml render JOB_ID --fact FACT_ID --fact OTHER_FACT_ID
uv run applypilot-agent --config configs/agent.yaml schema packet
```

`FACT_ID` / `fact_ids` are compatibility names for current typed evidence IDs
from `context.profile_evidence`, such as `contact.email` or a project record ID.
A whole-record citation requires every populated field to be confirmed and
applicable to this job. Select an individual field when only that field is known.
No separate editable facts list exists.

`render` returns the attachment's absolute path and SHA-256, plus HTML/Markdown
paths. Select actual confirmed fact IDs in the intended order; include useful
contact, experience and education context when relevant. Rendering a PDF is the
default. `--no-pdf` creates HTML, useful for inspection, not a substitute when
the application requires a PDF. Inspect generated files before including them.

Read [packet-example.json](packet-example.json) for shape only. Its values and
selectors are synthetic, its receipt fields are intentionally null, and its
hashes will fail validation on real jobs. Replace them from current context and
actual form observations; never mark the sample profile confirmed to make the
example pass.

A packet binds:

- Exact job/profile hashes from `context`.
- Each answer to confirmed fact IDs. Keys in `answers` must exactly match their
  browser CSS selectors, and answer values must equal planned field values.
  Use confirmed typed fact values for boolean controls, and respect any
  `scope_job_ids`; an answer confirmed for another job may not apply here.
- Each substantive material claim to its supporting fact IDs. Adding an ID does
  not make an invented accomplishment true.
- Attachments to existing local files, their current SHA-256 and evidenced `fact_ids`. Use the returned
  render attachment object, or hash the user-designated external file without
  changing it. External files require review before auto-submission.
- The browser plan to the current job's exact `apply_url`. Supported field kinds
  are `text`, single `select`, `checkbox` (boolean value), `radio` (select one
  observed option with `value: true`), and single `file`.
  For a select use its observed option value, not an assumed display label.
  Each file field's value must be the absolute path of a packet attachment.
- Submission and receipt selectors/text to independently established behavior.
  They may stay null for preparation; live submission requires them.

Do not infer sensitive, legal, demographic or preference answers from a name or
resume. An answer may be a previously confirmed choice, including a decision to
decline an optional disclosure. Unanswered optional fields need not be filled.
Group missing required answers across jobs and ask once.

Save the packet under the configured data directory, then validate and prepare:

```text
uv run applypilot-agent --config configs/agent.yaml packet-set data/local/packet.json
uv run applypilot-agent --config configs/agent.yaml execute JOB_ID
```

The latter is a dry preparation: the browser is closed after observation, and
the result says `submitted: false`. A later submitted run prepares the form
again and rechecks it; do not tell the user a persistent browser tab is waiting.
Keep each job's input files in a separate local subdirectory during real runs.

## Execute under actual authorization

If `packet-set` routes to automatic handling, execute within the saved scope:

```text
uv run applypilot-agent --config configs/agent.yaml execute JOB_ID --submit
```

If it routes to review, show the concrete answers and material files first.
Only after the user approves the displayed version:

```text
uv run applypilot-agent --config configs/agent.yaml approve JOB_ID PACKET_HASH --note "The user's actual approval instruction"
uv run applypilot-agent --config configs/agent.yaml execute JOB_ID --submit
```

The note must report the real user instruction, not the quoted example. A user
may review several displayed packets together; record the exact corresponding
hash for each. Changing a packet, relevant profile or policy requires the current
authorization to be re-evaluated; do not copy an obsolete approval forward.

The executor checks policy and budget, prepares/verifies fields, durably records
the intent, then submits once and observes a receipt. Report its returned state.
Before-submit errors can enter `needs_info`; inspect their last error and stop
repeating the same unsupported plan. Codex browser tools may assist inspection
and a user handoff but must not bypass this execution boundary.

## Reconcile uncertain outcomes

```text
uv run applypilot-agent --config configs/agent.yaml inbox
uv run applypilot-agent --config configs/agent.yaml events --job-id JOB_ID
```

If an attempt is `unknown`, consult actual receipt files and the user's available
application records. No confirmation email is not proof of non-submission. Do
not invent a receipt, change state directly, or click again to test whether the
first click worked.

The current reconciliation command records **user-attested** verification. Only
after the user has verified an outcome, write evidence JSON with nonempty
`source` and `observation` describing the actual record and save it locally:

```text
uv run applypilot-agent --config configs/agent.yaml reconcile JOB_ID data/local/reconciliation.json submitted --note "The user's actual verification instruction"
```

Use `not_submitted` only for genuinely verified non-submission; it returns
`retryable` and clears old approval. An unresolved outcome stays `unknown`.
Never describe user-attested reconciliation as a browser-verified receipt.

## Explicit browser configuration

The default context is isolated and permits the initial form origin. Advanced
`browser.allowed_origins` configuration can name the exact HTTPS origins a
verified form needs (including its initial origin); this is separate from
`policy.allowed_domains`, which controls the destination's auto-submission scope.
Do not turn blocked requests into a blanket allowlist or silently enlarge it.

`browser.storage_state` may point to a user-designated Playwright state JSON for
an authenticated session. The path resolves relative to the selected YAML.
Treat it as a credential-bearing local file: use it only when explicitly
designated for this task, never copy another browser's profile or print cookie
contents. Providing session state does not make unsupported controls supported.
