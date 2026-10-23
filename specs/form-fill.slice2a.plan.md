# 填表模块切片 2a 计划：逐页录制和 Workday 读回（2026-10-19，决定 26）

> 本文件保留当时的切片计划与审阅记录，不作为当前审批流程。当前授权按 spec §5.2 / 决定 25；历史“等用户确认、弹窗、前 5 份抽查”不再是生效门槛。尚未实现的冻结功能仍不启动。


规格：`specs/form-fill.md`；决定记录：`specs/form-fill.decisions.md`（决定 25、26）；切片 1 的计划：`form-fill.plan.md`。
本计划放在模块旁边，不追加到根目录的 `plan.md`。vendor 文件不改。用户 2026-10-19 授权全部按推荐项（决定 25 的原话），所以确认计划、写测试、写代码连着做，只在第 7 节列的地方停下来找用户。

## 1. 做什么、不做什么

**做**：

- **`checkpoint` 服务**（规格 4.2）：本模块自己的 stdio MCP 服务，填表 agent 的 Claude Code 和 Playwright MCP 一起启动它。
  - 工具只有一个：`checkpoint(stage, url)`，`stage` 是 `blank` 或 `filled`，`url` 是填表 agent 当前页的地址。
  - blank：录下空表，返回这一页每栏的编号和题目。filled：读回这一页，返回"已录"。同一页连续 3 次 filled 没换页，返回 STOP，填表 agent 停下，结果记 `stuck`。
  - 时限 5 秒（配置）。超时就返回"没录到，接着做"，记进 `checkpoints.json`。
  - 认标签页：在这次运行开始后新开的标签页里按地址找，找不到再用上一次的（决定 26）。
  - 清单条目写了 `ignore_filled_on_page` 时，忽略那一页的 filled 调用，照常返回（6.1 第 8 项）。
- **后台补录**：同一个服务每隔 1 秒（配置）读一次岗位的标签页，只读不改。换页时，上一页最后一次读到的值存为 `p<n>.auto.json`。
  - 一栏都没有的读（页面在加载、岗位介绍页）不算一页。
  - 新读到的栏比上一次少、而且全都在上一次里（页面正在拆掉）时，不覆盖上一次的补录。
- **页的标识和栏的编号**（规格 4.3，决定 26）：纯逻辑，放在 `recorder.py`。编号表存进 `state.json`，填表 agent 退出后代码重读当前页时接着用。
- **录制目录**（规格 6.2）：`recordings/<录制编号>/form/`（空表：结构、简化 HTML、截图、MHTML、网络记录）和 `fills/<运行编号>/`（预填值、读回值、补录、填完时的页面和截图、`final.json`、`checkpoints.json`、`requests.json`）。
  - 录制编号按规格 6.2 从地址算。
  - 同一个岗位再次真实访问、表单结构变了，另存 `form-v2/`。
  - MHTML 按 MIME 拆开打码再写回，扫描仍有命中就不存（决定 26）。
  - 网络记录：第一页加载时的 GET 响应先写进 `form/network.partial.jsonl`；运行结束后，只有一页、没出现过密码栏、没有一条响应带着联系方式的，才改名为 `network.json`，其余删掉（决定 26）。cookie、`set-cookie`、`authorization` 和名字里带 token、auth、session、csrf 的头都去掉；带联系方式的整条丢掉。
- **读回值从哪里来**（规格 4.3）：每页取最后一次 filled；没有就取补录（报告注明"补录"）；都没有，这一页的栏记"读不出（未录到）"。当前页用填表 agent 退出后的重读（`final.json`）。
- **页面预填**（规格 5.3）：blank 时有值、filled 时值没变的栏，来源记"页面预填"，照样核对。没有 blank 录制的页，非空栏记"来源未录到"，结论最多"检查后可提交"。其余非空栏写"未声明"（填表 agent 的来源声明在 2c），不定严重程度。
- **按页出报告**：每页分别对题、核对（规格 4.4 的"整页一起对"按页做），合在一起出一份报告。逐栏多出 `page`、`read_from`、`read_at`、`source`。运行结束时由代码直接写 `<岗位>.json` 和 `summary.md`；本机命令 `report <运行编号>` 照旧能重出，也认得切片 1 的运行。
- **已录制的岗位不再真实访问**（规格 4.1、6.5）：录制的 `meta.json` 记下最近一次的结论。结论属于"不用再访问"的那几种、清单里又没写 `revisit: true` 的岗位，这次不访问，报告写"已录制，请用重放重测"。
- **Workday 读回**（决定 23 第 1、11 处）：
  - Review 页读回不全：有的题读成 `*`，毕业日期、学校所在地、OPT 担保、政府雇员、受制裁地区和 Skills 没读到。
  - 普通填写页的自定义控件读不出来：Degree 漏了；Field of Study 读成了搜索框里没选中的字；日期分成 Month、Year 两栏，没带上 From、To；几处题目取错。
  - 做法：用主目录已存的两份页面复现（先问你，第 7 节），把页面结构改写成合成页面写测试，按通用的 ARIA 写法修读回，不为 Workday 写特殊逻辑。
  - 通用写法里已知要补的：`aria-haspopup="listbox"` 的按钮式下拉（值是按钮上显示的字）、`role=spinbutton`、多选框里已选的项。
- **改现有文件，只加可选参数**（规格 §9，/apply 不变）：
  - `apply_bridge.mcp_config` 加 `extra_servers`；`claude_command` 和 `run_application` 加 `extra_allowed`。不传时命令逐字节不变。
  - `config.py`、`configs/default.yaml` 的 `form_fill` 一节加：`recordings_dir`、`checkpoint_timeout_seconds`（5）、`watch_interval_seconds`（1）、`stuck_after`（3）、`network_max_mb`（30）。
  - `tools.py`：`start_form_fill` 的返回和 `form_fill_status` 多出录制编号、跳过的岗位和报告路径。

**不做**（2b、2c 及以后）：重放服务、重放自检、"能重测什么"的判定（2a 一律写"只能重测核对"，自检留给 2b）、`{{personal.email}}` 引用写法、填表 agent 的结构化结果和来源声明、来源核验、上传核对、独立评审、当场修正、接入 /apply。

## 2. 可行性验证（规格 6.1）

**冻结，等投递需要填表模块时再做**（决定 31，取代决定 29 里"合并后在主目录会话里做"那一步）。清单已起草，留在进度会话的笔记目录里（`form-fill-2a-feasibility-list.yaml`），不复制进 `data/local/`。解冻后按下面做：worktree 里的开发会话用的工具服务是从主目录启动的，所以在合并后的主目录会话里做；只修挡住投递达标的问题。

- 3 个真实岗位：一个 Greenhouse 或 Lever 单页表、一个 Workday、一个领英快速申请。我从投递清单里挑，作为推荐项列给你；你批准 `start_form_fill` 的弹窗。每个岗位只访问一次，永远不点提交。
- Workday 的那个，清单里写 `ignore_filled_on_page: 2`（第 8 项）。
- 第 7 项（重放自检）在 2b 做，用这次留下的录制，不再访问网站。

| 项 | 怎么算通过 | 谁判 |
| --- | --- | --- |
| 1 不干扰 Playwright MCP | 运行记录里 `checkpoint` 连上之后的 `browser_tabs`、点击和 `browser_file_upload` 都没有报错；上传栏读回到文件名 | 代码 + 你 |
| 2 认得出标签页 | 每次 `checkpoint` 记下的两个地址一致（翻页、跳转、新标签页之后也一致） | 代码 |
| 3 `checkpoint` 能用 | 每次都在 5 秒内返回，blank 返回了栏目编号 | 代码 |
| 4 读回覆盖 | `feasibility.md` 按页逐栏列出读回值，你对照截图和标签页标"一致 / 不一致 / 漏"。单页表读不出的栏为 0；Workday 和领英列出读不出的栏，由你决定能不能接受；列出禁用 `browser_run_code_unsafe` 后填不进去的控件 | 你 |
| 5 存得下 | 每页的结构、简化 HTML、截图都在且不为空；MHTML 存了或写明为什么没存；单页表有 `network.json` | 代码 |
| 6 打码生效 | 代码扫描读回值、选项、简化 HTML、MHTML、网络记录，`profile.json` 的每一项联系方式和页面上出现过的其他邮箱、电话命中 0 次；截图你看一遍 | 代码 + 你 |
| 8 换页补录 | Workday 第 2 页的值来自补录，和第 2 页填完时的截图一致，不是第 3 页的空表 | 代码 + 你 |

- 任何一项不通过，先回来和你商量（规格 6.1），例如去掉 `checkpoint`，只靠后台读页录制。
- 结果推翻录制的触发方式时，先改写规格，再做后面的部分。

## 3. 模块

`src/intent_job_agent/form_fill/` 新增 3 个文件：

```
recorder.py     页的标识、栏的编号、卡住的判定、补录的取舍、预填的判定（纯逻辑，状态能存成 JSON）
recording.py    录制编号、录制目录的读写、已录制的判定、网络记录的过滤和收尾、MHTML 打码
checkpoint.py   MCP 服务：checkpoint 工具、后台读页、经 CDP 读标签页（Playwright 只在一个线程里用）
```

要改的现有文件：`readback.py`（读回脚本补题组、步骤、标题和 Workday 那几类控件）、`models.py`、`runner.py`（启动 checkpoint 服务、结束时重读、收尾、出报告）、`report.py`、`cli.py`、`inputs.py`（`ignore_filled_on_page`）、`apply_bridge.py`、`config.py`、`configs/default.yaml`、`tools.py`、`docs/examples/form-fill-list.example.yaml`。

## 4. 数据结构

- **ReadField** 多出：`section`（所在题组）、`occurrence`（第几次出现）、`appeared_after`。编号改成 `p<页>.<序号>`。
- **页的记录**（`state.json`）：页号、地址、标题、全部题目、编号表（身份 → 序号）、下一个序号。另记每个标签页当前在哪一页、同一页连续 filled 的次数。
- **一次读**（`p<n>.values.json` 等）：页号、读的时间、标签页地址、读回栏。
- **CheckResult** 多出：`page`、`read_from`（filled / auto / final / unrecorded）、`read_at`、`source`（prefilled / unrecorded / undeclared）。
- **JobReport** 多出：`recording_id`、`pages`（每页的页号、标题、读回从哪里来）。
- **录制的 meta.json**：地址、公司、职位、平台、录制日期、页数、`replay_capability`（2a 一律 `check_only`）、最近一次的运行编号和结论。

## 5. 测试（先写，全部离线）

- `test_form_fill_recorder.py`（合成的读回快照）：
  - 页的标识：标题相同是同一页；追问栏出现不换页；标题一样、栏目全不同算换页；退回见过的页沿用页号；没有标题时按指纹；一栏都没有的不编页号。
  - 编号：插入追问栏后原有栏的编号不变，追问栏记 `appeared_after`；两段 Education 的 School 编号不同。
  - 卡住：同一页连续 3 次 filled 返回 stop，换页后重新计数。
  - 补录：3 页表单缺第 2 页 filled 时用第 2 页的补录，补录是第 2 页填完后的值、不是第 3 页的空表；页面拆到一半的读不覆盖；两样都缺时第 2 页全部"读不出"。
  - 预填：blank 时有值、filled 时没变的栏是"页面预填"，`form/` 里没有这个值；没有 blank 的页记"来源未录到"，结论不是"可以提交"。
  - 状态存成 JSON 再读回来，接着编号不变。
- `test_form_fill_recording.py`：
  - 录制编号：greenhouse、lever、ashby、workday、linkedin 和其他网站各一例；跟踪参数不影响编号。
  - 已录制的判定：上次的结论决定要不要 `revisit`。
  - 网络记录：cookie 和认证头被去掉；带联系方式的整条丢掉；非 GET 不存内容；多页、出现过密码栏或有响应带联系方式时，收尾删掉网络记录。
  - MHTML：打码后写回，联系方式不在里面；打不了码的不存。
  - 表单结构变了另存 `form-v2/`。
- `test_form_fill_checkpoint.py`：
  - 用假浏览器：blank 返回编号和题目；filled 返回已录；按地址认标签页、找不到时用上一次的；超时返回"没录到"；STOP；`ignore_filled_on_page`；每次记下两边的地址。
  - 后台读页：换页时上一页的补录落盘。
  - MCP 服务只列出 `checkpoint` 一个工具。
  - 真浏览器（本机有 Chromium 才跑）：另一个 Playwright 客户端扮演填表 agent，在合成的 3 页表单上翻页；后台读页拿到第 2 页填完的值；一个单页表的 GET 响应记了下来，cookie 被去掉。
- `test_form_fill_readback.py` 增补：按钮式下拉、`role=spinbutton` 的月份和年份带上所在题组（From、To）、多选框只读已选的项不读搜索框里的字、题目只有 `*` 时取旁边的文字，以及从你的两份 Workday 页面复现的合成页面。
- `test_form_fill_runner.py` 增补：填表命令的 MCP 服务表里有 checkpoint、没有 Gmail，放行名单有 `mcp__checkpoint__checkpoint`；指令里有 checkpoint 的规则；运行结束后有报告、录制的 `meta.json` 记了结论；已录制的岗位没有访问。
- `test_form_fill_report.py`、`test_form_fill_cli.py` 增补：按页核对；补录和未录到在报告里注明；切片 1 的运行照旧能重出报告。
- `test_slice6.py`：不传新参数时，/apply 的命令不变。
- 隐私：假 `profile.json` 的每一项联系方式都不出现在录制的任何文件里。

## 6. 步骤

1. 规格的修改、决定 25 和 26、本计划，一次提交。
2. 写第 5 节的测试并提交。这时测试会失败。
3. 写 `recorder.py`、`recording.py`、`checkpoint.py` 和运行器的改动，测试通过后提交。每完成一块就提交一次。
4. 问你能不能用主目录已存的两份 Workday 页面（第 7 节）。复现、写合成测试、修读回，提交。
   - 2026-10-20 做完（决定 28）：Review 页 34 栏 → 53 栏，My Experience 页的 Degree、Field of Study、日期、"I currently work here" 都读对了。
5. `git merge main`，跑完四条验证命令，结果给你看。然后告诉你"这个切片做完了，可以合并"，到主目录的会话里输入 `/merge <本分支名>`。
6. 合并前的独立审查（进度会话）：32 条问题都修了，每条补了合成测试（决定 32）。
7. ~~合并后在主目录的会话里做可行性验证~~：**冻结，等投递需要填表模块时再做**（决定 31）。解冻后，按结果修，只修挡住投递达标的问题，在存下的录制上重出报告，不再访问网站。

每次提交前都跑：`uv sync --locked`、`uv run ruff check src tests`、`uv run ruff format --check src tests`、`uv run python -m pytest -q`。

## 7. 需要你的地方

- **用已存的 Workday 页面**：主目录 `data/local/form-fill/runs/` 下 10-17 的两份（`20261017-085904-a375` 的 My Experience 页，`20261017-213100-2b5b` 的 Review 页）。只读打码后的 `page.html`、`raw.json`、`readback.json` 和 `meta.json`，不读 `log.jsonl`、`profile.json`、`.env`。
- **可行性验证的真实数据**：你 10-20 同意了。后来发现工具服务本来就用主目录的 `data/local/`，但跑的是 main 的代码，所以验证改到合并后做（决定 29）。
- **3 个岗位**：已经挑好（决定 29）。试填冻结（决定 31），解冻后在主目录会话里由你批准 `start_form_fill` 的弹窗。

## 8. 下一个切片（冻结，等投递达标：决定 31）

接入 /apply 不冻结，由 apply 会话做，只用切片 1 和 2a 做到的部分。下面两个切片冻结，没有开始：

- 2b 重放：本机只听 127.0.0.1 的服务、干净的浏览器、网络回放和静态页面两种做法、外部请求一律拦掉、提交的判定（没见过的非 GET 请求、点了提交按钮词表里的按钮）、重放自检和"能重测什么"、`replay_form_fill`、`{{personal.email}}` 引用写法。用 2a 留下的录制做，不再访问网站。
- 2c 独立评审和当场修正：填表 agent 结束时的结构化结果（`--json-schema`）、来源核验、上传核对、评审（新写的无工具 Claude Code 后端，`reviewer_model` 不弱于 Opus 5.5）、修正 agent 和修正单。用户 10-17 提的"只看截图的盲评"作为评审的一个选项。
