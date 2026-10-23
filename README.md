<div align="center">

<h1>岗位挖掘 Agent</h1>

<p><strong>找到适合你的机会，把求职流程交给助手。</strong></p>
<p>在 Codex 或 Claude Code 中，完成岗位发现、推荐反馈、材料准备与授权投递。</p>

<p>
  <a href="#快速开始">快速开始</a> ·
  <a href="#日常命令">日常命令</a> ·
  <a href="#投递如何执行">投递规则</a> ·
  <a href="docs/usage.md">完整使用指南</a>
</p>

</div>

![产品流程：发现与筛选岗位 → today 推荐及反馈 → apply-list 导出清单 → apply 准备材料、填表和按授权投递。橙色代表用户选择，绿色代表自动处理，灰色代表流程交接。](docs/assets/product-overview.svg)

每天查看 **7 个推荐岗位 + 3 个探索岗位**，用反馈调整求职偏好，再把合适的机会整理成投递清单。你可以只用找岗位和推荐，也可以准备好资料后启用投递。

支持多个招聘来源与岗位 JSON 导入，包括 Indeed、LinkedIn、Glassdoor、ZipRecruiter、Google Jobs，以及可选的 Workday、Greenhouse、Lever、Ashby、Avature 等站点。来源可用性取决于网站、地区与访问条件。[查看来源配置 →](docs/usage.md#岗位从哪里来)

## 日常怎么用

> **你：** 我想找美国的机器学习工程师岗位，优先远程。先帮我找岗位，不投递。
>
> **助手：** 列出今天的 7 个推荐和 3 个探索岗位。
>
> **你：** `1+ 2-坐班 3- 5+ 6?地点不对`
>
> **助手：** 根据反馈分析原因，给出偏好调整选项；读错的岗位信息单独修正。
>
> **你：** 选好方案，再生成投递清单。

*以上为对话示意。`+` 想投，`-` 不想投，`?` 信息读错了。未反馈的岗位不作为偏好信号。*

## 快速开始

### 1. 下载并安装

需要 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/)，以及已登录、可用的 **Codex 或 Claude Code**。项目使用 Python 3.13，uv 可按项目要求安装所需版本。

```sh
git clone https://github.com/WItaZhang/codex-job-agent.git
cd codex-job-agent
uv sync --locked
mkdir -p data/local
cp docs/examples/intent.example.yaml data/local/intent.yaml
cp docs/examples/searches.example.yaml data/local/searches.yaml
```

编辑这两个文件，填入自己的求职偏好、搜索关键词和地点。需要打开网页补全岗位原文时，再安装浏览器：

```sh
uv run playwright install chromium
```

### 2. 选择你的宿主

始终从仓库根目录启动。先审阅并信任项目指令和权限配置；工具可用不代表你已经授权自动投递。

| | Codex | Claude Code |
| --- | --- | --- |
| 启动 | 在 Codex 中打开本仓库，或运行 `codex` | 在仓库根目录运行 `claude` |
| 接入 | 信任项目后加载 `.codex/config.toml` | 信任项目，并批准 `intent-agent` MCP 服务 |
| 每日推荐 | `$today` | `/today` |
| 其他入口 | 使用 `$` 前缀 | 使用 `/` 前缀 |

两个宿主共用同一套技能与数据。如果没有看到技能，重启宿主；其他问题见[启动故障](docs/usage.md#启动故障)。

### 3. 初始化并查看推荐

在对话里发送：

> 我已核对 `data/local/intent.yaml` 和 `data/local/searches.yaml`。请初始化意图并检查搜索设置。本次只允许发现、打标签和推荐，不投递。

然后使用 `$today` 或 `/today`。当天还没有抓取岗位时，它会先运行岗位发现。

想先体验离线流程，可以复制[合成岗位示例](docs/examples/jobs.example.json)到 `data/local/jobs.json`，明确告诉助手“只使用示例岗位，不运行抓取”，让它导入、打标签并生成推荐。宿主模型仍会消耗额度。

## 日常命令

| 你想做什么 | Codex | Claude Code | 得到什么 |
| --- | --- | --- | --- |
| 找今天的岗位 | `$fetch-jobs` | `/fetch-jobs` | 抓取、筛选并打标签的岗位 |
| 查看适合自己的机会 | `$today` | `/today` | 7 个推荐 + 3 个探索，以及反馈调整流程 |
| 整理准备投递的岗位 | `$apply-list` | `/apply-list` | JSON / CSV 清单，记录已投和跳过 |
| 准备材料并投递 | `$apply` | `/apply` | 求职信、填表结果和投递状态 |

也可以直接说：“导入 `data/local/jobs.json`”“1–5 已投，7 跳过”或“停下投递”。

## 投递如何执行

投递是可选功能。**即使在 Codex 中使用，当前投递后端也需要 Claude Code CLI、Google Chrome 和 Node.js。** 按[投递配置指南](docs/usage.md#投递)准备简历、个人资料、标准答案和提交前确认的公司名单。

![投递边界：普通公司按授权自动提交；保护公司或未知公司停在提交前，由你亲自提交。](docs/assets/application-boundaries.svg)

- **你的确认名单始终有效**：缺少名单不能开始投递；重试与恢复也不会绕过保护。
- **遇到登录、验证码等门槛会停下**，留给你处理，不破解验证码。
- **不会填写 SSN、银行卡或支付申请费**，遇到仲裁协议也会停下。
- **可以要求停止**；已经进入自动提交的岗位会完成当前提交后再停，避免结果不明。

浏览器使用项目专属 profile，不复制日常 Chrome 资料。自动提交无法撤回，请先核对申请资料和授权范围。

## 数据、费用与当前支持

个人文件放在被 Git 忽略的 `data/local/`。岗位、偏好及反馈会按功能交给模型处理；投递时，必要的申请资料会交给投递模型和招聘网站。**不要把密钥贴进对话。** 详细说明见[数据去向、下载与费用](docs/usage.md#数据去向下载与费用)。

宿主订阅或 API 费用由你承担；可选模型服务和投递后端也可能产生费用。部分岗位来源无需额外模型 key，网站限流或改版仍可能影响抓取。

| 环境或能力 | 当前状态 |
| --- | --- |
| macOS arm64 | 已验证双宿主接入、技能与子 agent、合成推荐及投递授权流程 |
| Linux | CI 覆盖测试、浏览器及包检查；桌面宿主和投递安装未全面验证 |
| Windows | 暂未验证，不承诺可用 |
| 真实招聘网站投递效果 | 随网站与账户环境变化；合成测试不代表真实投递成功率 |

## 文档与开发

- [完整使用与配置指南](docs/usage.md)：来源配置、申请资料、恢复、升级与故障处理。
- [产品规格](spec.md) · [开发约定](CLAUDE.md) · [交付验证记录](specs/github-host-release.review.md)。
- 许可证：**AGPL-3.0-only**。岗位发现与投递复用 [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot)，来源说明见 [NOTICE.md](NOTICE.md)。
