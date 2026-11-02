# 切片计划：门槛清单与在场重跑（2026-11-02）

- 规格：`spec.md` 4.10 第 10 条，5.1，§9，§10 第 116–121 条。决定：`spec.decisions.md` 决定 35。
- 起点：main a8fc3db。分支 `worktree-agent-afc2b02d7bc57f6c4`。
- 为什么做：用户 2026-11-02 原话见决定 35。要它在用户不在时一直投，门槛岗位记下来，有空时一个一个处理、重新记结果。
- 冻结：只改门槛交给用户的流程（4.10 第 10 条，已实现），能让 B 达标（门槛岗位不被埋没），不碰冻结的来源、校准、定时任务、填表 2b/2c。不启动真实投递、填表或抓取。

## 0. 采访结论（按推荐项，决定 25 授权；见决定 35 第 1–8 条）

| 问题 | 定下的 | 没选 |
| --- | --- | --- |
| 怎么知道用户不在 | 开始时显式 `unattended=true`，默认照旧 | 按时间或空闲猜；一律关标签页 |
| 无人值守遇到门槛 | 记进清单，代码按编号关标签页 | agent 自己关；留着 |
| 清单范围 | 四种门槛，按种类筛 | 只收验证码 |
| 在场时怎么等 | agent 停下退出，代码看着标签页等你说"好了"，再起 agent 回原标签页 | agent 不退出自己反复看；代码猜门槛消失 |
| 时限 | 10 分钟/次，5 秒看一次，同一岗位一次重跑最多等 3 次 | 不限时 |
| 回答 | 新准备级 `gate_reply`：done / skip / decline / submitted | 沿用 `mark_applications` |
| 计数 | 当场处理掉的不算；到时限算一次没解决（第三次失败）；跳过不算 | 一律算；到时限也不算 |

## 1. 样例（synthetic，测试见 `tests/test_gate_list.py`）

- L1 无人值守的一步：8 Beta 自动提交卡在验证码（标签页 N8）。记成 held，清单一行有门槛、地址、NOTE、时间；代码关掉 N8；清单的标签页编号和 generation 为空、地址留着；后面的岗位照做。
- L2 在场的一步（不传 `unattended`）：同样卡在验证码，标签页留着，什么都不关（和以前一样）。
- L3 无人值守时 `interrupted`（工具服务中途停了）的不关；关标签页失败（实例换了、超时）清单照样去掉编号。
- L4 `apply_status`：`held` 有 `held_at`、`tab_open`，`held_count` 按门槛计数；重启后照样有。
- L5 `prepare_resume(live=true, gates=["captcha"])`：只取验证码的，按停下先后排，摘要开头"在场重跑 2 个（一个一个来，遇到门槛停在那一页等你，每次最多等 10 分钟）："；不给岗位时默认全部四种；`interrupted` 不点名不进；一个都没有报错；`live` 和 `unattended` 同时为真报错；摘要里的 `live` 和开始时不一致就拒绝。
- L6 在场重跑：标签页已关的老岗位从岗位地址重开（不接管）；agent 卡在验证码 → `needs_you`、通知"投递：现在需要你 / 验证码：8 Beta。处理完说“好了”"；用户说"好了" → 另起 agent 回到新标签页（`resume_tab` 是那个编号），交上了 → applied，清单去掉这一行。
- L7 等的时候页面变成"Thank you for applying" → applied、`submitted_by_user`、`observed_submission`（unknown），不再起 agent。
- L8 到时限：关掉标签页，次数 1 → 2，岗位 held；原来是 2 的 → failed、"第三次卡在门槛（在场重跑时没处理完）"、算一次允许的验证码失败。
- L9 用户说"跳过"：关掉标签页，次数不变，留在清单；用户关掉标签页：同样，不另关。
- L10 用户说"不处理了"：用户的 failed（原因 captcha），关标签页；说"已投"：用户的 applied，不关。
- L11 说"停下"：马上结束等待，岗位 held、次数不变、标签页编号留着；`stop_applying` 的消息写明它留在清单上。
- L12 名单公司（careful）在场重跑：填表模块填，卡在门槛等你；"好了"之后由填表模块从岗位地址重填、停在提交前（waiting），从不 submit。
- L13 "好了"之后又卡在门槛：再等一次；等满 3 次后再卡住按到时限算。
- L14 `gate_reply` 没有在等的岗位、或 `job_id` 不是正在等的那个：报错；`decline`/`submitted` 写决定日志 `application_status`（actor user）。
- L15 一次重跑两个岗位：第一个等你时第二个不开始；第一个跳过后第二个才开始。

## 2. 步骤（测试先行，每步通过后提交）

1. 规格、决定、本计划（一个提交）。
2. 测试 `tests/test_gate_list.py`（L1–L15），假的部分沿用 `FakeRunner`、`FormFillFake`（加 `close_tab`）和假通知器；时钟和等待换成假的（`Toolbox.clock`、`Toolbox.sleep`）。
3. 实现：
   - 配置 `apply.gate_wait`（`config.py`、`configs/default.yaml`）。
   - `store.update_hold`（只改清单一行，不写尝试记录）。
   - `readback.close_tab`（只按编号，限时）、`FormFillRunner.close_tab`。
   - `notify.now`。
   - `tools.py`：`start_applying`/`resume_applying` 的 `unattended`、`live`；`prepare_resume` 的 `job_ids=None`、`gates`、`live`；等你的循环；`gate_reply`；`apply_status` 的 `needs_you`、`held_count`、`held_at`、`tab_open`；`stop_applying` 的等待消息。
4. 说明：`mcp_server.py` 的说明、`/apply` skill、`docs/usage.md`。`gate_reply` 是准备级，和其他准备级工具一样不进 `.claude/settings.json`、`.codex/config.toml` 的提交级清单；`test_workspace` 的工具数加一。
5. 验证：CLAUDE.md 的四条命令。

## 3. 不做（留给以后）

- 代码自己判断门槛已经消失（需要按网站写规则）。
- 名单公司在原标签页接着填（填表模块第二刀）。
- 自动判断用户在不在。
- 改 `CLAUDE.md` 投递条款的那一句（决定 35 待你决定第 1 条）。
