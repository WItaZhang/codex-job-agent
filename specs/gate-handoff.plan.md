# 切片计划：门槛交给用户（第一刀，2026-10-20）

> 本文件保留当时的切片计划与审阅记录，不作为当前审批流程。当前授权按 spec §5.2 / 决定 25；历史“等用户确认、弹窗、前 5 份抽查”不再是生效门槛。尚未实现的冻结功能仍不启动。


- 规格：`spec.md` 4.10 第 1、2、5、6、10 条，§9，§10 第 57、58、68、89、91、95–106 条；`specs/form-fill.md` 3.1、5.5、5.6、第 8、9 节。
- 决定：`spec.decisions.md` 决定 22（采访 14 题）、23（样例阶段 8 题）；`specs/form-fill.decisions.md` 决定 34。
- 样例：`specs/gate-handoff.samples.md`（G1–G18，synthetic）。
- 分支 `claude/infallible-dewdney-35a1ba`，从 main 4356602 出发。
- **为什么做**：用户原话："我想遇到要登陆或者要验证码的就弹窗让我做，我说做了之后重新继续这些job的apply"；"能不要我做的就不要停下来等我"。10-20 进度会话核对：32 份失败里 10 份是登录、验证码和单点登录。
- **冻结**：能让 B 达标（§9：门槛接着做成了，提交数和成功率都上去），冻结期间可以做（spec §1.1）。不碰冻结的填表 2b、2c、6.1。填表模块只改这几处：门槛规则的文字、Gmail 只读、`meta.json` 和结果里多存标签页编号、读回里加一个"读一眼标签页的状态"的小函数；不改 checkpoint、录制和读回核对。

## 1. 设计

### 1.1 数据

- `applications.status` 多一种运行结果 `held`（`store.RUN_STATUSES`）。用户能设的仍是 pending / applied / skipped。
- 新表 `apply_holds`，一个等你处理的岗位一行：

  ```sql
  CREATE TABLE IF NOT EXISTS apply_holds (job_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, phase TEXT NOT NULL,
      n INTEGER, mode TEXT NOT NULL, via TEXT NOT NULL, reason TEXT NOT NULL, note TEXT,
      tab_id TEXT, tab_url TEXT, held_at TEXT NOT NULL, times INTEGER NOT NULL, resuming INTEGER NOT NULL DEFAULT 0)
  ```

  - `times` 是第几次等你（1 或 2，决定 23 第 7 条）；`resuming` 是"正在接着做"（spec §10 第 102 条）。
  - 写入只经过 `record_application_result`：结果是 held 时在同一个事务里写这一行，其他结果删掉这一行。所以投递状态和等你处理的记录不会对不上。
- `apply_attempts`：状态多一种 held，步骤多一种 `resume`。用户处理等你处理的岗位时，那一条用户记录带上原来的批次编号（第一步的判断要用，1.7）。
- 不存联系方式：`note` 是打码后的 NOTE（和现在的备注一样经过 `Redactor`）。

### 1.2 两条路的结果变成 held

- `apply_fill.GATES = ("captcha", "login_issue", "sso_required", "email_verification")`，两条路共用。
- ApplyPilot 那条路：`apply_bridge.outcome_status` 把这四种记成 held。
- 填表那条路：`apply_fill.application_result` 在"可能已提交""程序没跑起来"之后、"其余记 failed"之前，把这四种记成 held。报告照旧写"没填完：门槛"。
- 标签页：
  - 填表那条路：`JobOutcome` 加 `tab_id`、`tab_url`（读回时找到的那个），`meta.json` 加 `tab_id`。
  - ApplyPilot 那条路：`ChromeRunner` 在每个岗位前后各列一次 CDP 标签页，结果是 held 时用 `readback.find_job_tab` 找出标签页。`ApplyOutcome` 加 `tab`（编号和地址）和 `last_url`（agent 最后打开的地址，`find_job_tab` 要用）。接着做时已知原标签页的编号，就按编号在结束后的列表里找。
  - 测试里的 `FakeRunner` 直接给出 `tab`。

### 1.3 指令（`apply_bridge.py`）

- `localize` 里多做一组替换 `no_solving`，换掉 ApplyPilot 指令里破解验证码的 7 处，每处都要正好找到一次，否则报错，这个岗位退回待投（spec §10 第 98 条）：
  1. 整段 `== CAPTCHA ==` 到 `All else fails -> Output RESULT:CAPTCHA.`，换成一句"永远不解，按 GATES 停下"；
  2. 第 2 步、点 Apply 之后、第 5d 步、第 11 步里的"run CAPTCHA DETECT … solve"，换成"有验证码或人机检查就按 GATES 停下"；
  3. `CAPTCHA AWARENESS` 那一行；
  4. `RESULT:CAPTCHA -- blocked by unsolvable captcha` 的说明。
- 附加规则（两种模式共用）：
  - 新增 GATES 一段：四种门槛各自的结果码；停在那一页，不关这个标签页；写一行 NOTE，说明哪个网站、要做什么，不写邮箱和密码；Gmail 里等两分钟左右收不到验证码就算门槛。
  - 自动提交的 TABS：出结果后关掉这个标签页，停在门槛时除外。
  - 条款（spec §10 第 103 条）：必勾的条款、声明、授权照样勾；可选的营销短信、邮件订阅不勾；页面上写明仲裁的按 ARBITRATION 停下。
  - 接着做（只在接着做时加）：
    - 原标签页在：不开新标签页、不做第 1 步，列出标签页、选中开着 `<地址>` 的那一个，先看是否已经交过或显示已申请过，没有才接着填、提交。
    - 原标签页不在：新开标签页、从岗位地址开始，同样先查。
  - `ApplySpec` 加 `resume`、`resume_tab`、`resume_url` 三个字段。
- `run_application`：传给投递用 Claude Code 的环境里去掉 `CAPSOLVER_API_KEY`。
  - MCP 配置文件按有没有 `extra_servers` 选名字：填表模块带 checkpoint，用自己的文件，`/apply` 的 `.mcp-apply-0.json` 不变。
- `claude_command`：放行名单去掉写在 `extra_disallowed` 里的工具，所以填表模块的 `send_email` 既不放行也被禁用。
- 配置：
  - `apply.blocked_origins` 加上 CapSolver、2Captcha、Anti-Captcha、CapMonster 的接口地址；
  - `form_fill.disallowed_tools` 加 `mcp__gmail__send_email`；
  - 新增 `apply.notify: true`（`config.py` 的 `ApplySettings` 加这个字段）。

### 1.4 填表那条路（`form_fill/runner.py`）

- `RUN_RULES`：
  - 去掉"这次没有邮件工具""不要解验证码""单点登录"三行，这几条改由共用的 GATES 管；
  - 加一行"可以用 Gmail 搜索、读邮件找验证码或验证链接，永远不发邮件"。
  - "填到提交前""不判断地点""不符合资格""不关、不用原来的标签页"几行不变。
- `run_job` 加上 Gmail 服务（`gmail=True`）；`send_email` 由配置禁用（1.3）。
- `readback.tab_state(port, target) -> PageState`：只读页面状态，复用 `_find_page`、`_extract`、`page_state`，不写文件。`FormFillRunner.tab_state(target)` 包一层。
  - 接着做前读一眼原标签页时用：页面写着已经交上，就记 `submitted_by_user`（G12）。
- 填表报告里给门槛的那句"再说'重跑'"不改（单独运行本模块时照旧）。/apply 的 `apply_status` 给等你处理的岗位另写"要做什么"（1.6）。

### 1.5 提醒（新模块 `notify.py`）

- `notice(held, waiting, step) -> (标题, 正文) | None`，纯函数。
  - `held` 是这次等你处理的（序号、公司、原因），`waiting` 是这次填好等你检查的个数。
  - 写法见 G6：门槛分三种（要登录、验证码、邮箱验证码），最多列 5 个，后面写"等 N 个"；两样都没有时返回 None。
- `clean(text, limit)`：控制字符换成空格，截到 20 个字加"…"（G7）。
- `mac(title, body)`：
  - 执行 `osascript -e 'on run argv' -e 'display notification (item 2 of argv) with title (item 1 of argv)' -e 'end run' <标题> <正文>`，标题和正文作为参数传入，不拼进脚本；
  - 只在 macOS 上执行，10 秒超时，出错不抛、不重试。
- `Toolbox.notifier`：
  - 默认值：配置 `apply.notify` 打开、并且是 macOS 时用 `notify.mac`，否则什么都不做；
  - 测试换成一个把调用记下来的假通知器。
- 调用的时机：一步的后台线程、一次接着做的后台线程结束时各调一次，运行中途出错也调，按已经出来的结果数。

### 1.6 展示（`apply_status`）

- 多一组 `held`，从 `apply_holds` 读，所以 `idle` 时也有（G8）。每一行：
  - 批次、序号、岗位编号、职位、公司；
  - `gate`：中文，例如"登录和注册都不成""单点登录""验证码""邮箱验证码"；
  - `site`：标签页地址的主机名，没有标签页时用岗位地址的；
  - `todo`：agent 的 NOTE；
  - `then`：接着做时是自动提交，还是停在提交前（由填表模块从岗位地址重填）；
  - `times`：第几次等你；
  - `interrupted`：上次接着做时中断（`resuming` 为真，又没有在跑的接着做）。
- 这一步各岗位的 `result` 会出现 held。第一步还在等哪几个时，`notice` 写明（G17）。

### 1.7 第一步有等你处理的（spec §10 第 101 条，G17）

- `_sync_apply_results`：第一步结束时没有 waiting / applied、但有 held 的，不作废；把这几个岗位写进批次数据的 `review_holds`，第二步仍是 proposed。
- 判断由 `_review_verdict(batch)` 来做，读只追加的尝试记录，所以"成功过"不会因为后来跳过而消失：
  - `review_holds` 里有一个岗位，在这个批次下有过 waiting 或 applied 的尝试（接着做的结果，或用户"N 已投"的那一条，1.1）→ 可以开始第二步，清掉 `review_holds`。
  - 这些岗位都已经不是 held、却一个都没成 → 作废，和第 68 条一样写进批次。
  - 其余 → 还在等，`start_applying`（第二步）拒绝，并写明在等哪几个。
- `apply_status`、`_batch_view` 和 `start_applying` 调用它。

### 1.8 接着做（`prepare_resume`、`resume_applying`）

- 共用 `_resume_plan(job_ids)`：
  - 每个岗位必须是 held，不能重复。
  - 用投递用 Chrome 的标签页列表（`open_tabs`，不为此启动 Chrome）查原标签页：先按编号，找不到再按存下的地址；
  - 开着同一个地址的标签页不止一个时，这个岗位进 `problems`，不进摘要（G11 d）。
  - 摘要写法见 G10、G16；返回 `job_ids`（要接着做的）、`summary`、`problems`。
- `prepare_resume(job_ids)`（准备级）：只调 `_resume_plan`，不改任何东西。
- `resume_applying(job_ids, summary)`（提交级）：
  - 投递的一步、填表模块的运行或另一次接着做在跑时拒绝；
  - 重算 `_resume_plan`，`job_ids` 和摘要必须一字不差；
  - 和 `start_applying` 一样检查投递资料、标准答案，以及 `profile.json` 和 `answers.yaml` 是否一致；停在提交前的岗位写成填表清单 `<批次>-resume-<时间>.yaml`，用 `load_inputs` 校验。
  - 写决定日志 `resume`，把这几个岗位标成"正在接着做"（写进数据库），然后在后台按顺序做，每个结果一出来就写进数据库：
    1. 找得到原标签页时先 `tab_state`：写着已经交上 → applied、`submitted_by_user`，下一个（G12）。
    2. 自动提交的：`_apply_spec(job, "submit")` 加上标准答案和接着做的字段，交给 `ChromeRunner`（G11）。
    3. 停在提交前的：交给填表模块的 `run_job`，新开标签页从岗位地址重填（G13）。"要做什么"加一条：原来的标签页可以关掉。
    4. 又是门槛：`times` 是 1 → held，`times` 改成 2；`times` 已是 2 → failed，原因不变，备注写"第三次卡在门槛"（G14）。
  - 结束时：调用通知器；第一步的判断交给 1.7。
  - `apply_status` 按一步的样子展示这次接着做（`phase: resume`，没有下一步的摘要）。
- 接着做在跑时，`start_applying`、`start_form_fill` 拒绝（G15）；`mark_applications` 不能改正在接着做的岗位。

### 1.9 用户自己处理的（`mark_applications`，spec §10 第 104 条，G9）

- `Store.set_application_status` 遇到 held 的岗位：
  - 已投 → 用户的 applied；
  - 跳过 → 用户的 failed，原因是这道门槛的结果码；
  - 改回待投 → 用户的 pending。
- 三种都带上原来的批次编号，在同一个事务里删掉 `apply_holds` 那一行。

### 1.10 怎么数（`apply_fill.week_stats`，G18）

- held 放进"不算进去"，写作"等你处理 N 次"，排在"停在提交前"后面。
- 用户的 failed 照原因归类，算进失败。
- applied 且原因是 `submitted_by_user` 的，不管走哪条路，都算提交。
- `CATEGORIES["login"]` 加 `email_verification`。

### 1.11 工具清单、权限和说明

- `prepare_resume` 是准备级；`resume_applying` 是提交级，所以会带 `requiresUserInteraction` 标记，也自动不在定时任务的工具里。
- `.claude/settings.json` 的 ask 加 `mcp__intent-agent__resume_applying`；`.codex/config.toml` 加一节 `approval_mode = "prompt"`，注释改成"七个"。
- `tests/test_tools.py` 的提交级清单加上它。
- `mcp_server.py` 的说明，Applying 一段：
  - 永远不解验证码；
  - 等你处理的岗位按序号列出；
  - "好了" → `prepare_resume` → `resume_applying`；
  - "N 已投 / N 不处理了"用 `mark_applications`。
- `/apply` skill：
  - 开头先 `apply_status`，有等你处理的先列出来（G-h）；
  - 删掉"CapSolver 可选"；
  - 汇报时加上等你处理的一组；
  - 新的一节"门槛：好了以后"；
  - 失败原因里去掉"captcha … 可以说重跑"。
- `README.md`：删掉 CapSolver 可选和"用 CapSolver 违反条款"那条风险，写上"登录和验证码交给你"。

## 2. 测试先行

新文件 `tests/test_gate_handoff.py` 按样例写，假的部分沿用 `FakeRunner`、`FormFillFake`，另加假通知器。`FormFillFake` 多两样：

- `tab_states`：给 `tab_state` 返回的页面状态；
- 按编号认出原标签页。

| 样例 | 测试 |
| --- | --- |
| G1 | 四种门槛在两条路上都记 held：`apply_holds` 一行、尝试记录 held、不在清单里、下一批不选；后面的岗位照做；Gmail 读到验证码的照常成功 |
| G2 | 仲裁、SSN、支付、不许 AI、`unsafe_verification`、`expired` 照旧 failed，不进 `apply_holds` |
| G3 | 两种模式的指令里有条款规则：可选的不勾，仲裁例外 |
| G4 | 环境里设了 `CAPSOLVER_API_KEY`，两种模式的指令里没有 CapSolver、key、`MANUAL FALLBACK`、"solve"；有 GATES；假 claude 记下的环境里没有这个变量；7 处里任一处被改写就报错，岗位退回待投；`blocked_origins` 有 CapSolver |
| G5 | `ChromeRunner`（换掉 `list_targets` 和 `run_application`）给 held 的结果配上新开的标签页；两个新标签页时按最后的地址；自动提交的 TABS 写着"门槛除外" |
| G6、G7 | `notice` 的 4 种情况；一步结束时假通知器收到的标题和正文；什么都不用做时不发；`mac` 把公司名作为单独的参数传给 `osascript` |
| G8 | 同一个数据库新建一个 `Toolbox`，`apply_status` 是 idle，`held` 一组列得出 |
| G9 | `mark_applications` 改 held 的三种情况：状态、尝试记录、`apply_holds` 删掉 |
| G10 | `prepare_resume` 的摘要一字不差 |
| G11 | 回原标签页（按编号、按地址）、找不到从岗位地址重开、两个同地址进 `problems`；submit 模式、带标准答案、接着做的规则里有地址 |
| G12 | 原标签页写着已经交上：applied、`submitted_by_user`，`FakeRunner` 没被调用 |
| G13 | 停在提交前的岗位交给填表模块，stop 模式、waiting，"要做什么"里有"原来的标签页可以关掉" |
| G14 | 第一次 held，第二次 held（`times` 2），第三次 failed、算允许的失败；每次都通知 |
| G15 | 摘要不一致、不是 held、一步在跑、接着做在跑时开始别的，都拒绝 |
| G16 | `resuming` 留在数据库里，新的 `Toolbox` 的摘要写"上次接着做时中断" |
| G17 | 第一步全卡：不作废、第二步拒绝；接着做成一个 → 可以开始；都说不处理了 → 作废 |
| G18 | 尝试记录 → `week_stats` 的 line 一字不差 |

要按新规则改的旧测试（captcha、login_issue 原来断言 failed）：

- `test_apply_form_fill.py`：第 187–211、317、390、458–471、496–506 行一带；
- `test_slice6.py`：第 326、494、785、834 行一带；
- `test_slice5b.py`：第 196、292 行；
- `test_apply_fill.py`：第 88、114–120 行；
- 填表模块的指令文字和 Gmail（`test_form_fill_runner.py`、`test_slice6.py` 里比对命令的几处）；
- `test_tools.py` 的提交级清单。

填表模块自己的报告仍记 gate（`test_form_fill_runner.py` 第 421–448 行），不改。

## 3. 步骤（每一步测试先行，通过后提交）

1. **纯逻辑**：`apply_fill`（GATES、held 映射、`week_stats`、`email_verification` 归类）和 `notify.notice`、`clean`。测试 G6、G7 的纯函数部分，G18，G1 的映射部分。
2. **存储**：held、`apply_holds`、`record_application_result(hold=…)`、`holds()`、`set_application_status` 遇到 held。测试 G9 的存储部分。
3. **指令和运行命令**：`no_solving`、GATES、条款、TABS、接着做的规则、环境变量、放行名单、配置、填表的 `RUN_RULES` 和 Gmail、`JobOutcome` 的标签页、`ChromeRunner` 记下标签页。测试 G3、G4、G5。
4. **一步的结果**：`start_applying` 记 held 和标签页、结束时通知、`apply_status` 的 `held` 一组、第一步的判断、`mark_applications`。测试 G1、G2、G6、G8、G9、G17。
5. **接着做**：`tab_state`、`prepare_resume`、`resume_applying`、第三次失败、`submitted_by_user`、中断、拒绝。测试 G10–G16。
6. **权限和说明**：settings.json、Codex 配置、`mcp_server` 说明、skill、README。测试 `test_tools.py`。
7. **验证**：CLAUDE.md 的四条命令；对照 CLAUDE.md 的隐私条款自查一遍（通知、`apply_holds`、尝试记录、备注里都没有联系方式）；然后请用户批准一次真实检查（第 6 节）。

## 4. 不做（留给以后的会话）

- 抽查的和名单公司的岗位回原标签页接着填（第二刀）：要放开 checkpoint 只录新标签页的规则，改读回和"别的标签页没动"的检查。
- 一步还在跑时就叫你，或者这一步停下来等你（决定 22 第 3 条没选的两种）。
- 把等你处理的标签页自动切到 Chrome 最前面。
- Claude Code 自带的手机推送，以及在 Codex 宿主下实测通知。
- 让投递 agent 不退出、原地等你。
- 报告写得更细：单点登录写明哪个网站，登录失败引用页面的报错原文，告诉用户"重设密码"的做法（填表规格 5.5）。现在靠 agent 的 NOTE。
- 仲裁、SSN、支付、不许 AI 作答的岗位处理完接着做。
- 开始投递时一并批准"处理完自动接着做"（会改不可违反的批准规则）。
- 工具服务重启后，批次的一步在数据库里一直停在 running（原来就有，和本切片无关）。

## 5. 风险（用户知情，决定 22）

- 选错原标签页：开着同一个地址的不止一个时不接着做。
- 重复提交：先读一眼原标签页，agent 再查一遍；"正在接着做"写进数据库；只接受 held。
- 验证码过了以后凭证约两分钟失效：提交页上的建议用户直接点提交，再说"N 已投"。
- Mac 通知可能被专注模式挡住，第一次可能要在系统设置里允许"脚本编辑器"；Codex 宿主下没有实测。工具服务在 Claude Code 桌面版里启动，`osascript` 能不能弹出来，要在真实检查里看。
- Playwright MCP 经 CDP 能不能列出、选中别的 agent 开的那个标签页，离线测试测不到，也要在真实检查里看。
- 计数口径变了，前后两周的数字不能直接比。
- 每个门槛都占你的时间（每天 15 分钟）。

## 6. 完成的标准

- 第 2 节的测试全部通过，CLAUDE.md 的四条验证命令全部通过。
- 用户在弹窗里批准一次真实检查，拿 1 到 2 个真实的等你处理的岗位，看三件事：
  - 通知弹出来了；
  - 原标签页接上了；
  - 自动提交的交上去了，或者停在了该停的地方。
- 然后告诉用户：到主目录的会话里输入 `/merge claude/infallible-dewdney-35a1ba`。合并前，项目进度管理会话会做独立审查。
