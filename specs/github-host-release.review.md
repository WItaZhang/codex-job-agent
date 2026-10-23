# GitHub 双宿主源码交付记录

## 规格准入

- 用户已批准新目标及实施范围，非等待阶段审批。旧S2 8e5c06d干净、锁已释放，未动提交/stash；新树github-host-release claim成功，基线main3fe87bc，ff-only已是最新，uv sync --locked成功（Python3.13.16，54包安装）。未改主目录/其他树，未启动agent。
- 初读README确认待纠正承诺：复制日常Chrome、子agent碰不到提交工具、git pull不影响数据；当前代码证据核对和真实生命周期AR反证尚未完成，不能先宣称修复。
- OpenAI Docs核对入口（仅依据实际打开的官方页，不采第三方搜索结果）：[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)、[skills](https://learn.chatgpt.com/docs/build-skills)、[subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)。具体字段与本机支持待接线步骤记录。
- 本次仅规格/计划，不执行真实业务，不读个人profile/.env。完整四项和干净检出验收待实现收尾，不将uv sync等同交付完成。

## G07：旧宿主跨 Store 恢复报告（模块决定3）

基线28f0f84/3fe87bc，真实 resume_applying 后台线程与独立 Store，Event 阻塞第一岗位，第二岗位尚在排队；没有伪造 _apply_run。`uv run pytest -q tests/test_host_resume_reports.py` 首轮2条实际行为失败（active/queued均 DID NOT RAISE），日志 /tmp/github-host-resume-red.log。合成runner/页面，不启动浏览器或外部账户。

修复后 `uv run pytest -q tests/test_host_resume_reports.py tests/test_gate_handoff.py tests/test_stuck_run.py` 171 passed/43.06s（当时新文件仅2项，/tmp/github-host-resume-green.log）；补充持久标记/旧协调表/无关岗位/结束释放/同岗连带写入后，`uv run pytest -q tests/test_host_resume_reports.py tests/test_slice5b.py tests/test_slice5c.py` 150 passed/3.86s（/tmp/github-host-resume-report-regression.log）。新文件共7项。后一集合首轮149过1失败：测试使用普通相同URL构造未进入同岗集合，实际报“从未列入清单”，属于fixture错误；改用已有确定性同岗样例并实际列入清单后通过，不计产品红。定向ruff check/format通过；初次lint的行长及fixture导入重定义已修，没有过滤warning。

旧表迁移测试通过删新增列模拟旧协调结构，不冒充真实旧源码升级。保护集合仅岗位ID；不是授权认证、不提前标记queued已提交。完整四项、干净clone和双宿主实测仍未完成，此处仅AR旧路径小步证据。

## G02/G03/G08：启动配置与说明小步

实际执行两个配置的 command/args，在含空格的临时 git 根/子目录用 uv 哨兵记录 cwd/参数：旧Claude两条红（缺 --locked/未回根），Codex两条与软链检查通过。修后 `uv run pytest -q tests/test_host_launchers.py tests/test_tools.py tests/test_delegated_authority.py` 83 passed/8.44s。中间82过1失败是旧test_tools静态断言仍要求command=uv，已更新为新sh启动契约；未把该断言变化算产品红。日志/tmp/github-host-launchers-{red,green,green2}.log。

README最短路径和依赖分层、专属Chrome、授权、子agent行为边界及升级限制已更正。四技能仅维护源、补充当前用户授权范围；MCP指令同口径。skill-creator quick_validate逐个验证fetch-jobs/today/apply-list/apply全通过；不等于真实宿主行为验证。宿主仅版本已读：codex-cli0.163.0-alpha.10.2、Claude Code2.1.309，尚未启动模型交互。未读取个人宿主配置/密钥。

官方核对：Claude项目stdio配置 https://code.claude.com/docs/en/mcp；Playwright按包版本安装浏览器 https://playwright.dev/python/docs/browsers；Codex配置/skills/角色沿前述官方参考。实际MCP握手、干净检出及合成全链仍待后续；本小步没有修改宿主审批白名单。

## G04/G05：合成闭环与可复跑协议检查

新增 tests/test_github_host_journey.py：24个合成岗位经真实旧 JobSpy 适配/Store 与 Toolbox，外部scrape返回合成表；实际标签、7+3、显式合成反馈/理解参数、proposal选择、意图版本2、JSON清单、求职信校验和普通/保护/登录门槛结果。socket.connect毒丸，fake仅测试文件；没有S2 runtime或生产解锁入口。初稿错误依次为无用NoModel导入不存在、清单路径应在files下、FormFillFake复用GateRunner的stop调用应按mode判保护，不计产品行为红。修正后单例1 passed/0.46s。

scripts/check_host_checkout.py限定干净无data/local检出，实际使用两个宿主配置命令在根/子目录启动stdio，初始化仓库合成意图示例，列47工具/读意图；不调用外发业务。不把该脚本当宿主模型交互。固定提交后在独立clone实跑结果另记。

## 基线评审收尾：开发模式/授权/文档（模块决定5）

采纳 /tmp/github-host-release-baseline-review.md 的限定证据，基线1867/102.64s是评审方3fe87bc，不冒充本分支结果。新的hook实执行测试先2失败/1通过（普通clone被挡、enable-dev不存在）；修后 `uv run pytest -q tests/test_host_development_mode.py tests/test_tools.py tests/test_delegated_authority.py tests/test_github_host_journey.py` 84 passed/8.60s。覆盖普通clone配置/升级不claim、显式模式、原锁release后继续保护、共享新worktree拒绝、常见本地文件忽略；不更改其他开发树锁。当前检出已用enable-dev持久启用，本会话原锁保持。

/apply新增一次范围判定：含糊调用只填，明确自动投递当前清单可直接构成授权，作者既有委托不重问。仍需实际宿主受控验证，不用静态文字称模型行为已通过。工具docstring明确从源码根写data/local路径；历史plan去个人标识并标弃用验证码服务设想；不修改历史决定正文。skill-creator apply quick_validate通过。

固定2f11cbe干净含空格clone已完成uv sync --locked与scripts/check_host_checkout.py：四次真实stdio（两配置×根/子目录）47工具、合成示例初始化及读回、锁文件不变。/tmp/github-host-clean-protocol.json、.stderr和-install.log留底。安装利用本机现有Python/uv缓存，不当作全新OS/联网首次下载实证；没有真实宿主模型交互。

## 固定产品代码 f2c6611：完整检查与干净检出

- 产品HEAD：f2c6611c71fe3035529639ca57107a277e970053（之后只改README故障/可选工作区说明及本证据记录）。
- 四项完整命令依次为 `uv sync --locked`、`uv run ruff check src tests`、`uv run ruff format --check src tests`、`uv run python -m pytest -q`：全部exit0，**1885 passed /93.49s**。日志 /tmp/github-host-final-four.log。未放宽断言、没有warning过滤。
- 新的独立含空格git clone来自该HEAD，无data/local；uv sync --locked成功，随后运行 `uv run python scripts/check_host_checkout.py`，四次真实stdio均47工具，合成示例初始化/读回，uv.lock不变，子目录无错误data。证据 /tmp/github-host-final-clone-install.log、/tmp/github-host-final-protocol.json、/tmp/github-host-final-protocol.stderr；检出位置 /tmp/github-host-final-clone-path.txt。依赖与Python来自可用本机缓存，不宣称干净OS或首次联网下载。
- 24岗位合成闭环已包括在1885中；真实网站/邮箱/投递均未执行。网络毒丸用于此闭环，不声称整个测试套件都无本地浏览器/本地HTTP。

## G07独立验收与剩余故障说明

协调者采纳 /tmp/github-host-resume-review.md，固定508c128：176项/42.82s、150项/3.71s及7个独立反例/2.52s通过。评审使用真实main3fe87bc代码建旧DB升级，覆盖当前/排队、同岗、停止、崩溃、报告先赢锁；与开发删列模拟迁移分开。G07已限定签收，不扩普通apply-step报告语义或通用R6。O2没有可达反例，不加防御重构。README说明apply_status核对owner与prepare_resume检查已开始未知结果，不建议删标记或重复提交。

## G02/G03受控宿主会话（不是首次登录/真实业务）

实际版本：macOS arm64；Codex CLI 0.163.0-alpha.10.2、Claude Code 2.1.309。每种宿主只有一个成功的模型验证会话，均使用上述独立合成检出；模型前失败保留。没有开发worker、没有角色派生、没有其他连接器/业务调用，未复制/输出个人配置或凭据。

### Codex

最初受控命令使用 `exec --ignore-user-config --strict-config --ephemeral --sandbox read-only`，只覆盖 `mcp_servers.intent-agent.enabled_tools=["get_intent"]`，退出1/0.02s，错误invalid transport。该测试忽略用户配置、临时检出尚未加载项目信任，部分覆盖产生无command/args的MCP表；**不能据此判定仓库原生stdio格式无效**。

无模型对照：
- /tmp/github-host-codex-config-only.json：完整仓库配置放入临时CODEX_HOME（仅公开项目内容），原生配置及附加allowlist均mcp get exit0，实际解析为stdio/sh。
- /tmp/github-host-codex-canonical-parse.json：临时测试HOME中先有显式trusted项目记录时，debug prompt-input原项目层与allowlist均exit0。无凭据/模型调用。
- /tmp/github-host-codex-trust-red-green.json这个早期文件名过度乐观：两项均exit1；仅把trust_level放CLI覆盖（包括规范化路径）没有解决启动阶段加载问题。路径不是已证实唯一根因，不能声称该文件是红绿。

修正测试启动器：保留ignore-user-config与只读/禁apps/禁shell/禁web_search，额外从仓库配置原样传完整command及args，再加get_intent allowlist；不删MCP/技能，不写全局trust。真实会话exit0/11.89s。JSONL中有恰好一次get_intent成功、合成version1；模型自动发现fetch-jobs/today/apply-list/apply四skills，正确说明含糊/apply不授权自动提交、明确自动投递当前清单可授权。两个角色没有在可用角色定义中列出，**原生角色派生未验证**；禁shell意味着本次未读完整skill正文。原生命令红与修正命令/结果分别保留 /tmp/github-host-codex-controlled.*、/tmp/github-host-codex-controlled-fixed.*。这是完整显式配置测试，不冒称新用户首次信任流程通过。

### Claude Code

首轮精简env会话exit1/1.63s：未信任临时检出提示，返回Not logged in。用CLI auth status只取loggedIn/authMethod/apiProvider，不输出账户标识：正常继承环境为true/claude.ai，精简env为false/none；保留两个候选平台变量仍false。确认隔离方式丢失现有登录环境，**未证明具体单一环境变量因果**。不自动登录、不复制凭据、不改全局trust。/tmp/github-host-claude-auth-diagnostic.json、-auth-fixed.json保留。

修正为继承正常CLI登录环境，仍 `--setting-sources project --strict-mcp-config --mcp-config .mcp.json --tools Read`；显式仅允许Read/get_intent，逐名禁止其他46个项目MCP工具；不加载其他MCP配置。真实会话exit0/20.33s，permission_denials为空。它读六份项目定义并返回合成version1/七意图，正确解释一次范围授权与保护公司边界。输出为最终JSON，模型声明get_intent一次，**没有逐工具trace，不能冒称独立审计到所有调用次数**。未开放Skill/Agent工具，故**Claude实际slash-command注册/调用及角色派生未验**，六文件读取不代替该证据。/tmp/github-host-claude-controlled.*与-controlled-fixed.*保留；成功会话仍提示未信任项目allow条目被忽略，测试使用显式受限CLI允许项，不冒称首次批准流程通过。

两个修正启动器在/tmp/github-host-controlled-sessions.py，命令副本为对应.command.json，版本与HEAD已记录。修正时系统python3缺tomllib的一次本地脚本错误发生在宿主启动前，改用uv Python；不计产品故障或模型会话。

## 交审状态和真实缺口

| 切片 | 产品行为 | 状态 | 提交证据 | 验证结果 | 阻塞/限制 | 下一步及估时 |
| --- | --- | --- | --- | --- | --- | --- |
| GitHub源码双宿主 | 锁定启动、共用定义、新用户授权范围、开发模式、AR报告保护 | 已实现，交独立审阅 | 28f0f84/508c128/0077705/2f11cbe/f2c6611 | 1885全套；干净clone四握手；24合成岗位；两宿主受控返回 | 非新用户登录/购买；Claude slash与两宿主角色实际派生未验；真实业务未验 | 协调者审阅后决定整合，审查耗时不作承诺 |
| 平台安装 | macOS源码/当前CLI版本 | 限定已测 | 同上 | 已有Python/依赖缓存 | 无全新OS联网安装；Linux旧CI不等于当前HEAD远端CI；Windows未测 | 如需要扩大支持，另排平台实测，不虚构通过 |
| 投递权限 | 现有公司名单/未知公司只填、实际用户范围 | 规则与合成回归通过 | 同上 | 不改变工具白名单/平台策略 | 非R4工具层绝对提交拦截；不承诺恶意宿主隔离 | 维持既有限制 |

本次没有合入S2/R2、没有推送/合并，没有新增安装器/自建宿主。1885的产品代码与交审代码相同；最终文档差异只做链接/差异检查，不重复无关全套。

## EN-1 / AU-1 最小收尾

旧91f6dc3合成仓库真实enable-dev→release→worktree remove后，主检出Edit返回0，行为红见/tmp/github-host-en1-red.log。改用common git目录持久标记后，enable-dev和claim两条路径均保留保护；`uv run pytest -q tests/test_host_development_mode.py tests/test_host_launchers.py` 11 passed/1.68s，/tmp/github-host-en1-green.log。已有标记仍兼容，普通clone无标记不受管。授权证据仅当前会话/可用宿主历史，不新增存储。G03角色实际派生继续验收，不以先前只读定义代替。
