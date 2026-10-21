# 门槛交给用户：样例（synthetic）

规则见 `spec.md` 4.10 第 2、5、10 条和 §10 第 97–106 条，决定 22、23；计划见 `gate-handoff.plan.md`。下面的公司、网址、岗位编号、批次、标签页编号和 key 都是编的（synthetic），网址都用 `.test` 结尾。

- G-a 到 G-h 是样例阶段用户自己选的结果（只给情境、不标推荐项，决定 23）。
- 其余的由 agent 按规格推出来，请用户过目。

## 共同的设定

- 配置：`review_first: 5`，`batch_size: 30`，`apply.notify: true`。
- "停在提交前"的公司名单写的是 `Stripe` 和 `Iota`。每个岗位都已经有求职信。
- 批次 `batch-gate01`：

| n | 岗位编号 | 公司 | 职位 | 模式 | 走哪条路 |
| --- | --- | --- | --- | --- | --- |
| 1 | `g_acme` | Acme Robotics | ML Engineer | review | 填表模块 |
| 2 | `g_beta` | Beta Health | Data Scientist | review | 填表模块 |
| 3 | `g_gamma` | Gamma AI | Applied Scientist | review | 填表模块 |
| 4 | `g_delta` | Delta Labs | ML Engineer | review | 填表模块 |
| 5 | `g_eps` | Epsilon Bio | Research Engineer | review | 填表模块 |
| 6 | `g_stripe` | Stripe, Inc. | Applied Scientist | careful | 填表模块 |
| 7 | `g_zeta` | Zeta Pay | Data Scientist | submit | ApplyPilot |
| 8 | `g_eta` | Eta Cloud | ML Engineer | submit | ApplyPilot |
| 9 | `g_iota` | Iota Data | Data Scientist | careful | 填表模块 |
| 10 | `g_kappa` | Kappa Labs | ML Engineer | submit | ApplyPilot |
| 11 | `g_lambda` | Lambda Works | Applied Scientist | submit | ApplyPilot |
| 12 | `g_theta` | Theta Labs | Data Scientist | submit | ApplyPilot |
| 13 | `g_mu` | Mu Systems | Research Engineer | submit | ApplyPilot |

- 投递用的 Chrome 开始时开着两个标签页：`T1`（`https://news.example.test/`，你的）和 `T2`（上一批停在提交前、你还没交的表）。
- 每个样例只用到上表里的几个岗位，各自的经历互不相关。

## 一、遇到门槛

### G1 四种门槛，两条路都记成等你处理

| # | 岗位 | 页面上的情况 | agent 怎么做 | 投递状态 | 原因 | 标签页 |
| --- | --- | --- | --- | --- | --- | --- |
| a | 7 Zeta Pay | 存好的密码登不进去，注册时提示账号已存在 | 停在登录页，`NOTE: careers.zetapay.test: sign in to continue`，`RESULT:FAILED:login_issue` | held | `login_issue` | 留在登录页 |
| b | 8 Eta Cloud | 第一页是 Cloudflare 的"Verify you are human" | 不点，停下，`RESULT:CAPTCHA` | held | `captcha` | 留着 |
| c | 12 Theta Labs | 只能用"Sign in with Google" | 不点，停下，`RESULT:FAILED:sso_required` | held | `sso_required` | 留着 |
| d | 10 Kappa Labs | 注册后要邮箱验证码，Gmail 里两分钟内收到 | 读出验证码填进去，接着填、提交，`RESULT:APPLIED` | applied | — | 关掉 |
| e | 13 Mu Systems | 注册后要邮箱验证码，Gmail 里一直没有 | 停在填验证码那一页，`RESULT:FAILED:email_verification` | held | `email_verification` | 留着 |
| f | 1 Acme Robotics（Workday） | 注册后要邮件验证，Gmail 里有 | 填表 agent 读出验证码，接着填，停在提交前 | waiting | `check_then_submit` | 留着，等你检查 |
| g | 2 Beta Health | 最后一页有 hCaptcha | 停在那一页，`RESULT:CAPTCHA` | held | `captcha` | 留着 |
| h | 3 Gamma AI | 登录、注册都不成 | 停下，`RESULT:FAILED:login_issue` | held | `login_issue`（报告里写"登录或注册失败"） | 留着 |

- 每个 held 的岗位：`apply_holds` 里一行（岗位、批次、步骤、序号、模式、走哪条路、原因、打码后的 NOTE、标签页编号和地址、时间、第 1 次等你、没在接着做），尝试记录一条（状态 held）。
- held 的岗位不在投递清单里，下一批 `prepare_apply_batch` 也不会选它。
- 这一步接着做后面的岗位。
- 填表模块的岗位：报告照旧写"没填完：门槛"，只是投递状态从 failed 改成 held。

### G2 不是门槛的停下，照旧记成没投成

| 页面上的情况 | 结果码 | 投递状态 |
| --- | --- | --- |
| 写着"Mutual Arbitration Agreement"的勾选框 | `arbitration` | failed |
| 要填 SSN | `ssn_required` | failed |
| 要付申请费 | `payment_required` | failed |
| 写明筛选题要本人作答、不许用 AI | `screening_requires_candidate_written_answers` | failed |
| 要开摄像头做人脸验证 | `unsafe_verification` | failed |
| 岗位已下架 | `expired` | failed |

这些不进 `apply_holds`，没有"好了"之后接着做这条路。

### G3 条款和可选的勾选框（G-a）

一个注册页上有 5 个勾选框：

| 勾选框 | 必勾吗 | agent |
| --- | --- | --- |
| I agree to the Terms of Use and Privacy Policy | 必勾 | 勾 |
| I certify that the information provided is accurate | 必勾 | 勾 |
| I authorize a background check | 必勾 | 勾 |
| I agree to receive text messages and marketing emails | 可选 | 不勾 |
| I agree to the Mutual Arbitration Agreement | 必勾 | 不勾，停下：`arbitration`（G2） |

只在"Terms of Use"链接的全文里写了仲裁、页面上没写的，agent 看不到，照样勾第一个（用户知情）。

### G4 指令里没有破解验证码

`data/local/.env` 里写着 `CAPSOLVER_API_KEY=CAP-SYNTHETIC-123`。

- 两条路生成的投递指令里都没有：`CapSolver`、`capsolver`、`CAP-SYNTHETIC-123`、`MANUAL FALLBACK`、`Solve them yourself`、"If found, solve"。
- 指令里有 GATES 一段（永远不解、不绕过验证码和人机检查，不用破解服务；遇到门槛停在那一页、不关标签页、写 NOTE、输出结果码）。
- 投递用的 Claude Code 的环境变量里没有 `CAPSOLVER_API_KEY`。
- `apply.blocked_origins` 里有 `https://api.capsolver.com`。
- 换一个 vendor 版本，CAPTCHA 那一段的写法变了、找不到原文：报错，这个岗位不开始投递、退回待投（`system_error`），网站上什么都没发生。

### G5 记下标签页

7 Zeta Pay 卡在登录页时：

- 开始前 CDP 标签页：`T1`、`T2`。结束后：`T1`、`T2`、`N7`（`https://careers.zetapay.test/login`）。存下 `N7` 和这个地址。
- 网站弹出了登录小窗，结束后多了两个新标签页：按 agent 最后打开的地址认出是哪一个；认不出就编号和地址都不存，接着做时从岗位地址重开。
- 自动提交的附加规则：出结果后关掉这个标签页，停在门槛时除外。上面 G1 的 d 交上了，所以关掉；a、b、c、e 留着。

## 二、提醒和展示

### G6 一步做完的通知（G-b、G-c）

| # | 这一步的结果 | 通知标题 | 通知正文 |
| --- | --- | --- | --- |
| a | 第二步：7（要登录）、8（验证码）、12（单点登录）等你处理，6 Stripe 和 9 Iota 填好等你检查，其余交上了 | 投递：3 个等你处理，2 个填好等你检查 | 2 个要登录、1 个验证码：7 Zeta Pay、8 Eta Cloud、12 Theta Labs |
| b | 第一步：5 个都填好，没有门槛 | 投递：5 个填好等你检查 | 第一步做完 |
| c | 第二步：25 个交上，3 个已下架，没有等你处理的，也没有填好的 | 不发 | — |
| d | 一次接着做：2 个交上，8 又卡在验证码 | 投递：1 个等你处理 | 1 个验证码：8 Eta Cloud |

- 门槛在通知里分三种：要登录（`login_issue`、`sso_required`）、验证码（`captcha`）、邮箱验证码（`email_verification`）。
- 等你处理的超过 5 个时，正文只列前 5 个，后面写"等 8 个"。
- 通知里没有邮箱、电话和网址。对话里按序号列出每个岗位在哪个网站、要做什么。
- c 是 agent 推出来的（什么都不用你做就不发），请确认。

### G7 公司名里有奇怪的字

一个岗位的公司名是 `Acme"; do shell script "say hi` 加一个换行再加 50 个字。

- 通知正文里它是一段普通文字：换行换成空格，截到 20 个字（`Acme"; do shell scri…`）。
- 它作为参数传给 `osascript`，不进脚本，什么命令都不会执行。

### G8 服务重启以后，和 /apply 开头（G-h）

第二步做完，7、8 等你处理。之后工具服务重启了。

- `apply_status()` 返回 `status: idle`，同时有 `held` 一组，从数据库读出：

```text
7 Zeta Pay｜Data Scientist｜要登录｜careers.zetapay.test｜careers.zetapay.test: sign in to continue｜处理完会自动提交
8 Eta Cloud｜ML Engineer｜验证码｜apply.etacloud.test｜...｜处理完会自动提交
```

- 三天后你说"/apply"：先列出这两个还在等你处理，再选新的一批。它们不会自动算失败。

## 三、你来处理

### G9 你说"N 已投""N 不处理了"或改回待投

| 你说 | 投递状态 | 尝试记录（user） | 怎么算 |
| --- | --- | --- | --- |
| "7 已投"（你自己在标签页里交了） | applied | applied | 成功提交 |
| "8 不处理了"（验证码） | skipped | failed，原因 `captcha` | 允许的失败：验证码 |
| "13 不处理了"（邮箱验证码） | skipped | failed，原因 `email_verification` | 允许的失败：登录 |
| 把 3 改回待投 | pending | pending | 不算；回到清单，以后的批次从头再投 |

- 四种都删掉 `apply_holds` 里这一行；标签页你自己关。
- 同一个岗位的其他条一起改（spec §10 第 73 条）。

### G10 你说"好了"（G-f）

等你处理的有 2 Beta Health（填表，验证码，原标签页在）、7 Zeta Pay（自动提交，要登录，原标签页在）、8 Eta Cloud（自动提交，验证码，原标签页被你关了）。你只处理了 2 和 7，说"好了"，没说序号。

- 三个都接着做。`prepare_resume` 的摘要（弹窗里一字不差）：

  `接着做 3 个：自动提交 2 个：7 Zeta Pay（回原标签页）、8 Eta Cloud（从岗位地址重开）；停在提交前 1 个（填表模块从岗位地址重填）：2 Beta Health`

- agent 列出摘要后调用 `resume_applying`，弹一次窗。8 你没处理，会再卡一次（G14）。

## 四、接着做

### G11 自动提交的岗位回原标签页

7 Zeta Pay：你登录以后，`N7` 跳到了 `https://careers.zetapay.test/apply/step2`。

| # | 开始前代码查到的 | 怎么做 |
| --- | --- | --- |
| a | `N7` 还在，只有它开着这个地址 | 另起投递 agent，submit 模式，带标准答案和要求清单；附加规则：不开新标签页、不做第 1 步，列出标签页、选中 `https://careers.zetapay.test/apply/step2` 那一个，先看是否已经交过或显示已申请过，没有才接着填、提交 |
| b | `N7` 不见了（Chrome 重启过），存下的地址只有一个标签页开着 | 同 a，选那一个 |
| c | 一个都找不到 | 新开标签页，从岗位地址开始，先看是否已申请过（你可能已经登录了） |
| d | 有两个标签页开着这个地址 | 这一个这次不接着做，`problems` 写"7 Zeta Pay：同一个地址开着 2 个标签页，关掉多余的再说'好了'"；仍是等你处理 |

a、b、c 交上了（`RESULT:APPLIED`）→ applied，算成功提交，删掉 `apply_holds` 那一行，出结果后关掉标签页。

### G12 原标签页已经交上了（G-e）

你在 `N7` 里登录、填完、点了提交，页面写着"Thank you for applying!"，然后你说的是"好了"。

- 代码先读一眼 `N7`，认出申请已经交上。
- 记成 applied、原因 `submitted_by_user`，算你的成功提交；不起 agent。
- 停在提交前的岗位也一样（例如 2 Beta Health，你在原标签页里自己交了）。

### G13 抽查的和名单公司的岗位

2 Beta Health 接着做：

- 填表模块新开标签页，从岗位地址重填（你刚过了验证码，Chrome 里有了 cookie），停在提交前，出报告 → waiting，原因 `check_then_submit`。
- 报告的"要做什么"多一条：原来等你处理的那个标签页可以关掉。
- 永远不提交，和抽查时一样。

### G14 又卡在门槛（G-g）

8 Eta Cloud：

| 第几次 | 什么时候 | 结果 | 投递状态 |
| --- | --- | --- | --- |
| 1 | 第二步 | 验证码 | held（第 1 次等你），通知 |
| 2 | 第一次接着做 | 提交页又出来一个验证码 | held（第 2 次等你），通知 |
| 3 | 第二次接着做 | 又是验证码 | failed，原因 `captcha`，备注"第三次卡在门槛"；算一次允许的失败（验证码）；删掉 `apply_holds` 那一行 |

### G15 拒绝开始

| 情况 | 结果 |
| --- | --- |
| 弹窗里的摘要和 `prepare_resume` 给的不一样 | 拒绝，什么都不开始 |
| 5 Epsilon Bio 是 waiting，不是 held | 拒绝整批 |
| 第二步还在跑 | 拒绝：等这一步做完 |
| 一次接着做还在跑时说"继续"（第二步） | `start_applying` 拒绝 |
| 一次接着做还在跑时开始填表模块的单独运行 | `start_form_fill` 拒绝 |

### G16 接着做到一半，工具服务停了

接着做 7 时工具服务停了。`apply_holds` 里 7 那一行标着"正在接着做"。

- 重启后 `apply_status` 照样列出 7，写明"上次接着做时中断"。
- 再说"好了"：摘要写成 `7 Zeta Pay（上次接着做时中断，可能已经交过，请先看标签页；回原标签页）`。
- 接着做时代码先读一眼原标签页（G12），agent 也先查是否已交过，所以不会交两次。

## 五、第一步和怎么数

### G17 第一步有等你处理的（G-d）

另一批的第一步，5 个的结果：1 要登录、2 验证码、3 单点登录（都等你处理），4、5 已下架（failed）。

- 第一步结束：这一批不作废，第二步也还不能开始。`apply_status` 写"第一步还在等 1、2、3 处理完"；这时说"继续"，`start_applying` 拒绝。
- 你处理了 2，说"好了"，2 接着做完、填好停在提交前（waiting）。说"继续"：第二步可以开始，1、3 还在等也没关系。
- 另一种：1、2、3 你都说"不处理了"：一个都没成，这一批作废（spec §10 第 68 条）。

### G18 这一周怎么数

一周（10-19 到 10-25）的尝试：

| 岗位 | 尝试 | 算不算 |
| --- | --- | --- |
| 7 | 10-20 held；10-21 接着做交上 | held 不算；交上的算提交 |
| 8 | 10-20 held；10-21 held；10-22 第三次 captcha（failed） | 两次 held 不算；第三次算允许的失败（验证码） |
| 13 | 10-20 held（email_verification）；10-21 你说不处理了 | held 不算；不处理了算允许的失败（登录） |
| 2 | 10-20 held；10-21 接着做 waiting；10-22 你说"2 已投" | held、waiting 不算；你交的算提交 |
| 10 | 10-18（上一周）held；10-23 接着做时原标签页已交上（`submitted_by_user`） | 上一周的 held 不在这一周；交上的算提交 |

`apply_stats` 那一周的 line：

`2026-10-19 – 10-25：提交 3 份，失败 2 份，成功率 60%（3/5）。没达标：要 200 份、80%。失败里允许的 2 份：登录 1、验证码 1。不算进去：停在提交前 1 次、等你处理 5 次。`

- 这个改动之前记下的 captcha、login_issue 失败照旧算允许的失败，不回溯。
