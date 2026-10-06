# Verification record

## Independent repository release — 2026-10-06

The `codex-job-agent` distribution was exported into a fresh local repository
containing only the Codex runtime, its skills, configuration, evaluation data,
tests and documentation. The legacy `src/applypilot` package is not included.
Package metadata names WItaZhang as the author; provenance and the retained
license are recorded separately in `NOTICE.md` and `LICENSE`.

A new Python 3.11 environment installed successfully with
`uv sync --locked --extra dev`. The complete standalone test suite passed
**208 tests in 92.65 seconds**. Ruff lint and formatting passed for all 49
Python files; `applypilot-agent --help` ran in this standalone environment.
`uv build` produced a wheel and source archive. Archive inspection confirmed
that the wheel contains only the new runtime, includes both license and notice,
and excludes personal data and logs. The source archive includes the repository
skills, configurations, tests and lockfile. README, contribution and notice
links resolve locally, and all four skill UI metadata files parse successfully.

The three-scenario Chromium demo passed at
`logs/20261006_213837_412962_local_browser_demo/`: reviewed and automatic
applications were confirmed, the missing receipt remained unknown, and the
independent loopback ATS received exactly one POST per scenario. The synthetic
evaluator accepted the candidate and rejected the deliberately faulty baseline
at `logs/20261006_213854_378206_synthetic_contract_demo/`.

The earlier records below describe development-time validation before this
independent export. Their ignored local log paths are historical references,
not artifacts included in a fresh clone. Reproduce the mechanical checks with
the tracked tests, demo and evaluation configurations.

## Earlier development verification

Verified locally on Windows / Python 3.11 / uv 0.11.3. The initial runtime checks
ran on local date 2026-10-02; the quality lifecycle extension was verified on
2026-10-03. UTC artifact directories record their respective run times.

## Quality lifecycle extension — 2026-10-03

The updated full suite passed **208 tests in 74.38 seconds** on Windows. Ruff
lint/format, locked dependency sync, Chromium availability and the coordinator
skill validator passed. New coverage includes 18 audit tests, 30 calibration
tests and 13 development-improvement tests. Fault cases cover stable sampling
across restart/concurrency, missing or changed snapshots, unknown outcomes,
review citation binding, persistent sync errors remaining visible, interrupted
result receipt recovery, and invented metrics or human-validation scope.

The three-scenario Chromium demo passed again at
`logs/20261003_180132_511006_local_browser_demo/`: exactly one loopback employer
POST per scenario, review and automatic routes confirmed, and the missing
receipt remained unknown. The synthetic evaluator accepted the candidate and
rejected the deliberately faulty baseline at
`logs/20261003_180132_161593_synthetic_contract_demo/`. These retain their original
engineering-only scope.

Actual model review evidence is under
`logs/20261003_174944_quality_live_validation/` (ignored local artifacts):

- **Judge diagnostic:** six controlled synthetic cases, each presented in both
  A/B orders to a fresh ephemeral Codex CLI context: 12 model-proxy decisions,
  no missing/unknown result, padding preference 0/4, order inconsistency 0/6,
  same-length degradation failures 0/4, useful-longer failures 0/4. The host
  reported model alias `gpt-6-astra`, reasoning effort `ultra`; the underlying
  snapshot was not exposed. JSON event records showed no tool calls. The
  manifest, exact inputs, configuration, results, receipts and metrics remain
  in `20261003_174944_592304_judge_calibration/` and its adjacent reviewer logs.
- **Audit forward-test:** ten mocked submissions produced one stable random
  ticket. Every synthetic attachment contained an inserted unsupported 80%
  revenue claim; the reviewers were not told to find it. Separate hiring and
  factual contexts returned a specificity concern and an unsupported-claim
  finding, both citing the frozen sources. Recording created one report and
  one user-facing alert; no pending review remained. `audit-report.json` keeps
  both findings and limitations. No real employer was contacted. Document
  rendering was unavailable to these reviewers, so layout was not assessed.
- **UI:** generated the dashboard from that audit state and visually inspected
  `dashboard-quality.png`; the pending alert and job identity are visible.

This is one small diagnostic run and one injected-defect workflow test, not
evidence of bias elimination, representative defect recall or improved hiring
outcomes. Fresh sessions and read-only invocation are not a filesystem security
boundary; isolation is honestly recorded as `instruction_only`. The calibration
rubric and application audit rubric are separate instruments; this diagnostic
does not certify every future rubric or model configuration.

Independent code review identified and prompted fixes for hidden sync warnings,
acceptance of unverified evaluation claims, and result/receipt interruption
recovery. The runtime records RCA proposals and development validation but never
rewrites or deploys its own skills. See [QUALITY.md](QUALITY.md) for the operating
contract and [RESUME_NOTES.md](RESUME_NOTES.md) for evidence-bounded project wording.

## Evidence by requirement

| Requirement | Evidence | Scope |
| --- | --- | --- |
| Codex skill-set delivery | Four `.agents/skills/*/SKILL.md` entrypoints and UI metadata; all pass bundled `quick_validate.py` | Repository skill discovery, not a globally installed plugin |
| Modular implementation | Separate domain, config, store, matching, policy, materials, execution, browser, discovery, CLI and evaluation modules | New runtime does not import legacy ApplyPilot modules |
| First-use personalization | Onboarding skill + Profile/Fact/Constraint/Policy schemas + versioned profile storage | Confirmation comes from the user; no inferred biographical facts |
| Broad candidate coverage | Three public board adapters; 65 discovery tests; live read-only Lever demo GET | Greenhouse/Lever/Ashby discovery, not automatic submission compatibility |
| 50–100 daily processing design | YAML budgets; 100-job queue test; reserved high-fit capacity; UTC attempt budget | Functional scheduling evidence, not a measured 100-submission/day claim |
| High-fit evidence and materials | Referenced confirmed facts; PDF/HTML/Markdown rendering; attachment SHA256 and fact provenance; semantic rewrites default to review | Fact references alone do not prove semantic entailment |
| Automatic and review routes | Real Chromium three-scenario demo; independent employer POST ledger | Loopback synthetic ATS |
| User approval remains meaningful | Exact packet/policy binding; changed facts/files/job invalidation; zero-budget test | Normal tool path; not a security sandbox against arbitrary shell access |
| No blind duplicate retry | OS lease tests with live spawned processes and forced termination; unknown outcome blocked | Windows exercised locally; Linux CI configured but not run here |
| Persistent user rejection | Reassessment and execution tests retain explicit user-no | Explicit preferences over generated fit |
| Flexible Codex workflow | Independent agent invoked the skills and actual CLI against raw synthetic user inputs | One scenario set, not a broad model-quality benchmark |
| Usable consolidated UI | Local dashboard visually inspected; filter controls tested in Chromium | Static review snapshot; approval is still via Codex conversation |
| Independent GT evaluation | Strict data schemas, group split checks, missing-output denominators, unknown labels, external blind labels and runtime export | Public synthetic GT; no real-user hiring claims |
| Reproducible engineering | `uv.lock`, config/input snapshots, run logs, hashes, README, architecture and CI workflow | GitHub Actions workflow has not been remotely executed |

## Commands and results

```text
uv run ruff check src/applypilot_agent tests/agent
uv run ruff format --check src/applypilot_agent tests/agent
uv lock --check
uv run python -m pytest tests/agent -q
```

Final full suite at verification: **146 passed in 100.70 seconds**. A subsequent
evaluation-only change added explicit verified-submission coverage, separate
from selection recall; its full module regression passed **12 tests**, including
the new distinction. All other runtime code remained unchanged after the full
suite. Skill frontmatter, examples and actual CLI interfaces were also checked.

```text
uv run python -m applypilot_agent.demo --config configs/demo.yaml
uv run python -m applypilot_agent.evaluation --config configs/evaluation.yaml
```

The PDF browser demo passed and created:

`logs/20261003_052911_441855_local_browser_demo/`

- `overview.html`, `summary.json`, copied config and run log.
- Independent `employer-ledger.jsonl` and persisted runtime events.
- Exactly one POST per scenario, with received name/email and PDF SHA256 verified.
- Review-approved → submitted; automatic scope → submitted; missing receipt →
  unknown even though the server received it. Retrying unknown was blocked.

The standalone evaluator correctly accepted the synthetic candidate and rejected
the intentionally faulty baseline. Runtime observations exported from the demo
and independent skill test also passed the applicable structural gates. A first
attempt using only three positive demo cases correctly failed the false-selection
gate for insufficient negative labels; that failed report was retained.

## Independent skill forward-test

The test agent received skills, user preferences, raw synthetic profile/jobs and
a controlled-site form contract. It was not given expected outputs, implementation
patches, prior results or the employer ledger. It used the CLI rather than writing
the database or running the canned demo.

Observed outcomes:

- Data Engineer: automatically submitted with receipt `DEMO-one-1`.
- Onsite Rust role: skipped for violating the user's remote constraint.
- Analytics role with unspecified sponsorship: `needs_info`, no submission.
- Exactly one independent employer POST; no repeated user questions.

Artifacts: `logs/skill-forward-test/state/dashboard.html`,
`logs/skill-forward-test/dashboard.png`, and
`logs/skill-forward-test/runs/20261002_forward-session/session-report.json`.

The test exposed two integration issues, both fixed and documented: manual
loopback URLs were rejected by the CLI import boundary; an attachment schema
change during the running test required refreshed provenance metadata. Dashboard
labels and state-specific guidance were also corrected. This was a development
forward-test with repair, not a claim of a perfect first run.

## Real-site check and remaining boundaries

The official Lever demo form was inspected without entering personal data,
uploading anything or allowing POST requests. Its 52 visible fields include 18
native radio controls. Unique selectors for repeated names and radio support
were added and tested; all 52 controls then had selectors.

The page also uses hCaptcha/Cloudflare and posts the selected resume to
`/parseResume`, which can alter filled fields. **Lever automatic submission is
not verified or claimed.** It needs an explicit upload adapter, post-autofill
verification, ordinary human CAPTCHA handoff and a tested receipt contract.
Employer-only API keys are not assumed available to personal applicants.

Managed execution currently supports identifiable native single-page controls,
with configured origins and an established English acknowledgement contract.
Unknown receipt wording, login/CAPTCHA, iframes, custom widgets, multi-step flows
and preparatory background writes require a supported adapter or concrete handoff.
Browser acknowledgement is identified as `observed_page_acknowledgement`; it is
not authenticated ATS backend persistence.

No real applications, messages or user-personal profiles were used in testing.
The test ATS processes are stopped after verification. Local logs/artifacts are
ignored by Git; the tracked demo and tests reproduce their mechanics.
