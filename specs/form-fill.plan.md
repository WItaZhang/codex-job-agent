# 填表模块切片 1 计划（B）：用现有填表 agent 填到提交前，读回最后一页、核对、出报告（草案，2026-10-15，待用户确认）

规格：`specs/form-fill.md`；决定记录：`specs/form-fill.decisions.md`；已确认样例：`tests/fixtures/form_fill/samples_work_auth.yaml`（11 例）、`samples_profile.yaml`（18 例）。
本计划放在模块旁边，不追加到根目录的 `plan.md`，免得和其他分支冲突。**你确认之前不写实现代码。** vendor 文件不改。

## 1. 做什么、不做什么

**做**：
- **填表**：复用 `apply_bridge.run_application` 的"停在提交前"模式。
  - `mcp_config`、`claude_command` 加可选参数：不加载 Gmail，用 `--disallowedTools` 禁用 `browser_run_code_unsafe`（另外禁哪些见第 8 节第 2 问）。不传这些参数时，/apply 的命令逐字节不变。
  - 指令末尾追加两段：标准答案里联系方式以外的条目（写成"题目短语 → 答案"），以及要求清单原文。填表 agent 按它们填。
- **读回最后一页**：填表 agent 退出后，代码用 Playwright 的 `connect_over_cdp`（`playwright==1.56.0` 已是依赖）连投递用的 Chrome（端口 9222）。
  - 找岗位标签页：运行前后各取一次标签页列表，取新出现的页。新页不止一个时，取运行记录里 agent 最后访问的地址。找不到就记 `tab_lost`。
  - 两种读法，同一页都跑，结果合并：
    - (a) 表单控件：input、select、textarea、checkbox、radio，以及 `role=combobox` 这类自定义控件。读出题目、值、选项、是否必填。单页表靠这个。
    - (b) 确认页文字：成对的"题目：值"，比如 Workday 的 Review 页、LinkedIn Easy Apply 的最后一步。多步表单前几页的内容靠这个。
- **存下这一页**：打码后的简化 HTML、截图、`readback.json`、`meta.json`，存到 `data/local/form-fill/runs/<运行>/<岗位>/`。以后可以离线重跑核对和报告。
  - 打码用类别占位，例如 `[email]`、`[phone]`，密码写 `[已填]`。截图里页面上出现联系方式的地方涂黑，涂不了就不存截图。规格 5.6 那种 `{{personal.email}}` 引用写法是给重放用的，留到切片 2。
- **核对**：对题，依次用 `asks`、内置的常见栏目、`overrides`，对上多条记"有歧义"。再按 `kind` 比较，结果分五种：对 / 错 / 没有标准答案 / 有歧义 / 未审查。
- **报告**：每个岗位一份 JSON，加一份 `summary.md`。结论分四种：
  - 不能提交：有错的事实或无依据的猜测；
  - 检查后可提交；
  - 被门槛挡住；
  - 读不出。

  另附"需要你做的事"。
- **本机命令**：在存下的页上重跑核对和报告，不开浏览器。

**不做**（切片 2 及以后）：逐页录制、`checkpoint`、网络记录和 MHTML、重放、独立评审、当场修正、接入 /apply、跨岗位根因分析。

## 2. 第一步：可行性验证

- 你挑 3 个真实岗位：Greenhouse 或 Lever 的单页表、Workday、LinkedIn Easy Apply 各一个。
  - Workday 用你已有账号的公司，LinkedIn 先确认已登录。没登录的话会停在门槛上，测不到。
- 我起草清单，你同意后调用提交级工具 `start_form_fill`，**每次都由你在弹窗里批准**。每个岗位只访问一次。

| 项 | 怎么算通过 | 谁判 |
| --- | --- | --- |
| 1 找得到标签页 | 3 个岗位都定位到 agent 最后所在的那一页 | 代码 |
| 2 读回覆盖 | `feasibility.md` 逐栏列出"题目、读回值"，你对照标签页标"一致 / 不一致 / 漏"。单页表必须全部一致。确认页上的"题目：值"必须全部读出。读不出的栏列出来 | 代码 + 你 |
| 3 打码 | 扫描本次写出的全部文件，`profile.json` 里的联系方式（含规范化后的写法）和密码命中 0 次 | 代码 |
| 4 没有提交 | agent 的结果是 dry run；审计里没有 `submit: true`，也没有点过提交按钮 | 代码 |
| 5 别的标签页没动 | 运行前后，原有标签页的地址和个数不变 | 代码 |

- **全部通过**：继续写核对和报告。
- **第 2 项单页表有问题**：用合成页面复现后修读回，不为某个网站写特殊逻辑。
- **确认页读不全**：在结果表里写明哪些读不到，回来和你商量要不要把逐页录制提前。
- **第 3 项有命中**：停下，先修打码。
- **第 4 项可能已提交**：整次停下，回来和你商量。

**可行性验证之前只写这些**：输入校验、打码、找标签页、读回、存页、填表运行器、两个工具。对题、核对、报告放在验证之后写，在这 3 份存下的页上直接出报告，不再访问网站。

## 3. 模块

子包 `src/intent_job_agent/form_fill/`，共 9 个文件：

```
models.py     核心类型（第 4 节）
inputs.py     读入并校验清单、answers.yaml、checklist.yaml；profile.json 只查被引用的字段在不在
redact.py     打码和落盘扫描（联系方式写成掩码，密码写"已填"）
readback.py   连 CDP、找标签页、两种读法（页面里跑的脚本只取原始节点，整理在 Python 里做）、存页
runner.py     起填表 agent（复用 apply_bridge）、追加指令、后台线程、状态
matching.py   对题：asks、内置常见栏目、overrides、歧义
compare.py    按 kind 比较：text、choice、yes_no、date、month、number_min、url、record、联系方式
report.py     结论、报告 JSON、summary.md
cli.py        本机命令：report <运行编号>
```

**要改的现有文件**：
- `apply_bridge.py`：加可选参数。
- `config.py`、`configs/default.yaml`：新增 `form_fill` 一节，只有 `dir`、`answers_file`、`checklist_file`、`disallowed_tools`。模型和超时沿用 `apply.*`。
- `tools.py`：新增 `start_form_fill`（提交级）和 `form_fill_status`（准备级），都不进定时任务。它和 /apply 共用一个 Chrome，一方在跑时，另一方拒绝启动。
- `tests/test_tools.py`：提交级集合的断言加上 `start_form_fill`。
- `.claude/settings.json`：`start_form_fill` 加进"每次询问"。
- `docs/examples/`：新增 3 个示例，值都是编的。

## 4. 数据结构

- **清单条目**：id、url、company、title、resume、cover_letter、revisit
- **标准答案条目**：key、answer 或 from_profile 或 from_applicant、kind、accept、otherwise、asks、overrides、record 的子栏、source
- **读回栏**：编号、题目、来源（控件 / 确认页文字）、值、选项、是否必填
- **核对结果**：栏编号、对到的键（可以为空）、结果、期望值（打码后）、严重程度
- **报告**：岗位 id、状态（filled / gate / tab_lost / readback_failed / system_error）、结论、逐栏结果、需要你做的事

## 5. 测试（先写，全部离线）

- `test_form_fill_inputs.py`：校验规则，包括：
  - 未知类别、联系方式写了值、`overrides` 成环、键重复都不通过；
  - 清单的各种错误。
- `test_form_fill_matching.py`：29 个样例，每个都对到正确的条目。覆盖：
  - `asks`；
  - `overrides`（点名 OPT 的担保题）；
  - 歧义；
  - 笼统的签证支持题。
- `test_form_fill_compare.py`：29 个样例的结果都对（对 / 错 / 没有标准答案）。覆盖：
  - `yes_no` 的 `accept`；
  - 有拒答写法时不用 `otherwise`；
  - 薪资区间；
  - 月份写法；
  - 学历按学校别名对段；
  - 日期格式。
- `test_form_fill_readback.py`：读回解析，输入是合成的"页面原始节点"（标 synthetic）。三种页面：
  - 单页表的控件；
  - Workday 式确认页的"题目：值"；
  - LinkedIn 式最后一步。

  另有一个真浏览器测试：本机装了 Chromium 才跑，否则跳过。
- `test_form_fill_redact.py`：联系方式及其变体、密码；扫描能查到漏网的。
- `test_form_fill_report.py`：结论规则、`summary.md` 的格式。
- `test_slice6.py`（现有的投递测试）增补两条：
  - 不传新参数时，命令不变；
  - 传了以后，没有 Gmail 服务，禁用名单生效。
- 存在 `data/local` 下的真实页面：有就跑，没有就跳过。

## 6. 步骤

1. 你确认本计划。然后在同一次提交里放进：
   - 本计划；
   - 规格的修改：3.2 加"未审查"；5.6 加另外禁用的 3 个工具；状态行指向本计划。切片 1 的暂时简化（只存最后一页、不用 `checkpoint`）只写在本计划里，规格仍写整个模块；
   - 决定 13；
   - 两份样例。

   其他还没提交的改动，按第 8 节第 3 问处理。
2. 写测试并提交。这时测试会失败，因为功能还没写。
3. 写可行性验证要用的代码，测试通过后提交。
4. 可行性验证：3 个岗位，每次你批准。结果表给你看。
5. 写对题、核对、报告和本机命令，测试通过后，在 3 份存下的页上出报告，提交。
6. 你检查。

每次提交前都跑：`uv sync --locked`、`uv run ruff check src tests`、`uv run ruff format --check src tests`、`uv run python -m pytest -q`。

**为了快**：你确认后，第 1–3 步我连着做完，中间不停。只在需要你的时候停下来：挑岗位、弹窗批准、看结果。

## 7. 在你机器上检查

- **开始前**：在投递用的 Chrome 里登录 Workday 账号和 LinkedIn，再挑 3 个岗位。
- **跑完后**：
  - 看 `feasibility.md` 和每个岗位的 `summary.md`；
  - 3 个标签页都停在提交前，你对照报告逐栏看；
  - 报告里标"错"或"需要你做的事"的地方，告诉我哪里判得不对。这些会变成新的测试用例。

## 8. 未决（都附推荐答案）

1. **自己推断、又没对上标准答案的非空栏怎么算**（比如开放题）：推荐记"未审查"，严重程度为"无"，列进"需要你做的事"，结论最多"检查后可提交"。等切片 2 有了独立评审再细判。
2. **还禁用哪些工具**：推荐再禁 `browser_close`、`browser_network_request`、`browser_network_requests`。后两个会把带 Cookie 的请求头交给模型，进而写进运行记录。
3. **工作区里其他未提交的改动**（CLAUDE.md 里 Codex 那一段、`.codex/`、`.agents/` 等）：推荐在第 1 步之前单独提交一次，之后本模块的提交只含本模块的文件。
