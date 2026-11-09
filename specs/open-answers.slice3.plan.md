# 开放题答案库切片 3 计划：草稿/定稿对（2026-11-06）

规格：`specs/open-answers.md` 3.4、4.3、4.6、4.9；决定记录：`specs/open-answers.decisions.md` 决定 4、6、7；样例：`specs/open-answers.samples.md` 的 P1–P16、`tests/fixtures/open_answers/pairs.yaml`。用户全权委托（总规格决定 25），按规格先行、测试先行直接实施。vendor 文件不改。

## 1. 做什么

1. **记草稿**（`form_fill/checkpoint.py`）：`checkpoint` 工具加可选参数 `open_answers`（栏号加题型，pydantic 模型，题型取 `open_answers.QUESTION_TYPES`）。`Session.checkpoint("filled", …)` 从这次读回里取这些栏的原题和值，打码后写进这次运行的 `fills/<运行>/open_answers.json`；不在这一页、不是文字栏、空的、对到联系方式条目的不记，回复里按栏号说明。`runner.CHECKPOINT_RULES` 加一条说明，题型列表由代码生成。
2. **配成对**（新模块 `src/intent_job_agent/open_answer_pairs.py`）：
   - 模型：对（3.4 的字段）和选择题。
   - 纯逻辑：`classify`（词和标点、`SequenceMatcher`、配置的界线）、在一次读里找这一栏（同一题组、原题和第几次出现，找不到再按原题）、一个标签页结束时定稿怎么定（只认这一次看到的）、选择题和选项、回答怎么记。
   - 存储：`PairStore`，每对一个 JSON，写之前打码，原子替换。
   - `runner.run_job` 在岗位状态是 `filled`、找得到标签页、没有一页写着已经交上时，用 `RunContext.pairs_dir`（工具传入；不传就不配对）把这次的草稿配成对；草稿优先取 agent 退出后重读的那一页。
3. **收进答案库的唯一入口**（`open_answers.add_entry`）：校验现有文件、加上新条目后整体校验（联系方式、打码占位符、预算、id），在 `entries` 末尾追加，写临时文件、重读确认只多了这一条，再替换。
4. **只读的连接**（`form_fill/readback.py`）：
   - `_connected`：连接后给每个浏览器上下文挂一个什么都不做的对话框监听，并把下载交还给 Chrome（填表决定 41）。`_glance`（`tab_state`）用它。
   - `WatchReader`：一次看只连一次（决定 8），在看的线程里用到底。后来出现的上下文也挂监听；页面按 CDP 编号记住；`poll()` 每轮核对一次运行主人；一次读超过 `read_seconds` 就结束这个连接的驱动，下一轮重新连；`disconnect()` 只停驱动，不关浏览器。
5. **同一段只看不动的轮询**（新模块 `form_fill/watch.py`）：`find_tab`（同一个 CDP 编号、同一个 Chrome 实例）和 `wait`（直到被打断、到时限或标签页都结束；标签页不见了算 `closed`；时限可以是 `None`；`each_poll` 每轮先调一次）。`Toolbox._wait_for_user` 改用它，行为不变。
6. **工具服务**（`tools.py`、`store.py`）：
   - 看标签页：`_watch` 状态，`_start_watch`（运行结束后、`open_answer_pairs` 时；自己的协调记录，种类 `form_fill`、编号 `watch-…`；投递 Chrome 没开不看、不启动它），`_end_watch`，开始新运行时 `_watch_paused` 先让路；`_recover` 认得自己的看，`_refuse_other_session` 让出别的会话的看；`stop_applying` 也停下看；`_other_view` 写"读回开放题定稿"。
   - `Store.end_activity(owner, run_id=None)`：给了 `run_id` 就只删这一行。
   - `FormFillRunner.watch_reader()`：给一次看一个 `WatchReader`（每次读最多 `open_answers.watch.read_seconds`）。
   - 两个准备级工具：`open_answer_pairs(read_now=False, job_ids=None)`、`open_answer_choice(question_id, option, user_report, text=None, type=None, renderings=None)`。
   - `apply_status`、`form_fill_status` 有对在等或有选择题时多一个 `open_answers` 计数，提示调 `open_answer_pairs`。
7. **配置**：`open_answers.pairs.{dir, rewritten_above, ask_reason_above}`、`open_answers.watch.{timeout_seconds, poll_seconds, read_seconds}`（`timeout_seconds` 可以写 `null`：不限时）；代码里有同值默认，旧工作区不用改。
8. **说明**：`docs/usage.md` 开放题一节、`/apply` skill 的收尾和恢复两节、MCP 说明、示例答案库的 `from` 写法、总规格 5.1 和 4.10 第 5 条、填表规格 3.1 和 4.2 的指向。

## 2. 不做什么

- 不做冷启动采访、归纳回放、指标（切片 2、4、5）；不做按钮或界面。
- 自动提交的岗位不记草稿。
- 读回只读：不点、不打字、不跳转、不提交、不碰验证码、不动对话框；不读这个岗位的网站以外的页面；不启动投递 Chrome。
- 不让模型改写定稿；不截断；不替申请人选原因、不替他决定收不收。
- 不读任何 `profile.json` 或 `.env`（测试只用合成的）；不启动任何真实网站的填表或投递；不按网站写特例。

## 3. 测试（先写，先失败）

`tests/test_open_answer_pairs.py`，数据都是合成的：

| 测试 | 样例 | 看什么 |
| --- | --- | --- |
| 分类 | P1–P3 | 原样用 / 改过 / 重写按配置的界线；空定稿算重写；结果确定 |
| 记草稿 | P9 | `checkpoint` 的参数 schema 有 `open_answers`，题型是封闭集合；filled 时记下读回的原题和值（打码）；不收的按栏号说明 |
| 配成对 | P1、P14 | 填好的岗位每道开放题一个对，草稿取退出后的重读；门槛、读回失败、写着已交上、自动提交的没有对；不传 `pairs_dir` 不配对 |
| 读回 | P4–P8 | 交上、关掉、离开、改完了各自的定稿；只认这一次看到的；离开以后那个页面不读；只列标签页和读，不点不关 |
| 一个连接 | — | 一次看只连一次，每轮、每个标签页都不重新连；读卡住后下一轮重新连；让路以后再看重新连；时限写 `null` 时一直看到标签页结束 |
| 选择题 | P10–P13 | 选项编号和文字；只在有 `user_report` 时记；"其他（我自己说）"和"我自己写"逐字记；跳过不带信息 |
| 收进答案库 | P11–P13 | 追加一条、原有内容和注释不变、`from` 的写法；联系方式、占位符、预算都收不进、不截断；没配置答案库不问 |
| 打码 | P15 | 对的文件扫不出联系方式；工具返回没有草稿和定稿；数据库里没有 |
| 让路 | P16 | 新运行开始前先停下看；别的会话的看让出来；"停下"也停；在场重跑的等待照旧 |
| 工具 | — | 两个新工具是准备级、不在定时任务里；提交级不变 |
| 真实浏览器（有 Chromium 时） | — | 合成页面、临时资料：`WatchReader` 读得到文本框的值、两次读只连一次；页面开着 alert 时读到时限失败、下一轮重新连；连着的时候下载落在资料的下载目录；确认框留给页面自己的处理者；读一眼（`_connected`）也一样 |

已有的 `tests/test_gate_list.py` 不改，验证在场重跑的等待行为不变。

## 4. 验证

CLAUDE.md "验证" 的四条命令全部通过；`git merge main` 后再跑一遍。

要人在场看一次的（不进 CI）：

- 真实网站上，填表 agent 会不会照指令报 `open_answers`。
- 看标签页时，申请人改字、点提交顺不顺。协调者已经量过负担（决定 8）：一个连接看 6 个标签页约多用一个核的 10%，被看页面每 150 秒有 0–3 个超过 50 毫秒的长任务（最长 119 毫秒），还要在真实使用里确认感觉不到。
- 交上以后的页面认不认得出来；跳到别的网站的确认页算"离开"，要看一下实际是不是这样。
- 交上之前最后不到 5 秒改的字会不会漏掉。
- 多页表单（Workday）前面页上的开放题；富文本编辑器里的答案。
- 收进作者真实的答案库文件。
- 两个真实的工具服务之间，看是不是真的让路。
- 用户在 30 分钟和不限时之间选定以后，改 `open_answers.watch.timeout_seconds` 的默认值。（2026-11-08 已改成不限时，决定 9。）
