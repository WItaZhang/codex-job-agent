# Contributing

Read [AGENTS.md](AGENTS.md) and [the architecture](docs/agent/ARCHITECTURE.md)
before changing the runtime. Keep Codex planning separate from deterministic
policy, persistence, browser I/O and evaluation.

Use the locked environment:

```sh
uv sync --locked --extra dev
uv run playwright install chromium
uv run ruff check src/applypilot_agent tests/agent
uv run ruff format --check src/applypilot_agent tests/agent
uv run python -m pytest tests/agent -q
uv run python -m applypilot_agent.demo --config configs/demo.yaml
uv run python -m applypilot_agent.evaluation --config configs/evaluation.yaml
```

Use `uv add` for dependencies and include `uv.lock` with dependency changes.
Keep tunable paths, budgets and experiment settings in `configs/`. Commit only
labelled synthetic examples; personal state under `data/local` and experiment
outputs under `logs` must remain untracked.

Browser tests submit only to the isolated loopback ATS. Development checks
against real sites are read-only unless a user has authorized the specific
external action. Preserve evidence for unknown submission outcomes rather than
retrying by assumption.

Explain the behavior being changed, its evidence and relevant validation in
each change. Do not alter labels or evaluators to make a runtime change pass.
Model reviews and synthetic checks must not be described as measured hiring
outcomes. Update the operating guide when public behavior changes.
