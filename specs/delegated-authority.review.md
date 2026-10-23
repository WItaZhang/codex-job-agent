# 全权委托切片交审记录（决定 25）

| 字段 | 结果 |
| --- | --- |
| 切片 | delegated-authority；week_stats 切片暂停 |
| 产品行为 | 默认 review_first=0；非名单普通/only 重试自主推进，保留显式只填；保护公司和未知公司不进入自动提交路线，包含邮件 |
| 状态 | 实现完成，开发提交后交协调者独立审阅 |
| 提交证据 | 分支 WItaZhang/delegated-authority；英文提交包含 decision 25，提交号见交付消息 |
| 验证结果 | 见下方验证记录；全部离线合成资料/fake runner，没有真实投递 |
| 阻塞 | 无工程阻塞；真实登录/验证码/事实缺失仍是能力阻塞，不代编事实 |
| 下一步及估时 | 协调者独立审阅，建议预留 30–60 分钟；通过后由协调者安排合并/推送，本开发会话不整合 |

## 审阅问题处理

- A2：启动前重查名单；执行中名单变化只阻止对应岗位，普通岗位保持 pending，恢复保留原 held、次数及原因并清掉 resuming，继续后续岗位。名单消失时余下自动路线均不启动。
- A3：恢复原 INSTRUCTIONS 的 Avature/check_board、轮询、回填、排序/预算、一致率及密码/SSN/卡号/key 不进聊天等说明。A 校准逻辑未改。
- A4：复用每次工具调用已有分组，逐岗位 guard 只读当前名单和对应岗位/标签，不再全库打分。5000 合成岗位、30 次假投递：prepare 0.349 秒，start 0.215 秒，分组各 1 次；此耗时不含真实浏览器或模型。
- A5/B3：原公司名和现有 company 标签都检查；空原名即停，通用未知标记及名称等于来源/board 时保守停止。法人后缀规范化、双向保守包含匹配，不推断母子公司。允许误停，不声称能识别任意伪装公司名。
- A6：名单只有 Co/Inc./LLC/标点时报配置错误，不再静默全停。
- B1：读取已有批次 careful/protected 公司名，新批次/恢复仍保留旧保护；当前名单删除不能静默解锁。只有实际用户命令可增删名单，没有新增代理改名单工具或大权限框架。
- A1/B2：真实 label 必须来自实际用户消息，新增可选非敏感消息引用并明确未认证；参数不认证人。mark_applications 默认 user 保留旧客户调用兼容，宿主只能依真实用户报告使用；协调者显式 actor=delegated_coordinator 只能恢复确定可重试的 pending，不伪报已投/放弃，不写 via=user 尝试。收到回执保留 submitted_by_user 历史统计代码，另记 actor=unknown 的观察证据，不据此断言操作者。
- A7：仅七项 MCP 工具 prompt 改已核实的 approve；顶层 approval_policy/approvals_reviewer 保留。核对过 openai-docs 和本地实际 CLI schema；没有修改全局策略。
- B4（既存限制）：保留 dry-run 禁止 Submit 提示及 maybe_submitted 事后停批，未改造浏览器引擎。运行中的 agent 与下一次校验之间仍有时间窗口；不存在所有浏览器调用的最终提交硬拦。

## 旧规则审计

- CLAUDE.md / AGENTS 软链：开发阶段、claim、交接、新需求与两次纠正由协调者自主安排；不要求用户开开发会话或 /clear；真实 CODEX_THREAD_ID 加 codex: 命名空间，不覆盖其他会话锁。claim 使用独占创建，release 拒绝解除其他会话锁。
- spec / form-fill：原文改写并追加同名决定 25/36，旧决定只加受取代标记；§8 门槛、§9 stalled/interrupted 分类、起草答案逐条确认等生效措辞已收尾。§9 数量/成功率/人工时间/连续两周标准保留，不宣称达标；§10 第 96 条统计兼容未改。
- README / 当前计划 / 历史计划：当前说明改为委托；旧已完成计划加历史标记，不重写当时确认事实。
- 技能只改 .claude/skills 源；.agents/skills 与 .codex/agents 的软链保留。投递 runner、填表 runner、子 agent 的工具白名单未扩大；隐私 deny 保留。
- 两宿主仅移除项目提交级工具逐次询问及 MCP requiresUserInteraction；实际宿主强制审批仍遵守。Codex 原生子 agent 白名单仍属行为边界，并非宿主硬隔离。
- 默认 review_first=0 同时取消原前五份全失败阻止 rest 的第一步关卡，已记入决定；未新增熔断、定时投递、自动放弃、别名库、账本、互斥、备份或 CI。

## 验证记录

- 测试先行：第一组 15 失败/4 通过；补充边界组 7 失败/20 通过；通用占位名补测 2 失败/41 通过，随后修复。
- 定向新回归 49 通过；恢复/报告等联合回归 198 通过（之后新增的性能及缺名单恢复也通过）。
- 四项验证全部通过：`uv sync --locked`；`uv run ruff check src tests`；`uv run ruff format --check src tests`（79 文件）；`uv run python -m pytest -q`（1545 passed，60.16 秒）。
- skill-creator quick_validate：apply、apply-list、today、fetch-jobs、merge、cover-letter 六项通过。merge 旧 argument-hint 移入正文以兼容校验器。
- git diff --check 通过。独立 worktree 持有本会话锁，无共享 owner 编辑、无推送/合并、无真实申请网站访问或私人资料读取/搬运。

## 仍需诚实说明的边界

有任意文件/浏览器权限的宿主 agent 可越过项目行为约定；本切片没有提供对它的防篡改或身份认证。名单保护是保守路由、历史留底、执行时检查与受限填表工具组合，不能宣称网站提交绝对不可发生。工程验证完成不等于 B 真实业务验收完成。
