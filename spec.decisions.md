# 总规格：决定记录

规则写在 `spec.md`。这里按日期记录每条决定：谁定的，为什么定。

- 新决定加在后面，不改旧条目。
- 推翻旧决定时，另写一条新决定，注明推翻了哪一条。
- 2026-10-18 以前的决定没有搬过来，仍写在 `spec.md` §10 各条的括号里（"用户 某日 选择"）。
- 用户亲口说的，标"用户原话"。

## 决定 1（2026-10-18）：Workday 岗位存下全部地点，预打标签读懂各公司的写法

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
