# R1b 决定记录

## 1（2026-10-23）：B1/B2与S1/S2收敛（总决定29）

甲方采纳/tmp/public-r1b-plan-review.md：四种身份失效结果明确，submit已launch结果不明held/interrupted，未launch无虚假attempt；stop不能覆盖安全优先级。旧或不同generation不接管旧tab，但必须保留从岗位地址重开并核查已提交的恢复路线。

S1有条件采用：先用锁定MCP0.0.83/Playwright1.56.0空profile本地探针证明精确WS、CDP browserPID、PID/start/profile核验；成立后不用强制lsof，失败报告不退HTTP裸端口。GUID/CDP自报不认证恶意同用户进程。

S2只做小型管理锁＋已有apply_running owner，不做lease/心跳；独立start_form_fill也纳入跨会话互斥。旧复制profile从不采用、迁移或删除，重新登录为接受的兼容变化。保留local_only，不扩R4资料权限/R5提交边界。

本次明确阶段门：先规格/计划/探针证据交甲方和Claude复核，再产品实现。授权的测试仅空专属profile/about:blank/127.0.0.1，无真实业务。

## 2（2026-10-23）：S1本地实证与MCP错误GUID对照（总决定29）

同一MCP0.0.83正确WS列出about:blank合成标题；错误GUID实际调用browser_tabs返回工具错误且标题不可见，Python连接错误GUID也拒绝。PID/start/profile证据成立，当前macOS环境采用S1而不强制lsof；其他平台和产品迁移仍待验证。Python Playwright1.56.0与MCP自报及实际Node依赖1.64.0-alpha-1790635538000分开记录，没有升级依赖。探针不证明恶意同用户认证或所有未来重连场景。详见同名review及仓库JSON。
