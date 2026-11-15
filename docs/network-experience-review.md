# network-experience 修订审阅证据

本次沿原 c7b48e3 分支修改，唯一编辑者为当前会话；独立子agent只读审阅，未写代码、未读个人数据。模型证据与网站执行均使用 synthetic 数据。

审阅经历多轮，发现并关闭以下阻塞：

- 抓取入口整条禁用，以及测试绕开生产入口。
- 业务收尾和轨迹库之间的崩溃漏登记；通过业务事务handoff、幂等接收补偿。
- waiting/failed被unknown或interrupted抢先定型；统一终态并允许权威修订。
- 终态修订后相同经验无法重新生效；经验ID纳入水位。
- 本人解决未参与新证据校验；直接把持久resolution纳入范围水位，并生成独立反证事件。
- 核实未执行被误记为门槛解决；区分证据类型，明确门槛只接受实际解决报告。
- JSON shape错误可中断业务；采集端容忍缺失结构。
- 所有网络传输错误被误分类为timeout；只将TimeoutException记为超时。
- 日志清理跟随目录软链接及并发文件消失；拒绝软链接目录链，容忍文件竞态。
- SQLite executescript后的迁移检查失去事务保护；迁移使用独立BEGIN IMMEDIATE。

最后一轮独立审阅结论：以上阻塞项已关闭，代码审阅通过。真实网站、Claude真实宿主和第三方未暴露内部轨迹仍不在实证覆盖内。

当前Codex宿主实际理解整理的输入/输出与校验记录保存在 `tests/fixtures/network-dreaming/`。其中behavior对照明确衡量“是否再次调用执行入口”：基线调用后被Guard挡住，有经验时宿主直接等待本人；不夸称减少了已经被挡住的HTTP请求。合成本人解决之后，模型通过真实反证更新入口替换旧经验，并恢复fake backend的一次只填执行。

最终工程验证（2026-11-14 本地时区）：

- `git merge main`：Already up to date，已包含本地main。
- `uv sync --locked`：通过。
- `uv run ruff check src tests`：通过。
- `uv run ruff format --check src tests`：136 files already formatted。
- `uv run python -m pytest -q`：**2127 passed in 165.73s**。
- `uv run python -m intent_job_agent.network_experience --demo`：纯合成输入生成通过；明确标记model pending_online_host。

全量测试不代表真实业务验收、后台模型或宿主强制调度。
