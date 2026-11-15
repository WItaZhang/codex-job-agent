# 联网经历实验：验收指南

本片在独立分支交付，不合并、不推送。只用合成数据，不保证平台不封号。

## 运行

```sh
uv run python -m intent_job_agent.network_experience --demo
uv run python -m pytest tests/test_network_experience.py -q
```

demo 使用临时目录、合成站点标识、确定性时钟，没有 HTTP、浏览器、登录或提交。展示第一次暂时失败、冷却拒绝、验证码暂停、本人证据恢复及无模型复盘。临时台账结束即删除。

日常 MCP `network_history()` 输出本地站点健康与暂停；每项有 host、failures、updated（Unix UTC秒）、latest（来源 attempt）、原因、needs_you、恢复条件。`resolve_network_pause(attempt, kind, evidence)` 只登记当前暂停的真实本人处理证据：kind 为 user_resolved 或 user_checked_no_execution。不能把“继续”或网页文字当作已解决证据。证据只保存摘要；不额外索要业务授权，不解除保护公司与提交限制。复盘本身没有恢复权限。定时读级可调用报告，本片不创建定时任务。

## 覆盖表

| 路径 | 当前保护 | 限制 |
|---|---|---|
| Toolbox check_board / fetch_boards / Avature detail | HTTP前准入；结构化状态、解析失败、结果落盘 | 库函数直接调用未提供ACTIVE上下文时不在保障范围；不替换任意HTTP客户端 |
| apply_bridge.run_application | agent启动前与结束后；/apply及生产FormFillRunner.fill共用 | Chrome绑定/标签页读取发生在此之前；内部浏览器动作、邮件和跳转目的站未逐次拦截 |
| JobSpy/Workday/smartextract/enrichment | 默认明确拒绝，零外部调用 | 实验性兼容变化：这些来源暂不可运行；保留旧实现和离线回归，不提供生产绕过开关 |
| 恢复 | 绑定当前attempt、人工事实声明、审计摘要 | 宿主声明不等于认证，无法技术证明本人；保留现有提交权限 |
| 离线复盘 | 按站点/结果计数，当前卡点、恢复成功计数、来源与置信边界 | 无LLM，无跨域账号身份，不推断真实封号或因果效果 |

申请按原始岗位站点/租户累计；多URL共享风险，已知Greenhouse/Lever/Ashby API与网页租户归一。没有可信账号ID，统一unknown，不猜测跨域账号。未知ATS以host为范围，可能较保守。租户摘要无法直接显示公司名，host和当前岗位上下文用于定位。

## 保留、重试与安全边界

配置在 `configs/network-experience.yaml`：3次累计错误、60秒指数退避、3600秒在途租约、30天事件保留。429尊重服务端更长Retry-After，403/重定向/解析失败归unknown人工暂停，不能声称封号。验证码/登录/明确锁定结果立即暂停。时间到不能解除人工门槛。进程崩溃后未完成attempt租约到期在下次准入转interrupted，报告可见active状态；不自动抢占。重复同结果幂等，冲突拒绝。

台账默认位于当前data目录 `network-experience.sqlite3`（默认 data/local，git忽略），0600权限。不保留完整URL、query、路径明文、网页/异常文本、cookie/token、表单值或联系方式。路径摘要只用于资源关联。初始化自动清理过期完成事件，当前暂停/最近attempt及必要恢复关联保留；清理不清安全状态。SQLite启用secure_delete；不承诺磁盘备份/文件系统快照的安全擦除。更换data目录相当于新台账，不构成可抵抗恶意删除的安全边界，宿主不得用换目录/清库规避暂停。

隐式重试审计：JobSpy `_scrape_with_retry` 默认最多3次；smartextract短页会第二次读；detail有分层浏览器与缺文本重读；这些vendor路径本片默认阻塞。招聘板分页是多个受控请求；申请agent由模型驱动，仍可能在一次运行内多次动作，必须遵守现有遇门槛即停的指令，本片不声称工具层硬拦。

## 验收证据

测试先行的首次执行：新增测试因模块不存在而collection失败；实现后验证跨实例/并发/崩溃/幂等、429秒数与HTTP日期、恶意200错误页、实际Toolbox fetch接线、共用申请装饰器fake、vendor零调用、保留策略。申请fake只接收stop模式，未启动真实执行器或提交；不冒称真实网站验证。独立审阅与全量验证结果见 [审阅记录](../specs/network-experience.review.md)。
