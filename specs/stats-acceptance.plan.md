# 统计验收表达最小切片（决定 26）

甲方已批准范围；基线 main 3917cd6。独立分支 WItaZhang/stats-acceptance，唯一编辑者持锁；预计 1–2 小时。

1. 采访结论：§9 是完整业务标准；§10 第 96 条 met 只计算数量及成功率。工程交付不需要真实投递证明。
2. 规格原文及决定先行：met/计数/分母/分类/时区保持，新增 assessment，逐周说明证据不足。
3. 测试先行：200+40 程序错误、200+50 允许失败、199 成功、空数据、成功率不足、两周数值达到仍未证实、配置阈值和未知失败分类。
4. 最小实现：apply_fill.week_stats/_line 判断及表达；tools.apply_stats 工具说明；不改数据库、权限、投递路线或 skills。
5. 定向回归与四项验证后提交英文信息含 decision 26，交独立审阅；不合并/推送。

样例输出：200+40 -> met=true, assessment.b_acceptance=not_met, no_to_fix_failures=false；200+50允许失败 -> met=true, b_acceptance=unverified；199 -> quantity_met=false；空数据 -> success_rate_met=null。每例 missing_evidence 均包含 daily_user_time、two_consecutive_weeks。

兼容风险：旧消费者若忽略新字段仍可能误读 met；工具说明及 line 明确其范围。新增字段为增量，旧字段值不变；摘要文案有意改变。全程使用合成记录，不读取私人文件或真实投递库。
