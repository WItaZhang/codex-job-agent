# Evaluating agent iterations

This package evaluates **externally labelled tasks and observed outcomes**. It
does not ask the application agent to grade itself. The included synthetic
candidate and deliberately faulty baseline are fixed observations used to check
the evaluator's behaviour. They are not executions of the runtime, measurements
of improvement, or real-user validation.

```powershell
uv run python -m applypilot_agent.evaluation --config configs/evaluation.yaml
uv run python -m pytest tests/agent/test_evaluation.py
```

The Python API is `run_evaluation(config_path) -> dict`. CLI exit codes are `0`
for candidate gates passing, `1` for measured gates failing (including insufficient
labels), and `2` for invalid inputs. Paths resolve relative to the YAML file. Each
run creates `logs/<UTC timestamp>_<experiment>/config.yaml`, `metrics.json` and
`run.log`. The report records SHA-256 hashes of the config and input files. Source
inputs are read-only; the exact evaluated bytes are archived under the run's
`inputs/` directory. Run with that folder's `replay.yaml` to reproduce metrics
even if the original input files change. The original `config.yaml` is preserved
unchanged for audit, while `replay.yaml` points to the archived snapshots.

## Runtime review and judge calibration

Post-submission sampling now has a separate runtime in `applypilot_agent.quality`:
it freezes authorized material, exports neutral reviewer views, and validates
independent model-proxy reports. Judge calibration is available through
`python -m applypilot_agent.evaluation.calibration`; its synthetic controlled
contrasts test padding, content degradation, useful detail and swapped order.
These do not turn the optional comparison labels below into authenticated human
outcomes. See [the quality lifecycle](../docs/agent/QUALITY.md).

## Input contracts

One JSON object per line, with strict types and no unknown fields:

| Input | Required content |
| --- | --- |
| Cases | `task_id`, leakage `group_id`, `split` (`dev`/`holdout`), `provenance` (`synthetic`/`human_verified`), `description`, `label_source`, `expected` |
| Expected | `eligible`, `interested`, `high_priority` (each boolean or null), `must_review`, `allowed_fact_ids`, optional `expected_outcome` (acceptable state names) |
| Predictions | `task_id`, `selected`, `state`, `claim_fact_ids`, `submitted`, `review_obtained` |
| Optional blind comparisons | `task_id`, `reviewer_id`, `provenance` (`synthetic`/`human_verified`/`model_proxy`), `winner` (`baseline`/`candidate`/`tie`/`unknown`), `blinded`, `presented_first`, `evidence` |

Cases are frozen before evaluating a version. Collect user preference labels for
both selected and rejected jobs; otherwise false rejections are invisible. Facts
come from confirmed source material, authorizations from versioned user policy,
and operation results from independent execution evidence. Material quality is
assessed using blinded version comparisons, not a canonical target resume or
string similarity. A quality label must be curated against the exact artifact
versions evaluated; this first schema records input-file hashes but does not
verify resume artifacts or authenticate reviewers.

Duplicate task IDs, predictions for unknown tasks, duplicate reviewer/task
judgements, and groups spanning dev and holdout are rejected. These checks run
before split selection. Group IDs must be assigned by the dataset curator to
keep near-duplicate users, roles and templates together. The loader cannot detect
semantic duplicates that were incorrectly assigned different group IDs.

## Metrics and boundaries

- **Coverage**: selected jobs / all jobs labelled both eligible and interesting.
  It measures selection recall, not completed applications.
- **Verified submission coverage**: verified submissions among those target jobs,
  using the same full target denominator. An unknown result is not counted as a
  verified submission even when the test employer actually accepted it.
- **False selection rate**: selected jobs / all jobs explicitly labelled
  ineligible or uninteresting. Missing outputs remain in the applicable task
  denominators; completeness and outcome accuracy prevent omission from silently
  becoming success.
- **Unknowns**: tasks without enough eligibility/preference information remain
  separate from positives and negatives.
- **Outcome accuracy**: observed state is in the acceptable state set. A missing
  prediction is incorrect; tasks without a state label are not inventively scored.
- **Hard failures**: submission without a required review, submission of a known
  rejected/unselected task, or reference to a fact ID absent from allowed sources.
  Strong coverage cannot compensate for these errors.
- **Priority subgroup**: the same metrics on externally labelled priority jobs.
- **Quality**: external blind wins, losses, ties and unknowns, separated by label
  provenance. Unblinded reviews are counted and excluded from preference rates.
  Both judgement counts and unique task counts are reported; no independence or
  statistical significance is claimed from repeated judges.

Rates with no labelled denominator are null. A configured gate with no supporting
labels fails as `insufficient_labels`, not as a perfect score. Baseline/candidate
deltas are descriptive and use the same cases. Aggregate and subgroup reports do
not collapse correctness, coverage and open-ended quality into one reward.

Fact-ID membership does not prove faithful wording. `review_obtained: true` does
not establish authorization unless it was exported from a trusted, version-bound
approval record. This evaluator never authenticates arbitrary JSONL claims. The
runtime verifier and human semantic audit remain separate sources of evidence.

## Iteration protocol

1. Curate a small human-verified task set alongside synthetic boundary scenarios.
   Mark absent and disputed labels unknown; preserve the evidence and label time.
2. Give the coding agent development failures and a fixed budget to propose a
   change. Keep the acceptance labels and evaluator outside its writable scope.
3. Execute baseline and candidate against the same task inputs and controlled
   tools, exporting observations from the trusted harness. Never submit twice to
   a real employer for an experiment.
4. Run the frozen evaluation, inspect hard failures, coverage, priority outcomes,
   and a separately collected blinded material-quality comparison. Investigate
   disagreements; do not automatically rewrite expected labels to match outputs.
5. Promote only with independent review and evidence appropriate to the change.
   The public `holdout` examples in this repository test split handling; they are
   not secret acceptance data and do not demonstrate generalization.

Real recruiting outcomes are delayed and confounded. This runner intentionally
does not turn a pending response into rejection or equate an interview invitation
with proof that one execution trace is uniquely correct.

## Export actual runs

Prepare an external JSON mapping from evaluation task IDs to runtime job IDs,
then export with the same user configuration that ran those jobs:

```text
uv run applypilot-agent --config configs/agent.yaml export-observations data/local/task-map.json logs/observed.jsonl
```

The output includes a `.evidence.json` sidecar with relevant events. The exporter
checks state/evidence agreement, uses the approval associated with the submitted
packet, rejects one job inflated into multiple tasks, and leaves missing jobs
absent for the evaluator to count. This is local audit evidence, not authentication
of human approval or tamper resistance against an unrestricted filesystem writer.
