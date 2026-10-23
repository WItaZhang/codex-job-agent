# 正常停止被误判为中断：缺陷与证据留底

状态：甲方接受为已证实的既有恢复竞态，不阻塞R1。R1整合后单独开最小稳定性切片（R1s），只修此缺陷，再立即R1b；从R6提前，不提前整个R6，不混入当前R1。当前尚未修复。

## 机制

对象HEAD f62c32c；R1前比较基线dc6719b。`Toolbox._recover`先在数据库事务外调用`_sync_apply_results`；当时run.status仍running，正常收尾返回而未清activity。worker随后完成停止，将run.status设stopped，但`_ended → _close_run`尚未清理activity。poll再取得DB事务，看到同一owner却不再running，误走`_close_interrupted`：DB中submit岗位仍pending，因而写held/interrupted及attempt。

`BEGIN IMMEDIATE`只保护数据库，不能让这两次内存状态观察原子化。正常收尾使用run锁→DB的顺序；后续修复不得在持DB事务时反向等待run锁。对应代码：tools.py的`_recover/_close_run/_close_interrupted/_ended`，watchdog.py的`Live.begin/stop/launch/end`，apply_bridge.py的`run_application`启动门。

## 三组确定性合成对照（2026-10-23）

以Event屏障控制交错，不靠随机sleep；临时合成Store/applicant、fake runner。真实Popen/subprocess.run/httpx.request均设为调用即失败，进程身份为fake，kill函数只记录调用。复用真实Live停止门和工作线程/DB收尾，不启动Chrome、模型、邮件或申请网站。

| 场景 | LIVE.launch | j05 / j06 | j05 attempt | 恢复kill路径 |
| --- | --- | --- | --- | --- |
| 当前HEAD，强制_sync返回后worker停止、activity未清 | false | held / pending | interrupted | fake kill_agent及kill_orphans各1 |
| dc6719b的_recover方法回放，同一交错 | false | held / pending | interrupted | 同上 |
| 当前HEAD，worker已停止后才进入_sync（无窗口） | false | pending / pending | 无 | 无 |

基线回放是把git show提取的旧_recover绑定到当前合成Toolbox，不是完整基线checkout验收。AST对比另证实九个方法与基线相同：Toolbox的_close_run、_close_interrupted、_ended、stop_applying、_sync_apply_results；Live的begin、launch、stop、end。由此证实旧竞态存在，不依赖R1新校验才出现。

## 原失败与证据限制

首轮全套1608通过/1失败（66.98s）：`test_a_stop_before_an_auto_submit_agent_starts_keeps_it_from_starting`的j05为held，断言期待pending。stop已回报“不会再起”，fake pids文件不存在；该文件在fake读完stdin后才写，不能单凭不存在证明原运行Popen从未调用。原临时DB已不在，没有attempt原因或线程轨迹，**未证明首次失败的唯一交错**。

随后单例通过、stuck模块61通过/23.79s、整套1609通过/65.72s，不消除竞态。此次探针严格证实launch=false且保守held，不是提交绕过；错误会产生虚假中断记录和不必要的恢复处理。没有给整个legacy执行系统绝不提交的保证：已启动的自动提交任务按原规格做完再停。

R1路径校验可能改变线程调度/频率，未测概率；_recover的新增检查在_sync之前，run_application检查在LIVE.begin之前，并非在所述临界窗口内新增扫描。新workspace真实执行/恢复入口仍拒绝，缺陷保留在显式legacy路径。不能用一次复跑绿代替修复。

## 下一片边界与验收建议

只修本server正常停止收尾与恢复互相误判；保留真正owner丢失/崩溃时的保守held，不改保护公司或扩大授权。正式事件屏障回归应证明：stop-before-launch保持pending且无attempt；真实失联仍held；已提交结果不回退pending；重复close幂等，锁顺序不死锁。预计0.5–1有效工作日，作为R6预算中提前的小部分；具体独立检出由协调者另行安排。本次未实现。

原始辅助文件：`/tmp/public-r1-stop-race-analysis.md`、`/tmp/public-r1-stop-race-probe.py`、`/tmp/public-r1-stop-race-probe.json`、`/tmp/public-r1-stop-race-source-comparison.json`、`/tmp/public-r1-reviewed-pytest.log`。这些可能被清理；本文件已持久保存关键机制、结果及证据限制，不依赖/tmp才可理解结论。
