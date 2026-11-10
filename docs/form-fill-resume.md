# 继续独立填表的原页面

适用于 `start_form_fill` 已结束、停在登录或邮件验证码等门槛的运行。仍然只填不交，最后由本人检查、提交。更新项目后重启宿主的 intent-agent MCP，才能看到新工具；不要在任务正在执行时重启。

1. 从 `form_fill_status()` 或保存的报告取得 `run_id`、`job_id` 和原清单路径。运行 `done` 表示执行结束；岗位 `incomplete`、`gate`、`agent_result=stuck` 均未填完。
2. 调用 `get_form_fill_gate(run_id, job_id)`。它只检查专用浏览器、原标签页和保存的身份，不启动 Chrome，也不证明门槛已被用户解决。
3. 已有授权范围内，用同一清单调用 `resume_form_fill(list_path, run_id, job_id)`。执行器先查看原页，用户已手动完成登录或验证就从当前页面继续。发现申请已接收时直接报告，不再启动填表 agent。
4. `form_fill_status()` 查看新运行；新的报告与 `meta.json` 保留来源编号。重复调用同一来源返回已有运行，不会再次操作页面；跨 MCP 重启也沿用该记录。

原浏览器重启、标签页关闭、切到不相关网站或同地址存在多个页时，工具会拒绝接续。它不会新开页面或重新发送验证码。用户确实要从头重做时才用 `start_form_fill`。接续中断而结果不明时先核对已有运行，不能换编号绕过去重。再次遇到门槛，用该次新结果的 run_id 查询下一步。

查询拒绝接续时的 `reason_code` 区分 `browser_unavailable`（浏览器不可用）、`browser_generation_changed`（已不是原浏览器实例）、`original_tab_missing`（原标签页不在）、`site_changed`（原页已到其他网站）和 `ambiguous_url`（同地址有多个页）。地址重复并不表示原页丢失；先保留原页，核对其他页的归属，不要据此重新开始申请。`browser_identity_unverified` 或 `inspection_failed` 表示检查未能完成，不足以断言页面已经关闭。返回的诊断不包含原始异常、私人页面内容或连接参数。

## 后端 Gmail 不可用时交接验证码

使用者可先在原页面自行完成验证。若本人或已授权宿主已从**本次对应邮件**取得数字验证码，也可在上述接续调用中附 `verification_code` 和带时区的 `code_received_at`。仅限 4–12 位数字邮箱码；不接收 CAPTCHA 答案、密码或验证链接。默认要求邮件在最近 300 秒内收到，且不早于来源运行；可在 `form_fill.verification_code_max_age_seconds` 配置交接期限，网站仍可能提前判过期。

只使用一次，不自动重发或循环重试。不得在聊天回复、经验库或笔记中复制验证码。项目不把码存入数据库、报告或 checkpoint 配置；本次原始页面、截图与网络正文会停录，规范化读回与日志脱敏后保存，报告写明缺失证据。宿主自身可能保存工具参数，项目无法控制这部分历史。

该入口处理独立填表，不改 /apply 的 held 状态。/apply 的 `prepare_resume` / `resume_applying` 也已接上只填原页恢复，并负责更新 held。Gmail 首次授权见 [Gmail 配置指南](gmail-setup.md)。

## 避免重复开始

`start_form_fill` 遇到已有未完成访问会返回 `blocked`，其中 `run_id`、`source_job_id` 指向应检查的原运行（旧记录缺来源岗位编号时为空）；换清单或岗位本地编号不会重新访问网站。先用上面的接续工具恢复原页。已有完成记录仍返回 `skipped`。

确需从头开始时，依据实际请求和业务授权在清单条目中写 `revisit: true`、`restart_from: <最新运行编号>` 和非空 `restart_reason`。新访问开始后，同一个来源编号不能再次用于重开。不要为清除 blocked 而自动更新这些字段，或删掉录制记录。

/apply 原页失踪、站点变化、身份检查失败或地址重复时，会保留 held 并给出具体问题，不能换一个同地址标签页接管。之前无人值守或按在场等待规则结束时，只有代码确认关闭成功的页面才允许按既有重跑范围重新打开；关闭失败会保留原绑定。每次只填接续有独立报告，批次摘要链接每次报告，同一岗位以最后一次为当前状态，历史尝试保留。
