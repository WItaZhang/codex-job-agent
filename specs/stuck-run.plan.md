# 切片计划：一步卡住不结束（2026-10-21）

- 规格：`spec.md` 4.10 第 11 条，§5.1，§8，§9，§10 第 107–115 条；`specs/form-fill.md` 3.2、4.2、4.3、第 9 节。
- 决定：`spec.decisions.md` 决定 24；`specs/form-fill.decisions.md` 决定 35。
- 样例：`specs/stuck-run.samples.md`（S1–S25，synthetic）。
- 分支 `claude/stuck-run-1a7df5`，从 main 5be6f53 出发。
- **为什么做**：2026-10-20 第一次在场试跑，运行 20261020-183730-e7c8 的第 1 个岗位：`apply_status` 一直说 running，填表 agent 早已退出，只能重启工具服务；重启后这一批还没开始的第二步（25 个岗位）也丢了，只能重新生成批次。一个岗位卡住就挡住整批，挡住 §9 的每周 200 份。
- **冻结**：只修挡住 B 达标的问题（spec §1.1）。填表模块只改读回的上限、报告里的两个原因词和配置；不碰冻结的 2b、2c、6.1。
- **授权**：用户 2026-10-21 夜里："后面还有什么要开发测试的自己跑吧，我要睡觉了"。下面的选择题都按推荐项定（第 0 节），会推翻以前的决定、放宽隐私或批准规则、改变会不会提交的，列在第 6 节"待你决定"。

## 卡在哪里（核实过的）

- 读输出的循环 `for line in proc.stdout` 要等所有拿着管道写端的进程都关掉才结束，`proc.wait(timeout=60)` 排在它后面；900 秒的计时器只杀 agent 的进程组。
- 在本机用真的 `claude`（2.1.306）加一个假的 MCP 服务试过（离线，没有访问网站）：
  - MCP 服务和它的子进程都在 agent 的进程组里，计时器杀进程组时一起被杀；
  - MCP 服务的 stderr 是 Claude Code 自己开的，不是我们的输出管道；
  - agent 退出时管道马上读到结尾。
  - 所以"MCP 的子孙进程拿着管道"在现在的 Claude Code 上不一定成立；换了版本、或者有别的进程继承了 agent 的输出，就会卡住。
- 另一处没有上限：agent 退出后，代码用 Playwright 读回标签页（`readback.read_final_page`，接着做前的 `tab_state` 也一样），`pool.submit(...).result()` 不设时限，`frame.evaluate` 在页面不回应时（例如留着一个对话框）会一直等。这时这一步是 running，agent 已经不在，只剩读页的 Playwright 驱动，和那次看到的一样（agent 已退出、一个 18:37 启动的 Playwright 驱动还活着、没有 meta.json 和 readback）。
- 不读运行记录（项目设置禁止），所以两处都修，不赌是哪一处。
- 另外两处会让"进行中"留下：一步的后台线程碰到 `Exception` 以外的异常时状态不改；工具服务重启后，数据库里批次的一步一直是 running，第二步开始不了（门槛切片的计划第 4 节列为"原有问题"）。

## 0. 采访（按推荐项定）

每题第一个是推荐项，也是选定的；后面是没选的。

1. **一步怎么保证结束**：每一处等待都设上限：agent 退出后最多再读 10 秒剩下的输出；多久没有输出算卡住；总时限不变；结束时杀掉 agent 的整棵进程树（进程组，加上运行中记下的、换了进程组或父进程已退出的子孙进程）；读一次标签页最多 120 秒，超时结束这次读页的 Playwright 驱动。
   - 没选：再套一层"每个岗位总时限"，超时就丢下这个岗位的线程接着做。丢下的线程可能还连着 Chrome（读回的 Playwright 连接没有对话框处理器，会替页面关掉对话框），之后还会写报告文件。
   - 没选：只把超时调小。
2. **多久没有输出算卡住**：300 秒，写在配置。Opus 一轮长思考加浏览器等待一般在 1–3 分钟；在 Gmail 里等验证码时每次搜索都有输出。没选：120 秒；600 秒。
3. **卡住记成什么**：failed，结果码 `stalled`；一次工具调用都没有的退回待投（`system_error`，第 67 条）。填表报告写"没填完：卡住"。
   - 不用 `stuck`：`stuck` 已经是填表 agent 自己的结果码（checkpoint 让它停：同一页填了 3 次换不了页）。分开记，`apply_stats` 才看得出是哪一种要修。
   - 没选：退回待投重试；记成 `timeout`（算允许的失败）。
4. **停下怎么停**：准备级 `stop_applying()`（它只会少做，不提交，不弹窗）。没开始的不做了；正在填、停在提交前的（填表模块）马上结束，回到原来的状态，不写尝试记录；正在自动提交的做完再停，免得打断提交、说不清交没交。
   - 没选：正在做的一律马上结束；只在两个岗位之间停。
   - 没选：做成提交级（每次弹窗）。
5. **停下的第一步算什么**：和做完的第一步一样：有成功的就能"继续"第二步，一个都没成（也没有等你处理的）就作废；没做的留在待投。没选：一律作废（要重新生成批次，你又要多查 5 份）。
6. **`apply_status` 给什么时间**：每个岗位的开始、结束和最后活动时间；正在做的还有多少秒没动静、agent 在不在。没选：只给正在做的岗位。
7. **重启后**：新表 `apply_running` 记下哪个工具服务（进程编号和启动时间）在跑哪一批哪一步、正在做哪个岗位。下次调用时，那个工具服务已经不在了，就：结束它留下的 agent；正在做的自动提交岗位可能已经交了，记成等你处理（`interrupted`）；停在提交前的留在待投；这一步按数据库里的结果收尾；`apply_status` 在没有在跑的一步时也给出最近一批的下一步。
   - 没选：正在做的一律退回待投（自动提交的可能重复提交）。
   - 没选：只解开"进行中"，不管正在做的岗位。
   - 另一个会话的工具服务还活着时一律不碰（S16）：几个会话共用一个数据库，"这个进程里没有在跑"不等于"中断了"。
8. **察觉有人在用投递 Chrome**：agent 运行时每 3 秒用 CDP 的 `Target.getTargets` 看一眼标签页（带 openerId），岗位结束时对照 agent 自己的操作，五种变化写进报告（S18–S22），只报告。
   - 没选：发现就停下这个岗位（会改变投递结果，留给你定）。
   - 没选：只在岗位结束时比一次（看不到中途开了又关的）。
   - 用 CDP 的 websocket 而不是 `/json/list`：后者没有 openerId，网站自己弹出的标签页会被当成有人在用。
9. **提醒**：`start_applying`、`resume_applying` 返回一句 `reminder`，/apply skill 开始一步前说一次。

## 1. 设计

### 1.1 新模块 `watchdog.py`（不点、不打字、不跳转）

- **进程表**：`processes()` 跑一次 `ps -A -ww -o pid=,ppid=,pgid=,lstart=,command=`，10 秒超时。`lstart` 是启动时间：进程编号加启动时间才认得出同一个进程（编号会被重用）。`descendants(pid, table)` 找子孙进程。
- **`Tree(pid)`**：agent 的进程。
  - `scan()` 记下 agent 和它此刻的全部子孙进程（编号 → 启动时间）。
  - `kill()`：进程组还在时 SIGKILL 整个组；再 SIGKILL 记下的、编号和启动时间都对得上的进程，以及它们此刻的子孙进程。返回杀了哪些。
- **`Live`（全局一个 `LIVE`）**：这个进程里正在跑的 agent（只有一个投递 Chrome，所以同一时刻只有一个）：岗位、模式、开始时间、最后一次输出的时间、退出了没有，和一个停止信号。
  - `run_application` 开始时 `begin`、每读到一行 `touch`、结束时 `end`（留下最后一次的岗位和时间，读回阶段 `apply_status` 用）。
  - `stop(modes)`：正在跑的 agent 的模式在 `modes` 里时发停止信号，返回岗位编号。
- **`follow(proc, text, on_line, …)`**：
  - 指令由旁边的线程写进 agent 的输入（写不进去就算了）。
  - 主循环用 `select` 每次最多等 `interval`，读到的字节按行交给 `on_line`。
  - 结束的条件：读到结尾；agent 退出后过了 `drain_seconds`；总时限（`timeout`）；`stall_seconds` 没有新输出（`stalled`）；收到停止信号（`stopped`）。
  - 每隔 `interval` 调一次 `ticks`（`Tree.scan`、`ChromeWatch.check`）。
  - 返回为什么停（None 表示 agent 自己结束）。
- **`ChromeWatch(port, blocked_origins)`**：
  - `check()`：用 `cdp_targets(port)` 列一次标签页。第一次（agent 起来之前）记下开始前就开着的。连不上时记下第一次连不上的时间，连上了再记一次。
  - `report(events)`：纯函数式，按 1.7 的五条给出中文的几行。
- **`cdp_targets(port)`**：读 `/json/version` 的浏览器 websocket 地址，用一个最小的 RFC 6455 客户端发一次 `Target.getTargets`，读回答（处理分片），3 秒超时。不带 Origin 头。
- **`kill_new_drivers(before)`**：本进程在 `before` 以后启动的、命令行带 `run-driver` 的子进程（Playwright 驱动），连同它们的子孙进程一起结束。
- **`orphan_agents(paths)`**：父进程是 1、命令行里有 `--mcp-config <这几个路径之一>` 的进程。

### 1.2 `apply_bridge.run_application`

- `Popen` 用二进制；`follow` 读输出；每行先打码再写进运行记录，写完就 `flush`。
- 停止的原因到结果：`timeout` → `failed:timeout`；`stalled` → `failed:stalled`；`stopped` → `ApplyOutcome("pending", "stopped", ["你停下了这一步：这个岗位没有做完，网站上没有提交任何东西"])`。
- `SYSTEM_RESULTS` 加上 `failed:stalled`：一次工具调用都没有的卡住也退回待投。
- 不管怎么结束，`finally` 里 `Tree.kill()`，再 `proc.wait(10)`，关掉管道，`LIVE.end`。
- 卡住时备注："投递 agent {n} 秒没有任何输出，算卡住，已结束它"。
- `ApplyOutcome` 加 `chrome: list[str]`（`ChromeWatch.report` 的几行）。
- 读输出时也看 `tool_result` 里的 "Page URL:"，给 `ChromeWatch.report` 认 agent 去过的网站。
- `ChromeRunner`、`FormFillRunner` 不变（端口照旧从它们来）。

### 1.3 读标签页有上限（`readback.py`）

- `_bounded(read, seconds)`：在自己的线程里读，`result(timeout=seconds)`，超时就 `kill_new_drivers`，抛 `ReadbackError("读标签页 N 秒没有回应")`，线程池 `shutdown(wait=False)`。
- `read_final_page`、`tab_state` 多一个参数 `seconds`；`FormFillRunner.read`、`tab_state` 传 `apply.watchdog.read_seconds`。
- 调用的地方照旧：`_after_fill` 记 `readback_failed`（第 89 条照旧映射），`_resume_one` 当作没读到。

### 1.4 停下（`tools.stop_applying`，准备级）

- 没有在跑的一步或接着做：`{"stopped": False, "message": "现在没有在跑的一步"}`，什么都不改。
- 有：`run["stop"]` 记下时间；`LIVE` 里正在跑的岗位是这一步当前的岗位、模式是 stop 时，发停止信号。返回：正在做的岗位、它是不是马上结束、要等的岗位（自动提交的）。
- 一步的线程：每个岗位开始前看 `run["stop"]`，有就把剩下的记成 `stopped`。一个岗位的结果是 `pending`、原因 `stopped` 时不写数据库，状态 `stopped`；接着做的去掉"正在接着做"的标记。
- 线程结束时状态是 `stopped`（不是 `done`）；`_sync_apply_results` 把 `stopped` 当作做完收尾（1.6 的规则）。
- 填表报告：`report.REASONS` 加 `stopped`："你停下了"，`ADVICE` 加一句"它已退回待投，标签页里可能有填了一半的表"；`AGENT_REASONS` 加 `stalled` → `stuck`。

### 1.5 最后活动时间（`apply_status`）

- 一步的线程在每个岗位开始、结束时记 `started`、`finished`。
- 每一行加 `started`、`finished`、`last_activity`；正在做的一行加 `quiet_seconds`、`agent_alive`（从 `LIVE` 读，岗位对得上才用）。
- 整步加 `stopping`。时间是本机时间，到秒。

### 1.6 谁在跑、重启以后（`store`、`tools`）

- 新表：

  ```sql
  CREATE TABLE IF NOT EXISTS apply_running (id INTEGER PRIMARY KEY CHECK (id = 1), owner_pid INTEGER NOT NULL,
      owner_started TEXT NOT NULL, kind TEXT NOT NULL, batch_id TEXT, phase TEXT, job_id TEXT, mode TEXT, via TEXT,
      since TEXT NOT NULL)
  ```

  - `kind` 是 `step` 或 `resume`。最多一行：同一个数据库同一时刻只有一个投递活动。
  - `Store.begin_activity(row, batch=None)`：在同一个事务里写这一行，`batch` 给出时把批次的这一步改成 running。已经有一行时报错。
  - `Store.activity_job(job_id, mode, via)`：一步的线程每个岗位开始前写。
  - `Store.end_activity()`：`_sync_apply_results` 收尾时删掉。
- `watchdog.me()`：本工具服务的进程编号和启动时间（`ps -o lstart= -p`）。`watchdog.alive(pid, started)`。
- `_recover()`，在 `apply_status`、`prepare_apply_batch`、`start_applying`、`prepare_resume`、`resume_applying` 开头调用：
  - 有 `apply_running`，它的工具服务还活着：不动（是自己的就由 `_apply_run` 管；是别的会话的，`apply_status` 写 `other_session`，`start_applying`、`resume_applying` 拒绝）。
  - 有 `apply_running`，它的工具服务不在了：结束 `orphan_agents`；`kind` 是 `step` 时按下一条收尾；删掉这一行。
  - 批次的一步是 running、没有 `apply_running`（本切片以前留下的，或者刚收尾就被重启），也不是本进程的 `_apply_run`：同样收尾。
- 收尾一步（`_close_interrupted(batch_id, phase, row)`）：
  - 正在做的岗位还是待投时：模式 `submit` → held，原因 `interrupted`，备注"工具服务在这个岗位做到一半时停了，agent 可能已经交过：请先在投递用的 Chrome 里看它交上了没有"，写尝试记录（held、`interrupted`），hold 的标签页为空；其他模式 → 留在待投，写尝试记录（pending、`interrupted`）。
  - 从这一批这一步的尝试记录算出：成功过没有（waiting / applied）、哪些还是 held、有没有 `maybe_submitted`。
  - 用 `apply_fill.close_step` 收尾（和 `_sync_apply_results` 共用的纯函数）：这一步 done；第一步一个都没成：有 held → `review_holds`，没有 → 作废；`maybe_submitted` → 作废。
  - 批次里记 `interrupted`：步骤、岗位、时间，`apply_status` 的 `notice` 用。
- 一步的线程碰到任何异常（包括 `Exception` 以外的）都把状态改成 failed，不留下 running。
- `apply_status` 在没有在跑的一步时（`idle`）：`next` 是最近一批的下一步，只在它这一步的岗位都还是待投时给；`notice` 写上次停在哪里。

### 1.7 有人在用投递 Chrome

- `ChromeWatch.report(events)` 的五条（只写主机名；调用方再打码）：
  1. 连不上：`投递用的 Chrome 从 HH:MM:SS 起连不上了（被关掉、崩溃，或调试端口没有响应）`，又连上了加一句。
  2. 多出的新标签页：这次运行新开的、没有 opener、打开过网页（不只是空白页）的标签页个数，减去 agent 的 `browser_tabs`（`new`）和 `browser_tab_new` 次数，大于 0 时：`运行中多了 N 个不是 agent 开的新标签页（主机名）`。主机名取 agent 没去过的那几个。
  3. 被关掉的：同样的标签页里不见了的个数，减去 agent 的关标签页次数（`browser_tabs` 的 `close`、`browser_tab_close`、`browser_close`），大于 0 时：`这次运行开的标签页有 N 个被关掉了，不是 agent 关的`。
  4. 开始前就开着的：被关掉的、换了地址的个数和主机名。接着做时 agent 回去用的原标签页（`resume_tab`）不算。
  5. 打不开的网站：这次运行新开的标签页（有没有 opener 都算）去过 `apply.blocked_origins` 或学校系统（`edu`）的网站。
- 两条路都在 `ApplyOutcome.chrome` 里带回来：
  - 自动提交：`_masked` 打码后放进备注和 `apply_status` 每行的 `chrome_use`。
  - 填表模块：`run_job` 打码后放进备注（报告、`meta.json`）和 `JobOutcome.chrome_use`，再到 `_FormFilled.chrome_use`。
- `apply_status` 整步：有任何一行有 `chrome_use`，加 `chrome_warning`："可能有人在用投递用的 Chrome：第 N 个岗位运行时……。一步在跑时请不要用这个 Chrome；要用就先说'停下'。"

### 1.8 配置、工具清单、说明

- 配置 `apply.watchdog`：`stall_seconds: 300`、`drain_seconds: 10`、`read_seconds: 120`、`interval_seconds: 3`（`config.py` 加 `ApplyWatchdog`）。
- `stop_applying` 是准备级：不进 `.claude/settings.json` 的 ask、`.codex/config.toml`，也不在定时任务的工具里。
- `start_applying`、`resume_applying` 的返回加 `reminder`。
- `mcp_server.py` 的说明（Applying 一段）、`/apply` skill（开始前的提醒、停下、时间、`chrome_warning`、重启后的 `next` 和 `interrupted`）、`README.md` 的风险一节。
- `apply_fill.GATE_TEXT` 加 `interrupted`；`_resume_plan` 的 `interrupted` 也看原因是不是 `interrupted`。

## 2. 测试先行

新文件 `tests/test_stuck_run.py`。真的子进程只用假的 `claude`（Python 脚本，放在临时目录的 PATH 里）和 `sleep` 一类的假进程；不连 Chrome，不访问网站。时长用测试自己的配置（几百毫秒到几秒）。

| 样例 | 测试 |
| --- | --- |
| S1 | 假 agent 起一个 `setsid` 的孙进程拿着输出，写完结果退出：`run_application` 在几秒内返回、结果照读，孙进程被杀 |
| S2 | 假 agent 起孙进程后不再输出：`stalled`；有工具调用的 failed，没有的退回待投；两个进程都被杀；备注写秒数 |
| S3 | 假 agent 一直输出：`timeout` |
| S4 | 假 agent 不读输入、指令比管道缓冲大：照样在时限内结束 |
| S2、S5 填表报告 | `stalled` 和 `stopped` 在报告里是"卡住""你停下了" |
| S5 | `read_final_page`、`tab_state` 里换掉的读页函数挂住，并起一个带 `run-driver` 的假驱动：到时限抛 `ReadbackError`，假驱动被杀 |
| S6–S10 | 一步在后台跑，假的 runner 在第 3 个岗位等停止信号：`stop_applying` 之后第 3 个回到待投、没有尝试记录，4、5 待投，状态 `stopped`，第二步的摘要出来；自动提交的做完才停；第一个就停下时作废；接着做时停下仍是 held、去掉标记；没在跑时什么都不改 |
| S11 | `apply_status` 每行有 `started`、`last_activity`，正在做的有 `quiet_seconds`、`agent_alive` |
| S12–S14、S17 | 往数据库写一个"进行中"的一步和一行 `apply_running`（工具服务的进程编号是一个已经结束的进程），新的 `Toolbox` 调用 `apply_status`：按 S12、S13 收尾，`next` 是第二步，`notice` 写上次停在哪里，`interrupted` 的 held 能"已投"、"不处理了"、接着做 |
| S15 | 假的"留下的 agent"（父进程是 1 的脚本、命令行带本数据目录的 MCP 配置）被杀；别的数据目录的不碰 |
| S16 | `apply_running` 的工具服务是一个活着的进程：`apply_status` 写 `other_session`，不收尾；`start_applying`、`resume_applying` 拒绝 |
| S18–S23 | `ChromeWatch.report` 的纯函数测试；`cdp_targets` 连一个假的本地 CDP 服务（HTTP 加 websocket，回答分两片）；`run_application` 在假 CDP 上跑一遍，`ApplyOutcome.chrome` 有那一行 |
| S24 | 两个开始工具的返回有 `reminder` |
| S25 | 尝试记录 → `week_stats`：`stalled`、`interrupted` 在要修的里，held、pending 不算 |
| 工具清单 | `stop_applying` 是准备级，不在定时任务里，不在 ask 和 Codex 的确认里 |

要改的旧测试：`run_application` 不再用文本模式的 `Popen`（`test_slice6.py`、`test_gate_handoff.py` 里换掉 `run_application` 的几处不受影响）；`_sync_apply_results` 收尾时删 `apply_running`。

## 3. 步骤（每一步测试先行，通过后提交）

1. **watchdog 和 agent 的运行**：`watchdog.py`（进程表、`Tree`、`Live`、`follow`）、`run_application`、配置、`SYSTEM_RESULTS`、报告的两个原因词。测试 S1–S4。
2. **读标签页有上限**：`_bounded`、`kill_new_drivers`、两个读页函数和 `FormFillRunner`。测试 S5。
3. **停下和时间**：`stop_applying`、一步和接着做的线程、`apply_status` 的时间。测试 S6–S11。
4. **重启以后**：`apply_running`、`_recover`、`close_step`、`interrupted`、空闲时的 `next`、别的会话。测试 S12–S17、S25。
5. **有人在用 Chrome**：`cdp_targets`、`ChromeWatch`、两条路带回来、`chrome_warning`、`reminder`。测试 S18–S24。
6. **说明**：`mcp_server` 说明、skill、README，工具清单测试。
7. **验证**：CLAUDE.md 的四条命令；对照隐私条款自查（`chrome_use`、`apply_running`、备注里只有主机名，没有联系方式）；请项目进度管理会话做合并前的独立审查。

## 4. 不做（留给以后的会话）

- 单独运行的填表模块（`start_form_fill`）也写 `apply_running`、也能停下：模块冻结中，`/apply` 不经过它。
- 读回的 Playwright 连接没有对话框处理器，Playwright 会替页面关掉连上以后弹出的对话框（`checkpoint` 服务注册了空的处理器，读回没有）。要改读回，离冻结的 2b、2c 近。
- 准确认出"被 agent 以外的操作带离"：agent 下一次看页面就会去到新地址，分不清是谁带走的；现在只认出去了 agent 打不开的网站（S22）。
- 发现有人在用就停下这个岗位（第 6 节）。
- 停下以后自动接着做没做完的岗位。
- 停在提交前的岗位在中断后回原标签页接着填（门槛切片的第二刀）。
- 把卡住的岗位自动重跑。

## 5. 风险

- 卡住的阈值太短会误杀长时间思考的 agent：300 秒写在配置里，可以调大。
- 杀进程靠 `ps`：进程编号加启动时间（精确到秒）才认作同一个进程。同一秒里编号被重用、又恰好是 agent 的子孙进程，概率可以忽略。
- 多出的新标签页按"没有 opener"认：Chrome 自己开的页面（更新说明、崩溃恢复）也会被报成"可能有人在用"。
- 只看到标签页的增减和地址，看不到有人在页面里点、打字。
- 合并后，主目录里每个开着的会话都要重启一次工具服务，新代码才生效；还在用旧代码的工具服务跑的一步，会被新代码当成本切片以前留下的"进行中"收尾。

## 6. 待你决定（代码先按括号里的做）

1. **卡住（`stalled`）算哪一类失败**（先算要修的；`timeout` 照旧算允许的"网络超时"）。
2. **工具服务中途停了、正在自动提交的岗位**：记成等你处理（`interrupted`），而不是退回待投。这把"等你处理"从四种门槛扩大到"可能已经交过、要你看一眼"（决定 22 只定了门槛）。你说"不处理了"时算要修的失败（先这样）。
3. **重启后结束上一个工具服务留下的 agent**（先结束）：它可能正在点提交，结束它会打断；不结束，它可能接着交了、结果却记不下来，还可能和下一步的 agent 同时用一个 Chrome。
4. **发现有人在用投递 Chrome 时**（先只报告，不停下这个岗位）。
5. **停下时正在自动提交的岗位**（先等它做完；要不要也马上结束）。

## 7. 完成的标准

- 第 2 节的测试全部通过，CLAUDE.md 的四条验证命令全部通过。
- 项目进度管理会话的合并前审查确认的问题都修了，复核通过。
- 然后告诉用户：到主目录的会话里输入 `/merge claude/stuck-run-1a7df5`。合并后在下一次 /apply 里看：一步做完不再停在 running；说"停下"能停；`apply_status` 有时间。
