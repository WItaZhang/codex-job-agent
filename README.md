# Codex Job Agent

**English** · [简体中文](README.zh-CN.md)

**More relevant opportunities. More care for your strongest matches.**

A personal job-search agent built for **Codex**, delivered as repository skills and a local Python toolkit. Tell Codex what you want; it researches jobs, prepares applications and resumes work from saved state. You choose which applications can proceed automatically and which need your review.

- **Personal from the start.** Confirm your experience, preferences and constraints once; carry them into later sessions.
- **Coverage with priorities.** Discover openings across public Greenhouse, Lever and Ashby boards, merge duplicate targets and focus preparation on strong matches.
- **A manageable review queue.** Collect missing information, application decisions and quality findings in a local inbox and dashboard.

## How it works

![Three layers: Codex plans the work, local tools control execution, and evidence supports review.](docs/assets/architecture.en.svg)

**Codex plans and writes.** Four skills help it choose the next useful action from your goals and the current queue. It uses the active Codex session; no separate model API key is required by this project.

**Local tools maintain state and execute.** A structured CLI handles profiles, jobs, materials, policy, persistence and browser operations. Approval binds the complete application packet and its versions. The executor records intent before submission; an unclear receipt stays `unknown` until checked.

**Evidence supports review and development.** Frozen application packets support sampled quality reviews. Findings feed independent regression checks during development; the running agent does not rewrite its own code, skills or evaluators.

## Get started

You need [uv](https://docs.astral.sh/uv/) and Codex with access to a local project. `.python-version` selects Python; `uv.lock` fixes the dependency versions.

```sh
git clone https://github.com/WItaZhang/codex-job-agent.git
cd codex-job-agent
uv sync --locked --extra dev
uv run playwright install chromium
uv run applypilot-agent --help
```

Open this directory as a **Codex project**, then start with:

> Use $applypilot-onboard to set up my job-search assistant. I'll provide my resume. Help me confirm my experience, job preferences and constraints, then agree with me on which applications can be automatic and which need review.

For a later session:

> Use $applypilot to continue my search today. Process up to 80 openings, prioritize preparation for strong matches, and follow my saved application policy. Bring me the questions that need my input together.

The job count is a session budget for research, screening and preparation—not a measured throughput or a promise of that many submissions. Work runs in the active Codex session; there is no built-in background scheduler.

### Four skills, one assistant

| Skill | Role |
| --- | --- |
| `$applypilot` | Coordinate the session, queue, applications and quality reviews |
| `$applypilot-onboard` | Confirm facts, preferences, constraints and submission authorization |
| `$applypilot-discover` | Find openings, research requirements and record supported fit judgments |
| `$applypilot-prepare` | Prepare packets, inspect forms, dry-run and execute authorized submissions |

Skills live in [`.agents/skills/`](.agents/skills/). If they are not visible in the current session, open a new session in this project or ask Codex to read the relevant `SKILL.md`.

The project is named `codex-job-agent`; the `applypilot*` skill names, `applypilot-agent` CLI and `applypilot_agent` Python module remain for compatibility with existing usage.

## Your configuration and data

[`configs/agent.yaml`](configs/agent.yaml) controls workload, daily submission attempts, review policy, browser settings and quality sampling. **Review is required by default.** Onboarding sets the automatic submission boundary from your authorization. Configured paths resolve relative to the YAML file.

```sh
uv run applypilot-agent --config configs/agent.yaml status
uv run applypilot-agent --config configs/agent.yaml inbox
uv run applypilot-agent --config configs/agent.yaml dashboard
```

Personal state and application files stay in Git-ignored `data/local/`; experiment and demo outputs go to `logs/`. `dashboard` generates a local page. Keep resumes, contact details and browser login state out of Git. Content used for reasoning enters your Codex session; local storage does not mean offline inference.

## Quality and current scope

The executor freezes authorized materials before submission, and local tools sample confirmed submissions. The coordinating skill arranges independent Codex contexts to review relevance and factual support. Findings retain evidence and appear in the review queue. Evaluator bias checks and version comparisons support development.

Model reviews are **proxy judgments**; synthetic labels are **engineering fixtures**. Neither establishes real interview or offer outcomes. See the [quality design](docs/agent/QUALITY.md) and [verification record](docs/agent/VERIFICATION.md) for evidence and coverage.

- **Browser support:** observable native forms. Complex widgets, iframes, authentication, CAPTCHAs or uploads during form filling may require an adapter or human handoff; ATS support is not universal.
- **Materials:** the built-in renderer formats confirmed facts. Source references and file hashes check provenance and integrity; they cannot prove every rewrite is factually correct.
- **Execution boundaries:** tools check the supported execution path. They are not a security sandbox for an agent with full shell access.
- **Validation:** the initial release passed 208 local tests and [CI on Windows and Ubuntu](https://github.com/WItaZhang/codex-job-agent/actions/runs/37535531580). Real hiring outcomes and cost improvements have not been established.

<details>
<summary><strong>Run the engineering checks</strong></summary>

```sh
uv run ruff check src/applypilot_agent tests/agent
uv run ruff format --check src/applypilot_agent tests/agent
uv run python -m pytest tests/agent -q

# Browser demo against an isolated local mock ATS
uv run python -m applypilot_agent.demo --config configs/demo.yaml

# Baseline and candidate comparison on synthetic engineering cases
uv run python -m applypilot_agent.evaluation --config configs/evaluation.yaml
```

Tests cover authorization and packet versions, concurrent execution, submission intent, interruption recovery, unknown outcomes, quality sampling and evaluation inputs. The demo submits only to the local mock ATS.

</details>

## Explore the project

```text
.agents/skills/         Codex skills and operating references
configs/               Runtime, demo and evaluation configuration
src/applypilot_agent/   Modular runtime, quality and evaluation tools
tests/agent/           Unit, integration and local browser tests
evals/                 Evaluation fixtures with labelled provenance
docs/agent/            Design, usage and verification documentation
```

Detailed guides are available in English and Chinese, as indicated below.

| Guide | What it covers |
| --- | --- |
| [Architecture · 中文](docs/agent/ARCHITECTURE.md) | Components, responsibilities and design tradeoffs |
| [Usage · 中文](docs/agent/USAGE.md) | Setup, commands and operating boundaries |
| [Implementation](docs/agent/IMPLEMENTATION.md) | Contracts and module responsibilities |
| [Quality · 中文](docs/agent/QUALITY.md) | Sampling, evaluator calibration and controlled iteration |
| [Evaluation data](evals/README.md) | Ground truth, provenance and dataset conventions |
| [Verification](docs/agent/VERIFICATION.md) | Recorded checks and their scope |

## License and acknowledgements

Developed and maintained by **WItaZhang**. This independent repository contains the implementation designed for Codex. Project provenance and acknowledgements are recorded in [NOTICE.md](NOTICE.md). Licensed under [AGPL-3.0-only](LICENSE).
