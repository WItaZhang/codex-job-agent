# 统计验收表达交审（决定 26）

| 字段 | 内容 |
| --- | --- |
| 切片 | stats-acceptance：区分数值门槛与完整 B 验收 |
| 产品行为 | met 仍只计算数量/成功率；新增 assessment 及明确文案，待修复失败阻止完整验收条件，未采人工时间/未核验连续两周不显示通过 |
| 状态 | 实现完成，提交后等待独立评审，尚未整合 |
| 提交 | WItaZhang/stats-acceptance，基线 main 3917cd6；提交号见交付消息 |
| 测试 | 8 条新增合成回归先失败后通过；联合定向 90 passed；最终四项验证见下 |
| 阻塞 | 无工程阻塞；真实业务验收证据未收集，不影响本切片工程交付 |
| 下一步及估时 | 独立评审约 15–30 分钟，之后由协调者安排整合 |

## 接口及验收样例

原有 met、submitted、failed、success_rate、allowed、to_fix、not_counted、target、rate_target、week/end 的数值与意义保留。仅新增 assessment 并修正 line/tool docstring。

| 合成输入 | met | assessment.b_acceptance | 明确表述 |
| --- | --- | --- | --- |
| 200 成功 + 40 程序错误 | true | not_met | 数值门槛达到，但仍有待修复失败，本周完整验收条件未满足 |
| 200 成功 + 50 允许失败 | true | unverified | 数量/成功率门槛达到，完整 B 验收未证实 |
| 199 成功 | false | not_met | 数量门槛未达到 |
| 空数据 | false | not_met | 数据不足；成功率及 success_rate_met 为 null |
| 两个返回周各 200 成功 | 两周 true | 两周 unverified | 不自动证明人工时间或连续两周完整验收 |

assessment 还含 quantity_met、success_rate_met、no_to_fix_failures，missing_evidence 明确列 daily_user_time、two_consecutive_weeks。它只评价对应周的必要条件；不是整个历史的全局验收器，也不会把未完成的本周与上周直接拼成“两周验收通过”。完整 B 标准仍按 §9，不改 §10 第 96 条的 met 兼容意义。

兼容风险：旧消费者若仅看 met 仍可能误读；工具说明和 line 明确警示，使用方应读取 assessment。文案有意改变，既有失败分类、统计分母、时区、跳过及等待计数回归保留。无数据库迁移，无真实数据采集、申请网站访问或投递；无人工计时、成本账本、看板、冻结功能扩张。

## 验证

先失败：8 failed（缺 assessment）；实现后联合定向 tests/test_stats_acceptance.py、test_apply_fill.py、test_apply_form_fill.py：90 passed。

新检出通过 Orca 创建，无重复 agent；创建前临时 claim 空闲 main，创建后立即 release，再 claim 新检出。未改共享 owner，未使用已清理的 delegated-authority 检出。

首次全套 1560 passed / 1 failed，唯一失败是 gate_handoff 合成 fixture 的旧摘要逐字预期；仅更新预期 line，计数口径未改。针对该 fixture 及新统计回归重跑 9 passed；随后重新执行完整四项验证。

最终四项验证全部通过：`uv sync --locked`、`uv run ruff check src tests`、`uv run ruff format --check src tests`（80 文件）、`uv run python -m pytest -q`（**1561 passed，61.55 秒**）。`git diff --check` 通过。只提交本切片，未推送/合并，独立检出保留用于评审。
