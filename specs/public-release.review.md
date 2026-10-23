# 公众目标与排期交审（决定 27）

| 字段 | 内容 |
| --- | --- |
| 切片 | public-release-plan：公众目标、验收矩阵、依赖排期 |
| 产品行为 | 本片不改运行行为；明确完整本地公众产品目标及当前不具备发布条件，现有个人默认不能充当公众授权 |
| 状态 | 规格完成；提交后交独立审阅，未合并/推送 |
| 提交证据 | 基线ef8dbd3；分支WItaZhang/public-release-plan，最终HEAD见交付消息 |
| 验证结果 | 见下方最终验证记录；无实现测试新增，不宣称公众或B真实验收通过 |
| 阻塞 | 本规格提交无工程阻塞；产品发布阻塞见public-release.md矩阵；真实验收证据仍待验证 |
| 下一步及估时 | 独立评审约20–40分钟；整合后R1约2–3有效工作日，随后R1b浏览器隔离2–4日 |

## 证据来源与准确边界

本会话只读审计报告：/tmp/public-product-audit.md（最初基线3917cd6，含stats待审状态，是当时快照）；后续主线已更新到ef8dbd3，统计决定26已经合并。主审引用的核心文件在该更新中未改。当前规格以ef8dbd3为基线，不沿用“stats尚未合入”的旧状态。

甲方提供并确认的执行证据（不是本规格会话重跑的探针）：

- ef8dbd3四项验证全部通过，1561 passed in 63.38s，已push。
- /tmp/job-agent-release-audit：uv build成功；wheel77文件，仅包代码、许可证和vendor YAML，不含顶层configs/skills。
- /tmp/job-agent-wheel-smoke：独立venv安装49依赖，--help成功；cwd=/tmp无参数启动在读取configs/default.yaml时报FileNotFoundError，尚未开始应用/DB。
- /tmp/public-package-resource-probe.json：显式仅合成storage配置时启动，stdin EOF退出0；DEFAULT_LETTER_SKILL落到venv/lib/python3.13/.claude/skills/cover-letter，exists=false。本会话已读JSON核对。
- /tmp/public-package-handshake-probe.json：实际initialize/list_tools成功，47工具；仅握手/列工具，未调用投递。说明包能运行，缺口是默认初始化、资源及定位，不是包完全无法启动。
- /tmp/public-r1-review-cases.md：八组甲方样例已转为public-release.md §5的R1-01至08；不冒称实现完成。

独立Claude只读复核 /tmp/public-release-independent-review.md：本会话已读。采纳X1资源、X2cwd路径、X3盲连9222、X5evaluate等最终动作旁路、X7服务端缺少公众授权校验的具体事实。X6第三方Gmail凭据全局位置是评审者推测，保留待核实，不断言已跨用户泄漏。X4复制日常资料有代码证据；甲方要求专属空资料，不采用“获授权后自动复制”作为路线。

不采纳独立评审的首发裁功能建议：L0仅临时失败关闭；完整目标包含安全填到提交前、本人确认后提交。R5必须有受限执行、明确支持范围和失败关闭，不能以已知URL拦截或任意source/user字段证明未知旁路安全。R1不混入consent.json框架；公众自动业务须待R4/R5等发布门槛通过。X3以R1b优先处理，早于UI真实运行。

临时报告路径为本机证据位置，不是发行依赖；关键事实/局限已固化在本记录与矩阵。没有读取真实profile/.env、数据库或日志，没有访问申请网站；本片不复制个人资料。

本地界面安全输入：/tmp/public-ui-security-review-notes.md（甲方提供），已纳入R2/R4条件与反例，不在本片选技术。原文依据为[OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)及[MCP Python SDK DNS rebinding advisory](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-9h52-p55h-vw2f)，由甲方核查提供，本会话未另行联网核验。当前stdio不因此被判有HTTP漏洞。

## 规格变更及范围检查

- spec原文定义完整目标、公众用户、A/B隔离、当前解冻范围；§9标准及§10.96 met未改，只明确发布边界。
- CLAUDE冻结与公众用户委托范围一致；README明确开发者原型状态；form-fill当前状态及同名决定同步，2b/2c仍冻结，不启动6.1。
- 总决定14/15/25仅追加部分取代标记，历史正文保留；追加27。模块决定31/36仅加标记，追加37。
- 新public-release.md含12项证据矩阵、9个依赖切片和R1八组样例/兼容约定/文件责任。所有UI、授权、执行、备份、发行仍是必须交付，不以MCP/CLI替代。
- 无代码、测试、依赖、宿主配置或skill正文变更；无新审批门槛阻塞协调者开发。每个未来切片仍独立检出、持锁、测试先行。

## 最终验证

本片一次四项验证全部通过：`uv sync --locked`；`uv run ruff check src tests`；`uv run ruff format --check src tests`（80文件）；`uv run python -m pytest -q`（**1561 passed in 75.91s**）。sync有既存uv_build范围与本机uv版本warning，构建/安装成功；没有据此宣称发行制品完整。

9份改动文档的本地Markdown链接检查无缺失，`git diff --check`通过。排期逐行合计36–63有效工作日；决定27路线含R1b，模块决定31/36均有取代标记。纯规格切片未添加行为测试，没有反复全套重跑。工作区最终状态与release结果随提交HEAD交付；不合并、不推送、不删worktree。
