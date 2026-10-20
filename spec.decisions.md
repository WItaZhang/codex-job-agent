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

## 决定 4（2026-10-20）：重写地点读法样例 A1–A10

- 决定 3 说样例 A1–A10 已经和用户核对过，放在 `tests/fixtures/` 里，但那个文件从没提交过。原来的开发会话 10-18 起停了，样例只留下决定 3 里写的 A2、A10。
- 2026-10-20 换由新会话接着做本切片。用户原话（在进度会话里）："接管 Workday 地点：在它的 worktree 里开新会话接着做，所有选项选推荐的那个"；10-19 也说过"所有选项选推荐的那个"。
- 这次按 `spec.md` §10 第 31 条列出的写法重新起草 A1–A10（合成），A2、A10 照决定 3，写进 `tests/fixtures/workday_locations.yaml`。按用户 2026-10-19 的授权（全部按推荐项）定下，没有再逐条和用户核对。

| # | 地点原文 | 读出 |
| --- | --- | --- |
| A1 | US, CA, Santa Clara | us.santa_clara |
| A2 | US, CA, Santa Clara; US, CA, Remote; US, NY, New York; US, WA, Seattle | us.santa_clara、us、us.new_york、us.seattle；work_mode 没把握 |
| A3 | California - Palo Alto; California - San Francisco | us.palo_alto、us.san_francisco |
| A4 | India, Bengaluru | india.bengaluru |
| A5 | United States of America, McLean, Virginia | us.mclean |
| A6 | Israel - Tel Aviv | israel.tel_aviv |
| A7 | US, CA | us |
| A8 | USA.VA.Reston | 认不出 |
| A9 | Massachusetts - Cambridge; Digital | 认不出（Digital 不是地点，整个 location 没把握） |
| A10 | San Francisco, CA - New York, NY | 认不出 |

## 决定 5（2026-10-20）：州 - 城市，城市和州同名时也读成城市（已被决定 10 取代）

- 合并前审查（进度会话，4 个视角分头找、每条 3 个 agent 试着反驳，3 票成立）发现："New York - New York" 读成 `location.us`，"District of Columbia - Washington" 也只读出 `location.us`，还算有把握。意图有 `require location.us.new_york` 时，这种岗位在打标签前就被放到一边；刷新地点时，子 agent 原来打的 `us.new_york` 也会被换成 `us`。
- 原因：后一段是州名就只读国家，这条不管前一段是国家还是州都生效了。
- 改成：只有前一段是国家时（"USA - California"）才只读国家；前一段是州或省时，后一段总是城市。这是第 31 条"地区 - 城市"本来的意思，审查没有给别的选项。

**规则写在** `spec.md` §10 第 31 条。

## 决定 6（2026-10-20）：国家在前的三段写法，两段都能当州时只认"缩写加全名"（已被决定 10 取代）

- 合并前审查发现（3 票成立）："United States of America, Washington, District of Columbia" 读成 `us.district_of_columbia`，"USA, New York, NY" 读成 `us.ny`，"US, Washington, DC" 读成 `us.dc`，都算有把握。原因：代码只看第二段是不是州，是就把第三段当城市。
- 审查建议：两段都能当州时一律算没把握，交给子 agent。
- 定下的（按用户 2026-10-19 授权全部按推荐项，由本会话起草推荐项）：
  - **两段一个是缩写、一个是全名：缩写是州或省，全名是城市（推荐项）**。缩写只可能是州，不会是城市名，所以没有歧义。"US, NY, New York"、"USA, New York, NY" 读成 `us.new_york`，"US, Washington, DC" 读成 `us.washington`。
  - 两段都是全名或都是缩写：算认不出，交给子 agent（这一半照审查建议）。
  - 没选的：两段都能当州时一律算没把握。代价：决定 3 里用户核对过的样例 A2 有 "US, NY, New York" 这一段，A2 的 location 会变成没把握；NVIDIA 这种"国家, 州缩写, 城市"的写法里，纽约、华盛顿这类城市都要交给子 agent。

**规则写在** `spec.md` §10 第 31 条。

## 决定 7（2026-10-20）：work_mode 的词全部从地点里去掉；Virtual、Nationwide 这类词算认不出（第 2 款已被决定 11 取代）

- 合并前审查发现（3 票成立）："US - Work From Home" 读成 `us.work_from_home`，"India, Work From Home" 读成 `india.work_from_home`，"United States - In Office" 读成 `us.in_office`，"US - Virtual"、"USA - Nationwide" 也读成城市，都算有把握。原来的代码这几条都算没把握。
- 原因：Work From Home、WFH、In Office 这些词只用来读 work_mode，没有像 Remote 一样从地点里去掉；"地区 - 城市"和"国家在前"两种新写法会把剩下的词当成城市。
- 定下的（按用户 2026-10-19 授权全部按推荐项，即审查的推荐）：
  - 读 work_mode 的词全部从地点里去掉，和 Remote 一样；只按整词去掉，不会把 "Kingston Site" 里的 "on Site" 当成 On-site。"US - Work From Home" 读成 `us`，work_mode 读成 remote。
  - 站在城市位置上、不是地名的词（Virtual、Nationwide、Multiple Locations、Field Based 这类），让整个 location 算没把握。名单写在代码里，新见到的这类词照例交给子 agent，以后在开发流程里补名单。

**规则写在** `spec.md` §10 第 31 条。

## 决定 8（2026-10-20）：去掉 work_mode 的词后留下的空段不算一段

- 合并前审查发现（3 票成立）："United States - Remote - Texas" 去掉 Remote 后变成 "United States - - Texas"，切开时第二段带着 "-"，结果把州读成城市 `us.texas`，算有把握；"USA - Remote - CA" 读成 `us.ca`，"Canada - Remote - ON" 读成 `canada.on`。"只能有两段"那道检查也因此失效。
- 定下的（按用户 2026-10-19 授权全部按推荐项）：空段去掉后再切。"United States - Remote - Texas" 按 "United States - Texas" 读，前一段是国家、后一段是它的州，读成 `us`（决定 5）；"US - Remote - CA - Santa Clara" 去掉空段后仍是三段，算认不出。审查给的另一个选项"算没把握"没选：这几条去掉 Remote 后就是第 31 条已经认的"国家加州"写法。

**规则写在** `spec.md` §10 第 31 条。

## 决定 9（2026-10-20）：读地点之前先规整空白、破折号和带点的缩写；work_mode 词的各种写法

- 复核（进度会话，每条 1 个 agent 重跑证据再试几十种相近写法，新问题各由 3 个 agent 试着反驳）发现，决定 7、8 只修好了原来的证据：
  - 空白没先规整：词中间有两个空格、不换行空格或制表符时，work_mode 词既读不出也去不掉（"US - Work  From Home" 读成 `us.work_from_home`）；"Work-From-Home"、"Remote-First" 也一样（剩下的 "First" 读成城市）。
  - 破折号一侧没空格时去不掉空段："United States - Remote -Texas"、"USA - Remote-CA"、"US - WFH-NY" 读成 `us.texas`、`us.ca`、`us.ny`。
  - 带点的缩写当成城市："US, Washington, D.C." 读成 `us.d_c`。
- 定下的（按用户 2026-10-19 授权全部按推荐项，即复核的推荐修法 (a)(b)）：
  - 解析前先规整：空白（含不换行空格、制表符）合成一个空格，各种破折号统一成 "-"，带点的缩写去掉点。
  - work_mode 词的词和词之间是空格还是连字符都算（work-from-home、in-office），Remote-First 也算 remote。
  - 两边至少一边有空格的 "-" 是分段的破折号；去掉词后留下的空段一律丢掉。决定 8 的规则照旧，只是不再要求破折号两边都有空格。
  - 测试改成组合矩阵（`tests/test_location_matrix.py`）：每种写法乘上空白、破折号和 work_mode 词的变体。

**规则写在** `spec.md` §10 第 31 条。

## 决定 10（2026-10-20）：城市位置用同一条守门规则，所有写法都一样（取代决定 5、6）

- 复核发现（每条 1 个 agent 重跑证据再试相近写法，新问题各由 3 个 agent 试着反驳），决定 5、6 只修好了原来的证据：
  - 决定 5 带进新问题：州后面跟它自己的缩写，缩写被读成城市（"New York - NY" 读成 `us.ny`，"Ontario - ON" 读成 `canada.on`）。刷新地点时，子 agent 原来打对的 `us.new_york` 会被换成 `us.ny`。
  - 决定 6 的理由（缩写只可能是州）没考虑到全名多半也是州："US, GA, Georgia" 读成 `us.georgia`，"Canada, BC, British Columbia" 读成 `canada.british_columbia`。
  - 城市在前的写法是同一类错："NY, New York" 读成 `us.ny`，"California, CA" 读成 `us.california`，"District of Columbia, Washington, United States" 读成 `us.district_of_columbia`。
- 定下的（按用户 2026-10-19 授权全部按推荐项，即复核的推荐修法 (c)）：
  - **一条守门规则用在所有写法的城市位置上**：是州或省（全名、缩写、带点的缩写）、国家、两个字母以内或带数字的、或者不是地名的词，就不当城市。
  - **读不成城市时算没把握，不退到国家**（复核让本会话在两者里选，按"宁可没把握，不能有把握地读错"选了这个）。退到国家看起来没错，但 location 会算有把握：意图有 `require location.us.new_york` 时，预筛会在打标签前把 "New York - NY" 放到一边；刷新地点时也会把子 agent 打的城市标签换成只有国家。
  - **和州或省同名的知名城市用一份短名单作例外**：New York（纽约州）、Washington（DC）、Quebec（魁北克省），只在旁边一段正是它所在的州或省时当城市。这样 "New York, NY"、"US, NY, New York"、"USA, New York, NY"、"Washington, D.C." 这类常见写法仍读得出；"US, WA, Washington"（华盛顿州的 Washington 不是这个城市）算没把握。
  - 这条比决定 5 窄：州后面跟一个和州同名、又不在名单里的词（"Georgia - Georgia"、"California - Washington"），现在算没把握。
  - 国家在前的三段写法，两种分法（第二段是州或第三段是州）都试，只有一种读得通、或者两种读出同一个城市时才算读出。"United States of America, Washington, District of Columbia" 因此读成 `us.washington`（决定 6 原来让它算没把握）。
  - 加拿大三段城市在前的写法（"Toronto, ON, Canada"），中间一段现在也必须是省，和美国一样。
- 测试是组合矩阵：每种写法 × 州写成全名、缩写、带点缩写 × 城市位置上是本州的城市、本州缩写、本州全名、别的州 × 美国和加拿大的各种国家名。

**规则写在** `spec.md` §10 第 31 条。

## 决定 11（2026-10-20）：不是地名的词按整词比；名单外的词仍可能被读成城市（取代决定 7 第 2 款）

- 复核发现：决定 7 写"新见到的这类词照例交给子 agent"，代码实际把名单外的词当成城市，例如 "US - Work at Home"、"US - Teleworking"、"US - Head Office"。名单是整个短语比的，换个说法就漏。
- 定下的（按用户 2026-10-19 授权全部按推荐项，即复核的推荐修法 (d)，由本会话在两种改法里选推荐项）：
  - **名单改成按词比**：城市位置上的一段里只要有一个整词在名单里（virtual、nationwide、locations、home、office、work、field、telework、hq 等），就不当城市，location 算没把握。"Work at Home"、"Head Office"、"Teleworking"、"Remote Eligible" 都因此算没把握；按整词比，"Homestead"、"Fairfield" 这样的地名不受影响。
  - **决定 7 的文字如实改写**：名单外、不是地名的词仍可能被读成城市（用户知情）。做不到"新见到的都交给子 agent"：代码里没有地名库，分不出一个没见过的词是不是地名。
  - 没选的：让代码做到原文写的那样。要接地名库，超出本切片。
  - 名单里有 international，所以 "International Falls, MN" 这类真地名会算没把握，交给子 agent（宁可没把握）。

**规则写在** `spec.md` §10 第 31 条。
