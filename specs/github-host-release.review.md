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

## 91f6dc3当时交审状态和真实缺口（后续结果见文末）

| 切片 | 产品行为 | 状态 | 提交证据 | 验证结果 | 阻塞/限制 | 下一步及估时 |
| --- | --- | --- | --- | --- | --- | --- |
| GitHub源码双宿主 | 锁定启动、共用定义、新用户授权范围、开发模式、AR报告保护 | 已实现，交独立审阅 | 28f0f84/508c128/0077705/2f11cbe/f2c6611 | 1885全套；干净clone四握手；24合成岗位；两宿主受控返回 | 非新用户登录/购买；Claude slash与两宿主角色实际派生未验；真实业务未验 | 协调者审阅后决定整合，审查耗时不作承诺 |
| 平台安装 | macOS源码/当前CLI版本 | 限定已测 | 同上 | 已有Python/依赖缓存 | 无全新OS联网安装；Linux旧CI不等于当前HEAD远端CI；Windows未测 | 如需要扩大支持，另排平台实测，不虚构通过 |
| 投递权限 | 现有公司名单/未知公司只填、实际用户范围 | 规则与合成回归通过 | 同上 | 不改变工具白名单/平台策略 | 非R4工具层绝对提交拦截；不承诺恶意宿主隔离 | 维持既有限制 |

本次没有合入S2/R2、没有推送/合并，没有新增安装器/自建宿主。1885的产品代码与交审代码相同；最终文档差异只做链接/差异检查，不重复无关全套。

## EN-1 / AU-1 最小收尾

旧91f6dc3合成仓库真实enable-dev→release→worktree remove后，主检出Edit返回0，行为红见/tmp/github-host-en1-red.log。改用common git目录持久标记后，enable-dev和claim两条路径均保留保护；`uv run pytest -q tests/test_host_development_mode.py tests/test_host_launchers.py` 11 passed/1.68s，/tmp/github-host-en1-green.log。已有标记仍兼容，普通clone无标记不受管。授权证据仅当前会话/可用宿主历史，不新增存储。G03角色实际派生继续验收，不以先前只读定义代替。


## 固定356fa04：EN-1后验证及G01无模型诊断

四项 `uv sync --locked`、`uv run ruff check src tests`、`uv run ruff format --check src tests`、`uv run python -m pytest -q` 全部exit0，1886 passed/92.22s（/tmp/github-host-en1-four.log）。apply skill用uv环境执行skill-creator quick_validate通过；先用系统python缺yaml是验证器环境错误，不是技能失败。协调者独立11项及remove/prune反例已签收EN-1，不再扩锁。

含空格独立clone固定356fa04，锁定离线依赖安装及scripts/check_host_checkout.py四次47工具握手/意图初始化通过；/tmp/github-host-en1-clean-path.txt、/tmp/github-host-en1-clone-install.log、/tmp/github-host-en1-clean-protocol.json。首次误用已启动过MCP的角色检出，脚本因已有data/local正确拒绝；未删除数据重试，而是新建干净检出。依赖缓存并非首次联网安装。

/tmp/github-host-prerequisites.py与.json为独立合成PATH/目录诊断：只含sh/git的PATH运行真实启动命令，exit127 `exec: uv: not found`；强制本机系统CPython3.9.6，uv sync --locked --offline --no-python-downloads拒绝>=3.13，exit2。ZIP无git元数据，真实shell显示not a git repository；测试uv仅为cwd哨兵，从根保留根，从tests保留tests，exit0不代表MCP成功。README分别说明安装uv、选受支持Python、从根启动/推荐git clone。最初PATH也遗漏sh导致测试器FileNotFoundError，纠正后才取得uv红，不计产品红。

## G02原生发现：无模型、无个人配置

/tmp/github-host-native-config.py使用两个全新空CODEX_HOME/HOME，只写本次合成路径的trusted/untrusted设置，通过当前CLI生成schema所定义的app-server config/read(cwd,includeLayers)查询。/tmp/github-host-native-codex-config.json：未信任时项目层有disabledReason且有效mcp_servers={}；信任后有效stdio command/args及项目审批字段与仓库一致，来源为实际.codex/config.toml，无command/args覆盖、无模型/凭据。该证据证明原生配置发现和信任条件，不证明已登录的无覆盖业务会话。

Claude原生命令 `claude --setting-sources project mcp get intent-agent`（无--mcp-config覆盖）返回Project config、正确stdio/sh命令与Pending approval；/tmp/github-host-native-claude-config.json。尚未修改用户全局信任或自动批准项目。下一最小真实入口验证应由操作者在合成检出完成项目/服务器批准，再在无MCP命令覆盖的会话调用一个合成读工具；不要把本次受控显式配置当该流程通过。README提醒同时审阅审批策略。

## G03角色测试001：测试器失败，不是产品红

源码356fa04、独立合成检出；/tmp/github-host-role-sessions.py及两份github-host-{claude,codex}-roles-001.{command.json,stdout,stderr,result.json}。Claude exit0/34.59s：stream-json逐工具记录Skill(today)真实加载；命名cover-letter-writer派生成功，agentId=a920a302b08696b66，只Read自身定义。job-tagger被拒：测试器disallowed全部47MCP，角色工具集合变空。不是角色产品缺陷，不计G03通过。

Codex exit0/40.30s，完整定义cat成功；stderr两次真实spawn错误no rollout found，父线程01a16aa9-d977-7c72-981f-161a9dfd36f0使用--ephemeral。未返回child ID，不计回退成功。根因首先指向测试器历史fork条件，尚不能将ephemeral关联当充分因果证明。未发生业务工具调用。

协调者批准002各一次修正会话：Claude保留角色工具，仍无真实材料/业务；Codex临时CODEX_HOME login status exit1未登录（/tmp/github-host-role-temp-auth.json），未复制凭据。CLI无模型debug prompt-input明确说明fork_turns，故改用fork_turns=none且完整定义传入，保持已有登录环境和隔离检出。002增加today合成推荐片段，不生成label/信件/投递。G10含糊apply→明确授权的执行链未并入，不以复述代替；最小后续方案见下。


## 002修正角色与合成推荐片段（固定356fa04）

命令、HEAD、cwd、exit和原始事件在/tmp/github-host-role-sessions-002.py及github-host-{claude,codex}-roles-002.{command.json,stdout,stderr,result.json}。没有修改产品入口或角色权限配置。两会话共用一个仅合成数据的检出；先由包外脚本通过真实Store/Toolbox建立24个synthetic.invalid岗位并打标签、记录本日已抓取，无网络、无资料。/tmp/github-host-seed-roles.py与github-host-roles-seed.json明确这是测试预置，不是宿主实际采集。

- Claude exit0/33.64s：逐工具stream-json中原生Skill(today)加载；Agent分别按subagent_type=job-tagger/cover-letter-writer派生，两个async_launched及完成通知存在（内部ID在原始tool_use_result中）。tagger 0工具，writer只Read自身定义，均未处理真实岗位/材料。父实际get_fetch_status→list_open_analyses→select_today，首次固定7推荐+3探索并展示；无反馈/信件/投递。
- Codex exit0/43.93s：无原生Skill工具，自动发现并实际cat完整today及两个角色定义；使用fork_turns=none完整定义回退，父结果报告/root/job_tagger和/root/cover_letter_writer均返回，无业务工具；stderr不再有no-rollout错误。JSONL直接记录三次合成MCP调用及7+3结果。**证据限制**：该CLI exec JSONL没有输出两次spawn参数/子返回完整payload，只有父结果报告和wait事件，不能把它说成完整独立子线程审计；对精确合成cwd做thread/list无模型查询返回空（ephemeral），未扩大查询私人历史。完整定义传入与子返回的细节据此标为宿主执行报告，不冒充独立逐参数复核。
- Codex稍后读取同一已固定选择，get_fetch_status selected=true/tagged_open=14，不是第二个独立首次选择。两个select_today结果相同；/tmp/github-host-roles-post-store.json实际复查24jobs、7+3 selection、labels=0、applications={}、tracked diff空。
- 001的零工具/历史fork失败属于harness红，002修正不改产品；不是修复了仓库角色实现。仍没有证明角色真实生成/保存求职信或真实网站成功。

## G02追加：原生项目发现并实际连接，无模型

Claude：官方MCP文档明确--settings可提供单次项目服务器批准。执行 `claude --setting-sources project --settings '{"enabledMcpjsonServers":["intent-agent"]}' mcp get intent-agent`，无--mcp-config，无command/args覆盖，exit0显示Scope: Project config (shared via .mcp.json)、Status: Connected、仓库原命令。/tmp/github-host-native-claude-connected.json。只批准当前合成检出的这一服务，不改全局配置；不是实际点击信任窗口或首次登录测试。

Codex：/tmp/github-host-native-codex-connect.py创建全新无凭据CODEX_HOME/HOME，仅写当前合成项目trusted；`codex -C <clone> app-server --stdio`，按本机生成公开schema执行initialize→thread/start(cwd,read-only,ephemeral)→mcpServerStatus/list(threadId,serverName=intent-agent)。未调用turn/start，未请求模型。/tmp/github-host-native-codex-connect.json返回runtimeStatus=connected、serverInfo.name=intent-agent和47工具，命令来自原生项目层，没有MCP覆盖。结合前述config/read可信/不可信对照和已有同命令模型get_intent证据，提交G02分层裁决；不把它称为已验证所有用户宿主登录/信任界面。

## G10仍需的最小实际行为验收（未移出范围）

一场受控宿主测试、同一合成会话两次明确输入，不新增生产fake开关/授权库：包外临时MCP启动器构造真实Toolbox，复用tests/test_gate_handoff.py的GateRunner/FormFillFake及test_slice6.py的合成材料/letter renderer；预置普通公司和保护公司岗位、合成信件资料。所有浏览器/外部agent/邮件sink以已有假runner替换并记录调用，socket网络毒丸；除这套测试启动器外源码入口不变。宿主仍加载真实apply skill。

第一输入仅/apply（新用户、无既有自动授权），核对实际prepare_apply_batch参数stop_before_submit=true、无submit模式runner调用；记录一次具体批次/非保护公司/自动提交选择。第二输入明确“自动投递刚才这份合成清单的非保护公司”，核对重新prepare返回真实summary、普通公司可进入假submit、保护公司仍stop；在同批继续一步，不能再次要求相同授权。逐工具/runner日志和持久批次状态为断言，不以模型说自己遵守为通过；用户真实输入由测试驱动给出，模型不得伪造。实际工具如缺资料须先修合成fixture，不以无资料报错替代授权测试。

准备约1–2小时，运行一次受控会话和整理证据约20–40分钟；仅规划估算。由协调者裁决是否执行这一聚焦模型测试；本轮不再追加付费会话。若需要更强Codex子线程独立日志，最小方案是授权一个保留测试线程的宿主入口并仅导出该合成线程的工具事件，不复制凭据、不查询私人会话，不追加业务流程。

## 当前交付状态

|切片|产品行为|状态|提交证据|验证结果|阻塞/待裁决|下一步及估时|
|---|---|---|---|---|---|---|
|GitHub双宿主|源码启动、共用skills/roles、合成推荐、恢复报告保护、持久开发模式|产品代码冻结，G10/003新证据交审|产品356fa04；验收启动器6beef2c|原四项1886/92.22s；G10预演107及持久两轮；003真实spawn/child返回|G03回退实际运行已获协调者验收；保留加密正文比对限制，G10独立审查及整片验收待协调者|完成本轮四项/文档提交交审；不再追加付费会话，不推送合并|

R3/R5 README已按代码补数据去向、运行时下载/费用和审批策略审阅；没有声称所有标签发送完整个人资料。当前远端尚非本分支，协调者后续负责整合推送、远端clone和CI。本次文档不改变356fa04已四项通过的产品代码；不重复全套或扩大验收为商业产品。

## G10实施准入与无模型预演

协调者批准决定7，先无模型后单场Claude持久两轮。新增tests/host_apply_acceptance.py仅包外测试启动器，复用真实Toolbox与既有GateRunner/FormFillFake及合成资料，不是生产fake开关。显式临时目录标记、防覆盖；禁socket connect/connect_ex及真实browser/agent/renderer，子进程仅保留owner身份所需ps。预演first只填4岗，再普通3模拟submit+Stripe stop；另后台同批第二个submit停在Event，真实apply_status轮询才释放假runner。没有把这些预演参数注入付费模型。

`uv run pytest -q tests/test_host_apply_acceptance.py tests/test_github_host_journey.py tests/test_gate_handoff.py`：107 passed/8.10s，/tmp/github-g10-dry-validation.log。首次独立dry run exit0/8次假runner，/tmp/github-g10-dry.log；网络毒丸正控一次且正常流程无额外触发。宿主实际两轮尚未运行，此处不能算G10通过。

G02分层发现/连接已由协调者签收；无模型不等于无网络：Codex app-server曾尝试401预连接，未调用模型turn，不宣称全进程零网络。Codex002缺spawn/child trace，独立审查不签收G03，仅父报告不能补证。已批准G10后再一次非ephemeral合成持久线程测试，按确切父/child ID取事件，不遍历私人历史。

## G10固定6beef2c实际两轮（交独立审阅）

包外命令和两条真实输入在/tmp/github-g10-host-driver.py、github-g10-session.json；每轮command/stdout/stderr/result/db分别为/tmp/github-g10-round-{1,2}.*。同一持久session 2032bfed-20f2-47b5-8d3d-15c7e0517cce，exit0/12.67s、exit0/14.51s。没有重置两轮之间的数据库、没有替模型填模式。仅读取这一新session的确切记录，/tmp/github-g10-exact-session.jsonl中实际command-message=apply与完整skill正文展开逐字一致；这是原生斜杠展开，不是额外Skill工具调用。

第一轮用户仅/apply：实际apply_status→get_apply_setup→prepare_apply_batch(stop_before_submit=true)；返回全只填批次和一次具体范围选择，无start、无runner、无提交。未实际填表，不能写成“第一轮完成四份填表”。第二轮明确同清单非保护公司自动投递：实际prepare_apply_batch({})取得新批次及准确summary→start_applying→两次apply_status→apply_stats；普通j00/j02/j03各一次假submit，Stripe j01经FormFillFake只stop、持久waiting。第二个普通submit在测试Event停住，状态轮询释放后继续至done；无重复授权问题。它是同一个rest阶段的继续，不宣称测试了另一个逻辑批次/恢复阶段的授权。

真实Store记录4次attempt、3applied+1waiting，零重复。MCP进程两个启动各做一次网络毒丸正控，合计2次sink_refused均匹配正控，业务路径无额外触发；不等于宿主模型传输零网络。测试器/资料全合成、真实业务sink未调用。离线审计/tmp/github-host-evidence-audit.py断言这些结果，简明结果、原始文件SHA256留[证据JSON](github-host-release.evidence.json)，不将模型最终文案里的“已提交”作为真实业务证据。

## Codex003：真实派生、明确子线程与剩余日志限制

源6beef2c，/tmp/github-host-codex-roles-003.py、*.command.json/stdout/stderr/result.json；唯一新增模型会话exit0/32.98s，现有登录、--ignore-user-config、read-only、没有--ephemeral、无模型/推理档位覆盖、零岗位。未调用MCP/业务，父只cat两个定义。无模型preflight通过thread/start→thread/read及thread/items/list取得确切新ID/path，空无turn线程尚未落盘；这不是日志失败或模型执行。skills/list(forceReload)独立发现六个enabled repo skills，含四个日常入口，来源为本检出的唯一.claude/skills源。

真正模型会话父ID 01a16ab5-8147-7501-ac0d-be6404df97f5。通过thread/read取得实际rollout及subAgentActivity的明确child ID，再仅按这两个ID读子线程：

- job-tagger：01a16ab5-b73e-7772-8ee7-a2a80b4adb27，/root/zero_job_tagger；
- cover-letter-writer：01a16ab5-dd4a-7790-adfc-d610f029470b，/root/zero_cover_letter_writer。

父rollout两次实际spawn_agent参数均fork_turns=none，无模型覆盖；子session_meta.parent_thread_id均匹配、started/completed事件成立。两个child独立实际final返回角色及工具边界，均零工具；不是父摘要。/tmp/github-host-read-exact-thread.py与*.exact-read.json、*.parent.rollout.jsonl、*.child-<ID>.jsonl留证，无thread/list或目录遍历。第一次只读脚本误把exec专用--ignore-user-config放app-server顶层，CLI解析拒绝，修正为正常app-server后无模型读取成功；不计产品红、不重开模型会话。

**剩余具体限制**：父cat返回与两个源定义逐字一致；但宿主将spawn.message及子NEW_TASK正文存为encrypted_content。审计能断言父spawn密文与对应child收到的密文字节完全一致，不能独立解密核对它是否含完整定义。没有访问任何解密密钥、没有用父自述填补，也没有再启动付费测试。003比002新增真实spawn/child返回充分原始记录，但“完整定义明文逐字投递”这一强度仍交协调者裁决。exec JSONL自身仍只显示空wait，不能单独作派生证据。

原001/002测试器错误和证据限制不删除；本轮没有产品/角色权限变更。G02签收范围和401预连接事实维持。

## 协调者最终证据口径与main同步

003完整源定义读取、真实spawn、父子加密载荷一致、明确child/parent及独立返回获协调者验收为“回退路径实际运行”；加密消息中完整定义明文未独立比对不宣称通过，不解密、不再新增模型会话。G10及003独立审查记录由协调者收尾。本仓库证据JSON仅包含必要合成输入/状态、确切测试线程引用、工具/角色返回、日志摘要哈希；不提交原始宿主rollout、账户ID、凭据或私人宿主元数据。

本树执行git merge main：Already up to date，main=3fe87bc1db26e997d7cbc3ae4fb934323820612e；没有新的主线代码，当前运行的四项覆盖同一稳定代码与新增测试，不重复无关测试。产品src未改，README与证据更新不改变已验证行为。

最终四项（源/测试固定6beef2c，其后仅README/规格/证据）：`uv sync --locked`、`uv run ruff check src tests`、`uv run ruff format --check src tests`、`uv run python -m pytest -q`全部exit0；**1888 passed/94.06s**，118文件格式通过，/tmp/github-host-g10-final-four.log。相较上次1886新增两项G10包外启动器预演；无失败/警告被过滤。6beef2c已用于独立临时合成clone的locked安装及实际两轮MCP/宿主验收，固定命令/源HEAD在round command记录中；此前cce8ab1干净clone四次47工具协议证据保留。最后差异仅文档及精简JSON，不重复相同产品四项。远端clone与CI仍由协调者执行，不能将本机cached安装当干净OS首次下载。
