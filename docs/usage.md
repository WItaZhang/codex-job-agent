# 使用与配置指南

[返回产品首页](../README.md) · 本文包含完整配置、投递规则、故障处理和开发说明。

每天给你 7 个推荐岗位和 3 个探索岗位。你在对话里给每个岗位打 label（想投 / 不想投 / 读错了），
agent 分析哪里没对上，给出几个修改选项；用户或授权协调者选择方案后，系统才精确修改你的求职意图里对应的那一条。

需求和规则见 [spec.md](../spec.md)，开发约定见 [CLAUDE.md](../CLAUDE.md)。

这是在 Codex 或 Claude Code 中运行的源码项目。先配置自己的求职意图，再使用发现、推荐和清单功能；投递需要另外准备资料与程序。每位用户须在自己的宿主明确授权业务范围，作者的开发委托不会随 clone 传给你。

## 最短开始路径

请按 [README 的快速开始](../README.md#快速开始)完成安装、工具连接检查、配置摘要核对与意图初始化。初次使用不需要先运行推荐命令，也不要直接沿用示例偏好。

宿主助手可以根据你提供的需求协助编写配置；现有初始化工具读取 `data/local/intent.yaml`，并将意图保存到数据库。搜索配置 `data/local/searches.yaml` 单独决定抓取范围。初始化完成后，修改初始文件不会自动更新数据库中的偏好。

这里使用项目提供的显式 `--config configs/default.yaml` 源码入口；不要改为 `init --workspace` 期待真实业务可用，后者目前仍是本地只读模式。

需要打开网页补全原文时，安装浏览器：`uv run playwright install chromium`。Linux 还可能需要系统库，见 [Playwright 安装说明](https://playwright.dev/python/docs/browsers)。

### 启动故障

- `uv: not found`：安装上方链接中的 uv，确认启动宿主的终端 PATH 能找到它，再重启宿主。
- `incompatible with the project's Python requirement: >=3.13`：不要强制使用旧系统 Python；让 uv 选择符合项目要求的版本。首次下载需要网络，离线环境须预先准备。
- `not a git repository`：ZIP 没有 git 元数据，启动脚本无法定位根目录；请从含 `pyproject.toml` 的解压根启动，推荐 git clone。ZIP 子目录启动不受支持。这个 stderr 本身不证明 MCP 已启动或失败，仍须检查实际握手。

### 数据去向、下载与费用

岗位发现会请求配置的招聘来源。宿主模型接收工具返回的岗位内容用于标签，以及意图、标签和反馈原因用于分析；不是每次标签都发送完整个人资料。可选发现增强会把页面/提取上下文交给你配置的模型服务。求职信使用你允许的简历和材料；投递后端把联系方式及申请所需资料交给投递用 Claude Code 模型和目标网站，启用 Gmail 时还会访问相应邮箱工具。不要将密钥或私人档案贴进普通对话；git 忽略不代表数据永不外发。

`uv sync` 下载缺少的 Python/依赖，`playwright install` 下载匹配浏览器；投递启动的 `npx -y` 可能下载配置锁定的 `@playwright/mcp@0.0.83`，启用邮箱时还有 `@gongrzhe/server-gmail-autoauth-mcp@1.1.11`。宿主、可选模型服务及投递后端消耗各自订阅额度或 API 费用；投递默认模型为 `claude-opus-5-5`，见 `configs/default.yaml`，项目不承诺固定费用或免费运行。

## 在 Codex 里使用

在 Codex 中打开本仓库，完成下方相同的依赖和个人文件准备，再开始新对话。先审阅并信任本项目，包括 `.codex/config.toml` 的项目审批策略和工具 `approval_mode`；信任后这些设置会参与生效，但不是业务授权。Codex未信任项目时不会加载项目配置，不要通过放宽全局策略绕过。项目级 `.codex/config.toml`
接入同一个 `intent-agent` MCP 服务。使用 `$fetch-jobs`、`$today`、`$apply-list`、`$apply`；
`$cover-letter` 也可单独调用。若 skill 列表没有刷新，重启 Codex。

Claude Code 和 Codex 共用一份内容，维护位置如下；所有软链接都使用仓库内的相对路径，移动仓库后仍有效：

| Codex 入口 | 唯一内容来源 |
| --- | --- |
| `AGENTS.md` | `CLAUDE.md` |
| `.agents/skills` | `.claude/skills/`（整个目录，新增 skill 也自动可见） |
| `.codex/agents/job-tagger.md` | `.claude/agents/job-tagger.md` |
| `.codex/agents/cover-letter-writer.md` | `.claude/agents/cover-letter-writer.md` |

两个 `.codex/agents/*.toml` 是原生角色入口，只引导读取上述软链接，不复制 agent 正文。
Codex 子 agent 默认继承主会话模型，不使用 Claude 的 haiku/sonnet 名称。当前宿主不能按角色名派生时，
skill 会把共用定义传给独立子 agent；并发数服从宿主可用槽位。

Codex 与 Claude Code 的项目配置仅放行指定的提交级工具，不扩大子 agent 或投递 agent 的工具范围。宿主强制审批仍遵守。决定日志区分代理选择与用户事实；实际投递后端仍需 Claude Code、Chrome 和 Node.js。

实现依据：[Codex skill 目录与软链接](https://learn.chatgpt.com/docs/build-skills)、
[自定义子 agent](https://learn.chatgpt.com/docs/agent-configuration/subagents)、
[MCP 工具确认配置](https://learn.chatgpt.com/docs/config-file/config-reference)。

## 在 Claude Code 里使用

1. 安装依赖：`uv sync --locked`，然后 `uv run playwright install chromium`（补全原文要用浏览器）
2. 把个人文件放进 `data/local/`（已被 git 忽略）：
   - 意图初始文件：参照 [docs/examples/intent.example.yaml](../docs/examples/intent.example.yaml)，存为 `data/local/intent.yaml`
   - 搜索设置：参照 [docs/examples/searches.example.yaml](../docs/examples/searches.example.yaml)，存为 `data/local/searches.yaml`
     （按关键词 × 地点搜索，与 ApplyPilot 相同；也可以让 agent 根据你的意图生成）
   - 可选：`employers.yaml`（Workday 雇主，见 [示例](../docs/examples/employers.example.yaml)）、`sites.yaml`（smartextract 站点）、
     `boards.yaml`（Greenhouse / Lever / Ashby / Avature，见 [示例](../docs/examples/boards.example.yaml)）
   - 其他来源的岗位：参照 [docs/examples/jobs.example.json](../docs/examples/jobs.example.json)，存为 `data/local/jobs.json` 后导入
3. 从仓库根目录启动 Claude Code，审阅项目指令及 `.claude/settings.json` 审批设置并完成项目信任。第一次批准项目 `intent-agent` MCP 服务；`claude mcp get intent-agent` 显示 Pending approval 时，回到交互界面批准，不要放宽全局权限。
4. 第一次：对 agent 说"用 data/local/intent.yaml 初始化我的意图"。
5. 每天的命令（项目里的 skill）：
   - `/fetch-jobs`：抓今天能抓到的岗位，打标签。不管当天校不校准都可以跑。第一次会帮你检查岗位发现的设置
     （生成 searches.yaml、问你要不要配 LLM key）。
   - `/today`：今天还没抓就先抓，然后列出 7 个推荐 + 3 个探索。一行回完，例如 `1+ 2-坐班 3- 5+ 6?地点不对`
     （`+` 想投，`-` 不要，`?` 标签读错了，原因可写可不写），再按编号选原因、选怎么改。
   - `/apply-list`：按当前意图生成要投的岗位清单，写成 `data/local/apply/<日期>.json` 和 `.csv`，对话里先列 20 个，
     最上面是最近 7 个标注日的一致率（意图越准，清单越可信）。投完说"1-5 已投，7 跳过"，下次只列待投的。
   - `/apply`：从清单取一批投递（见下面"投递"）。
   - 其他时候也可以直接说："中国也可以"、"导入 data/local/jobs.json"、"选 2"。

打标签怎么省钱：代码先从地点、远程、雇佣类型、职级等字段读出预标签；明确违反硬约束的岗位（例如你只要美国，
它在伦敦）先放一边，意图变了会重新判断；其余按当前意图粗排，按顺序打完整标签，每天最多 2000 个（`configs/default.yaml`
里的 `tagging.daily_budget`），这是上限，不保证当天抓取的岗位全部处理完。完整标签使用宿主子 agent：Claude 角色指定 haiku，Codex 默认继承主会话模型。此步骤不要求另配抓取 API key，但仍消耗宿主额度。角色定义限定只调用三个标签工具；Codex 原生子 agent 可能继承其他 MCP 工具，这份清单是行为约束，不是宿主强制工具隔离保证。

## 岗位从哪里来

岗位发现复刻自 [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot)：用 JobSpy 按关键词和地点搜索 Indeed、LinkedIn、
Glassdoor、ZipRecruiter、Google Jobs，另有 Workday 雇主搜索和 smartextract（解析招聘页面），最后打开岗位页面补全原文。

另有可选的公司招聘板，写在 `boards.yaml` 里：Greenhouse、Lever、Ashby，以及用 Avature 搭建招聘站点的公司（如联想）。
Avature 没有跨公司的搜索入口，可以对 agent 说"帮我找用 Avature 的公司"：它按你的意图搜索候选站点，逐个试读第一页，
列成编号选项给你挑，选中的才写进 `boards.yaml`。招聘板抓取在后台运行，每家读完整个站点，只为新岗位打开详情页。

- **LLM key（可选）**：只有 smartextract 和补全原文的最后一步需要。要用的话，自己在 `data/local/.env` 写一行
  `GEMINI_API_KEY=...`（或 `OPENAI_API_KEY=...`、`LLM_URL=...`），不要把 key 贴进对话。不写时 smartextract 跳过；原文增强仍会尝试确定性提取，模型兜底不可用可能失败，查看来源报告，不把跳过或失败当成已补全。
- **代理（可选）**：被限流或封 IP 时，设置环境变量 `INTENT_AGENT_PROXY=host:port`（或 `host:port:user:pass`）。
- **风险**：抓取可能被限流或封 IP，网站改版后可能失效。使用前自行核对目标站点的当前许可和使用条款，项目不替你取得访问许可。
- 没有自己的 `employers.yaml`、`sites.yaml` 时使用 ApplyPilot 自带的列表（以加拿大雇主和站点为主）；
  只想搜美国岗位的话，可以写 `employers: {}` 和 `sites: []` 把它们关掉。

## 投递

借用 [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot) 的投递流程：每个岗位由一个 Claude Code 在 Chrome 里填表、上传简历和求职信。
显式只填的岗位和"停在提交前"名单里的公司由填表模块（[specs/form-fill.md](../specs/form-fill.md)）来填：填到提交前，代码读回每一栏、对照标准答案核对，每个岗位一份报告。

1. 准备投递资料（都在 `data/local/applicant/`，git 不管）：
   - `applicant.yaml`：参照 [示例](../docs/examples/applicant/applicant.example.yaml)。写明简历（PDF 和文本）、必填的 careful_companies 公司名单；名单仅按用户明确命令增删，不能为推进任务清空；
     可以接入自己的选简历规则和 cover-letter skill（比如指向本机的个人知识库）。
   - 可选 `apply_agent_read`：投递 agent 可以只读的目录（比如你的知识库），用来回答申请表里的问题。不能是根目录、用户目录，
     也不能包含 `data/local/`。写求职信的子 agent 也能读文件（只读），读项目以外的目录时会弹窗问你。
   - 可选 `onsite_ok` 和 `relocation`：现场或混合办公可以接受的地点（英文，例如 `[United States]`，地点内任何城市都算），
     以及搬家类筛选题的英文回答。不写时照 ApplyPilot：只接受 `profile.json` 里那一个城市，搬家题答不能搬家。不从意图推导，地点变了要自己改。
   - `profile.json`：ApplyPilot 的格式，参照 [示例](../docs/examples/applicant/profile.example.json)。联系方式、工作许可、薪资、招聘网站密码由你自己填写；
     不要写 SSN 和银行卡。
   - `answers.yaml`、`checklist.yaml`：标准答案和要求清单，参照 [示例](../docs/examples/applicant/)。两步都照它们答题，自动提交的也一样；
     工作许可、期望薪资和 EEO 要和 `profile.json` 一致，不一致就不开始投递。
2. 本机需要：Chrome、Node.js（`npx`）、Claude Code 命令行。可选：Gmail MCP 授权（读邮箱验证码；填表 agent 只能搜索和读，不能发信）。
3. 输入 `/apply`：
   - 先给这一批写求职信（子 agent 写，代码校验，打印成一页 PDF）。
   - 先确定你授权的范围：明确“自动投递当前清单/这些岗位”即可，依据当前会话或宿主可用历史中的明确授权沿用至撤销；跨会话取决于宿主保留的上下文，项目不会自动记忆。没有可查授权证据、只有含糊/apply时，先准备材料和只填，展示具体批次及非保护公司范围供你一次选择。默认 review_first=0，已授权的非保护公司自动投递，无需人工抽查前 5 份。要只填不交，可显式 stop_before_submit=true；独立填表模块保持不提交。
     条款、隐私声明、"信息属实"这类必勾的框由 agent 勾，可选的营销短信不勾。
   - 公司名单命中、空/未知公司始终由填表模块填好并停下，等你本人确认、亲自提交；邮件投递也不能发送。名单缺失不能当作空名单。普通、重试和恢复都会重新检查保护，已有 careful 不自动升级。
   - 页面写明筛选题要本人作答、不许用 AI 时，agent 不答，停下这个岗位，把原文告诉你。
   - 门槛交给你：登录和注册都不成、单点登录、验证码和"确认你是真人"、Gmail 里收不到的邮箱验证码，agent 停在那一页、标签页留着，
     这个岗位"等你处理"，不算失败。系统从不破解验证码。一步做完时 Mac 弹一条通知；实际门槛解决后，在用户已授予的业务范围内依据证据继续；实际宿主审批仍须遵守。自己交了就说"7 已投"，不想处理就说"7 不处理了"。
   - 一步一定会结束：agent 5 分钟没有任何动静就算卡住，结束它和它启动的进程，接着做下一个。说"停下"随时能停：
     正在填、停在提交前的马上结束；正在自动提交的做完再停，免得打断提交。工具服务中途重启了，下次 /apply 会按已经记下的结果收尾，
     显式开启抽查时保留第一步失败作废规则；默认不抽查时直接执行普通批次；正在自动提交的那个可能已经交了，列在"等你处理"里请你看一眼。
   - 一步在跑时请不要用投递用的 Chrome（开新标签页、关标签页、在里面点）：agent 和你共用它。运行时代码看着它，
     多出或被关掉的标签页、Chrome 连不上，报告里会写"可能有人在用投递用的 Chrome"。
   - 问"这周达标了吗"：`apply_stats` 按每次尝试给出本周和上周的提交数、成功率和各类失败。
   - 不要求项目逐步批准；仲裁协议、SSN、银行卡或申请费，agent 会停下并告诉你。结束时给一张检查单：每个岗位打开过的其他网站、发出的邮件。
4. 投递使用工作区自己的专属 Chrome profile：首次新建空目录，同工作区后续复用，需在该浏览器重新登录。不会复制日常 Chrome 资料，不接管已有固定 9222 端口。自动提交撤不回，网站可能封号；专属 profile 不是凭据加密或恶意同用户进程隔离保证。

## 全权委托与保护公司

作者对协调者的开发委托仅适用于本项目开发；新用户的业务委托由该用户在自己的宿主明确给出，未授权范围不得自行执行。已有明确委托内可由协调者推进。记录 label 必须有用户真实判断，缺失个人事实不能编造；意图修改仍通过 proposal、版本及摘要校验，代理选择记为 delegated_coordinator，不冒称用户本人批准。

唯一保留的业务批准边界是明确“提交前确认”的公司真正提交。本项目不提供解锁该公司的自动提交入口；填好后由用户检查并亲自提交。保守匹配包含通用法人后缀和已合并岗位的公司名，不猜母子公司。名单只能按用户明确命令维护。

项目移除逐次询问配置和 requiresUserInteraction 标记；实际宿主强制审批、工具白名单、隐私限制仍有效。dry-run 指令和可能已提交停批检测保留，但浏览器工具没有最终提交的绝对硬拦保证。

## 恢复或报告被拒绝时

恢复仍在运行时，先用 `apply_status()` 查看并等待收尾，不在另一会话报告或重置同批岗位。服务异常结束后，`apply_status()` 会按已有 owner 身份核对协调记录；已开始但结果未知的岗位仍受保护。

对于这种未知岗位，先 `prepare_resume(job_ids)` 查看当前门槛与页面核对计划，条件满足后由既有恢复流程先核查是否已提交。不能删数据库标记、用 pending 重置绕过核对，或把未知当未提交再次投递。即便你已在网页手动提交，也先让恢复流程核查结果；保护公司仍只填不交。

## 升级

先停止抓取/投递并退出 MCP，备份自己的完整 data/local 和自定义配置，再更新源码、运行 `uv sync --locked`、重启宿主/MCP。Git 忽略个人数据不代表升级不会迁移数据库；旧版本不一定能打开新库，项目尚无完整安装器升级/回滚保证。不要同时让新旧版本操作同一数据库。备份含私人资料，由你自己保管，不上传到仓库或对话。

## 支持与验证范围

当前本机开发与合成验证为 macOS arm64（Python 3.13.16、Claude Code 2.1.309、Codex CLI 0.163.0-alpha.10.2）；受控CLI读工具和合成7+3已测，Claude原生Skill/两个命名角色及Codex角色回退实际派生已有证据；Codex日志中的加密任务正文未独立作明文逐字比对。原生配置发现和MCP连接另有无模型证据。一次合成/apply两轮已验证：含糊调用不启动提交，明确同清单授权后普通岗位使用假提交、保护公司停在提交前；这不是实际网站效果，详见交付证据。Linux CI 覆盖合成测试、浏览器及包检查，不等于已验证所有 Linux 桌面宿主/投递安装。Windows 未验证，当前启动脚本和进程管理依赖 POSIX，暂不承诺可用。真实招聘网站、邮件、验证码和账户权限随用户环境而异。

本片的固定版本、命令、通过项与尚未验证项记录在[交付证据](../specs/github-host-release.review.md)。协议握手、合成流程与已登录宿主交互是不同层级，不把测试通过称为真实投递效果达标。

## 许可证

AGPL-3.0-only，来源说明见 [NOTICE.md](../NOTICE.md)。

## 开发

普通clone配置和升级无需claim。参与受管理开发时，在独立worktree运行 `python3 .claude/hooks/code_owner.py enable-dev`，再 `python3 .claude/hooks/code_owner.py claim <切片名>`；开发锁仅本地保存；开发模式标记在 common git 目录，删除 worktree 也不会关闭。旧仓库如果已无残留锁或标记，请显式执行 enable-dev。已有锁/开发标记的共享仓库继续执行唯一编辑规则，release不关闭开发模式。Codex不执行Claude hook，仍须遵守同一约定。

```text
uv sync --locked
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m pytest -q
```

公司保护留底来自实际读取的 `careful_companies` 快照，保存在 decisions。用户明确解除时，按真实请求删除配置条目后调用 `release_careful_companies(companies, user_report, actor="user")` 并重新准备批次；仅删除配置不会解锁，旧 careful 批次仍只填。该入口记录真实用户请求，不认证身份，agent 不得自主解除。

## 本地只读工作区与包检查（可选）

上述源码流程使用显式 `--config configs/default.yaml`。保留的 `intent-job-agent init --workspace /absolute/path` 会创建 **local-only** 工作区：可检查本地状态，但服务端拒绝抓取、模型外发、浏览器、邮件、投递和业务写入。它不是新用户必须做的初始化步骤，也不能通过改 review_first 解锁。不要让显式 legacy 配置指向这种标记工作区。

CI 的 wheel/sdist 测试不是面向用户的安装器或下载发行。开发者可运行 `uv run python scripts/check_package.py` 核对包资源和离线协议；资源唯一源仍是 configs/default.yaml 与 .claude/skills/cover-letter。修改资源后用 `uv run`/`uv sync` 刷新并重启常驻进程，不使用源码父目录回落。详见[工作区契约](../specs/public-release.md)。

在专用、干净且没有 data/local 的临时 clone 中，可运行 `uv run python scripts/check_host_checkout.py` 验证两份宿主命令与合成初始化；该命令会创建合成 data/local，不应在个人日常库运行。这只验证协议，不会启动模型或访问业务网站。
