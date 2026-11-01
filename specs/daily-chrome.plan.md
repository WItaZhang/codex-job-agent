# 用户自己的 Chrome 实例：最小计划（决定 33）

规格：spec 4.10 第 6 条、§10 第 56 条。一个切片，分支 `worktree-daily-chrome`。

## 样例（合成，测试里）

| 情况 | 期望 |
| --- | --- |
| 没有 `applicant.yaml`，或没写 `chrome_user_data_dir` | 和现在一样：工作区 `browser/profile`，带专属profile的兼容参数 |
| 写了一个已存在的独立目录 | 投递在这个目录启动 Chrome，只用最小参数；不写 `owned-profile.json`，不在目录里建任何文件 |
| 相对路径、不存在、软链、Chrome 默认数据目录或其中的 `Profile 2`、工作区 `browser/` 里 | 拒绝，说明原因，不启动 |
| `intent-job-agent chrome`，实例没开 | 启动并记下身份（没有运行 owner）；随后的投递接管同一个实例，不重启 |
| `intent-job-agent chrome`，实例已开着 | 只让它新开窗口，不再起带调试端口的进程，身份记录不变 |
| 实例开着但没有身份记录 | 投递和 `chrome` 都拒绝接管，提示关掉重开 |
| 记录里是旧的专属profile实例，配置改成自己的实例 | 旧实例不动，启动新实例并换掉记录 |
| 代码关浏览器（`close`） | 对用户实例拒绝 |
| 填表的核对服务 | 用同一个 profile 核验 |

## 步骤

1. `applicant.yaml` 加可选 `chrome_user_data_dir`（`~` 展开），示例文件说明。
2. `browser_session`：`scope()` 带上 profile；`profile_dir()` 校验；`ensure()` 用它；`launch_arguments(own=True)`；`open_instance()`；`close()` 拒绝用户实例。
3. `form_fill` 核对服务配置带 `browser_profile`。
4. `intent-job-agent chrome` 子命令。
5. docs/usage.md：迁移步骤与风险。

迁移本人资料、在本机实际打开实例不属于代码，由用户确认后手动做。
