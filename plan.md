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
