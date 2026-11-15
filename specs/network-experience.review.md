# 联网经历实验审阅与证据

日期：2026-11-14。独立基于 main `4c5a7fb64cd5c2c19a0e31ae36533870967a92f0`；未读取/搬运真实data/local、profile或.env，未登录/抓真实岗位/投递。未依赖长期记忆实验。

## 独立只读审阅

审阅 agent：`/root/read_only_review`。三轮只读审阅，未改文件/锁/Git，未自行重跑测试。

初审发现并修复：200错误页提前成功、Avature预览/详情绕过、vendor默认拒绝未落地、准入拒绝计业务失败、首次HTTP失败无摘要、清理未调用、SSO映射及local_only报告遗漏。

复审发现并修复：Ashby租户路径索引；Avature单站暂停终止整批；分页部分成功清零风险预算；恢复缺少接线回归。新增真实Toolbox恢复测试证明拒绝后hold次数、标签页、业务attempt数不变。

最终结论原文：

> 最终只读复审：上述四项修复已确认，可按文档明确的实验范围验收；未发现新的阻塞项。

其接受前提：不声称全网络防护，不声称保证不封号。浏览器内部动作、跳转站点、账号unknown、vendor不可用、宿主声明非身份认证均如实写在覆盖表。

## 主会话验证

- 测试先行：新增测试首次因模块不存在而collection失败。
- 核心合成测试：`uv run pytest tests/test_network_experience.py -q`，12 passed。
- Avature及核心测试：53 passed（当时核心10例）；新暂停语义更新原Avature立即重试断言。
- 实际申请连续性状态机：`uv run pytest tests/test_apply_continuity.py -q`，21 passed，包含新增network_paused恢复回归。
- 工具/权限/workspace/委托及核心：138 passed（当时核心10例）。
- 合成demo：`uv run python -m intent_job_agent.network_experience --demo`，exit 0，零外部调用。
- 首轮全量：2101 passed，5个新增工具清单/权限断言失败；已补双宿主配置与清单。
- 中间全量发现Avature详情Paused未被站点层处理，已修复并经独立复审。

旧vendor适配器保留，原有全套合成回归fixture仅在测试里选用保留实现；生产默认拒绝另有零配置/零调用测试，不以旧测试证明生产路径可用。

最终验证（吸收main检查返回 Already up to date 后）：

- `uv sync --locked`：exit 0，58 packages resolved，54 checked。
- `uv run ruff check src tests`：All checks passed。
- `uv run ruff format --check src tests`：134 files already formatted。
- `uv run python -m pytest -q`：**2116 passed in 147.54s**，exit 0；[原始终端结果](evidence/network-experience-pytest.txt)。
- Workday保留适配器合成回归：85 passed；实际受控招聘板全链路替代原JobSpy journey：1 passed。
- 最终核心与连续性：33 passed。全量已包含这些测试。

全量过程中发现旧Workday测试仍经已阻塞的生产入口运行；将其明确归为保留适配器合成回归，生产默认拒绝的验证不变。24租户全链路验收则改走实际受控招聘板入口，未绕过准入。

所有测试只是合成工程证据，不能冒称真实网站/宿主模型或真实dreaming收益。
