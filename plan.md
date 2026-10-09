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

# 切片 2b 计划：从公开招聘板抓岗位（待用户确认）

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
