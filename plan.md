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

# 切片 2e 计划：显式修改意图 + 学历要求维度（用户 2026-10-10 确认；已实现）

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

# 切片 2f 计划：每日两个模块——拉岗位（漏斗 + 子 agent 打标签）和 /today 校准（用户 2026-10-11 确认，已实现）

来源：用户 2026-10-11 提出把每日流程拆成三个模块：① 不管当天校不校准都拉岗位并打标签；② 选 7+3、打 label、校准意图；③ 按当前意图生成投递清单（留到以后单独设计）。本切片做 ① 和 ②。
用户选择：打标签由对话中的 agent 完成，可以派子 agent 并行；不配模型 key。
实现时的补充见 spec §10 切片 2f。

## 1. 模块 ①：拉岗位（`/fetch-jobs`，也会被 `/today` 自动调用）

漏斗，越往后越贵、越少：

| 层 | 做什么 | 谁做 |
| --- | --- | --- |
| 1 抓取 | `discover_jobs`（和已配置的招聘板），按网址去重 | 已有 |
| 2 代码预打标签 | 从结构化字段读出：地点（"City, ST" / 国家名 → `location.<国家>.<城市>`）、远程（`work_mode.remote`）、雇佣类型、薪资；用词表别名匹配标题（例如标题含 "Machine Learning" → `role.ml_engineering`，含 Senior/Staff → `seniority.senior`） | 代码，免费 |
| 3 硬约束预筛 | 只用代码**有把握**读出的预标签判断：明确违反硬约束的岗位标为"预筛排除"（不删除；意图变了会重新判断）；读不出的维度一律放行 | 代码，免费 |
| 4 粗排 | 用同一个打分函数对预标签打分，决定打标签的先后 | 代码，免费 |
| 5 完整打标签 | 只给粗排前 N 个打（N = 配置 `tagging.daily_budget`，默认 50）：主 agent 每 10 个一批派子 agent，子 agent 读原文，在预标签基础上补齐、修正后提交 | 子 agent |

- 没打完整标签的岗位留在库里，以后意图变了、排序变了还会轮到。
- 每天的 7+3 只从**完整打过标签**的岗位里选。
- 子 agent 定义在 `.claude/agents/job-tagger.md`：`model: haiku`（便宜模型，走 Claude Code 额度，不需要 key）；**只能用** `get_tagging_rules`、`show_job`、`submit_job_tags`，碰不到提交级工具。主 agent 把岗位编号分好批交给每个子 agent，避免重复打。
- **详细的打标签规则写在代码里**（新的读级工具 `get_tagging_rules`，`list_untagged_jobs` 也一并返回）：逐个维度写明判断标准和例子，优先用已有的 key，没把握就不打。主 agent 和子 agent 看到的是同一份。
- 子 agent 从代码给出的预标签开始补齐、修正（用户 2026-10-11 确认以上三点）。
- 第一次抓取之后，建议把 `searches.yaml` 的 `hours_old` 改成 24，只取新岗位（由 agent 提示你，不自动改）。

## 2. 模块 ②：`/today`

1. 今天还没拉过岗位 → 先跑模块 ①。
2. `select_today` → 用紧凑格式列出 10 个岗位（编号、推荐/探索、标题、公司、地点、关键标签）。
3. 你一行回完：`1+ 2-坐班 3- 5+ 6?`（`+` 想投、`-` 不要、`?` 读错了，冒号后写不写原因都行）→ 一次 `record_labels`，弹一次窗。
4. 逐个处理分析：先选原因，再选怎么改（编号）→ `decide` 弹窗。读错了的岗位问正确标签 → `correct_tags` 弹窗。
5. 最后一两句话总结今天意图改了什么。

两个命令都是项目里的 skill（`.claude/skills/fetch-jobs/`、`.claude/skills/today/`），只编排现有工具，不新增写入路径。

## 3. 新增代码

```
src/intent_job_agent/
  pretag.py      纯函数：结构化字段 + 标题别名 → 预标签（带"有把握"的维度列表）
  funnel.py      预筛与粗排（复用 scoring.score_job），决定打标签顺序
store / tools    记录预标签、预筛状态、每日抓取记录；list_untagged_jobs 按粗排顺序、受每日预算限制；
                 新的读级工具 get_fetch_status(day)：今天拉过没有、各层数量
```

## 4. 测试（先写，离线，合成数据）

- 预打标签：常见地点写法（"San Francisco, CA"、"Remote - US"、"New York, NY (Hybrid)"、"London, UK"）、远程、标题别名、资深级别；读不出时不猜。
- 预筛：只在有把握的维度上排除；没把握的放行；意图修改后重新判断。
- 粗排顺序与打分函数一致；每日预算生效；已打完整标签的岗位不受预标签影响。
- 子 agent 定义里的工具列表不含任何提交级工具；两个 skill 文件存在并引用的工具都存在。
- 之前的 121 个测试继续通过。

## 5. 不做

- 模块 ③（投递清单）和自动投递：涉及资格判断（背景层，切片 3）和投递授权，单独设计。
- 定时自动运行（需要模型 key，本次不配）。

---

# 切片 2g 计划：接入 Avature（用户 2026-10-10 确认；已实现）

目标：用 Avature 搭建招聘站点的公司都能写进 `boards.yaml`，由 `fetch_boards` 持续更新。agent 按你的意图找候选公司，你来挑，联想是其中一家。不再靠一次性脚本。
spec 改动见 §8 的 2g 和 §10 第 39–48 条。

已定（2026-10-10 你的选择）：
- 抓取方式：每家读全站（能判断下架），不按搜索词搜。
- 公司名单：agent 搜索候选，用 `check_board` 验证，你挑选。

编号说明：未合并的切片 2d 草案（分支 `claude/slice-2d-discovery-fixes`）也用了 §10 第 27–30 条，后合并的一方要重新编号。2026-10-11 与已实现的切片 2f（每日抓取漏斗）合并时，本切片从 2f 改为 2g，spec 规则顺延为第 39–48 条。
**用户确认本计划之前，不写实现代码。**

## 1. 做什么，不做什么

| 做 | 不做 |
| --- | --- |
| `provider: avature` 招聘板：逐页读列表，严格判断是否读全（spec 第 40 条） | 用站点的筛选参数在服务器端按国家过滤：参数是各站点自己的数字字段号（联想页面上是 `13036[]`、`13037[]` 这样的名字），取值由页面脚本加载，不通用 |
| 只为新的、通过过滤的岗位读详情页 | RSS（`SearchJobs/feed/`）：每页只有 20 条，没有地点、职能和原文；sitemap 只有链接 |
| `country_include` 国家过滤（只用于 Avature） | 浏览器、登录、申请 |
| 所有招聘板按链接与已有岗位去重 | 修改已手动导入的 42 个联想岗位 |
| `check_board` 对 Avature 只读第一页 | 按搜索词搜（像 Workday 那样） |
| agent 找候选公司：网页搜索，`check_board` 验证，你挑选后写入 `boards.yaml` | 代码自动发现公司；项目自带公司名单 |
| `fetch_boards` 后台运行，`boards_status` 查进度（决定 5） | 为个别站点写特殊解析（如 IBM、McKinsey） |
| 手动导入的 JSON 格式加可选 `location` | 联想的 Workday：它的 Req # 大多以 WD 开头，但没有找到公开的 Workday 站点 |

## 2. 实际会怎么用

1. 你说"帮我找用 Avature 的公司"。agent 按你的意图用网页搜索找候选，逐个运行 `check_board`，再列成编号选项让你挑：

   ```
   1) Lenovo     https://jobs.lenovo.com/en_US/careers    999+ 个岗位   例：Junior Data and AI-ML Engineer（United States of America, North Carolina, Morrisville）
   2) Bloomberg  https://bloomberg.avature.net/careers    329 个岗位    例：Buy-Side OMS (AIM) Sales Specialist, Dubai, Financial Solutions（Dubai, Dubai, United Arab Emirates）
   …
   ```

   验证失败的站点不列出，会说明原因。
2. 你选中的公司由 agent 写进 `data/local/boards.yaml`，每家一项。标题关键词和国家名由你定，国家名照 `check_board` 显示的写法填：

   ```yaml
   - provider: avature
     board: https://jobs.lenovo.com/en_US/careers
     company: Lenovo
     title_include: [engineer, scientist, developer, researcher, data, machine learning, software]
     country_include: [United States of America]
   ```

3. 运行 `fetch_boards`，它在后台跑，用 `boards_status` 查进度。以联想为例：约 103 页列表，美国岗位约 250 个，标题过滤后剩下一部分。之前手动导入的 42 个计入 `already_known`，其余的读详情后以"未打标签"入库。每个招聘板的结果例如：
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
                 后台运行，新工具 boards_status 查进度（准备级，定时任务可用）；
                 check_board 对 avature 只读第一页，返回样例标题和地点
  mcp_server.py  说明里加"怎么找用 Avature 的公司"：搜索 → check_board → 编号选项 → 用户选中后写入 boards.yaml
  importing.py   RawJob 加可选 location
configs/default.yaml   discovery.avature: {request_delay_seconds: 0.5, max_pages: 300}
docs/examples/boards.example.yaml、README：加 avature
CLAUDE.md      "岗位来源"一行加 Avature（已改）
```

不加新依赖，httpx 和 beautifulsoup4 已经在用。

## 4. 取舍（用户 2026-10-10 的选择记在每条末尾）

1. **国家过滤**
   (a) `country_include`：按站点自己的国家写法整段匹配（推荐）。规则简单，可以预测；写错国家名会过滤掉所有岗位，但 `check_board` 的样例地点和 `filtered_out` 计数能看出来。
   (b) 不按国家过滤，只用 `title_include`：联想的详情请求约多 4 倍，非美国岗位也进入待打标签队列，最后靠 location 硬约束排除。
   (c) 等切片 2d 的国家别名规则（`workday_countries`，能认州名和缩写）合并后共用：更宽松，但 2d 还没确认。
   **→ 选择（2026-10-10）：(a)。**
2. **按链接去重的范围**
   (a) 所有招聘板（推荐）。规则统一，也能避免 ApplyPilot 搜索和招聘板抓到同一链接时重复入库。
   (b) 只用于 Avature。
   两种选择下，42 个手动导入的联想岗位都始终在架，不受站点下架影响。要让它们跟着站点下架，就得把它们改成 avature 来源，不在本切片做。
   **→ 选择（2026-10-10）：(a)。**
3. **导入格式加 `location`**
   (a) 这次一起加（推荐）。改动只有几行，以后手动导入不用再把地点塞进描述。
   (b) 不加。
   **→ 选择（2026-10-10）：(a)。**
4. **单个详情页失败**
   (a) 这个岗位本次不入库，下次重试，其他岗位照常入库（推荐）。
   (b) 整个站点算失败。
   **→ 选择（2026-10-10）：(a)。**
5. **`fetch_boards` 后台运行**
   (a) 改成后台运行，和 `discover_jobs` 一样用状态工具查进度（推荐）。名单一长，读全站要十几分钟，一次工具调用等这么久，宿主可能超时。
   (b) 保持同步，先用几家公司试试，太慢再改。
   **→ 选择（2026-10-10）：(a)。**

## 5. 测试（先写，全部离线）

合成的详情页和错误页放在 `tests/fixtures/avature/`，列表页由测试代码按模板生成，每个页面都标注 `<!-- SYNTHETIC -->`。页面结构仿照 Avature 模板的 class 名（`article--result`、`list-controls__pagination`、`paginationNextLink`、`article--details`、`article__content__view__field__label/value`、`visibility--hidden`），公司和域名都是虚构的（`*.example.com`）。准备两种布局：A 是"国家, 州, 城市"，带职能、Req # 和发布日期，每页 3 个；B 是"城市, 州, 国家"，只有地点，每页 4 个。页面用 httpx 的 MockTransport 回放，测试中请求间隔为 0。

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
- 后台运行（第 5 问选 (a) 时）：`fetch_boards` 立即返回"运行中"；`boards_status` 在完成后给出每个招聘板的结果；运行期间再次调用被拒绝；`wait=true` 时同步返回结果；定时任务模式包含 `boards_status`。现有的 `fetch_boards` 测试改用 `wait=true`。
- 现有 121 个测试继续通过。

实现后的真实核对需要你同意，在本机运行，只读：
1. 按第 2 节第 1 步找候选公司，你挑选。
2. 运行 `fetch_boards`，核对 42 个手动导入的联想岗位都计入 `already_known`，各家的新增数量符合标题和国家过滤的预期。

---

# 切片 5 计划：投递清单（模块③）（用户 2026-10-14 选定 1A 2A 3A 4A，同日确认计划；已实现）

规则见 spec 4.9，实现时的补充见 spec §10 切片 5。

## 1. 做什么，不做什么

- 做：spec 4.9 的清单（谁进、排序、为什么）、待投 / 已投 / 跳过状态、最近 7 个标注日的一致率、`/apply-list` 命令。
- 不做：资格判断（等切片 3 的背景层）、自动投递、定时生成清单（切片 4）、把"跳过"当成信号。

## 2. 实际会怎么用

```
你：/apply-list
agent：最近 7 个标注日一致率 87%（45/52；想投 18/20，不要 27/32）
       待投 63 个，其中新的 12 个。文件：data/local/apply/2026-10-14.json、.csv
       1 [新][你标了想投] Senior ML Engineer — Acme · us.seattle · https://…
       2 [新] ML Engineer, Ads — Beta · us.new_york · https://…
            role.ml_engineering 强烈偏好；specialty.recsys 偏好；work_mode.onsite 回避
       …（共列 20 个）
       投完可以说"1-5 已投，7 跳过"。
你：1-5 已投，7 跳过
agent：已更新。还剩 57 个待投。
```

## 3. 样例（合成，写进测试）

| # | 情况 | 结果 |
| --- | --- | --- |
| 1 | A：已打标签、在架、够推荐线，从没给你看过 | 进，标"新"，列出命中的条目和等级 |
| 2 | B：分数够，但在伦敦，被 `location.us: require` 排除 | 不进 |
| 3 | C：分数够，你标过 reject；后来一次修改把这条 label 标成已过时 | 不进 |
| 4 | D：分数不够，你标过 want | 进，排在最前，注明"你标了想投" |
| 5 | E：Greenhouse 岗位，最新一次抓取里已经没有了 | 不进（已下架） |
| 6 | F：还没打完整标签 | 不进 |
| 7 | 把 A 标成已投、G 标成跳过，再生成 | A、G 不再出现；其他待投的不再标"新" |
| 8 | 确认一个修改后 H 不够推荐线了，之后又改回来 | H 先消失；再出现时不标"新" |
| 9 | label 分布在 9 个标注日（中间隔了几天没打），包括 misread 和已过时的 | 只算最近 7 个标注日里未过时的 want / reject，分方向计数 |
| 10 | 一条 label 都没有 | 只显示"还没有 label"，不给百分比 |
| 11 | 薪资低于下限的岗位仍够推荐线 | 进，"为什么"里多一句"薪资低于下限" |

## 4. 模块

```
src/intent_job_agent/
  apply_list.py   纯逻辑：谁进清单、排序、"为什么"的文字、标注日窗口和一致率
                  （复用 scoring.score_job 和 replay.evaluate，不另写打分）
  store.py        新表 applications(job_id, status, first_listed, updated_at)
  tools.py        build_apply_list(day=None)：准备级。写 apply/<day>.json 和 .csv，返回一致率、
                    待投数、新增数和前 N 个（带编号和 job_id）
                  mark_applications(job_ids, status)：准备级。status 为 待投 / 已投 / 跳过；
                    不放进定时任务能用的工具
configs/default.yaml   apply_list: {agreement_window_days: 7, shown: 20, dir: apply}
.claude/skills/apply-list/SKILL.md
```

## 5. 需要你确认的取舍

1. 标过 want 的排最前，其余按分数排。
2. 不展示分值：spec 3.1 规定用户只看到等级文字。我在问题 4 里说的"分数"改为"为什么进清单"。
3. 标"已投 / 跳过"用准备级工具，不弹窗：它不改意图、不改 label，标错了可以改回待投。如果你希望它也弹窗，就改成提交级。
4. 清单里某个岗位的标签读错了：用已有的 `correct_tags` 修正（弹窗），修正后下次生成时重新判断。
5. 文件按日期命名（`apply/2026-10-14.json`），同一天重复生成会覆盖。

## 6. 测试（先写，离线，合成数据）

- 样例 1–11。
- 文件：JSON 字段齐全、CSV 有表头、都不含岗位原文；写在数据目录的 `apply/` 下。
- 工具分级：两个新工具都是准备级；`mark_applications` 不在定时任务的工具里；状态值只接受三种。
- `/apply-list` skill 里调用的工具都存在，并且不预先批准提交级工具。
- 已有的测试继续通过。

---

# 切片 6 计划：投递（模块④，借用 ApplyPilot 的 apply/）（用户 2026-10-14 确认；已实现）

规则见 spec 4.10，实现时的补充见 spec §10 第 54–62 条。

## 1. 做什么，不做什么

- 做：
  - 把 ApplyPilot 的 `apply/`（`launcher.py`、`prompt.py`、`chrome.py`、`dashboard.py`）原样放进 `vendor/applypilot/apply/`，只改 import，修正放在适配层，和切片 2c 一样。
  - 通用的 cover-letter skill，可以换成用户自己的。
  - 求职信校验；"停在提交前"的公司名单；仲裁协议拦截；批量批准；`/apply` 命令。
- 不做：按岗位改写简历；定时自动投递；投递后的跟进邮件。

## 2. 实际会怎么用

```
你：/apply
agent：今天清单里待投 20 个。这一批：
       自动提交（15）：1 ML Engineer — Acme · us.seattle …
       停在提交前（5，在你的公司名单里）：16 Applied Scientist — Stripe …
       每个岗位都先写求职信。开始吗？
你：开始 → 弹窗（start_applying，显示这一批的摘要）→ 你批准
agent：（派子 agent 写 20 封求职信，代码校验，生成 PDF；不合格的退回重写）
       （后台逐个投递；你可以随时问进度）
       Stripe 停在提交前了，Chrome 窗口开着。你检查后自己点提交，然后说"16 已投"或"16 不投"。
       …
       结果：已投 12；等你提交 5；没投成 3（验证码 1、已下架 1、要求接受仲裁协议 1：Acme，条款原文 …）
```

## 3. 第一次设置（通用：别人也这样用）

`data/local/applicant/`（git 忽略）：

| 文件 | 内容 |
| --- | --- |
| `profile.json` | ApplyPilot 的格式（`docs/examples/applicant/profile.example.json`，即 ApplyPilot 的 `profile.example.json`）：联系方式、密码、工作许可、薪资、经历年限、技能范围、平等就业调查的默认答案 |
| `resumes/` | 一份或几份简历的 PDF 和文本（`.md`/`.txt`），文本给求职信核对数字用 |
| `careful_companies.yaml` | "停在提交前"的公司名单 |
| `plugins.yaml`（可选） | 接入自己的东西：`cover_letter_skill`（skill 目录）、`resume_selection`（选简历的规则文件）、`rules`（投递时要遵守的个人规则文件） |

你的接法：`plugins.yaml` 指向你本机的 qianqian-agent-kb：cover-letter skill、`resumes/README.md`（选简历规则）、`AGENTS.md`。`profile.json` 可以从知识库里的 `sources/profile/applypilot-profile-excerpts.json` 和 `profile/facts.json` 补全，由你确认后写入。

另外需要：Node.js（`npx` 跑 Playwright MCP）、Chrome、Claude Code 命令行（ApplyPilot 每个岗位启动一次 `claude -p`，用你的 Claude 额度）；可选 CapSolver key 和 Gmail MCP，写进 `data/local/.env` 和 Gmail 授权，不在对话里传。

## 4. 模块

```
src/intent_job_agent/
  vendor/applypilot/apply/   ApplyPilot 原文件，只改 import
  apply_bridge.py            适配层：
                               - 把批准的这一批写进 ApplyPilot 的工作库（简历路径、求职信路径、申请链接）
                               - 每个岗位：公司在名单里 → ApplyPilot 的 dry-run（填好不点提交）；否则正常提交
                               - 在 ApplyPilot 的提示后面追加：仲裁协议不接受、停下报告（RESULT:FAILED:arbitration）；
                                 接入的个人规则（plugins.yaml 的 rules）
                               - MCP 服务固定版本（ApplyPilot 用 @latest）
                               - 后台运行；停在提交前的岗位暂停那个 worker，等你说"已投 / 不投"
                               - ApplyPilot 的结果码 → 清单状态
  cover_letter_check.py      纯函数：ApplyPilot 的禁用词、模型套话、长破折号、字数范围；信里的数字必须在简历文本里出现
  store / tools              清单状态加 failed（附原因）和 waiting（停在提交前，等你）；新工具：
                               prepare_apply_batch(day)        准备级：这一批、每个是自动还是停、缺什么设置
                               check_cover_letter(job_id, text) 读级：校验结果
                               save_cover_letter(job_id, text)  准备级：校验通过才保存，并生成一页 PDF
                               start_applying(batch_id, summary) 提交级：开始投递这一批（弹窗）
                               apply_status()                   准备级：进度和结果，结果写回清单状态
.claude/skills/apply/SKILL.md          /apply 的编排
.claude/skills/cover-letter/           通用 cover-letter skill（从你的 skill 去掉个人内容改写），附 render_pdf.py
.claude/agents/cover-letter-writer.md  写信的子 agent（sonnet；只能读岗位、读简历、check / save 求职信）
configs/default.yaml                   apply: 字数范围、worker 数、模型、MCP 版本
```

## 5. 取舍（用户 2026-10-14 的回答记在每条末尾）

1. **批量批准**：agent 不会自己开始投递。每一批先列出来（哪些岗位、自动提交还是停在提交前），你批准一次（`start_applying` 弹窗，摘要必须和展示的一致）才开始。定时任务永远不投递。（没有异议，按此做）
2. **每批先检查 10 个**：每一批的前 10 个（按清单顺序）都停在提交前，每个开在单独的标签页里，填好的表留着给你检查、自己提交。你说"继续"后，agent 再调用一次 `start_applying`（再弹一次窗），其余的才开始；公司名单里的仍然停在提交前。（你的要求）
3. **Chrome 资料**：照 ApplyPilot 复制你日常 Chrome 的登录状态，不用逐个网站登录。（你的选择）风险控制见下面。
4. **填表规则**：筛选题和薪资照 ApplyPilot（同领域的工具答 YES、自动报薪资）；你的知识库只用于选简历和写求职信。所以 `plugins.yaml` 只接 `cover_letter_skill` 和 `resume_selection`，不接填表规则。（你的选择）
5. **求职信由对话里的子 agent 写**（不需要 API key），每个岗位一封，代码校验不通过就退回重写，最多 3 次，仍不通过就这个岗位不投、报告给你。（确认）

### 风险控制（用户 2026-10-14 回答；都在代码里自动完成，每次投递你不用做任何事）

你要保护的是**支付密码和 SSN**，其他无所谓。

1. **复制 Chrome 资料时去掉三样**：保存的密码（支付网站的密码在这里）、银行卡、表单自动填充记录（以前填过的 SSN 可能在这里）。cookies、书签等都留着，各网站仍是登录状态。
2. **SSN 和付款永远不填**：投递资料里不放 SSN 和支付信息；表单要求 SSN、银行卡或申请费时，停下这个岗位并告诉你（结果码 `RESULT:FAILED:ssn_required` / `payment_required`），和仲裁协议一样。
3. **高风险网站打不开**：网页版邮箱、网银和支付、云盘、密码管理器（Playwright MCP 的 blocked-origins，名单在配置里）。（同意）
4. **只给浏览器和 Gmail，并且不跳过权限检查**：投递 agent 没有命令行和读写文件；Claude Code 不用 ApplyPilot 的 `bypassPermissions`，改成白名单：只预先放行 Playwright 的浏览器工具和 Gmail 的 `search_emails` / `read_email` / `send_email`，其余一律自动拒绝（用户 2026-10-14 选 A：开发环境的安全检查拦下了跳过全部权限确认的设计）。求职信的修改和生成 PDF 在投递之前、由对话里的子 agent 和 `save_cover_letter` 完成，投递时只上传现成的 PDF（浏览器自带的上传，不需要命令行）；表单里的文字题直接在网页里填。（待你确认，见对话）
5. **每批结束的检查单**：每个岗位打开过的与招聘无关的网站、发出的每封邮件（收件人、标题）。（同意）

第 1、3、4 条需要在你的 Mac 上实际跑一次确认生效（第 3 条依赖 Playwright MCP 在连接已有 Chrome 时也执行屏蔽，第 4 条依赖你本机 Claude Code 的版本）。

## 6. 测试（先写，离线，合成数据；不启动 Chrome 和 Claude Code）

- vendored 的 `apply/` 只改了 import；
- 适配层：名单内的公司（不区分大小写）走 dry-run；写进工作库的字段；提示里有仲裁协议规则和接入的个人规则；MCP 版本固定；结果码对应的状态；停在提交前的岗位会暂停、你回复后继续（用假的 runner）；
- 投递资料不进 `agent.sqlite`，也不出现在打标签、分析工具的返回里；
- `start_applying` 是提交级、不在定时任务里、摘要不一致就拒绝；没批准的岗位不会被投；
- 求职信校验：禁用词、套话、长破折号、字数、数字不在简历里；允许 "May–June" 这样的短横；
- 两个 skill 和子 agent 只引用存在的工具，子 agent 碰不到提交级工具。

## 7. 风险（你知情）

- 自动提交撤不回；同一家公司投错岗位会留下记录。
- 招聘网站可能封号；用 CapSolver 破解验证码违反多数网站的条款。
- 投递 agent 有你的密码和 Gmail 权限，同时在读不可信的网页；网页里的注入文本可能诱导它做别的事。
- 每个岗位跑一次 Claude Code，几分钟一个，消耗你的 Claude 额度。

---

# 切片 2h 计划：接入 BOSS 直聘（草案，2026-10-13，待用户确认）

来源：2026-10-13 用户要求把 BOSS 直聘接为岗位来源。
**用户确认本计划之前，不写实现代码。** 确认后先改 spec（§7、§8，§10 从第 86 条起）和 CLAUDE.md 的"岗位来源"一行，再写测试，最后写代码。

## 0. 先说清楚三件事

1. **BOSS 没有公开接口，也不能匿名读取。** 搜索和详情都要登录。接口要带浏览器脚本生成的风控令牌（`__zp_stoken__`）；请求频率一高就会被限流、要求验证，严重时账号会被限制。它的用户协议禁止自动抓取。风险由**你自己的 BOSS 账号**承担：性质和 2c 里 LinkedIn 的风险一样，但更直接，那边最多封 IP，这边是封账号。
2. **不绕过风控。** 不自己生成或伪造令牌，不做无头浏览器反检测，不自动通过验证码或安全验证，也不换账号、换 IP 来躲限制。遇到"环境异常"、安全验证或限流就停下，把情况报告给你，由你在浏览器里处理。
3. **按现在的意图，BOSS 的岗位一个都进不了推荐。** 当前意图是 `location.us：必须`，代码预筛会把能读出"中国"的岗位全部放到一边。接入后，要先用显式修改（`propose_intent_edit`）把中国加进来，例如 `location.china：必须`，与美国是"或"的关系。这是运行时的操作，不属于本切片的开发。

## 1. 接入方式（取舍 1）

| | 做法 | 好处 | 代价 |
| --- | --- | --- | --- |
| **(a) 推荐** | 依赖开源的 boss-cli（GitHub `jackwener/boss-cli`，PyPI 包 `kabi-boss-cli`，pyproject 声明 Apache-2.0）。你自己在终端运行 `uv run boss login`，它会从你已登录的浏览器读取 BOSS 的 Cookie，也可以用 App 扫码。本项目只调用它的只读命令：`boss search … --json`、`boss detail … --json` 和 `boss status` | 能进入 `/fetch-jobs`，以后也能进定时任务；BOSS 改版时多半上游先修好，我们升级版本就行；它遇到安全拦截（code 121/122）会直接报错，不绕过 | 账号风险，见第 0 节第 1 条；登录态过期后要你重新运行 `boss login`，多久过期一次要实际用过才知道 |
| (b) | agent 用 Claude in Chrome，在你已登录的 Chrome 里打开搜索页，读取岗位卡片和详情，整理成 JSON 后用 `import_jobs` 导入 | 用的就是你本人的浏览器，最接近正常浏览 | 只能在对话中运行；每个岗位都要打开页面，慢，也费 token；进不了定时任务（浏览器自动化在切片 6 之后已不在 §7 的"不做"里，所以不需要为此改 spec） |
| (c) | 不做自动化：你把感兴趣的岗位文本贴进导入文件，用现有的 `import_jobs` 导入 | 零风险，零开发 | 全靠手工 |

(a) 的几点细节：
- **作为依赖使用，不复制代码**，这一点和 ApplyPilot 不同。逆向得来的接口要经常跟着 BOSS 改，如果 vendor 进来，就得我们自己维护。版本锁在 `uv.lock` 里，升级时运行 `uv lock --upgrade-package kabi-boss-cli`。
- **通过命令行的 JSON 输出对接，不 import 它的内部类。** 它的 `--json` 输出是带版本号的固定格式（`ok`、`schema_version`、`data`、`error.code`），比内部 Python 接口稳定。
- **调用封装里写死命令白名单**，只允许 `search`、`detail`、`status` 三个命令。打招呼、投递、聊天、读简历这些操作一律不调用。
- 实现之前，我会通读它读取 Cookie 的代码，确认它只读 zhipin.com 的 Cookie、也只发往 zhipin.com，然后锁定这个版本。登录凭证保存在它自己的 `~/.config/boss-cli/` 目录里，本项目和 agent 都不读取，也不显示。

## 2. 做什么，不做什么（按 (a) 写）

| 做 | 不做 |
| --- | --- |
| `data/local/boss.yaml`：搜索词 × 城市，加上 BOSS 自带的筛选条件（经验、学历、职位类型、薪资），格式见第 3 节。第一次由 agent 按你的意图起草，你看过之后才用 | 打招呼、投递、聊天，读取你的简历或投递记录 |
| `discover_jobs` 新增来源 `boss`：有 `boss.yaml` 就运行，在后台跑，结果在 `discovery_status` 里单独报告新增数和错误 | 生成或伪造风控令牌、无头浏览器、自动通过验证、轮换代理 |
| 准备级工具 `check_boss(query, city)`：检查登录状态，再搜一页，只返回总数和几个样例（标题、薪资、经验），不入库 | 保存招聘者（HR）的姓名、职位、头像和活跃状态 |
| 只为**新的、并且通过过滤的**岗位读取详情，每次有数量上限 | 判断岗位是否下架（见取舍 3） |
| 预打标签认识 BOSS 的结构化字段：城市、学历、经验、实习（第 4 节） | 国内大厂的校招官网（见第 7 节） |

## 3. 实际会怎么用

1. 你先在浏览器里登录 zhipin.com，再在终端运行一次 `uv run boss login`。
2. agent 按你的意图起草 `boss.yaml` 给你看，例如：

   ```yaml
   searches:
     - query: 算法工程师
       cities: [北京, 上海, 杭州]
       exp: 在校/应届
       job_type: 全职
       pages: 1            # 每个"搜索词 × 城市"读几页，默认 1
   title_exclude: [实习, 销售, 外包]
   ```

   搜索词、城市、筛选条件和 `title_exclude` 都是来源范围，不属于意图，校准不会修改它们（和 `searches.yaml` 一样）。
3. agent 先用 `check_boss` 验证登录状态，并试跑其中一个搜索。之后 `/fetch-jobs` 照常运行，BOSS 只是多了一个来源。
4. 结果例如 `{"source": "boss", "searched_pages": 3, "cards": 45, "filtered_out": 6, "already_known": 0, "new": 30, "details_deferred": 9, "errors": []}`。
5. 登录过期时，结果里只有一句提示："BOSS 登录已过期：在浏览器里打开 zhipin.com，再运行 `uv run boss login`"。其他来源照常运行。

## 4. 岗位怎么落库

| 本项目字段 | 来自 BOSS |
| --- | --- |
| `id` | `boss-` 加 `encryptJobId`。这个 ID 是稳定的，同一个岗位出现在多次搜索里也只算一次 |
| `url` | `https://www.zhipin.com/job_detail/<encryptJobId>.html` |
| `title` / `company` | 职位名 / 品牌名 |
| `location` | 城市名，来自结构化字段，例如"北京"；区和商圈放进 attributes |
| `description` | 详情里的职位描述全文。它是不可信内容，只用来打标签 |
| `salary` | "20-40K·15薪" 解析为人民币、按月、2 万到 4 万，"15薪"放进 attributes；"200-300元/天"这类按天计的、"面议"都记为未知 |
| `attributes` | 经验、学历、行业、规模、融资阶段、技能标签、福利标签、区、商圈 |
| 不保存 | 招聘者的姓名、职位、头像和活跃状态；`securityId`、`lid` 这类和会话相关的令牌 |

预打标签（由代码完成，不用模型）新增以下几项。它们都来自结构化字段，所以都算"有把握"：
- **城市**：加一张中国城市表，把中文名映射为 `location.china.<拼音>`，例如北京 → `location.china.beijing`。手动导入时写中文城市名也能识别。
- **学历**：本科 → `degree_requirement.bachelors`，硕士 → `masters`，博士 → `phd`。学历不限、大专、中专不打标签，留给子 agent 判断。
- **经验 → seniority**（取舍 4）：在校/应届 → `new_grad`；1 年以内、1-3 年 → `junior`；3-5 年 → `mid`；5-10 年、10 年以上 → `senior`；经验不限不打标签。
- **实习**：标题含"实习"，或者职位类型是实习 → `employment_type.internship`。
- 打标签规则（`get_tagging_rules`）补充几条中文写法，比如校招/应届、统招本科，以及"博士优先"不算学历要求。

## 5. 取舍（请逐条选）

1. **接入方式**：(a) boss-cli（推荐）/ (b) Claude in Chrome / (c) 手动导入。选 (a)，就等于你知情接受了第 0 节第 1 条的账号风险。
2. **每次抓取的量**：(a) 每次最多读 10 页搜索结果、30 个详情，请求间隔至少 3 秒（推荐，写在配置里）/ (b) 更保守：5 页、15 个详情 / (c) 你来给数字。超出详情上限的岗位本次不入库，下次如果还在搜索结果里，会接着读。
3. **下架判断**：(a) 和 JobSpy 一样，一律视为在架（推荐）。每日 7+3 只选没展示过的岗位，所以影响不大，等做投递清单时再考虑 / (b) 连续 N 天没在任何搜索结果里出现，就不再进入推荐。
4. **经验到 seniority 的映射**：(a) 用第 4 节的表（推荐）/ (b) "1 年以内"也算 `new_grad` / (c) 经验字段只放进 attributes，不预打标签，全部交给子 agent。
5. **定时任务里能不能抓 BOSS**（定时任务要到切片 4 才做，这里先把规则定下来）：(a) 能，和其他来源一样属于准备级（推荐）/ (b) 不能，只在你在对话中时抓取，这样出现验证时你能当场处理。

## 6. 模块

```
src/intent_job_agent/
  boss.py        新增。解析部分是纯函数：search / detail 的 JSON 输出 → 卡片、Job；薪资文本解析。
                 调用部分：命令白名单（search / detail / status）、请求间隔、错误分类
                 （not_authenticated、rate_limited、安全拦截 → 整个 BOSS 来源停下）。命令运行器可以注入，测试时换成假的。
  pretag.py      中国城市表；学历、经验、实习字段
  tools.py       discover_jobs 加 boss 来源；check_boss（准备级，定时任务能否调用按取舍 5）
  config / boards  读取 boss.yaml（只能放在数据目录下）
  domain.py      Job.source 加 boss
  prompts.py     打标签规则补充中文写法
  mcp_server.py  说明：先跑 check_boss；登录由用户自己在终端完成，agent 不碰 Cookie；遇到验证只报告，不处理
configs/default.yaml   discovery.boss: {max_search_pages, max_details, request_delay_seconds, timeout_seconds}
docs/examples/boss.example.yaml、README、NOTICE（列出对 boss-cli 的依赖）
```

新增依赖：`kabi-boss-cli`（锁定版本）。

## 7. 顺带一提（不在本切片内）

如果你在国内主要找**校招**：字节、腾讯、阿里、百度、美团等大厂的校招岗位主要发在自家校招官网上，BOSS 上并不全。这些官网大多有公开接口，不用登录，风险比 BOSS 低，可以像 Avature 那样做成招聘板。需要的话，我单独出一份计划。

## 8. 测试（先写，全部离线）

用假的命令运行器回放合成的 JSON 输出，放在 `tests/fixtures/boss/`，标注 synthetic，公司和岗位都是虚构的。
- 搜索解析：卡片字段正确；同一岗位出现在多个搜索词或城市里，只算一次；`title_exclude` 生效；已入库的岗位（按 ID 或链接判断）不读详情。
- 详情：字段按第 4 节映射；招聘者信息和会话令牌不落库；描述里写给 AI 的指令原样存为文本，不影响任何行为。
- 薪资："20-40K·15薪"、"15-25K" 能解析；"200-300元/天"、"面议" 记为未知。
- 详情上限：超出上限的岗位不入库，计入 `details_deferred`；下一次运行时会读到它们。
- 错误：遇到 `not_authenticated`、`rate_limited` 或安全拦截时，BOSS 来源立即停止，不再发出任何请求，也不重试；已经读完详情的新岗位照常入库；结果里有给用户的一句提示；其他来源照常运行。
- 命令白名单：调用 search / detail / status 以外的命令，直接报错。
- 预打标签：北京、上海、深圳 → `location.china.*`，算有把握；学历和经验按表映射；"实习" → internship。预筛：意图为 `location.us：必须` 时，BOSS 岗位被放到一边；加上 `location.china：必须` 后，它们重新回来。
- `boss.yaml` 只能放在数据目录下，格式错误时报错。
- `check_boss` 不入库，只发一次搜索请求。
- 定时任务可用的工具集合符合取舍 5。
- 现有 246 个测试继续通过。

实现后的真实核对要你在本机完成（测试环境里没有你的登录态）：运行 `uv run boss login` → `check_boss` → 跑一次 `/fetch-jobs`，看 BOSS 的新增数，再抽查几个岗位的字段。

---

# 切片 2c 修正：导入的岗位没有原文（用户 2026-10-15 选定 1(b) 2(a)，同日确认计划；已实现）

来源：2026-10-14 合并投递清单重复岗位（切片 5b）时发现，约四分之一的待投岗位没有原文，只能按地点判断是不是同一个岗位，打标签和写求职信也缺上下文。
**用户确认本计划之前，不写实现代码。** 确认后先改 spec（§10 切片 2c 的第 18、19 条，不新增编号，避免和 5b、2h 的编号冲突），再写测试，最后写代码。只改适配层（`applypilot_bridge.py`），vendor 文件不动。

## 1. 现在的数据（只读核对 `agent.sqlite` 和 ApplyPilot 工作库的备份）

| 来源 | 存的原文 | 岗位数 | 已打标签 | 在投递清单上（待投） | 展示过 / 打过 label |
| --- | --- | --- | --- | --- | --- |
| Glassdoor | 字符串 "None" | 231 | 230 | 125 | 0 |
| LinkedIn | 字符串 "null" | 83 | 83 | 64（其中 1 个已写求职信） | 0 |
| ZipRecruiter | 字符串 "null" | 1 | 1 | 1 | 0 |
| SimplyHired | 空 | 40 | 40 | 34 | 0 |
| LinkedIn | 空 | 2 | 2 | 0 | 0 |

## 2. 原因

1. **Glassdoor "None"**：JobSpy 没给 Glassdoor 的描述（Python 的 None）。ApplyPilot 存 JobSpy 结果时用 `str(...)`，只过滤了 "nan"，于是存成字符串 "None"；投递链接 `application_url` 也一样是 "None"（253 行）。ApplyPilot 的补全原文本来就跳过 Glassdoor（`SKIP_DETAIL_SITES`），所以不会再补。适配层取原文时用 `full_description or description`，"None" 不是空，就当成原文存了。
2. **LinkedIn / ZipRecruiter "null"**：JobSpy 没给描述，补全原文的前两步（JSON-LD、页面选择器）没找到，第 3 步让 LLM 读页面，LLM 把 `"null"` 当字符串写进了原文。"null" 不是空，ApplyPilot 记为补全成功，以后不再重试。84 行都是 2026-10-13 补全的。其中 ZipRecruiter 那一个，JobSpy 其实给了原文，但适配层优先用了 "null"。
3. **SimplyHired 空**：smartextract 存下的是相对地址（`/job/…`）。ApplyPilot 自带的 `sites.yaml` 里 `base_urls` 没有 SimplyHired，补全时无法换成完整地址，打不开页面（"Cannot navigate to invalid URL"）。所以原文是空的，而且**本项目里存的岗位链接也是相对地址，打不开**：投递清单上 34 个都投不了。
4. LinkedIn 那 2 个空的：补全时断网。原文确实是空，存得没错。

投递只用岗位自己的链接，所以 "None" 的 `application_url` 只是多余字段，不影响投递。

## 3. 一定会做（新导入）

- 从 ApplyPilot 工作库读到的字段，整个值（去掉首尾空白、不分大小写）是 `None`、`null`、`nan` 的，一律当作没有：原文、投递链接、标题、地点、薪资都一样。
- 原文取第一个真正的值：补全的原文 → JobSpy 给的描述 → 空。
- 刷新未打标签、未展示岗位的原文时用同一规则（不会用 "null" 覆盖已有原文）。

## 4. 需要你选的取舍

**取舍 1：已存的 315 个占位原文（Glassdoor 231、LinkedIn 83、ZipRecruiter 1）怎么办**

- **(a) 只清掉**：下次导入时把这些占位原文改成空。标签、label、投递清单状态都不动，不重新抓。改动最小；但这些岗位以后也一直没有原文。
- **(b) 清掉，并重新补全一次（推荐）**：在 (a) 之外：
  - ApplyPilot 补全过、却没拿到原文的行（原文为空或是占位词），适配层在下次补全前把它们标回"未补全"，**每行只重试一次**（记在 ApplyPilot 工作库旁边的一张表里，和现在另记公司名的做法一样），以后新出现的这种行也一样。这次会重试 LinkedIn 86 个（84 个 "null"、2 个断网）、ZipRecruiter 1 个，以及取舍 2 选 (a) 时的 SimplyHired 40 个；Talent.com 补全失败的 32 个也会各试一次。Glassdoor 不在内（ApplyPilot 的补全不处理 Glassdoor），只能保持为空。
  - 原文为空、**还没展示过**的岗位，拿到真正的原文时换上原文；如果已经打过标签，就退回待打标签队列，在同一次 `/fetch-jobs` 里由子 agent 按原文重新打。重新打完之前，它暂时不在投递清单上，投递状态保留。spec 第 18 条"已打标签的岗位原文不再变化"加这一条例外。
  - ZipRecruiter 那一个不用等补全：它现在就有 JobSpy 给的原文，下次导入时直接换上，退回重新打标签。
  - 风险：LinkedIn 对未登录的访问有限流，重试也可能还是拿不到；拿不到的保持为空，不再试第二次。
- **(c) 不动旧数据**，只修新导入。

**取舍 2：SimplyHired 的相对链接**

- **(a) 本切片一起修（推荐）**：适配层给 ApplyPilot 的 `base_urls` 补上缺的站点，取该站点搜索页的域名（SimplyHired → `https://www.simplyhired.com`）；`base_urls` 里明确写了 null 的站点（RemoteOK、WelcomeToTheJungle）照旧不补。已存的 40 个岗位改成完整链接，岗位编号不变，标签和投递状态都不动；导入时先按同一规则把相对地址换成完整地址再查重，ApplyPilot 下次补全时把工作库里的地址改写成完整地址，也认作同一个岗位，不会重复入库。原文：取舍 1 选 (b) 时会重新补全。
- (b) 另开一个切片，这次只修原文。

## 5. 模块

```
src/intent_job_agent/
  applypilot_bridge.py   占位词判断；原文取值规则；sync 的刷新规则
                         取舍 1(b)：补全前的"只重试一次"（表 intent_agent_retried，在 ApplyPilot 工作库里）
                         取舍 2(a)：configure 补全 base_urls；sync 先把相对地址换成完整地址；已存岗位改链接
  store.py               取舍 1(b)：退回待打标签（删掉 tagged 和 backfills 里的记录，标签留到重新打时覆盖）
tests/fixtures/applypilot_missing_text.yaml   合成的 ApplyPilot 工作库行（synthetic），覆盖上面四种情况
```

## 6. 测试（先写，全部离线，合成数据）

- 新导入：JobSpy 给 None 描述的 Glassdoor 行、补全原文是 "null" 的 LinkedIn 行、"NaN" / " NULL " 等大小写和空白变体，都存成空原文；"None" 的 `application_url` 不进属性；有 JobSpy 描述而补全原文是 "null" 的行，存 JobSpy 的描述；原文里正常出现 "null" 这个词的不受影响。
- 刷新：补全原文是 "null" 时，不覆盖已有原文。
- 旧数据（按所选取舍）：(a) 占位原文清成空，标签、投递状态不变；(b) 只重试一次（第二次运行不再标回）、Glassdoor 不重试、拿到原文的已打标签未展示岗位退回待打标签队列、展示过的岗位不动、投递状态保留。
- 取舍 2(a)：没有 `base_urls` 的站点用搜索页域名补上，明确写 null 的不补；相对链接的已存岗位改成完整链接、编号不变；ApplyPilot 改写地址后再导入，不新增岗位。
- vendor 文件和 ApplyPilot 的原有测试不变；现有测试继续通过。

实现后在你本机核对：跑一次 `/fetch-jobs`，看占位原文是否清掉、重试补全拿到多少原文；SimplyHired 的链接能否打开。本切片不开始任何投递，不改投递状态。

---

# 切片 5b 计划：投递清单里同一个岗位只列一次（用户 2026-10-14 选定四个推荐项，同日确认计划；已实现）

规则见 spec 4.9 第 6 条，实现细节见 spec §10 第 71–76 条。
不开始任何投递，不改任何已有的投递状态。

## 1. 做什么，不做什么

- 做：生成清单时把同一个岗位的几条合成一行；改状态时整个岗位一起改；开始投递前再查一次，批次里不会有同一个岗位的两条。
- 不做：
  - 每日推荐（模块②）里的重复：同一个岗位的两条仍可能同时出现在 /today，留作后续。
  - 导入时合并、迁移已有的 label 和求职信。
  - 公司名别名（Amazon 和 Amazon Science 认不出来）。
  - 按最终投递链接判断（LinkedIn 等来源打开之前没有这个链接）。

## 2. 实际会怎么用

```
你：/apply-list
agent：待投 929 个，其中新的 0 个。……
       3 Machine Learning Engineer, Radar — Stripe · us.bay_area, us.seattle, us.new_york · https://www.linkedin.com/jobs/view/4476982356
            另有 2 条：https://…/4476984167、https://…/4476969830
            role.ml_engineering 强烈偏好；……
你：3 已投
agent：已更新（同一个岗位的 3 条一起改）。还剩 928 个待投。
```

`/apply` 生成的新批次里，每个岗位只出现一次。2026-10-14 的旧批次 batch-8852d95dd9fc 第二步里有重复（Handshake、RK Infotech、Intuit），`start_applying` 会拒绝开始，要重新生成批次（什么时候重新生成由你决定）。

## 3. 样例（合成，写进 `tests/fixtures/duplicates.yaml`）

公司名、文本都是编的，形状照 2026-10-14 那一批的真实情况。

| # | 情况 | 结果 |
| --- | --- | --- |
| 1 | Acme Pay 的同一个岗位，LinkedIn 上旧金山、西雅图、纽约各一条，正文相同（照 Stripe） | 一行；地点 3 个；另有 2 条链接 |
| 2 | Beta 的同一个岗位在 Indeed、LinkedIn、ZipRecruiter 各一条：LinkedIn 正文被截断，ZipRecruiter 多一个标题行（照 Handshake） | 一行 |
| 3 | 公司名 "Gamma Infotech LLC" 和 "Gamma Infotech"（照 RK Infotech） | 一行 |
| 4 | Delta 官网 Workday 上 R100001 和 R100002，正文只差年限和薪资（照 Adobe） | 两行 |
| 5 | 再加一条 LinkedIn 上的 Delta，和两个编号都像 | 只并进其中一个；仍是两行 |
| 6 | 同一个编号在 Workday 上有两个链接（地点路径不同，或末尾多了 `-1`） | 一行 |
| 7 | 公司名为空的两条，标题、正文一样（照 Talent.com） | 两行 |
| 8 | 同公司、同标题，正文不同（两个团队） | 两行 |
| 9 | 同公司、同正文，标题不同（"ML Engineer" 和 "ML Engineer II"） | 两行 |
| 10 | 其中一条没有正文（Glassdoor 存的是 "None"）：地点相同 / 地点不同 | 合并 / 不合并（用户 2026-10-14 实现时补充：真实清单里约四分之一的岗位没有正文） |
| 10b | Acme 再加一条没有正文的 Glassdoor 纽约岗位 | 通过纽约那条并进 Acme 那一行 |
| 11 | Acme 纽约那条标了 reject，其他没标 | 整个岗位不进 |
| 12 | Acme 纽约那条标了 reject，旧金山那条标了 want | 进，"你标了想投" |
| 13 | Beta 的 Indeed 那条投递没成功（failed） | Beta 不再列出；把 Beta 任意一条改回待投，三条一起回来 |
| 14 | 把 Acme 的一行标成已投 | 三条都是已投 |
| 15 | 排第二的那条已经有求职信 | 这一行和批次用那条的 job_id，不算缺求职信 |
| 16 | 昨天进过清单的岗位今天多了一条新来源 | 这一行不标"新" |
| 17 | 用清单生成批次 | 每个岗位一次；先检查 / 停在提交前 / 自动提交的划分不变 |
| 18 | 旧批次的同一步里有同一个岗位的两条 | `start_applying` 拒绝，要求重新生成批次 |
| 19 | 旧批次第一步里一条没投成，第二步里有它的另一条 | `start_applying` 拒绝，要求重新生成批次 |

## 4. 模块

```
src/intent_job_agent/
  duplicates.py   新增，纯逻辑：名称规范化、官网编号、正文重合度、same_posting(a, b)、
                  group(listings)（按顺序加入第一个"和每一条都相同"的组）
  apply_list.py   候选分组成岗位：reject / want 规则，选用哪一条去投（有求职信的优先），
                  每行的地点和其他链接
  tools.py        build_apply_list：一行一个岗位，加 also 列，每一条都记成进过清单；
                  mark_applications：整个岗位一起改；start_applying：开始前查重复
  config.py, configs/default.yaml   apply_list.duplicate_shingle_words: 5, duplicate_overlap: 0.9
.claude/skills/apply-list/SKILL.md   每行下面列出"另有 N 条"；改状态会连带同一岗位的其他条
```

## 5. 测试（先写，离线，合成数据）

- 样例 1–19。
- 判断是确定性的：同样的输入换个顺序，合成的结果（哪些条在一起）不变，只有排序跟着清单走。
- 已有的测试继续通过。
- 另外用真实数据只读核对一次（不写进测试）：2026-10-14 那批的 Stripe、Handshake、RK Infotech、Intuit 各合成一行，Adobe R171718 / R171719 仍是两行。

# 切片 5c 计划：每日推荐里同一个岗位只出一次，"同一个岗位"改为同时看链接、编号、城市和国家（用户 2026-10-15 选定；审查后修订，同日确认计划）

规则见 spec 4.6、4.8、4.9 第 6 条，实现细节见 spec §10 第 77–85 条。起因和真实数据的核对结果见 4.9 第 6 条"为什么这样定"。
不改任何已有的投递状态、label 和已经存下的每日选择；不开始任何投递。

你选的：每日推荐当天和以后都只出一次（1A）；label 只算展示的那条（2A）；列表里不附其他条（3B）；misread 的修正连带其他条，地点除外（4B）；正文里写的编号不同、正文只差数字时分开（1A）；公司名为空时用 company 标签（2A）；reject 的原因只有地点时只挡这一条和同城市的条（A）。

## 1. 做什么，不做什么

- 做：
  - `duplicates.py` 换成新规则：投递链接规范化，同一个系统的编号不同就分开，正文里的编号和"只差数字"，城市和国家的条件，公司名为空时用 company 标签。
  - 每日推荐：当天同一个岗位最多一条；一个岗位展示过一条，其他几条以后不再出现。分组用所有已打标签的条，所以中间那条下架或被排除了也挡得住。
  - misread 的修正连带同一个岗位已打标签的其他几条，location 除外；弹窗里列出会连带的条。
  - `get_fetch_status` 的 `tagged_open` 数岗位。
  - 配置 `apply_list.duplicate_*` 移到新的 `duplicates` 一节。
  - spec 4.8 和 CLAUDE.md"岗位内容是不可信输入"一条补一句：确定性代码也用岗位内容判断是不是同一个岗位，只作比较。
- 不做（以后再说）：
  - 投递时记下"开始申请的第一个页面"。投递之前没有这个页面，只能在投递时作为第二道检查。
  - 抓取时解开短链接（prng.co、appcast，要联网）；读 LinkedIn 的站外投递链接（现在用的 JobSpy 1.3.0 不读）。这两样能让更多跨城市的岗位有链接可比。
  - 正文里写的编号相同、或者正文里的编号就是另一条链接里的编号，当作"同一个职位"的证据（今天的数据里结果不变）。
  - `application_url` 在导入之后才补上的，不会更新到岗位库（快照里只有 Talent.com 受影响，它的链接本来就不能用）。
  - 国家标签的写法统一（`in` / `india` 等，属于词表的问题）。
  - 公司名别名（Amazon 和 Amazon Science），以及同一个职位在两个前端（Cisco 官网和它的 Workday）认不出来。
  - Glassdoor 的正文和链接存成字符串 "None" 的导入问题（另一个会话在修）。

## 2. 实际会怎么用

```
你：/today
agent：1 [推荐] Machine Learning Engineer, Risk — Acme Pay · us.seattle · hybrid · ...
       ……（10 个里没有 Acme Pay 的另外两条；以前展示过其中一条的话，三条都不会出现）
你：1? 是 onsite 不是 hybrid
agent：1 现在的标签：……。同一个岗位还有 2 条会一起改（LinkedIn 旧金山、Indeed 纽约；地点不动）。
       （弹窗：correct_tags(1, remove=[work_mode.hybrid], add=[work_mode.onsite], also=[那 2 条])）
```

`/apply-list` 里：Turnitin 三个城市的三个编号变成三行；PayPal 圣何塞那条 LinkedIn 并进圣何塞那个编号的一行；Trustly 的 Talent.com 和 LinkedIn 两条是一个岗位，你 reject 过，整个岗位不进清单。

## 3. 样例（合成，加进 `tests/fixtures/duplicates.yaml`）

公司名、文本都是编的；新样例的链接用招聘系统真实的链接格式（只是字符串，不会打开）。5b 的样例 1–19 结果不变。

| # | 情况 | 结果 |
| --- | --- | --- |
| 20 | 招聘网站上两个城市的同一个职位：Indeed 的链接是跳转包装加追踪参数，ZipRecruiter 的多了 `lever-source`，解开后是同一个 Lever 编号（照 Zoox、Meta） | 一个岗位 |
| 21 | Indeed 上三个城市，正文一样，SmartRecruiters 编号各不相同（照 Turnitin） | 三个岗位 |
| 22 | 官网 Workday 上芝加哥 R1、圣何塞 R2，正文一样；LinkedIn 圣何塞一条和 LinkedIn 芝加哥一条，都没有链接（照 PayPal） | 圣何塞的并进 R2，芝加哥的并进 R1；R1、R2 分开 |
| 23 | 官网 Workday 一条"3 Locations"（带一个别的城市的标签），LinkedIn 西雅图一条，正文一样（照 Cisco） | 一个岗位 |
| 23b | 官网 Workday 一条写明芝加哥，LinkedIn 西雅图一条，正文一样 | 两个岗位 |
| 24 | LinkedIn 两个城市，正文一样，各写一个 Job ID（照 Amazon）；只有一份写了编号 | 两个岗位；一个岗位 |
| 25 | LinkedIn 两个城市，正文只差各自城市的薪资数字（照 TikTok）；只差截止日期 | 两个岗位；一个岗位 |
| 26 | 招聘网站上美国一条、加拿大一条，正文一样，没有链接 | 两个岗位 |
| 27 | Workday 同一个编号，美国、加拿大两个地点路径；再加一条 LinkedIn 美国的同正文、没有链接 | 三条一个岗位 |
| 28 | 手动导入的两个城市的同名岗位，两个 Google 职位编号（照 Google）；再加一条 LinkedIn 另一个城市的同正文 | Google 两个编号分开；LinkedIn 那条不按正文并进写明城市的官网条 |
| 29 | Talent.com 两条（公司名为空）各有一个相同的 `company.*` 标签、同一个城市、正文一样；第三条公司名为空，也没有 company 标签；第四条有两个 company 标签 | 前两条一个岗位；后两条各自单独 |
| 30 | Talent.com 一条（公司名为空，company 标签对得上，location 标签 us.bay_area 和 us.new_york）和 LinkedIn 一条（没有正文，原文地点写法不同，location 标签只有 us.bay_area）（照 Trustly） | 一个岗位；reject 那条挡住整个岗位进清单 |
| 31 | 链接：不同的 grnh.se 短链接 / 相同的 grnh.se 短链接 / appcast 追踪链接 / 招聘首页 / 字符串 `None` | 不算证据 / 同一个职位 / 不算 / 不算 / 不算 |
| 32 | 今天的候选里有 Acme 三个城市的三条（排第 1、3、9） | 只出第 1 那条，后面的依次补上 |
| 33 | 昨天展示过 Acme 纽约那条（现在已下架）；今天候选里有旧金山、西雅图 | 两条都不出 |
| 34 | 推荐位里有某个岗位的一条，探索位的候选里有它的另一条 | 探索位不选它 |
| 35 | 展示过的那条标了 want，其他几条没 label；另有一个 5c 之前两条都标了 want 的岗位 | 前者回放和一致率只算一条，投递清单按 want 进整个岗位；后者两条照旧各算一次 |
| 36 | 展示过的那条（LinkedIn 西雅图）标了 misread，修正：去掉 work_mode.hybrid、加上 work_mode.onsite，去掉 location.us.seattle、加上 location.us.portland。同一个岗位还有：Indeed 西雅图（已打标签）、ZipRecruiter 纽约（没打标签）、以前展示过并标了 want 的一条 | 本条都改；Indeed 和标了 want 的那条只改 work_mode；ZipRecruiter 不改；未处理的分析标为需要刷新；`also` 和算出的不一致时拒绝 |
| 37 | `get_fetch_status` | `tagged_open` 是岗位数，不含被硬约束排除的 |
| 38 | 同样的候选再选一次 / 不含"和两个编号都像"的样例时换输入顺序分组 | 结果一样 / 分组不变 |
| 39 | 已经存下的今天的选择里有同一个岗位的两条（5c 之前选的） | 不重算 |
| 40 | 西雅图那条 reject，原因 key 只有 location.us.seattle；同一个岗位还有旧金山一条、西雅图另一个网站的一条；另一个岗位的 reject 原因是 location 加 work_mode / 没写原因 | 旧金山那条照常进清单，西雅图两条都挡；另一个岗位整个挡住 |

## 4. 模块

```
src/intent_job_agent/
  duplicates.py   apply_key(job)：规范化的投递链接（系统 + 编号 / 其他链接 / 没有）；城市和国家；正文里的编号、
                  只差数字；company 标签；compare / same_posting / group 按 §10 第 81 条（正文特征只给同名的条算）
  selection.py    select_daily(intent, candidates, settings, shown=(), others=())：先分组，组里有展示过的跳过，
                  每组留排名最前的候选，再选 7+3
  tools.py        select_today、get_fetch_status：所有已打标签的条，修正后的标签；show_job：same_posting；
                  correct_tags：also 参数，连带同一个岗位的其他条
  engine.py       correct_tags 的连带：缺的标签跳过，location 不动；改到已打 want / reject 的条时让分析变旧
  store.py        tag_revision 也数"连带改到已打 label 的条"的修正
  apply_list.py   分组时用修正后的标签（地点和 company 标签都从标签来）；原因只有地点的 reject 只挡同城市的条
  config.py, configs/default.yaml   duplicates: {shingle_words: 5, overlap: 0.9}（从 apply_list 移过来）
.claude/skills/today/SKILL.md   第 5 步：先用 show_job 列出会连带的条，再带 also 调 correct_tags
.claude/skills/apply-list/SKILL.md   第 3 步（标签读错了）同样带 also（实现后审查时补上）
CLAUDE.md       "岗位内容是不可信输入"一条补一句（和 spec 4.8 一致）
```

## 5. 测试（先写，离线，合成数据）

- 样例 20–40；5b 的样例 1–19 结果不变。`test_slice5b.py` 里有几处按新接口改写，断言不变：`requisition` 换成 `apply_key`，配置路径换成 `duplicates`，分组测试里的每一条带上 fixture 里的 `places` 作为 location 标签。
- 链接规范化单独测：跳转包装、追踪参数、Workday 的几种写法（末尾的 `-1` 去掉、`_2026-1225` 整个保留、地点路径、`myworkdaysite.com`）、Greenhouse 两种写法、短链接、招聘首页、`None` / `null`。
- 已有的测试继续通过。
- 实现后用真实数据的只读副本核对一次（不写进测试）：Stripe 三个城市合成一个；Turnitin、Ruchman、Google、Lenovo、PayPal、TikTok、Amazon 不再合错；batch-8852d95dd9fc 的 30 条是 24 个岗位；Trustly 两条合成一个；用现在的意图重选一次今天的 10 个，看看会跳过哪些。

## 6. 你需要知道的

- 5b 还没合进 main。这个分支是在 5b 上做的（已经把 5b 合进来），合进 main 时会连 5b 一起。
- 新规则会改变投递清单：有几处岗位分开（多出几行），也有几处合并（例如 Trustly）。已有的状态不改。
- 已经生成的批次 batch-335e662b7664 用新代码不能开始：Handshake（第 8、11 个）和 RK Infotech（第 9、10 个）各是同一个岗位，Handshake 的 Indeed 那条已经没投成，按 §10 第 75 条 `start_applying` 会拒绝。合并后要重新生成批次，那时 Trustly 也不会再进。
- 正在运行的工具服务要重启才会用新代码，配置也要用这个分支的 `configs/default.yaml`（新的 `duplicates` 一节）。
