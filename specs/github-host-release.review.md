# GitHub 双宿主源码交付记录

## 规格准入

- 用户已批准新目标及实施范围，非等待阶段审批。旧S2 8e5c06d干净、锁已释放，未动提交/stash；新树github-host-release claim成功，基线main3fe87bc，ff-only已是最新，uv sync --locked成功（Python3.13.16，54包安装）。未改主目录/其他树，未启动agent。
- 初读README确认待纠正承诺：复制日常Chrome、子agent碰不到提交工具、git pull不影响数据；当前代码证据核对和真实生命周期AR反证尚未完成，不能先宣称修复。
- OpenAI Docs核对入口（仅依据实际打开的官方页，不采第三方搜索结果）：[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)、[skills](https://learn.chatgpt.com/docs/build-skills)、[subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)。具体字段与本机支持待接线步骤记录。
- 本次仅规格/计划，不执行真实业务，不读个人profile/.env。完整四项和干净检出验收待实现收尾，不将uv sync等同交付完成。

## G07：旧宿主跨 Store 恢复报告（模块决定3）

基线28f0f84/3fe87bc，真实 resume_applying 后台线程与独立 Store，Event 阻塞第一岗位，第二岗位尚在排队；没有伪造 _apply_run。`uv run pytest -q tests/test_host_resume_reports.py` 首轮2条实际行为失败（active/queued均 DID NOT RAISE），日志 /tmp/github-host-resume-red.log。合成runner/页面，不启动浏览器或外部账户。

修复后 `uv run pytest -q tests/test_host_resume_reports.py tests/test_gate_handoff.py tests/test_stuck_run.py` 171 passed/43.06s（当时新文件仅2项，/tmp/github-host-resume-green.log）；补充持久标记/旧协调表/无关岗位/结束释放/同岗连带写入后，`uv run pytest -q tests/test_host_resume_reports.py tests/test_slice5b.py tests/test_slice5c.py` 150 passed/3.86s（/tmp/github-host-resume-report-regression.log）。新文件共7项。后一集合首轮149过1失败：测试使用普通相同URL构造未进入同岗集合，实际报“从未列入清单”，属于fixture错误；改用已有确定性同岗样例并实际列入清单后通过，不计产品红。定向ruff check/format通过；初次lint的行长及fixture导入重定义已修，没有过滤warning。

旧表迁移测试通过删新增列模拟旧协调结构，不冒充真实旧源码升级。保护集合仅岗位ID；不是授权认证、不提前标记queued已提交。完整四项、干净clone和双宿主实测仍未完成，此处仅AR旧路径小步证据。
