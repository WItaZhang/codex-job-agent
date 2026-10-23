# R1 implementation plan — decision 28

Read-only research baseline: 5991c8c; implementation base: main dc6719b. No private profile/.env/log contents read. Approved eight cases remain the contract. R1 is internal, local-only and not a public launch.

## Execution boundary and inventory

Use a persisted local_only workspace mode carried by Settings into Toolbox and adapters, never a hidden-MCP-only policy. A workspace marker identifies layout/version, not consent or human identity. Explicit old config without a workspace retains legacy semantics. Loading a marked workspace config through --config must not downgrade its mode. Reject corrupted marker/config, out-of-root storage and mismatched direct Toolbox/Store directories. No public toggle to enable real execution; fake tests use synthetic fixtures and existing dependency injection, never an HTTP/MCP bypass flag.

Toolbox direct methods are guarded before touching inputs or spawning work. An explicit local read allowlist is checked against all registered tools; all other business mutations are disabled in new workspaces pending R4/R5. Preserve local initialization and offline report generation. Read-like methods with side effects need local-only branches, not automatic permission by tier.

| Entry/path | Real side effect / R1 treatment |
| --- | --- |
| check_board / _fetch / _read_board | HTTP board/detail fetch including Avature; reject before network |
| fetch_boards / discover_jobs | threads, JobSpy/Workday/smartextract, enrichment/model calls; reject before threads/configure |
| applypilot_bridge.run_sources/configure | loads env, alters global vendor paths, creates staging DB; reject local mode before importing/configuring vendor |
| start_applying / resume_applying / start_form_fill | Chrome, Claude, npx MCP and Gmail; reject before private input/preparation/activity mutation |
| prepare_resume | _recover then CDP open_tabs despite preparation name; reject |
| apply_status / stop_applying / _recover | ps/kill/orphan cleanup; local status must not recover/kill, stop must not touch global live runner |
| save_cover_letter | headless Chrome PDF subprocess; reject real renderer in workspace; synthetic renderer tested separately |
| get_discovery_setup | currently loads .env into process environment; local-only metadata response must not load it |
| get_apply_setup | readiness may parse profile/answers; local-only response reports disabled, no profile loading or browser probe |
| ChromeRunner.__call__, ensure_chrome, run_application | second guard with same Settings before configure/Chrome/Popen; do not rely on mode=review/dry-run |
| FormFillRunner ensure/open_tabs/fill/read/tab_state and run_job | same settings guard before CDP/worker setup; checkpoint separate subprocess never launched in workspace |
| llm.make_client / Engine structured calls | local mode rejects real/host external model execution; only explicit synthetic FakeClient permitted in test harness; no real adapter implemented in R1 |
| Gmail send/read/search / npx | only via existing runner MCP child config; deny launching child, do not invent a new mail client |
| form_fill CLI report | offline local synthetic recording processing remains allowed; explicit --data-dir keeps legacy override semantics |
| watchdog/readback/checkpoint raw helpers | inventory and sink assertions cover no reachability from workspace service/runner; not a universal sandbox against arbitrary Python/vendor imports by OS owner |

All 47 registered tools must be classified; add a test that additions require review. Allowed reads initially: get_intent/get_today/show_job/get_tagging_rules/get_fetch_status/get_discovery_setup/list_open_analyses/get_analysis/get_apply_setup/get_cover_letter_materials/get_cover_letter_file/get_resume/check_cover_letter/apply_stats plus pure local apply_status/form_fill_status. No network even for public job discovery. Raw library OS access is not human auth and not claimed as tamperproof.

## Resource/build choice

Choose Hatchling wheel force-include mapping, keep uv for dependency locking/sync/build. Map only configs/default.yaml and .claude/skills/cover-letter (including references/structure.md) into a dedicated installed resource namespace; avoid source-relative fallbacks. Keep source YAML/skill single-maintained. Include those exact sources in sdist so rebuilding a wheel from extracted sdist repeats the mapping. Vendor YAML remains under main package. No entire .claude directory, host permissions or private data in either artifact. Verify editable install too; if mapping cannot supply resources there, adjust mechanism based on a failing test, never silently search repository paths in installed runtime.

Official references checked: https://hatch.pypa.io/latest/config/build/#forced-inclusion (directory mapping is recursive); https://docs.astral.sh/uv/concepts/build-backend/#file-inclusion-and-exclusion (source-include only expands sdist, not arbitrary wheel includes). Inputs: /tmp/public-r1-build-notes.md. Pin tested build backend version during implementation; no build probe performed in read-only phase.

## Layout and compatibility

init creates workspace marker + settings with absolute root/db/data paths. Empty personal state, no applicant/intent/credentials/consent/browser state. Platform default root, explicit --workspace normalized at invocation. Child paths remain root-relative, validate containment/symlink resolution. Repeat init validates existing workspace and preserves every byte; nonempty unmanaged dir, malformed marker/config or unwritable paths fail without fallback/overwrite. Preflight before Store construction prevents starting DB on bad settings. Default CLI requires init with actionable error, --help works without workspace. --config and --workspace mutually exclusive. Legacy --config relative argument and internal relative paths retain cwd semantics; marked workspace config may never use that compatibility to remove local-only mode.

## Tests first (all synthetic)

1. Missing init/workspace APIs and default resources fail; then actual wheel install init/MCP initialize/list_tools succeeds outside source (47 baseline tools).
2. SKILL.md and references readable, source hashes match both direct wheel and sdist→wheel; no site-packages parent/.claude fallback.
3. Same W from cwd A/B: same DB; W1/W2 outputs isolated; reject absolute/traversal/symlink escapes in workspace paths, legacy overrides unchanged.
4. Repeat init byte-identical; marker/settings corruption, partial workspace, read-only and conflicting directory fail without creating DB or replacing user sentinels.
5. MCP/form_fill share resolver; explicit config, data-dir priority, conflicting flags and missing recording errors regress.
6. Parametrize direct Toolbox methods and lower adapters with valid synthetic inputs; network/Popen/Chrome/npx/mail/model spies must remain zero. Include wait=True/False, resume, read-like status, rendering, .env loader. Ensure not merely failing on missing applicant files. A changed new-workspace YAML review_first/tools list cannot authorize.
7. Unique resources + internal prerelease marker; no publish job/download release; missing source reference fails artifact check.
8. Build direct wheel plus sdist, extract sdist outside repo, build second wheel, install each in fresh venv; unset PYTHONPATH, run isolated subprocess in unrelated cwd, verify imported distribution is installed wheel. No editable/source path in artifact probe. Real MCP handshake with synthetic records only, never application tools.

## Steps, ownership, verification

One developer/new worktree: plan+decision/spec first; tests red; runtime/resources/paths+guards; focused green; small commit; build roundtrip/CI; four checks once final and package smoke. Files: pyproject/uv.lock, resource helper, workspace module/CLI, config.py, mcp_server.py, tools.py, applypilot_bridge/apply_bridge/form_fill runner/CLI, llm/engine if needed, tests and internal CI script/workflow; source skill unchanged unless essential (then skill-creator). No vendor changes or R1b browser rewrite. No real browser/site/submit/private data.

Estimate 3–4 effective days; environment-independent probes/complete boundary coverage matter more than number of changed files. CI local equivalent recorded; remote CI remains pending coordinator push. Final report includes red/green evidence, exact HEAD, clean tree/lock release, remaining platforms/UI/auth/runtime/ATS blockers. No merge/push.

## Implementation refinements / evidence so far

Repository is PUBLIC (coordinator verified); internal prerelease is maturity, not access control. CI must not upload wheel artifacts or publish Releases/Pages/PyPI. Current workflow only checkout/setup, checks and disposable package probe.

The chosen force-include namespace is intent_job_agent_data; it also works in editable installs without a source-tree fallback. Direct wheel and extracted-sdist wheel resources compare exact hashes including references. Pure renderer/notify/readback/vendor OS helpers without Settings are not an arbitrary-Python sandbox; their product call paths are guarded. run_job(ctx.settings), both real runners and discovery bridges are independently guarded; per-workspace Settings, no global mode flag. The 47-tool registry defaults to denial outside the explicit local-read set; release/list mutation suggestions in the external audit are not adopted as scope expansion.

Explicit-config failures were reproduced before fixes: nonexistent/custom filename in marked workspace; settings.yaml symlink to external legacy; marker symlink. Original path context is checked before resolving, marker/config must be regular, no fallback. Offline report applicant paths are also contained within the selected data directory. Legacy CLI tests now explicitly select their old config instead of relying on removed default cwd lookup.

Red evidence: first16 tests failed; selected missing/config and symlink cases failed; run_job direct entry created output before guard and report path escape failed its expected boundary. Green checkpoints:16,28,55/59 then89 focused tests. Initial full1595/67.07s preceded final path/adapter additions; final checks recorded separately.

### Accepted early-review corrections (F1–F5)

Legacy configuration must reject resolved data_dir/db_path and their ancestors marked as a workspace, including direct Settings adapters and Store/Toolbox construction. Ordinary explicit legacy paths retain their semantics. This is a product boundary, not protection against arbitrary Python or an actor deleting markers.

Editable resource copies must follow the unique source via uv cache inputs (pyproject plus default config and the entire cover-letter tree); verify modification/addition/deletion using a disposable synthetic checkout. Installed wheels never discover source directories. Developers use uv run/sync after resource edits.

Local reads may append the actual observed company protection snapshot (decision25); this audit-only exception does not create authorization, feedback, intent or application changes. Fake flows seed the Store directly in tests; R1 does not expose intent initialization as an authorized public action. Empty vocabulary/intent reads report uninitialized state. Setup reports missing local files using metadata/stat only, never loads profile/env or probes programs.

Explicit form_fill --data-dir takes precedence over workspace data root for offline reporting. This deliberate override is outside the default workspace isolation guarantee; its selected directory still bounds local applicant paths. Legacy override semantics remain unchanged.

交审证据与入口/sink矩阵见 [public-r1.review.md](public-r1.review.md)。

### 最终验收补充：CI静态发行护栏

按甲方决定补最小测试：仓库仅已批准check workflow；contents:read，限定批准的checkout/setup与验证命令，不允许额外job/step/权限/发布触发。合成插入artifact、Release、Pages、PyPI及任意附加shell命令应失败。测试只读YAML、不执行步骤；这是防意外改动的契约回归，不防恶意改测试、被信任脚本的任意shell或供应链攻击。先红再实现，定向验证，不重跑无关大套、不触发重复CI。
