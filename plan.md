# 阶段 3：切片 1 实现计划（用户已确认；已实现，见 spec §10）

范围：spec §8 的切片 1，包括意图模型、根因选项、回放和确认写入。
只用合成岗位和 fake client。不做 CLI、冷启动、标签抽取，也不接入真实模型和岗位来源。
**用户确认本计划之前，不写实现代码。**

## 1. 做什么，不做什么

| 做 | 不做（留给后续切片） |
| --- | --- |
| 意图模型、词表、location 层级，以及所有不变量校验 | 冷启动、简历处理 |
| 确定性打分和回放 | 从岗位原文抽取标签（切片 1 直接使用给定的标签） |
| 检测不一致、合并共同原因、生成候选原因和方案、筛选方案 | 每日 7+3 选岗、硬约束体检 |
| 唯一的写入入口、版本快照、决定日志、标记已过时的 label | CLI 界面；Anthropic/OpenAI 适配器 |
| `structured()` 接口和 fake client | 抓取岗位 |

## 2. 数据结构（Pydantic）

- `Level`：exclude / strong_avoid / avoid / prefer / strong_prefer / require。四个软等级的分值从配置读取。
- `Dimension`：固定的 11 个维度，分别是 role、domain、specialty、seniority、company_type、company、location、work_mode、employment_type、tech_stack、sponsorship。
- `KeyRef`：`dimension.key`。location 的 key 是 `国家` 或 `国家.城市` 两级路径。
- `Vocabulary`：每个维度一份词表，每个 key 带有 `aliases`。
- `IntentEntry`：`id`、`level`、`note`、`evidence: [label_id]`、`exceptions: [{when: KeyRef, level}]`。
- `IntentModel`：`dimension → key → IntentEntry`，外加 `compensation: {currency, period, floor} | null`、`version`、`parent_version`。
- `Job`：`id`、`title`、`company`、`tags: [KeyRef]`、`salary?`、`description`（只用于展示，**不进入分析**）。
- `Label`：`id`、`job_id`、`day`、`slot`（recommended/exploration）、`value`（want/reject/misread）、`reason_keys`、`reason_text`、`superseded`。
- `Change`（一次修改中的单个操作）：`SetLevel`、`AddEntry`、`DeleteEntry`、`AddException`、`RemoveException`、`SetCompensation`。
- `Proposal`：`id`、`cause`、`changes: [Change]`、`evidence`、`replay`、`warning`。
- `Decision`：`proposal_id` / `no_change` / `feedback(text)`，加上时间戳。

## 3. 模块

```
src/<pkg>/
  domain.py     数据结构 + 不变量
  config.py     读取 configs/default.yaml
  scoring.py    纯函数：score(intent, job) -> (excluded, score, 命中条目)
  changes.py    纯函数：apply(intent, changes) -> 新 intent（会校验）；改动大小
  replay.py     纯函数：回放、一致率、修好了哪些 label、打破了哪些 label
  analysis.py   检测不一致 → 合并 → 候选原因 → 方案 → 筛选
  llm.py        structured(prompt, schema) 接口 + FakeClient
  store.py      SQLite：labels、版本快照、决定日志；唯一写入入口
```

**不变量**写在 `domain.py`，每次构造或写入时都会校验：
1. 同一个 `dimension.key` 只有一条记录（由结构保证）。
2. exception 的等级不能和默认等级相同，每条 exception 只有一个条件，而且 `when` 必须引用词表中已有的 key。
3. 国家一级是 `exclude` 时，这个国家下面的城市不能出现非排除的条目。
4. dimension 只能取上面固定的 11 个之一。

**唯一写入入口**：`store.apply_decision(proposal_id)`。它只接受本次分析生成、并且由用户选中的方案，会把这个方案打破的历史 label 标记为已过时，然后生成新的版本快照。其他任何代码路径都不能写入意图。

## 4. 模型与代码的分工

原则是：**候选项由代码穷举，模型只做理解和排序，结果由代码校验并回放。**

| 步骤 | 代码 | 模型（切片 1 中用 fake） |
| --- | --- | --- |
| 检测不一致 | 推荐位上的 reject、推荐线以下的 want、原因和现有条目方向相反 | — |
| 合并 | 找出多个不一致共有的 key | — |
| 候选原因 | 穷举：岗位上没被意图覆盖的 key；岗位命中的现有条目；沿着 evidence 回查，找出"当初归因错了"的条目 | 把自由文本映射到 key，或者给出"需要先问清楚范围"的问题；为候选原因排序，保留前几个 |
| 方案 | 针对选定的原因，生成各个目标等级的方案；硬约束方案旁边自动配上对应的软等级；如果方案产生冗余 exception，在同一个 diff 里一并删掉 | 补充参考历史行为得出的范围类方案（比如只保留某些国家），输出必须是 `Change` 结构 |
| 筛选 | 修不好当前不一致的方案直接丢掉；打破比修好多的方案标 ⚠；回放结果相同时只留改动更小的方案 | — |

模型返回的任何 `Change` 都必须通过 `domain` 校验和回放，才能出现在选项里。
岗位的 `description` 字段**从不进入模型的 prompt**，所以注入文本在结构上就到不了模型那里。

## 5. 需要你确认的几个设计取舍

1. **推荐线**：回放时，"一致"的定义是：want 的岗位没被排除，并且分数 ≥ θ；reject 的岗位被排除了，或者分数 < θ。θ 写在配置里，默认取 1，含义是净值至少有一个"偏好"。
2. **怎么算"改动更小"**：先比操作的条数，再比等级移动的档数。新增 exception 视为比改等级更大的改动。
3. **存储用 SQLite**：Python 自带，不用额外安装依赖。labels、版本和决定日志都只追加，不修改。
4. **切片 1 没有交互界面**：用 Python API 加测试来驱动，CLI 放到切片 3。

## 6. 测试（阶段 4 先写）

- `tests/fixtures/scenarios.yaml` 里的 16 个场景逐个转成测试。依赖模型理解的部分，比如 s02、s14 的自由文本，用 fake client 返回预设结果，测试只检查代码负责的保证。
- 不变量测试：
  - 不经过 `apply_decision` 就无法写入意图；
  - 任何时候都不会出现同一个 key 有两条记录；
  - 用户没选过的 exception 不会出现；
  - 回放的数字和一个独立编写的简单重算函数结果一致；
  - 岗位 description 不会出现在 fake client 收到的 prompt 里。
- 多天模拟：从 base_intent 开始连续跑多天，检查 spec §9 的四条完成标准。

---

# 切片 2 计划：工具层和每日闭环（用户已确认；已实现）

目标：做完以后，你可以在 Claude Code 里导入真实岗位，每天收到 7+3 个岗位，在对话中打 label、选原因、确认修改。
**用户确认本计划之前，不写实现代码。**

## 1. 做什么，不做什么

| 做 | 不做（后续切片） |
| --- | --- |
| MCP 服务（`mcp` 官方 Python SDK，stdio），工具分读 / 准备 / 提交三级 | 冷启动起草（切片 3）；在那之前用 YAML 文件初始化意图 |
| `.claude/settings.json`：提交级工具全部设为"每次询问"；`.mcp.json` 注册服务 | ATS 抓取、定时任务、硬约束体检（切片 4） |
| JSON 导入岗位；agent 读岗位原文后提交标签 | 网页界面；独立运行模式的模型适配器 |
| 选出每日 7+3 并保存，label 只能打在当天选出的岗位上 | Codex 的审批配置（先只配 Claude Code，文档里说明） |

## 2. 工具清单

| 级别 | 工具 | 说明 |
| --- | --- | --- |
| 读 | `get_intent` | 当前意图（文字等级，不显示分值） |
| 读 | `get_today(day)` | 当天 10 个岗位：编号、推荐/探索、标题、标签，**不含原文** |
| 读 | `show_job(job_id)` | 岗位原文，包在"不可信内容"标记里返回 |
| 读 | `list_open_analyses` / `get_analysis(id)` | 待处理的分析，选项带编号 |
| 准备 | `import_jobs(path)` | 从 `data/local/` 下的 JSON 文件导入岗位 |
| 准备 | `list_untagged_jobs` / `submit_job_tags(job_id, tags)` | agent 读原文后提交标签；代码校验维度、规范化别名、标出新 key |
| 准备 | `select_today(day)` | 选出 7+3 并保存；被硬约束排除的岗位不进入 |
| 准备 | `rank_causes(analysis_id, order)` | agent 为候选原因排序 |
| 准备 | `choose_cause` / `refresh` / `decline` | 和切片 1 的 engine 一一对应 |
| 准备 | `feedback(analysis_id, text, changes)` | agent 把你的意见翻译成修改，代码校验后生成新选项 |
| 提交 | `record_labels(day, labels)` | 一天的 label 一次提交，只批准一次；推荐位 / 探索位以当天保存的选择为准，agent 无法伪造 |
| 提交 | `decide(analysis_id, proposal_id, inputs)` | 确认方案 |
| 提交 | `correct_tags(job_id, remove, add)` | misread 修正 |
| 提交 | `initialize_intent(path)` | 从 YAML 初始化（切片 3 之前的临时入口，只能用一次） |

`record_labels` 的每条 label 可以带：点选的原因 key、你的原话、agent 对原话的理解（对应的 key / 需要问的范围 / 是否提到薪资 / 现有维度表达不了的内容）。

## 3. 每日 7+3 怎么选

- 候选：已打标签、从未展示过、没被硬约束排除的岗位。
- 推荐位：按分数取前 7 名。
- 探索位：从剩下的岗位里取 3 个，优先选命中"回避/强烈回避"条目的，不够时从排名中段补齐。具体比例写在配置里。这一条是 spec 未决问题"探索位的具体抽样方法"的暂定做法。

## 4. 模块

```
src/intent_job_agent/
  tools.py        工具函数和级别登记表（不依赖 MCP，方便测试）
  mcp_server.py   把 tools.py 注册成 MCP 工具
  selection.py    纯函数：每日 7+3
  importing.py    JSON 岗位导入、标签提交校验
```

engine 的改动：理解结果可以通过参数直接传入，没有传入时才调用 `structured()`。

## 5. 测试（先写）

- 每个工具都登记了级别；`.claude/settings.json` 里"每次询问"的列表正好等于提交级工具；定时任务能用的工具不包含提交级。
- `record_labels` 拒绝不在当天选择里的岗位；推荐位 / 探索位取自保存的选择，而不是参数。
- 7+3：硬约束排除的岗位不进入；探索位优先命中回避条目的岗位；不会重复展示。
- 标签提交：未知维度被拒；别名被规范化；新 key 被标出。
- 用工具从头跑一遍合成的一天：导入 → 打标签 → 选 7+3 → 记录 label → 分析 → 选原因 → 确认。切片 1 的全部测试继续通过。

---

# 切片 2b 计划：从公开招聘板抓岗位（用户已确认；已实现）

目标：你在一个文件里列出关注的美国公司，agent 抓取它们在 Greenhouse / Lever / Ashby 上的公开岗位，打标签后进入每日 7+3。
这是 spec §8 切片 4 中"接入招聘板"的部分，提前做；定时任务和硬约束体检仍留在切片 4。
**用户确认本计划之前，不写实现代码。**

## 1. 许可证（用户 2026-10-09 选择 A）

- 复用 `main` 上 `src/applypilot_agent/discovery.py` 的抓取和解析代码。
- 新项目采用 AGPL-3.0-only：加入 `LICENSE`（全文），`NOTICE.md` 写明来源（`main` 上的 Codex Job Agent，后者起源于 ApplyPilot），`pyproject.toml` 声明许可证。

## 2. 做什么，不做什么

| 做 | 不做 |
| --- | --- |
| `data/local/boards.yaml`：你关注的公司（招聘板类型 + board token + 公司名） | 自动发现公司；国内招聘系统；任何需要登录或违反服务条款的抓取 |
| 工具 `check_board(provider, board)`：试抓一次，只返回岗位数和几个标题，不入库，用来核对 token | 定时任务本身（切片 4） |
| 工具 `fetch_boards()`：抓取列表里的所有公司，新岗位以"未打标签"入库 | 根据原文自动打标签（仍由 agent 读原文后提交） |
| 记录每个招聘板最近一次成功抓取到的岗位；已下架的岗位不再进入每日推荐 | |
| 每个公司可选 `title_include` 关键词（例如 engineer、scientist），在入库前过滤明显无关的岗位 | |

两个工具都属于准备级，定时任务也可以调用。

## 3. 需要你知道的取舍

- **一家大公司常有几百个岗位**，全部交给 agent 打标签又慢又费。所以：
  1. `title_include` 先挡掉明显无关的岗位。这是**来源范围**的设置，不属于意图模型，也不参与校准。
  2. 每次最多列出若干个未打标签的岗位（数量写在配置里），最新抓到的排在前面。
- **抓取失败不等于没有岗位**：某家公司抓取失败时，这家公司已有岗位的上架状态保持不变，并在结果里报告错误；其他公司照常抓取。
- 岗位原文（含地点、部门、薪资等字段）仍是不可信内容，只用于打标签。Ashby 和 Lever 返回的结构化薪资会解析成 `salary`，用于薪资下限判断。

## 4. 模块

```
src/intent_job_agent/
  discovery.py   从 main 移植：三家接口的抓取与解析（httpx + BeautifulSoup），输出本项目的 Job
  boards.py      读取 boards.yaml、记录每次抓取的快照、判断岗位是否仍在架
```

新增依赖 `httpx`、`beautifulsoup4`；超时、每次列出的未打标签岗位数等参数写在 `configs/default.yaml`。

## 5. 测试（先写，全部离线）

- 用合成的接口响应（标注 synthetic）通过 httpx 的 MockTransport 回放，覆盖三家的解析、分页、未公开岗位被排除。
- 一家公司抓取失败：报告错误，其他公司照常入库，失败公司的岗位上架状态不变。
- 重复抓取不产生重复岗位；从招聘板消失的岗位不再进入每日推荐；手动导入的岗位不受影响。
- `title_include` 过滤生效；`boards.yaml` 只能放在数据目录下。
- 定时任务模式包含 `check_board` 和 `fetch_boards`。
- 之前全部 75 个测试继续通过。

---

# 切片 2c 计划：复刻 ApplyPilot 的岗位发现（用户已确认；已实现，补充见 spec §10 第 17–26 条）

用户 2026-10-09 决定：完整复刻 ApplyPilot（https://github.com/Pickle-Pixel/ApplyPilot ，AGPL-3.0）的岗位发现与补全，并在 NOTICE 中致谢。
因此 spec §7 的"不抓取服务条款禁止抓取的平台"一条改为：**用户知情选择**接入 JobSpy 等抓取来源，仅供个人使用，风险见第 3 节。
**用户确认本计划之前，不写实现代码。**

## 1. 移植范围

| ApplyPilot 模块 | 在本项目中 | 配置（个人文件，位于 data/local/） |
| --- | --- | --- |
| `discovery/jobspy.py` | 按关键词 × 地点搜索 Indeed / LinkedIn / Glassdoor / ZipRecruiter / Google Jobs；重试、地点接受/排除、标题排除词、按 URL 去重 | `searches.yaml`，格式与 ApplyPilot 相同 |
| `discovery/workday.py` | Workday 雇主搜索与详情 | `employers.yaml`（可选） |
| `discovery/smartextract.py` | 任意招聘页面的智能提取（JSON-LD / 接口响应 / CSS 选择器，由 LLM 选择策略） | `sites.yaml`（可选） |
| `enrichment/detail.py` | 补全原文和申请链接：结构化数据 → CSS 规则 → LLM | 无 |

切片 2b 的 Greenhouse / Lever / Ashby 招聘板保留为额外来源：`boards.yaml` 改为可选，没有就跳过。

## 2. 接入本项目的方式

- 新工具（准备级，定时任务可用）：
  - `discover_jobs()`：依次运行 JobSpy、Workday、smartextract、招聘板，新岗位以"未打标签"入库，按 URL 去重，逐个来源报告新增数和错误。
  - `enrich_jobs(limit)`：为描述过短的岗位补全原文。
- 待打标签队列优先列出已有完整原文的岗位。
- **`searches.yaml` 的初始版本由 agent 根据你的意图生成**（相当于 ApplyPilot 的初始化向导），你可以修改。搜索词、地点、标题排除词属于"从哪里找"的来源范围，不属于意图模型，校准不会修改它们。
- 抓到的所有字段仍是不可信内容，只用于打标签；JobSpy 给出的薪资区间会解析成 `salary`。
- 搜索来源的岗位不做"下架"判断（ApplyPilot 也没有），视为在架。
- 需要 LLM 的部分（smartextract 选择提取策略、补全的第三步）：在 `configs/default.yaml` 中配置 provider、模型名和存放 API key 的环境变量，通过 `structured()` 接口调用（Anthropic 或 OpenAI 兼容接口）。**没有配置 key 时自动跳过这些步骤**，其余部分照常工作。
- 代理：与 ApplyPilot 一样支持 `host:port[:user:pass]`，从环境变量读取。

## 3. 风险（用户已知情）

- LinkedIn、Indeed、Glassdoor 等的服务条款禁止自动抓取；可能被限流或封 IP，网站改版后可能失效。ApplyPilot 自身也把 Glassdoor 和 Google 列入详情补全的跳过名单。
- 只用于个人求职，不做大规模抓取，默认参数沿用 ApplyPilot（每站每个搜索词最多 100 条，只取 72 小时内发布的）。

## 4. 依赖

`python-jobspy`（它在元数据里锁死了某个 numpy 版本，ApplyPilot 用 `--no-deps` 绕开；这里用 uv 的依赖覆盖解决）、`pandas`、`playwright`（首次使用需 `uv run playwright install chromium`）。

## 5. 测试（先写，离线）

- JobSpy：用伪造的 `scrape_jobs` 返回合成 DataFrame，覆盖去重、地点过滤、标题排除、薪资解析、重试只针对临时错误。
- Workday：用合成接口响应回放搜索和详情。
- smartextract 和补全：用本地合成 HTML 页面（本机 Chromium，回环地址）测试 JSON-LD 和 CSS 路径；LLM 路径用 fake client；未配置 key 时跳过。
- `discover_jobs` 某个来源失败时，其他来源照常入库并报告错误。
- 之前的 93 个测试继续通过；云端无法访问真实网站，真实抓取需要你在本地验证。

---

# 切片 2e 计划：显式修改意图 + 学历要求维度（用户 2026-10-10 确认）

来源：2026-10-09 第一天打 label 时用户提出两条要求，现有系统做不到：
1. "工作地点中国也可以"：需要显式修改意图（加 `location.china: require`），但系统只有根因分析一个写入路径，没有显式命令的入口（spec §5.1 写了，未实现）。
2. "新加学历字段，phd 的 exclude"：维度是封闭集合，按 spec §3.1 走开发流程新增 `degree_requirement`。

spec 改动见 §3.1 维度表、§3.1.2、§5.1、§8、§10 第 27–29 条。
**用户确认本计划之前，不写实现代码。**

## 1. 做什么，不做什么

| 做 | 不做 |
| --- | --- |
| 准备级工具 `propose_intent_edit(text, changes)`：把显式命令整理成"命令分析"，给出编号方案（含硬约束对应的软等级、回放、提意见、这次不改） | 新的写入入口：确认仍然只走现有的 `decide` |
| `degree_requirement` 维度、词表 aliases、打标签规则写进工具返回的 rules | 用户自己的学历（背景层）参与打分 |
| 回填：`list_backfill_jobs(dimension)` 列出缺这一维度的已打标签岗位，`backfill_tags(job_id, tags)` 只允许新增该维度的标签 | 回填时改动其他维度的标签（仍然只能走 misread） |
| 回填改变已打 label 岗位后，当天未处理的分析标为需要刷新 | 岗位发现加中国地点（见第 4 节第 3 问，由你选） |

## 2. 实际会怎么用

1. 实现后，agent 先回填：读已打标签的 100 个岗位，只补 `degree_requirement` 标签。
2. 刷新 Roblox 那条还开着的分析：候选原因里会出现"意图里还没有 `degree_requirement.phd`"，你选它，再在 强烈回避 / 排除 之间选（附回放）。
3. "中国也可以"：`propose_intent_edit` 给出 `location.china：未设置 → 必须`（与 `location.us` 是"或"），旁边列出 `强烈偏好` 并说明在 `location.us` 仍是"必须"时，软等级不会让中国岗位出现；你选一个，宿主弹窗批准后写入。

## 3. 模块

- `domain.py`：`DIMENSIONS` 加 `degree_requirement`。
- `engine.py`：`propose_edit(text, changes)` 生成 kind 为 `command` 的分析（没有 label，不做方向筛选）；`decide` 记录 `approved_via` 和来源"显式命令"。
- `proposals.py`：硬约束方案自动附上对应软等级（复用现有逻辑）；`command` 分析跳过"往正确方向推动"的筛选。
- `store.py`：`add_backfill_tags(job_id, tags)`，只追加、不删除；记录回填日志；把当天未处理的分析标为 stale。
- `tools.py` / `mcp_server.py`：三个新工具（都是准备级；`propose_intent_edit` 依赖用户，不进定时任务）和使用说明。
- `configs/default.yaml`：`tagging.backfill_dimensions: [degree_requirement]`。

## 4. 取舍（用户 2026-10-10 的选择记在每条末尾）

1. **维度名和 key**：`degree_requirement.{phd, masters, bachelors}`，只打满足要求的最低学历（推荐）；还是分成 required / preferred 两套 key？**→ 选择：只打最低学历。**
2. **回填已打 label 的岗位**要不要你批准：按普通打标签处理（准备级，推荐，因为它只补岗位原文里写明的事实，而且你之后选的修改都要再确认）；还是作为提交级，每批弹窗批准？**→ 选择：按普通打标签处理（准备级）。**
3. **岗位发现加不加中国**：ApplyPilot 的 Indeed 国家是全局设置，一次只能搜一个国家；LinkedIn 可以按地点搜 "China" / "Shanghai, China" 等；国内招聘网站（Boss 直聘、猎聘等）不在 ApplyPilot 的支持范围。选项：(a) `searches.yaml` 加 LinkedIn 的中国地点（推荐）；(b) 暂时只靠 Workday 里本来就有的中国岗位；(c) 先不加。**→ 选择：先不加，只改意图。**

## 5. 测试（先写，离线，合成数据）

- 显式命令：加 `location.china: require` 返回"必须"和对应"强烈偏好"两个方案、提意见、这次不改；意图版本不变，直到 `decide`。
- `decide` 命令方案：写入新版本，决定日志标明显式命令；方案原文不一致时拒绝；基于旧版本的方案拒绝。
- 命令方案不受方向筛选：回放"修好 0 条"的方案也展示。
- 违反层级规则的命令（如国家已排除时给城市设偏好）被拒绝并说明原因，不生成方案。
- 两个国家 `require` 时，美国或中国的岗位都不被排除，其他国家被排除。
- `degree_requirement` 在维度集合中；aliases 规范化（"博士" → `degree_requirement.phd`）。
- 回填：列出缺该维度的已打标签岗位（含已展示、已打 label）；只接受该维度的标签，其他维度拒绝；不删除已有标签；已展示状态不变。
- 回填已打 label 的岗位后，当天未处理的分析变为 stale；刷新后新 key 作为候选原因出现，选它后方案包含"强烈回避"和"排除"。
- 定时任务不能调用 `propose_intent_edit`。
- 现有 109 个测试继续通过。

---

# 切片 2f 计划：Avature 招聘站点（草案，待用户确认）

目标：把联想（`jobs.lenovo.com`）这类用 Avature 搭建的公司招聘站点写进 `boards.yaml`，由 `fetch_boards` 持续更新，不再靠一次性脚本。
spec 改动见 §8 的 2f 和 §10 第 30–37 条。
编号说明：未合并的切片 2d 草案（分支 `claude/slice-2d-discovery-fixes`）也用了 §10 第 27–30 条，后合并的一方要重新编号。
**用户确认本计划之前，不写实现代码。**

## 1. 做什么，不做什么

| 做 | 不做 |
| --- | --- |
| `provider: avature` 招聘板：逐页读列表，严格判断是否读全（spec 第 31 条） | 用站点的筛选参数在服务器端按国家过滤：参数是各站点自己的数字字段号（联想页面上是 `13036[]`、`13037[]` 这样的名字），取值由页面脚本加载，不通用 |
| 只为新的、通过过滤的岗位读详情页 | RSS（`SearchJobs/feed/`）：每页只有 20 条，没有地点、职能和原文；sitemap 只有链接 |
| `country_include` 国家过滤（只用于 Avature） | 浏览器、登录、申请 |
| 所有招聘板按链接与已有岗位去重 | 修改已手动导入的 42 个联想岗位 |
| `check_board` 对 Avature 只读第一页 | 把 `fetch_boards` 改成后台运行（实际太慢再说） |
| 手动导入的 JSON 格式加可选 `location` | 联想的 Workday：它的 Req # 大多以 WD 开头，但没有找到公开的 Workday 站点 |

## 2. 实际会怎么用

1. 在 `data/local/boards.yaml` 加一项（标题关键词由你定，下面只是示例）：

   ```yaml
   - provider: avature
     board: https://jobs.lenovo.com/en_US/careers
     company: Lenovo
     title_include: [engineer, scientist, developer, researcher, data, machine learning, software]
     country_include: [United States of America]
   ```

2. agent 先运行 `check_board("avature", "https://jobs.lenovo.com/en_US/careers")`。它返回 "999+"、前几个标题和地点，用来核对国家名的写法。
3. 运行 `fetch_boards`：联想约 103 页列表，美国岗位约 250 个，标题过滤后剩下一部分。之前手动导入的 42 个计入 `already_known`，其余的读详情后以"未打标签"入库。每个招聘板的结果例如：
   `{"provider": "avature", "fetched": 1027, "filtered_out": …, "already_known": 42, "new": …, "detail_errors": 0}`
4. 以后每次 `fetch_boards` 只为新岗位读详情。从站点消失的联想岗位不再进入每日推荐。手动导入的 42 个仍按手动导入处理，始终在架，见第 4 节第 2 问。

## 3. 模块

```
src/intent_job_agent/
  avature.py     新增。解析是纯函数：列表页 → 卡片、下一页 offset、总数；详情页 → attributes 和描述。
                 读取部分负责逐页读列表和读单个详情页，输出本项目的 Job。
  discovery.py   PROVIDERS 加 avature，fetch_board 把 avature 交给 avature.py；复用 _url、stable_job_id、_html_to_text
  boards.py      provider 加 avature；加 country_include；avature 的 board 必须是站点地址，其他招聘板仍是 token；
                 keeps() 同时检查 country_include
  domain.py      Job.source 加 avature
  store.py       is_open 改用招聘板列表（含 avature）；按链接查找已有岗位
  tools.py       fetch_boards：过滤 → 按 ID 和链接去重 → （avature）读详情 → 入库 → 记录快照；
                 check_board 对 avature 只读第一页
  importing.py   RawJob 加可选 location
configs/default.yaml   discovery.avature: {request_delay_seconds: 0.5, max_pages: 300}
docs/examples/boards.example.yaml、README、mcp_server.py 的说明：加 avature
CLAUDE.md      "岗位来源"一行加 Avature（确认后再改）
```

不加新依赖，httpx 和 beautifulsoup4 已经在用。

## 4. 取舍（需要你选，推荐项排第一）

1. **国家过滤**
   (a) `country_include`：按站点自己的国家写法整段匹配（推荐）。规则简单，可以预测；写错国家名会过滤掉所有岗位，但 `check_board` 的样例地点和 `filtered_out` 计数能看出来。
   (b) 不按国家过滤，只用 `title_include`：联想的详情请求约多 4 倍，非美国岗位也进入待打标签队列，最后靠 location 硬约束排除。
   (c) 等切片 2d 的国家别名规则（`workday_countries`，能认州名和缩写）合并后共用：更宽松，但 2d 还没确认。
2. **按链接去重的范围**
   (a) 所有招聘板（推荐）。规则统一，也能避免 ApplyPilot 搜索和招聘板抓到同一链接时重复入库。
   (b) 只用于 Avature。
   两种选择下，42 个手动导入的联想岗位都始终在架，不受站点下架影响。要让它们跟着站点下架，就得把它们改成 avature 来源，不在本切片做。
3. **导入格式加 `location`**
   (a) 这次一起加（推荐）。改动只有几行，以后手动导入不用再把地点塞进描述。
   (b) 不加。
4. **单个详情页失败**
   (a) 这个岗位本次不入库，下次重试，其他岗位照常入库（推荐）。
   (b) 整个站点算失败。

## 5. 测试（先写，全部离线）

合成 HTML 放在 `tests/fixtures/avature/`，每个文件标注 `<!-- SYNTHETIC -->`。页面结构仿照 Avature 模板的 class 名（`article--result`、`list-controls__pagination`、`paginationNextLink`、`article--details`、`article__content__view__field__label/value`、`visibility--hidden`），公司和域名都是虚构的（`*.example.com`）。准备两种布局：A 是"国家, 州, 城市"，带职能、Req # 和发布日期，每页 3 个；B 是"城市, 州, 国家"，只有地点，每页 4 个。页面用 httpx 的 MockTransport 回放，测试中请求间隔为 0。

列表与分页：
- 两种布局的卡片都能解析出标题、链接、地点和 attributes。副标题第一项是 "Req #: …" 时地点为空；"No jobs found" 卡片不算岗位。
- 每页 3 个和每页 4 个都能按 offset 翻页，在没有"下一页"的结果页停止；最后一页的确切总数与去重后的数量一致。
- 以下每种情况都报错，不入库，不记录快照，原有岗位的在架状态不变：
  - 中途出现 "Oops" 错误页。
  - 中途出现 "No jobs found"，并且"下一页"跳回 offset 10。
  - "下一页"的 offset 与计算不符。
  - 总数对不上。
  - 超过页数上限。
  - 卡片缺少 JobDetail 链接。
  - JobDetail 链接指向其他域名或其他路径前缀。
- 第一页就是"没有岗位"：抓取成功，0 个岗位，记录空快照。
- 同一岗位换了标题 slug 或语言路径，岗位 ID 不变。

详情：
- 带标签的字段进 attributes；不带标签的段落连同小节标题组成描述；隐藏字段跳过；重复段落只保留一次。描述里写给 AI 的指令原样作为文本保存，不影响任何行为。
- 只为通过过滤的新岗位请求详情页（用 MockTransport 统计请求数）：被 `title_include` 或 `country_include` 过滤掉的、已入库的、按链接去重的岗位，都不请求。
- 某个详情页返回 500：该岗位不入库，计入 `detail_errors`；其他岗位照常入库，快照包含它；下一次抓取会重试它。

过滤、去重和配置：
- `country_include` 按整段匹配：国家在开头或结尾都能匹配，忽略大小写；"United States" 不匹配 "United States of America"；没有地点的岗位保留。
- 以下写法让 `boards.yaml` 无效：`country_include` 写在非 Avature 招聘板上；Avature 的 `board` 不是 HTTPS 站点地址，或者带查询参数、IP 地址、非默认端口。其他招聘板的 token 规则不变。
- 手动导入的岗位与抓到的岗位链接相同：不重复入库，计入 `already_known`，手动导入的岗位不变且仍在架。第 4 节第 2 问选 (a) 时，Greenhouse 同样适用。
- `check_board("avature", …)` 只发一个请求，返回计数文字、样例标题和样例地点，不入库。
- 从站点消失的 Avature 岗位不再进入每日推荐；抓取失败时保持原状。
- 导入的 JSON 带 `location` 时写入 `Job.location`，不带时为空（第 3 问选 (a) 时）。
- 定时任务模式仍然包含 `check_board` 和 `fetch_boards`。
- 现有 121 个测试继续通过。

实现后的真实核对需要你同意，在本机运行，只读：先 `check_board` 联想站点，再 `fetch_boards`，核对 42 个手动导入的岗位都计入 `already_known`，新增数量符合标题和国家过滤的预期。
