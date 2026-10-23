# 全权委托切片交审记录（决定 25）

| 字段 | 结果 |
| --- | --- |
| 切片 | delegated-authority；week_stats 切片暂停 |
| 产品行为 | 默认 review_first=0；非名单普通/only 重试自主推进，保留显式只填；保护公司和未知公司不进入自动提交路线，包含邮件 |
| 状态 | 基础切片及 F1/F2/F3 修正已获甲方最终验收，修正提交交整合 |
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
- B1 / F1–F3：留底改为首次及变化时成功读取的完整 careful_companies 快照，追加现有 decisions；不再扫描批次推导名单，不把未知名或匹配岗位名永久保护。配置减少不静默解锁；仅真实用户明确解除请求可在配置删除对应项后通过 release_careful_companies 记录解除，记录实际请求及 decision_id。新批次可按解除后的有效名单运行，旧 careful 批次保持只填；再次加入恢复保护。线程最后检查使用同一有效名单来源。
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
- 上一轮定向新回归 49 通过；恢复/报告等联合回归 198 通过（之后新增的性能及缺名单恢复也通过）。
- 上一轮提交的四项验证全部通过：`uv sync --locked`；`uv run ruff check src tests`；`uv run ruff format --check src tests`（79 文件）；`uv run python -m pytest -q`（1545 passed，60.16 秒）。
- skill-creator quick_validate：apply、apply-list、today、fetch-jobs、merge、cover-letter 六项通过。merge 旧 argument-hint 移入正文以兼容校验器。
- git diff --check 通过。独立 worktree 持有本会话锁，无共享 owner 编辑、无推送/合并、无真实申请网站访问或私人资料读取/搬运。

## 仍需诚实说明的边界

有任意文件/浏览器权限的宿主 agent 可越过项目行为约定；本切片没有提供对它的防篡改或身份认证。名单保护是保守路由、历史留底、执行时检查与受限填表工具组合，不能宣称网站提交绝对不可发生。工程验证完成不等于 B 真实业务验收完成。

## F1/F2/F3 修正交审（未提交）

输入输出回归：

| 输入 / 变化 | 预期结果 |
| --- | --- |
| 首次读取 [Stripe, Acme]，尚无批次；配置减少为 [Stripe] | Acme 后续新批次仍 careful，线程 guard 同样阻止 |
| 名单 [Stripe]；空原公司名配 company.acme，或 Stripe Research 保守命中 | 这些岗位只填；Acme Corp 和 Research 不会因前者留底而永久保护 |
| Stripe 已留底；用户明确要求解除并将配置改为 [Beta]；调用 release_careful_companies([Stripe], 实际请求, actor=user) | 返回 released、有效名单、decision_id；新批次 Stripe 可 submit，旧 careful 批次不升级 |
| 解除后配置重新加入 Stripe | 观察到重新加入即恢复保护 |
| 线程执行中观察到新增 Beta 后配置又删除它 | Beta 不启动自动路线，后续 Gamma 继续 |
| 代理自主解除、空请求、配置仍有待解除项、非留底岗位名 | 拒绝；source/actor 不是身份认证，不能伪造真实用户请求 |

四条先失败回归复现了 F1/F2/F3；随后补齐线程留底、精确快照/去重/重开数据库及定时任务不暴露解除入口。新观察用独立数据库连接，避免后台线程把快照写入前台工具事务。不新增表、不扩 L2/L3、不修改宿主配置或子 agent 工具白名单。

首次部署只能留底实际观察到的配置，无法凭旧批次重建从未记录的历史名单；旧 careful 路线继续保守，不能据此推断名单成员。

本轮补测并发旧解除晚于重新加入落库的情况：下一次观察以当前实际配置为准恢复保护，且再次删除配置仍保留该重新观察到的保护。已通过 57 项修复定向回归；恢复/工具联合定向 181 项通过。

本轮最终四项验证：uv sync --locked、ruff check、ruff format --check 全部通过；全套 pytest **1553 passed，60.62 秒**。apply skill 的 quick_validate 及 git diff --check 通过。以上验证针对 28da7bf 后的修正；甲方已最终验收，授权提交，未推送、未合并。

## 独立复核与最终验收

甲方已验收 `/tmp/delegated-authority-retention-review.md` 的独立只读复核：F1/F2/F3 阻塞全部解除，线程最后检查的 L1 同时解决，独立合成复现 5 项全部通过。复核未重跑全套；本开发检出的最终全套结果为 **1553 passed（60.62 秒）**，甲方已确认通过。

N1 已在 spec §5.1 和 tools 模块说明读名单可追加保护审计快照，不修改意图、label、投递状态；此次收尾仅补说明，没有新增行为。N2（数据库写锁争用时安全失败，可能使整步停止）列为后续事项，本切片不追加实现。N3（未被观察到即删除的名单项无法留底）是已接受边界，不做文件监控。

按最终授权提交修正后确认工作区干净，释放开发检出锁；不合并、不推送、不删除 worktree，由协调者完成整合。新提交号由最终交付消息提供。
