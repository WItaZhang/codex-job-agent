# 质量评测与受控迭代

质量机制分成三层：发布前检查评审器偏差，运行中抽检已确认投递的材料，
开发时把问题转成根因分析和回归验证。模型意见始终标为 `model_proxy`；
它不是公司领导的真实反馈，也不等于人类验收或招聘效果。

## 冻结材料与抽检

执行器在提交意图落盘前冻结当前获授权的申请包、岗位、资料和附件字节，
用 attempt ID、版本和哈希绑定。后来修改简历或资料不会改变这个快照。
这是当时本地授权材料的证据；不能证明第三方服务器实际保存的字节。

每满一批已确认投递的不同岗位，随机选一个并持久化批次成员和抽样结果。
默认批量为十；重启或重复同步不重新抽签。同一岗位不重复计数，试填不计入，
unknown 只有核实为 submitted 后才进入总体。旧投递缺少快照时仍保留在总体中；
抽到它就标记受阻并告警，不能换一个容易评的样本。

`configs/agent.yaml` 中的配置为：

```yaml
quality:
  enabled: true
  directory: quality  # 相对于 data_dir
  batch_size: 10
```

关闭抽检会停止新快照和自动同步。历史记录保留；之后启用不会凭空补出旧材料。
成功回执先提交到数据库，再同步抽检；后者失败不能把已知成功改成 unknown。
同步失败另留告警，状态页与待办只读已保存记录，不会因再次同步失败遮住告警。

## 独立评审与用户反馈

协调技能在当前 Codex 会话中调用以下命令：

```sh
uv run applypilot-agent --config configs/agent.yaml quality-sync
uv run applypilot-agent --config configs/agent.yaml quality-inbox
uv run applypilot-agent --config configs/agent.yaml quality-prepare TICKET_ID
uv run applypilot-agent --config configs/agent.yaml schema quality-report
uv run applypilot-agent --config configs/agent.yaml quality-record data/local/review.json
uv run applypilot-agent --config configs/agent.yaml quality-report TICKET_ID
uv run applypilot-agent --config configs/agent.yaml quality-ack ALERT_ID
```

`quality-prepare` 输出中立材料包和当前报告结构。只包含浏览器计划里实际要发送的
答案和附件，隐藏生产 Agent 的自评分、历史判断和未发送的备选内容。它不移除
简历中的姓名；这里的盲评是对生产过程盲化，不是人口属性匿名化。

两个无历史上下文的独立会话分别阅读：

- **招聘视角**：岗位、申请材料和固定量表；模拟岗位负责人判断相关性和表达。
- **事实核查**：另加已确认事实及来源映射；检查文字是否真的有依据。

报告必须绑定材料哈希、量表版本、两个不同的会话 ID 和模型信息；疑似问题
附原文引用、位置、严重度和建议。JSON 引用核对原文；二进制附件绑定文件并
要求页码等位置，程序不能认证模型是否正确读懂了 PDF。当前量表为
`application-audit-v1`，不匹配的旧包会被拒绝，需要开发期明确迁移方案。

协调技能须把新增疑似问题及时展示给用户，再确认已读。`quality-ack` 只表示
已经呈现，不代表问题已解决。没有独立评审能力时保留 pending 并说明交接。
CLI 负责证据和状态，不会自己调用模型或发外部消息。具体步骤见
[协调技能的评审流程](../../.agents/skills/applypilot/references/quality-review.md)。

这是投递后的抽样观察，无法阻止已发送的问题，也不能保证未抽到的九份正确。
默认没有后台监视器、统计漂移检验或自动暂停所有投递。现有工作台展示积压、
疑似问题、缺失快照与同步异常；只有实际记录的耗时、token、费用才进入汇总。

## 发布前校准评审器

```sh
uv run python -m applypilot_agent.evaluation.calibration prepare --config configs/judge_calibration.yaml
uv run python -m applypilot_agent.evaluation.calibration record --run RUN_DIR --result RESULT_JSON
uv run python -m applypilot_agent.evaluation.calibration report --run RUN_DIR
```

配置、合成用例、匿名 A/B 包、随机映射和输入哈希冻结在时间戳日志中。参考答案
不发给评审器。每次评审使用独立上下文，交换 A/B 顺序检查位置偏差；重复次数
不能冒充更多独立案例。当前六组用例、十二次判断分别检查：

- 冗余扩写：只增加长度、不增加信息，是否获得不合理偏好。
- 等长劣化：长度相同，但信息变得不准确，是否能分辨。
- 有用扩写：增加真实相关证据，是否误把“越短越好”当作规则。

每份结果绑定模型配置、量表、材料哈希和唯一会话 ID，并引用两侧原文。
哈希不符、重复结果、不同评审版本混入或证据缺失都会拒绝。记录和汇总共用
进程锁；中断后只允许用完全一致的有效结果恢复缺失回执，不能借恢复改判。

默认配置的模型元信息未知，因此不能取得发布资格。实际运行时复制配置，填入
宿主报告的模型和设置；不要猜测底层模型快照。合成指标夹具只测计算逻辑，
不能充当真实模型校准。通过配置门槛的 `release_eligible` 也仅表示本次诊断
满足门槛，不能解释为消除了偏差或证明真实招聘质量。仍需更多业务样本和人工校准。

## 根因分析与回归

`ImprovementService` 记录开发期变更的证据链：

```sh
uv run applypilot-agent --config configs/agent.yaml schema improvement
uv run applypilot-agent --config configs/agent.yaml improvement-propose data/local/proposal.json
uv run applypilot-agent --config configs/agent.yaml schema improvement-validation
uv run applypilot-agent --config configs/agent.yaml improvement-validate data/local/validation.json
uv run applypilot-agent --config configs/agent.yaml improvements
```

提案绑定真实告警快照，写明根因假设、证据、修正、具名回归用例和预期效果。
验收由与提案作者不同的 reviewer 记录，关联不同 baseline/candidate 版本；
接受前核查具名 JUnit 用例实际通过，校验比较报告的冻结输入并重新计算两版
指标和门槛。报告、JUnit 和输入字节归档，不能只提交一个 `passed: true`。

这些是可追踪的开发记录，不是身份认证或 Git 提交真实性证明，也不会替代代码
审查与发布决定。接受记录不会部署、改策略或修技能。运行中的求职 Agent
不修改代码、skill、标签和评审器；由开发任务修复，独立检查后再按用户授权发布。
已查看的失败案例用于回归；不能继续把它宣传为未见过的保密验收集。
