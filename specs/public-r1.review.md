# R1 交审记录（决定28）

## 产品行为与范围

R1 提供源码外初始化、包内运行资源、按用户工作区隔离的本地读取和制品回归。它是完整公众产品的第一片，不是公众可用结论或最终 CLI 产品。空目录不植入档案、意图、授权或密钥；真实抓取/模型/浏览器/邮件/投递和业务写入在新 workspace 关闭。显式普通 legacy 开发配置维持原语义，不能指向标记 workspace 解锁。

作者：本 worktree 唯一持锁开发会话；没有改共享 owner、没有复制私人数据、没有真实业务访问、没有启动重复 agent、没有推送/合并。独立早审 `/tmp/public-r1-boundary-review-draft.md` 是问题输入，不是最终批准。协调者安排针对交审 HEAD 的 Claude 复核。

## 入口、拒绝点与 sink 证据

| 入口 | 拒绝点/本地行为 | 验证 |
| --- | --- | --- |
| discover_jobs / fetch_boards / check_board | `_tool` 在解释参数、创建线程、检查个人资料之前拒绝；公开岗位网络也关闭 | direct calls、wait=True/False，Thread.start/httpx/Popen spy 0；真实 MCP full/scheduled 返回 local-only 错误 |
| start_form_fill / start_applying / resume_applying / prepare_resume / stop_applying | 同一装饰器；后台 `_recover` 也独立检查 Settings | 有完整合成 applicant 的调用仍明确拒绝，不拿缺资料错误代替权限测试；kill/CDP/通知 spy 0 |
| save_cover_letter | prepare 入口拒绝，达不到 PDF renderer | 浏览器/进程 spy 0；原始无 Settings renderer 不是 Python 沙箱 |
| 其余非本地工具 | 默认拒绝集合以外的工具，含本地业务 mutation、名单解除 | 注册清单 47 个，其中 31 个直接调用在函数体前拒绝；不解除冻结 |
| apply_status / setup 读 | 不恢复、不 kill、不 CDP、不加载 env；setup 只读元数据并 stat 文件 | 旧 activity 字节值不变；os.environ 不变；有合成 .env 的测试；程序探测与 profile/env 内容读取 spy 0 |
| 其余本地读 | 只使用 Store/合成资料；空词表返回 InvariantError | 独立早审 P2 的 16 读入口 sink 0；新增空词表/缺资料回归；下述快照例外 |
| applypilot_bridge configure / run_sources / sync | Settings require_external，另校验实际 data_dir/store.path | local-only direct adapter 拒绝；legacy 指向 marked data override 拒绝 |
| apply_bridge configure / ensure_chrome / run_application / ChromeRunner call及_tabs | Settings policy + 实际 data/profile 路径检查（适用时） | Popen/CDP 入口前拒绝，legacy data override 回归 |
| FormFillRunner ensure_chrome / targets / open_tabs / fill / read / tab_state；run_job | Settings policy；run_job 在 mkdir 前拒绝 | 六方法参数化；run_job 不生成输出目录 |
| 真实模型 | make_client 非 fake 拒绝；local Engine 仅接受 FakeClient/NoModelClient | 不创建真实 provider、不读密钥、不外发；fake仅测试种数据 |
| Chrome/npx/Gmail | 通过上述申请/填表/发现适配路径才能从公开产品入口到达 | Thread/Popen/subprocess/httpx/CDP sink 0；未启动子agent，邮件工具不能到达 |
| Settings.load / Store / Toolbox / require_external | legacy 的 data_dir、db_path、实际覆盖路径逐一解析并检查所有祖先 marker | 外部配置 P4 等价红→绿，嵌套/符号链接/两个字段；普通 legacy 同进程仍能走合成后端 |
| form_fill report | 共享 resolver，仅离线合成报告；明确 --data-dir 优先 | 实际制品离线报告；参数优先级、资料越界回归 |

31 个拒绝工具完整清单：backfill_tags, boards_status, build_apply_list, check_board, choose_cause, correct_tags, decide, decline, discover_jobs, discovery_status, feedback, fetch_boards, import_jobs, initialize_intent, list_backfill_jobs, list_untagged_jobs, mark_applications, prepare_apply_batch, prepare_resume, propose_intent_edit, rank_causes, record_labels, refresh, release_careful_companies, resume_applying, save_cover_letter, select_today, start_applying, start_form_fill, stop_applying, submit_job_tags。

16 个本地读完整清单：apply_stats, apply_status, check_cover_letter, form_fill_status, get_analysis, get_apply_setup, get_cover_letter_file, get_cover_letter_materials, get_discovery_setup, get_fetch_status, get_intent, get_resume, get_tagging_rules, get_today, list_open_analyses, show_job。

读 applicant 时保留决定25例外：追加实际名单保护快照。测试断言只有一条 careful_companies_snapshot，intent_versions/labels/applications/proposals 均为空；不创建授权/反馈。Store 的本地数据库初始化/schema 属存储基础操作。原始 vendor、readback、watchdog、notify、renderer 等无 Settings 的通用函数不是任意 Python 防沙箱；纯命令/配置构造函数不执行外部进程。完全控制文件/任意 Python 的宿主不在此边界保证内。

## 红测、制品与资源证据

- 首批16红；显式不存在/非canonical config、config及marker symlink、离线资料越界和 run_job 过早建目录均先复现后修复。
- F1/F4 六红记录 `/tmp/public-r1-audit-red.log`：外部 legacy 的两个字段×根/后代四例、空词表 TypeError、缺资料占位状态；修复后六绿。后续补实际 adapter override、只追加保护审计且不读 profile/env。
- F2 `/tmp/public-r1-editable-red.log`：默认 uv 缓存未追踪资源，配置修改后仍读旧副本。`tool.uv.cache-keys` 保留 pyproject/setup/src 必要输入并增加 default.yaml 与 cover-letter/**/*；临时源码副本实证 config修改、引用修改、新增、删除，uv run/sync均刷新。源码 skill 正文未修改。
- [uv 官方 Dynamic metadata](https://docs.astral.sh/uv/concepts/cache/#dynamic-metadata)说明自定义键替换默认及 file glob；[Hatch force-include](https://hatch.pypa.io/latest/config/build/#forced-inclusion)用于唯一源映射。uv继续管理依赖；已安装wheel无源码搜索/补路径。开发者修改资源后通过uv run/sync并重启进程，不承诺旧常驻进程/--no-sync自动刷新。
- `scripts/check_package.py` 实际构建 direct wheel 与 sdist→wheel，比较默认配置、SKILL及全部references哈希；各自新venv安装，在源码外python -I、清除PYTHONPATH执行。实际安装console init、缺workspace CLI错误、MCP initialize/list_tools(47)、full/scheduled拒绝、合成资料列包资源、离线报告通过。未调用真实业务。
- 源码四项使用 uv.lock；wheel安装按发行元数据解析依赖版本范围，未假装wheel安装被仓库lock锁定。
- `.github/workflows/check.yml` 在runner内运行四项、两类制品探针和editable更新探针，无upload-artifact/Release/Pages/PyPI。仓库PUBLIC，预发布是成熟度而非访问控制。

## 验证结果与交接

首个实现提交 197e193；该检查点最终四项1600 passed/64.89s，direct与sdist往返制品通过。早审修复后的最终结果在下方追加，作为本次交审依据。

待验证：协调者push后远端Linux CI；Windows/Linux原生安装路径与权限；公众UI/引导/真实provider运行时、独立用户授权、浏览器身份与可靠提交边界、ATS获准环境、恢复升级备份与公众发行。macOS当前本地证据不能替代这些验收。R1b紧随R1，盲连9222问题仍属下一片，不借R1运行真实浏览器。

下一步：独立复核本次提交，协调者决定整合；未批准公众自动业务。无新增甲方决策需求。

早审修复后：workspace/resources 定向48通过；四项中的 sync/ruff/format通过。第一次全套为1608通过、1失败：`test_a_stop_before_an_auto_submit_agent_starts_keeps_it_from_starting` 无模拟提交进程生成，但停止后岗位为held而非期待pending。单例复跑通过，整个stuck_run模块61通过/23.79s。没有为此修改恢复逻辑或放宽断言；根因尚未定位，不能断言属于既存缺陷，保留为非稳定测试风险供复核。最终完整复跑结果另记。

制品最终探针 `/tmp/public-r1-reviewed-package.log`：direct wheel、sdist→wheel均通过。`/tmp/public-r1-reviewed-editable.log`：uv run/sync 修改/新增/删除资源均通过。没有源码fallback、没有可安装制品上传。脚本自身ruff检查/格式检查及git diff --check通过。

最终完整复跑：**1609 passed in 65.72s**（`/tmp/public-r1-reviewed-rerun.log`）。第一次完整1608+1失败记录保留，不以最后一轮绿覆盖；失败后的源码未改动，仅补交审说明。已观察事实是停止前无agent启动而岗位状态断言不一致；具体竞态根因未证实，不标为“已确认既存时序波动”。剩余风险交独立复核，不放宽断言。四项最终状态：uv sync --locked、ruff check src tests、ruff format --check src tests、完整pytest均通过；84个源/测试文件格式通过。

| 切片 | 产品行为 | 状态 | 提交证据 | 验证结果 | 阻塞/待核实 | 下一步及估时 |
| --- | --- | --- | --- | --- | --- | --- |
| R1 / decision28 | 源码外初始化、包资源、本地workspace拒绝副作用、显式legacy兼容 | 实现完成，交独立复核 | 首步197e193；本交审文件随最终修复提交，HEAD由提交后回报 | 1609全套；48定向；61停止模块；两种wheel与editable变更探针通过 | 首次非稳定失败根因未定位；远端Linux CI待跑；非公众发布通过 | 协调者安排Claude定向复核；主目录持锁推此分支触发CI（不先合main），通过后决定整合；审阅/CI耗时依执行环境，不承诺已通过 |
