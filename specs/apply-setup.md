# 投递环境与邮箱检查

本片修复既有投递入口的环境误报，不扩展提交授权、网站访问或填表范围。决定见 [apply-setup.decisions.md](apply-setup.decisions.md)。宿主验证码交接、同 generation 原页恢复与重复继续幂等不属于本片；不把重跑称为原页接续，也不把宿主邮箱授权称为后端已授权。

## 就绪状态

`get_apply_setup(check_connections=false)` 默认只做本机检查，不访问邮箱，不读 OAuth 文件内容，不启动授权。资料与答案检查、必需程序（Claude Code、npx、Chrome）分别报告；缺任何必需程序时 `can_start_web=false`、`ready=false`。`can_start_web` 表示网页申请的已检查前提满足，不保证模型登录、网站访问或提交成功。

Gmail 是可选能力，状态为 `disabled`、`not_configured`（OAuth 客户端文件缺失）、`authorization_required`（授权文件缺失或授权无效）、`not_checked`（文件存在但未连接）、`missing_program`、`connection_failed`、`timeout`、`ready`。文件存在不能表示连接成功。启用 Gmail 且未验证时 `ready=false`，但不阻止 `can_start_web=true` 的网页申请；必须明确验证码需人工处理、邮件投递不可用。显式禁用 Gmail 不参与总体 `ready`，同时不得声称邮箱可用。

`ready` 只汇总已检查的资料、程序和启用的 Gmail 搜索能力，返回 `ready_scope` 说明此边界；Chrome/Claude 的程序存在检查不是其登录、会话或端到端业务实测。

## 配置与连接

`apply.gmail` 配置启用开关、OAuth/授权文件路径、连接超时和探测查询。默认使用固定版本 Gmail MCP 的 `~/.gmail-mcp/gcp-oauth.keys.json` 和 `credentials.json`；兼容 `GMAIL_OAUTH_PATH`、`GMAIL_CREDENTIALS_PATH`，环境覆盖配置。路径在投递与检查中统一解析为绝对路径并传给子服务，不依赖宿主自己已有的 Gmail 插件。

在使用者已授权的投递或邮箱检查范围内，调用 `get_apply_setup(check_connections=true)`。用同一固定版本 Gmail MCP、同一路径，先完成 MCP 初始化，再调用 `search_emails` 作一次有时限的只读检查。查询为互斥日期条件，最多一条，不读取正文；任何返回内容、token、邮箱地址或子进程原始错误不得出现在报告和日志里。服务的 `isError` 和普通文本 `Error:` 都是失败；无响应、非预期响应不能当成功。只有成功的 API 调用可报告 `ready`，初始化/list_tools 不足以证明授权。

服务启动在空临时目录里，避免上游从当前目录复制 OAuth 文件；检查不能执行 `auth`、开启浏览器、发信或读取用户 profile/.env。local-only 模式不检查邮箱文件、不启动子进程、不访问网络；资料一致性未检查时 `materials_ready=null`，不能声称资料完整可投。

## 首次配置与运行

使用指南给出：创建自己的 Google Cloud OAuth 客户端、启用 Gmail API、放置密钥文件、在本机终端运行固定版本服务的 `auth`、本人完成 Google 授权、重新检查连接。项目提供 `python -m intent_job_agent.gmail_setup --config ... auth|check|status`，统一使用配置路径；auth 只能由用户明确要求配置邮箱时运行，使用上游授权流程，本片不代用户接受授权。不得要求把密钥/token 贴进对话；说明上游 OAuth scope 比填表 agent 工具白名单更宽。

真实投递 agent 启动前重新检查启用的 Gmail；不可用就省略 Gmail MCP 服务及全部 Gmail 放行工具，附加明确的不可用状态和人工处理指令。需要验证码或邮件投递时记现有 `email_verification` 门槛，明确是邮箱能力不可用，不能冒称搜过或没收到邮件。网页填表照常继续；填表 agent 仍不能发信或提交。服务配置、授权文件恢复后下一次运行重新验证，不沿用过期的成功状态。

## 验收

用 synthetic 文件和协议服务覆盖：缺客户端文件、缺授权、仅文件存在、授权撤销（含普通文本错误）、连接失败、超时、成功空搜索、必需程序缺失、显式关闭、local-only、实际执行降级及权限不扩张。先记录失败测试，再实现并运行仓库验证。测试不得读取真实凭据或访问真实邮箱；真实 OAuth/Google API 未实测时明确报告，不据离线通过宣称邮箱业务达标。
