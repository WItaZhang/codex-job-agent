# 用户档案模型 v2

档案采用有固定字段的领域模型。简历经历、联系方式和签证信息各有自己的结构，
不再以通用 `facts` 列表保存。模型定义在 `src/applypilot_agent/profile_models.py`。

## 模板与分区

- [空白档案](../../.agents/skills/applypilot-onboard/references/profile-template.json)：用于首次建档，未知值为 null，经历列表为空。
- [完整结构示例](../../.agents/skills/applypilot-onboard/references/profile-example.json)：包含各类记录，全部是未确认的虚构内容，不能作为个人信息导入使用。
- `schema profile` 返回当前实现的 JSON Schema。

| 分区 | 主要内容 |
| --- | --- |
| `personal` | 姓名、邮箱、电话、所在地、个人链接 |
| `work_authorization` | 按国家记录身份/签证描述、工作许可、当前和未来是否需要担保、有效期 |
| `education` | 学校、学位、专业、起止时间、成绩、论文与其他成果 |
| `work_experience` | 雇主、职位、雇佣类型、地点、起止时间、职责、成果、技术 |
| `projects` | 项目名称、角色、时间、介绍、成果、技术、链接及所属工作经历 |
| `publications` | 标题、作者、发表渠道、年份、状态、DOI、链接、个人贡献 |
| `competitions` | 比赛、主办方、年份、奖项、排名、团队、个人贡献 |
| `skills` | 技能名称、类别、熟练程度、支持这项技能的经历记录 |
| `availability` | 最早入职时间、通知期 |
| `preferences` | 求职方向、硬约束、偏好词和回避词 |

日期保留来源精度，接受 `YYYY`、`YYYY-MM`、`YYYY-MM-DD`。不编造月份或日期。
论文状态区分草稿、已投稿、审稿中、已接收、已发表、已撤回；不能把投稿当成发表。
签证/工作许可中的 null 表示未知，false 表示已记录的否定，两者不同。
填入签证名称不会自动推导工作资格或担保需求。

偏好当前仍沿用明确的词项和约束规则，不是完整的薪资区间、条件偏好推理系统。
硬约束只支持岗位字段上的 equals/contains/excludes/one_of；缺字段保持 unknown。
投递授权在配置的 `policy` 中独立管理，档案字段不能授予提交权限。

## 一份当前档案，按记录修改

SQLite 中的 `profile/active` 是唯一运行时档案。每段经历、每篇论文都有稳定的 `id`。
例如论文从审稿中变为接收，更新 `publications` 中同一 ID 的 `status`，不新增一条
“现在已接收”的事实。删除或清空字段直接改变当前值，不附加否定旧值的指令。

每次保存生成不可变的版本快照供历史核对；这些快照不会合并进当前档案。
求职运行时读取 active；已提交材料的审计读取提交时冻结的版本。
修改档案会改变 profile_hash，使旧评估和申请包失效，未完成申请的旧审批被清除。

可读文件通过显式导出获得：

```text
uv run applypilot-agent --config configs/agent.yaml profile-export data/local/profile.json
```

输出中包含该版本的 `profile_hash`。编辑文件后，用此值保护保存，避免覆盖期间发生的其他更新：

```text
uv run applypilot-agent --config configs/agent.yaml profile-set data/local/profile.json --expected-hash CURRENT_PROFILE_HASH
uv run applypilot-agent --config configs/agent.yaml profile-export data/local/profile.json
```

文件是草稿/快照，成功执行 `profile-set` 后才成为有效档案。不会在文件和数据库之间
隐式双向同步。首次建档可不传 expected-hash；后续修改应先导出并携带版本。
技能负责定位原记录、保留无关字段并更新来源，不能拿旧导出文件覆盖当前状态。

## 来源、确认与引用

每个背景记录包含 `evidence`：`source`、`confirmed`、`scope_job_ids`。
记录中不同字段可以在 `field_evidence` 中覆盖来源、确认状态或适用岗位。
例如姓名已确认而邮箱待确认，应只允许姓名用于表单；不能因为同属一个 personal
记录就把邮箱一起确认。来源应包含可定位的文件/页码/段落或用户陈述。

`profile_evidence.py` 从当前结构即时生成只读证据视图，不存第二份事实库。
`context JOB_ID` 中的 `profile_evidence` 仅包含对该岗位已确认且适用的引用：

- `contact.email` 引用一个字段。
- `project-data-platform` 引用完整项目，要求其所有有值的事实字段已确认且同时适用。
- `project-data-platform.summary` 只引用项目介绍，不暗含其他字段的确认。

旧命令参数 `--fact`、申请包的 `fact_ids` 和评估的 `evidence_fact_ids` 为兼容保留名称，
其值现在指向上述记录/字段。列表顺序改变不会改变记录 ID。技能与项目间的外键只是
关联，不自动增加证据或确认状态。引用仍只证明来源关系，不能保证任意改写都被来源支持。

简洁材料生成器按分区排版，只输出明确选中的已确认记录/字段；联系方式也需要选择。
未选中的个人信息、签证和其他经历不会自动进入简历。正式定制简历仍需检查版式与内容。

## 旧数据与实验

旧 `facts` 档案不会自动猜测转换：加载会报出明确的 v2 转换提示，原数据库内容不变。
迁移时需依据原来源整理到各类记录，对无法判断的字段保持未知，并保留原材料待核实。
旧投递审计快照仍支持只读导出，不能作为新的活动档案写入。

本次修改没有替用户建立真实个人档案，也没有导入旧 ApplyPilot 的私人数据。
之前的 profile-memory/LangMem 对比使用独立的简化实验模型，其成绩不代表本模型的建档质量。
