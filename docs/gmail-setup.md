# Gmail 配置与检查

投递后端启动独立的 Gmail MCP。你在聊天宿主连接过 Gmail，并不会把授权交给这个后端。邮箱能力可选；未配置时网页填表可继续，需要邮件验证码时会停下等你处理，不能发送邮件申请。当前没有把宿主读到的验证码注入等待中填表任务的入口，也不支持从原验证码页继续填表；重新运行可能新开页面并重新发码。

## 首次配置

1. 安装 Node.js（含 `npx`）。在自己的 Google Cloud 项目启用 Gmail API，设置 OAuth 同意屏幕；测试模式下将自己加入测试用户。按 [Google 的 Gmail 快速入门](https://developers.google.com/workspace/gmail/api/quickstart/nodejs) 创建 Desktop app OAuth 客户端并下载 JSON。也可使用 Web application 客户端，其回调地址需包含 `http://localhost:3000/oauth2callback`，见 [上游说明](https://github.com/GongRzhe/Gmail-MCP-Server#installation--authentication)。
2. 将下载的客户端 JSON 放到自己电脑的 `~/.gmail-mcp/gcp-oauth.keys.json`，只由本人保存；不要提交 Git，也不要把文件内容、token 或验证码贴进对话。已有文件先核对来源，不覆盖不明配置。
3. 在项目目录的本机终端运行以下命令，并由本人在浏览器中核对账号、应用和权限后完成 Google 授权：

   ```sh
   uv run python -m intent_job_agent.gmail_setup --config configs/default.yaml auth
   ```

   这会调用配置锁定的 `@gongrzhe/server-gmail-autoauth-mcp@1.1.11 auth`，默认在本机 3000 端口接收回调，授权文件保存为 `~/.gmail-mcp/credentials.json`。自定义授权文件路径时先创建其父目录。上游请求 `gmail.modify` 和 `gmail.settings.basic`，范围宽于填表 agent 的搜索/读取工具白名单；工具白名单不等于 Google OAuth 权限被缩小。只在你接受这些权限时授权，也可以关闭邮箱能力。
4. 明确检查自己授权的邮箱连接：

   ```sh
   uv run python -m intent_job_agent.gmail_setup --config configs/default.yaml check
   ```

   只有 `status: ready` 表示这次 Gmail API 空搜索成功。检查使用互斥日期，最多请求一条，不读取邮件正文，不输出邮件或凭据。首次下载 npm 包超过超时时，可检查网络后重试。出现授权或同意屏幕错误时，按 [Google 故障排查](https://developers.google.com/workspace/gmail/api/troubleshoot-authentication-authorization) 检查自己的 OAuth 设置；不要绕过组织限制。

只检查本机文件是否存在、不连接 Gmail：将上面命令末尾的 `check` 换成 `status`。授权未就绪时退出码为 1；`auth` 成功仍需再运行 `check`。命令不会读取投递 profile 或 `.env`。

## 路径与关闭邮箱

`configs/default.yaml` 的 `apply.gmail` 包含 `enabled`、`oauth_path`、`credentials_path` 和 `timeout_seconds`。使用自己的配置文件时，把命令中的 `--config` 指向它；宿主也必须加载同一份配置。`GMAIL_OAUTH_PATH`、`GMAIL_CREDENTIALS_PATH` 环境变量优先于配置；终端与宿主环境不同会导致检查不同文件。检查结果只显示路径及存在性，便于核对。

检查、授权和实际服务使用相同解析规则，并从空临时目录启动，防止上游从工作目录自动复制另一份 OAuth 客户端文件。探测查询固定为互斥日期，不接受普通邮件搜索条件。`apply.gmail.enabled: false` 关闭邮箱检查和后端工具；local-only 模式也不检查凭据或连接邮箱。

## 如何解释检查结果

| 状态 | 含义与下一步 |
| --- | --- |
| `not_configured` | 缺 OAuth 客户端文件；完成首次配置第 1–2 步 |
| `authorization_required` | 缺授权文件，或 API 报授权无效/范围不足；本人重新授权后检查 |
| `not_checked` | 文件存在但没有验证连接；在已授权范围内运行 `check` |
| `missing_program` | 找不到 `npx`；安装 Node.js 并检查宿主 PATH |
| `connection_failed` | 无法验证服务/API；检查路径、JSON 格式、Gmail API 启用情况及网络后重试 |
| `timeout` | 在配置时限内没有完成检查；检查网络/包下载后重试 |
| `ready` | 本次 Gmail 空搜索成功；不证明发信能力、Chrome、Claude 登录或网站流程可用 |
| `disabled` | 已关闭邮箱，或处于 local-only 模式 |

`get_apply_setup()` 默认离线；`check_connections=true` 才做连接检查。它另外报告 `materials_ready`、`missing_programs` 和 `can_start_web`。后者仅表示资料及程序存在检查通过，不是浏览器已连通。启用但未验证 Gmail 时总 `ready=false`，网页前提仍可满足；显式关闭 Gmail 后总 `ready` 不包含邮箱能力。每次执行前会重查邮箱，不沿用旧的成功结果。

本片工程验收使用合成凭据与合成 MCP；不代表任何真实 Google 账号已配置，也不代表某份真实申请已恢复或提交。
