# 原页接续验收记录

2026-11-09，协调者按总决定 39、填表决定 42 审阅本片。源分支已吸收 main，开发只使用合成资料、临时浏览器与本地页面；未读取真实 profile/.env、私人运行日志或验证码，未操作真实申请。

## 已验证

- 原始 run/job 与清单身份匹配；旧运行仅从同次 checkpoint 的 root/db/run/job 取得缺失 generation。
- 原浏览器 generation、原 CDP 标签页及唯一地址贯通执行器、checkpoint 与最终读回。接管在管理锁内验证，不因浏览器消失而启动替代实例。
- 本地两套独立浏览器同开相同地址，读回只访问原任务页面；其他浏览器的内容不变，不创建新页，不点击提交。
- 只填模式、禁止发信和既有工具限制保留。已接收页面不启动 agent；不根据回执推断提交者。
- 当前邮箱码可在后端 Gmail 未配置时交接到受限子进程；配置与保存产物不包含合成验证码。完整码、结构化分位输入、转义字符覆盖脱敏；原始页面、截图和网络正文停录。宿主自己保存工具参数不在项目控制范围内。
- 过期、未来、缺时区及早于来源运行的码拒绝；CAPTCHA 不接受码。网站判过期的结果保留为 gate，不自动重发。
- SQLite 两个并发连接只有一个接续预留成功；跨 Toolbox 重复请求返回同一结果，中断记录不自动重跑。启动失败、收尾写入失败均释放本次 activity。
- stuck、needs_answer 即使读回成功也返回 incomplete；原有开放题草稿保留，不因状态变更丢弃。
- 新入口遵守 local-only 拒绝；两宿主工具配置与技能说明同步。原有 /apply held 清单语义保留。

## 结果

`uv sync --locked`、`uv run ruff check src tests`、`uv run ruff format --check src tests`（130 files）通过。

`uv run python -m pytest -q`：**2070 passed in 130.96s**，日志 `/tmp/form-fill-resume-source-final-pytest.log`。`git diff --check` 与变更指南的本地链接检查通过。合并后验证与推送结果另存 `/tmp/form-fill-resume-result.json`。

## 验证边界及后续

这证明工程路径，不证明真实网站门槛已解除或投递成功。真实用户事实仍由本人提供。填表仍依赖既有独立 Claude Code，不在本片替换为宿主原生子 agent；Dreaming/wiki 未接入。

业务另报告原生 radio 漏读及截图过度遮挡，已取得不含个人信息的结构统计，归下一独立切片，不计入本片修复完成。/apply held 清单的只填分支仍按既有重填流程处理；本片原页入口用于独立填表运行。
