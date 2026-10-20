# 总规格：决定记录

规则写在 `spec.md`。这里按日期记录每条决定：谁定的，为什么定。

- 新决定加在后面，不改旧条目。
- 推翻旧决定时，另写一条新决定，注明推翻了哪一条。
- 2026-10-18 以前的决定没有搬过来，仍写在 `spec.md` §10 各条的括号里（"用户 某日 选择"）。
- 用户亲口说的，标"用户原话"。

## 决定 1（2026-10-19）：投递 agent 换成 Opus 5.5，开放题按用户的写法

**背景**：

- 2026-10-19 主目录的日常会话正在跑 /apply（batch-875c9a9af5a7，22 个岗位）。用户要求这一批的开放题用 Opus 5.5 写，并换成自己的写作要求。用户原话："开放题要用 Opus 5.5 写。要求紧扣题目，不要堆积经历，识别面试者想得到的答案，增加我的竞争力。语言要简练有力。"
- 当时投递 agent 用 `apply.model: sonnet`（运行记录里是 claude-sonnet-5-5），开放题照 ApplyPilot："Write 2-3 sentences … Connect it to a real achievement from the resume"，和"不要堆积经历"冲突。
- 过去 40 次投递都用 Sonnet 5.5。每个岗位按 API 价格折算：中位数 $0.34，平均 $0.54，最多 $1.87；用时中位数 53 秒，最长 345 秒（超时 900 秒）。用订阅时消耗的是额度。Opus 5.5 每个 token 的价格是 Sonnet 5.5 的 2 倍（每百万 token 输入 $4、输出 $20，对 $2、$10）。

**用户的选择**（2026-10-19，选择题）：

1. **模型怎么换**：整个投递 agent 换成 Opus 5.5（推荐项）。用户先选了"只有开放题用 Opus"，随后改了主意，用户原话："都换成opus吧"。
   - 整个换：一行配置；每个开放题都由看着页面的 Opus 写；总花费约翻倍（每个岗位平均约 $1.07，这批 22 个约 $12 → $24），也更慢一些；填表模块沿用 `apply.model`，一起换。
   - 没选的"只有开放题用 Opus"：填表仍用 Sonnet，开放题交给用 `--agents` 定义的 Opus 子 agent，每题多花约 $0.05–0.10。代价：投递 agent 要多放行 Task 工具，还要另外挡住 Claude Code 自带的子 agent；算不算开放题由 Sonnet 判断，代码管不住，只能事后查记录；当时在开发会话的沙箱里试跑，Claude 登录不了，没能实测。
2. **开放题规则的文字**：照 agent 起草的英文规则（推荐项），规则见 `spec.md` 4.10 第 5 条。替换对所有人都做，不放进 `applicant.yaml`：这几条是通用的好写法，用户没有要求按人配置。
3. **写求职信的子 agent**：照旧用 sonnet（推荐项）。这一批的求职信已经写好，换了也只影响以后的批次。没选的：也换 Opus。
4. **和"Workday 地点"分支的先后**：两边都新建本文件，本分支先合并（推荐项），因为日常会话在等这个改动，才开始这一批自动提交的那一步。那个分支合并前先合入 `main`，把它的两条改成决定 2、3。没选的：等那个分支先合并。

**规则写在** `spec.md` 4.10 第 5、9 条；配置 `apply.model`。填表模块的对应改动记在 `specs/form-fill.decisions.md` 决定 24。

**没有定的**：投递用的 Claude Code 推理强度（`--effort`）不固定，沿用 Claude Code 的默认值。

## 决定 2（2026-10-18）：Workday 岗位存下全部地点，预打标签读懂各公司的写法

原编号 1，合并 main 时改号（`main` 先合入了 2026-10-19 的决定 1）。

**背景**（2026-10-17 只读核对主目录的 `agent.sqlite`，并用 Workday 公开接口抽样核对详情）：

- 用户检查填表模块的报告时发现，Salesforce JR214107（Research Scientist - Salesforce AI Research）在库里的地点只是 "2 Locations"。详情接口的主地点是 "California - Palo Alto"，其他地点是 "California - San Francisco"。用户判定这是导入的问题，另开需求修。
- 原因：ApplyPilot 的 Workday 抓取只存列表页的 `locationsText`，读详情时把 `location`、`additionalLocations` 丢掉了。
- 库里 Workday 岗位 1151 个：
  - "N Locations" 378 个（NVIDIA 247 个）。其中已打标签 341 个，没有展示过的；在投递表上 79 个，待投 75 个。
  - 地点为空 98 个（Thomson Reuters 81 个、Moderna 17 个）。其中已打标签 89 个，展示过 1 个（已 reject），待投 30 个。
- 地点就算存全了，预打标签也读不懂多数 Workday 写法。单地点的岗位里，NVIDIA 295 个只读出 10 个，Intel 112 个读出 0 个，Salesforce 10 个读出 1 个。各公司写法不同："US, CA, Santa Clara"、"California - Palo Alto"、"India, Bengaluru"、"United States of America, McLean, Virginia"、"USA.VA.Reston"、"US FL JAX 347"。

**用户的选择**（2026-10-18，四个选择题）：

1. **已有岗位怎么补**：下次抓取时顺带补（推荐项）。ApplyPilot 本来就给每个搜到的岗位读详情，不加新工具，也不多发请求。
   - 没选的：另做一次性补录（约 476 次请求，抽样时遇到过 403 / 404）；只管新导入。
2. **已打标签的怎么办**：代码只换地点标签。预打标签能把每个地方都读出来时，只替换 location 标签；读不全的退回队列重打。
   - 没选的：全部退回队列重打（推荐项，和第 18 条原文为空时的做法一样，约 430 个）；只改原文、不动标签。
3. **哪些不变**：展示过的，以及投递状态不是待投的（推荐项）。当时停在提交前的有 Adobe "6 Locations"、Salesforce "2 Locations"。起草规格时又补了"修正过标签的"：按第 83 条，这种岗位不能再重打，只能走 misread。
4. **预打标签读哪些写法**：数据里出现的通用写法（推荐项）：地区 - 城市、国家在前，再补一些常见国家名。读不出的照旧交给子 agent。
   - 没选的：只读"地区 - 城市"；不改预打标签。

**规则写在** `spec.md` §10 第 18、19、31 条。

## 决定 3（2026-10-18）：地点读法的样例 A1–A10，以及几个地方的 work_mode

原编号 2，合并 main 时改号。

- 用户请 agent 先给出 10 条地点原文的预期读法，逐条核对后回复"对"。样例见 `tests/fixtures/` 里的地点读法样例（合成）。
- 由此新定一条规则：一条地点里有几个地方、只有一部分写了 Remote 时（样例 A2："US, CA, Santa Clara; US, CA, Remote; US, NY, New York; US, WA, Seattle"），work_mode 不从地点读，交给子 agent。原来的代码只要文字里出现 Remote 就读成 remote，并且算有把握。
- 同时确认样例 A10："San Francisco, CA - New York, NY" 算认不出。原来的代码会只读出 San Francisco，并且算有把握。
- 核对时主目录数据库里没有一条地点写了几个地方，所以新规则只影响以后的 Workday 多地点岗位。

**规则写在** `spec.md` §10 第 31 条。
