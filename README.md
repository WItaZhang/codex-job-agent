# 岗位挖掘 Agent（意图维护）

每天给你 7 个推荐岗位和 3 个探索岗位。你在对话里给每个岗位打 label（想投 / 不想投 / 读错了），
agent 分析哪里没对上，给出几个修改选项；用户或授权协调者选择方案后，系统才精确修改你的求职意图里对应的那一条。

需求和规则见 [spec.md](spec.md)，开发约定见 [CLAUDE.md](CLAUDE.md)。

这是在 Codex 或 Claude Code 中运行的源码项目。先配置自己的求职意图，再使用发现、推荐和清单功能；投递需要另外准备资料与程序。每位用户须在自己的宿主明确授权业务范围，作者的开发委托不会随 clone 传给你。

## 最短开始路径

准备 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和一个已经可用的 Codex 或 Claude Code。宿主账户和模型费用由你自己的订阅或 API 配置承担。

```sh
git clone https://github.com/WItaZhang/codex-job-agent.git
cd codex-job-agent
uv sync --locked
mkdir -p data/local
cp docs/examples/intent.example.yaml data/local/intent.yaml
cp docs/examples/searches.example.yaml data/local/searches.yaml
```

自行编辑这两个示例，核对词条、地点和搜索范围，然后从仓库根目录启动宿主。不要把示例偏好当成你的真实判断。首次对话可说：

> 我已核对 data/local/intent.yaml。请初始化意图，检查搜索设置；本次只允许发现、打标签和推荐，不投递。

如果先离线试用，可复制 [合成岗位示例](docs/examples/jobs.example.json)，让宿主导入并打标签，不运行抓取。要打开网页补全原文，另安装锁定 Playwright 对应的浏览器：

```sh
uv run playwright install chromium
```

Linux 还可能需要系统库；官方提供 `uv run playwright install --with-deps chromium`（涉及系统安装权限）。浏览器版本必须与 Python 包匹配，见 [Playwright 安装说明](https://playwright.dev/python/docs/browsers)。不要用关闭 sandbox 解决启动失败。

这里使用项目提供的显式 `--config configs/default.yaml` 源码入口；不要改为 `init --workspace` 期待真实业务可用，后者目前仍是本地只读模式。

## 在 Codex 里使用

在 Codex 中打开本仓库，完成下方相同的依赖和个人文件准备，再开始新对话。项目级 `.codex/config.toml`
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
   - 意图初始文件：参照 [docs/examples/intent.example.yaml](docs/examples/intent.example.yaml)，存为 `data/local/intent.yaml`
   - 搜索设置：参照 [docs/examples/searches.example.yaml](docs/examples/searches.example.yaml)，存为 `data/local/searches.yaml`
     （按关键词 × 地点搜索，与 ApplyPilot 相同；也可以让 agent 根据你的意图生成）
   - 可选：`employers.yaml`（Workday 雇主，见 [示例](docs/examples/employers.example.yaml)）、`sites.yaml`（smartextract 站点）、
     `boards.yaml`（Greenhouse / Lever / Ashby / Avature，见 [示例](docs/examples/boards.example.yaml)）
   - 其他来源的岗位：参照 [docs/examples/jobs.example.json](docs/examples/jobs.example.json)，存为 `data/local/jobs.json` 后导入
3. 在仓库目录启动 Claude Code。第一次会询问是否启用项目里的 `intent-agent` MCP 服务，选择启用。
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
显式只填的岗位和"停在提交前"名单里的公司由填表模块（[specs/form-fill.md](specs/form-fill.md)）来填：填到提交前，代码读回每一栏、对照标准答案核对，每个岗位一份报告。

1. 准备投递资料（都在 `data/local/applicant/`，git 不管）：
   - `applicant.yaml`：参照 [示例](docs/examples/applicant/applicant.example.yaml)。写明简历（PDF 和文本）、必填的 careful_companies 公司名单；名单仅按用户明确命令增删，不能为推进任务清空；
     可以接入自己的选简历规则和 cover-letter skill（比如指向本机的个人知识库）。
   - 可选 `apply_agent_read`：投递 agent 可以只读的目录（比如你的知识库），用来回答申请表里的问题。不能是根目录、用户目录，
     也不能包含 `data/local/`。写求职信的子 agent 也能读文件（只读），读项目以外的目录时会弹窗问你。
   - 可选 `onsite_ok` 和 `relocation`：现场或混合办公可以接受的地点（英文，例如 `[United States]`，地点内任何城市都算），
     以及搬家类筛选题的英文回答。不写时照 ApplyPilot：只接受 `profile.json` 里那一个城市，搬家题答不能搬家。不从意图推导，地点变了要自己改。
   - `profile.json`：ApplyPilot 的格式，参照 [示例](docs/examples/applicant/profile.example.json)。联系方式、工作许可、薪资、招聘网站密码由你自己填写；
     不要写 SSN 和银行卡。
   - `answers.yaml`、`checklist.yaml`：标准答案和要求清单，参照 [示例](docs/examples/applicant/)。两步都照它们答题，自动提交的也一样；
     工作许可、期望薪资和 EEO 要和 `profile.json` 一致，不一致就不开始投递。
2. 本机需要：Chrome、Node.js（`npx`）、Claude Code 命令行。可选：Gmail MCP 授权（读邮箱验证码；填表 agent 只能搜索和读，不能发信）。
3. 输入 `/apply`：
   - 先给这一批写求职信（子 agent 写，代码校验，打印成一页 PDF）。
   - 默认 review_first=0，非保护公司自动投递，无需人工抽查前 5 份。要只填不交，可显式 stop_before_submit=true；独立填表模块保持不提交。
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

## 升级

先停止抓取/投递并退出 MCP，备份自己的完整 data/local 和自定义配置，再更新源码、运行 `uv sync --locked`、重启宿主/MCP。Git 忽略个人数据不代表升级不会迁移数据库；旧版本不一定能打开新库，项目尚无完整安装器升级/回滚保证。不要同时让新旧版本操作同一数据库。备份含私人资料，由你自己保管，不上传到仓库或对话。

## 支持与验证范围

当前本机开发与合成验证为 macOS arm64；Linux CI 覆盖合成测试、浏览器及包检查，不等于已验证所有 Linux 桌面宿主/投递安装。Windows 未验证，当前启动脚本和进程管理依赖 POSIX，暂不承诺可用。真实招聘网站、邮件、验证码和账户权限随用户环境而异。

本片的固定版本、命令、通过项与尚未验证项记录在[交付证据](specs/github-host-release.review.md)。协议握手、合成流程与已登录宿主交互是不同层级，不把测试通过称为真实投递效果达标。

## 许可证

AGPL-3.0-only，来源说明见 [NOTICE.md](NOTICE.md)。

## 开发

```text
uv sync --locked
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m pytest -q
```

公司保护留底来自实际读取的 `careful_companies` 快照，保存在 decisions。用户明确解除时，按真实请求删除配置条目后调用 `release_careful_companies(companies, user_report, actor="user")` 并重新准备批次；仅删除配置不会解锁，旧 careful 批次仍只填。该入口记录真实用户请求，不认证身份，agent 不得自主解除。

## R1 内部工作区与制品检查（不是公众发行）

本仓库源码公开。R1 的“内部预发布”表示成熟度，不表示仓库私有；CI只在runner中构建、安装和测试，不上传可安装wheel，不创建Release/Pages/PyPI发行物。当前使用入口是上述源码加宿主流程；这段仅说明保留的本地工作区与包检查能力，不是另一套必装步骤。

安装开发制品后，可用 `intent-job-agent init --workspace /absolute/path` 创建空工作区，再用
`intent-job-agent-mcp --workspace /absolute/path` 启动本地只读MCP。省略workspace选择平台用户应用目录中的默认工作区，尚未初始化时会提示init。初始化不复制Chrome、不生成个人档案或授权；重复运行保留已有文件。

新workspace在服务端拒绝真实网络抓取、模型外发、浏览器/邮件/投递及业务写入，包括直接Toolbox调用和scheduled profile。状态查看不恢复旧进程、不连接CDP。它不是一个通过改review_first或填source=user即可开启的授权系统。

源码开发者原有 `--config configs/default.yaml` 显式legacy入口保留cwd相对路径语义，已有两个宿主配置已显式传入此参数。`--config`与`--workspace`不可同传；标记工作区中只接受普通文件`settings.yaml`，不存在配置、损坏或符号链接不回退legacy；外部legacy配置及直接适配入口若指向标记workspace的存储路径或后代，也会拒绝。离线表单报告也接受`--workspace`，`--data-dir`显式覆盖保持；报告不会访问浏览器。

包资源来自`configs/default.yaml`和`.claude/skills/cover-letter/`唯一源，Hatchling在构建时映射到安装包，uv继续管理依赖。`uv run python scripts/check_package.py`验证直接wheel及sdist重建wheel，使用临时合成资料、真实MCP握手和离线报告；不会发起投递。

开发检出通过 `uv run` / `uv sync` 刷新打包资源：uv cache keys 跟踪默认配置及完整 cover-letter 资源树（含 references），没有已安装 wheel 猜测源码父目录的回落。修改资源后不要用 `--no-sync` 或旧的常驻进程验证；重新 `uv run` 并重启进程。`scripts/check_editable_resources.py` 在临时合成源码副本验证修改/新增/删除后的实际重建。

R1 本地读允许追加实际观察到的公司保护审计快照（决定25），不写授权、反馈或投递状态。空 workspace 不植入意图；合成流程测试直接种 Store，公开初始化意图入口仍关闭。`get_apply_setup` 仅检查本地资料路径是否存在，不加载 profile/env 或探测程序。离线 form_fill 报告的显式 `--data-dir` 优先于 workspace 根目录，属于主动覆盖，不属于默认目录隔离保证。
