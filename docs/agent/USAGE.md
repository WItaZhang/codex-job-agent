# 在 Codex 中使用 Codex Job Agent

这一版通过仓库内的四个技能运行：Codex 负责理解、研究、规划和写作，
本地 Python 工具负责资料、状态、授权检查、浏览器执行和证据记录。
使用当前 Codex 会话即可，不需要再配置一个模型 API key。

项目与发行包名为 `codex-job-agent`。为保持使用兼容，当前版本保留
`applypilot-agent` 命令、`applypilot_agent` Python 模块和四个 `applypilot*`
技能标识。仓库仅包含这套面向 Codex 的工具运行时，不包含旧版 ApplyPilot 管道。

## 安装与启动

首次获取仓库并安装：

```sh
git clone https://github.com/WItaZhang/codex-job-agent.git
cd codex-job-agent
uv sync --locked --extra dev
uv run playwright install chromium
uv run applypilot-agent --help
```

依赖版本由 `uv.lock` 固定。Python 环境由 uv 管理；不要在系统 Python 中另外
安装一套依赖。首次安装 Chromium 需要网络。

把这个仓库作为 Codex 项目打开。技能位于 `.agents/skills/`：

| 技能 | 用途 |
| --- | --- |
| `$applypilot` | 开始或继续一次求职会话，协调候选、材料、待办和提交 |
| `$applypilot-onboard` | 保存个人事实、方向、限制和自动投递边界 |
| `$applypilot-discover` | 寻找公司与岗位，读取公开岗位板，做有证据的匹配判断 |
| `$applypilot-prepare` | 准备材料、观察表单，按授权审核或提交支持的申请 |

若已有会话尚未加载新技能，可以在同一个项目开启新会话，也可以明确让 Codex
读取对应的 `.agents/skills/<技能名>/SKILL.md`。技能保存在仓库中；本项目没有
另外安装一个全局插件，也没有修改你的其他求职技能。

## 第一次使用

直接描述背景和希望的边界即可。例如：

> 用 $applypilot-onboard 配置我的求职助手。我会提供一份简历作为背景来源。
> 我偏向数据平台岗位，地点和签证问题先和我核实。特别合适的岗位，我想先看
> 定制材料；其他符合要求的岗位可以按我们确认的范围自动投递。

Codex 会先利用你提供的内容整理资料，再把重要缺口合并询问。你不需要填写
JSON，也不需要每次求职都重复介绍自己。

它会分开保存：

- **结构化档案**：个人信息、工作许可、教育、工作经历、项目、论文、竞赛、技能和
  可入职时间各有固定字段；每条记录有稳定 ID、来源和确认状态。
- **偏好与限制**：希望做什么、哪些条件必须满足、哪些条件只是更喜欢。
- **投递授权**：哪些匹配档位可以自动提交、哪些公司必须审核、允许的投递站点
  和每天的提交尝试上限。

未确认的信息不会自动补成事实。例如，“简历里没有写 Kubernetes”不等于
“用户不会 Kubernetes”，也不意味着助手可以替用户编出相关项目。

查看[档案模型说明](PROFILE.md)、[空白模板](../../.agents/skills/applypilot-onboard/references/profile-template.json)
和[完整结构示例](../../.agents/skills/applypilot-onboard/references/profile-example.json)。
运行时只读 SQLite 中的一份当前档案；修改原记录的字段，不叠加相互否定的事实。
`profile-export data/local/profile.json` 可导出可读文件，编辑后通过 `profile-set`
保存才会生效。更新时携带导出返回的 `--expected-hash`，防止旧文件覆盖较新档案。

默认配置不允许自动提交。你可以在首次设置时授权，也可以之后修改。已经明确
授权的范围内，助手不需要每个岗位都再问一次；超出范围才会交给你处理。

## 日常使用

例如：

> 用 $applypilot 继续今天的求职工作，最多处理 80 个岗位。优先把最匹配的岗位
> 准备好，其余按已保存的边界执行；需要我回答的问题集中给我。

它会先读已有状态，继续未完成的工作。可能先补岗位，也可能直接处理已经准备
好的申请；没有固定要求每个岗位都重新经过一整条流水线。

“处理 80 个岗位”包含筛选、研究和准备，不承诺提交 80 份申请。高匹配岗位有
预留处理名额；提交尝试还受独立的每日限额约束。待审核材料、共用的缺失答案、
不支持的表单和结果不确定的申请会集中展示，其余可继续在当前会话中处理。

针对单个岗位也可以说：

> 用 $applypilot-prepare 准备这个已经在列表中的岗位，先让我看看材料。

或者：

> 用 $applypilot-discover 扩大到另外几家适合我方向的公司，优先检查公司官网的
> 当前岗位，不要把搜索结果摘要当成仍在招聘的证据。

技能本身不会在 Codex 关闭后定时醒来。如果希望每天运行，请明确提出调度需求，
再通过当前 Codex 环境支持的自动化功能配置；仓库没有偷偷安装后台服务。

## 材料抽检与质量反馈

默认每十个已确认投递的不同岗位抽检一份。协调技能读取投递前冻结的材料，
交给两个独立上下文分别从招聘视角和事实依据评审，疑似问题进入本地待办和
工作台，并在当前会话中向你呈现。它是模型代理意见，不是公司实际反馈。
抽检不能追回已经发送的材料，也不能保证未抽中的申请没有问题。

人工检查可用 `quality-sync`、`quality-inbox` 和 `quality-report TICKET_ID`。
评审积压不会自行消失；Codex 未运行时没有后台评审。发布前偏差测试、配置、
完整命令和根因分析流程见 [质量评测与受控迭代](QUALITY.md)。

## 文件和配置

默认配置为 `configs/agent.yaml`。其中的路径相对于该 YAML 所在目录解析：

```yaml
data_dir: ../data/local
logs_dir: ../logs
daily_job_limit: 100
high_fit_reserved: 20
request_timeout_seconds: 20
browser:
  headless: true
  timeout_ms: 15000
policy:
  auto_fit: []
  review_companies: []
  allowed_domains: []
  daily_submission_limit: 50
  require_review_for_rewrites: true
```

这份示例保留审核模式。`auto_fit` 可选择 `possible`、`strong` 中获你授权的档位；
例如仅选 `possible`，高匹配岗位仍会要求审核。`allowed_domains` 填写准确的主机名，
如 `jobs.lever.co`，不写协议、路径或通配符。`review_companies` 使用数据库中的公司
标识；公开岗位板当前保存的是 board token，Codex 会先核实标识再配置。

`daily_job_limit` 限制一次计划返回的岗位数，协调技能同时遵守本次会话的处理预算。
`daily_submission_limit` 由执行器按 UTC 日统计**提交尝试**；结果不确定的尝试也
消耗额度。改写事实句默认需要审核；即使你允许改写自动提交，内容也必须有事实支持。

不同用户应复制一份配置并设置各自的 `data_dir`，不要共用一个用户档案。命令统一
使用全局选项 `--config`，放在子命令前：

```sh
uv run applypilot-agent --config configs/agent.yaml status
```

本地数据默认位于 `data/local/`，包括 SQLite 状态、资料、材料和浏览器证据；日志
在 `logs/`。不要把它们连同简历、联系方式或浏览器会话凭据提交到 Git。模型处理的
内容会进入当前 Codex 会话；本地保存不等于所有推理都离线完成。

少数已核实的表单可以另外配置 `browser.allowed_origins`，列出它需要的准确来源
（包含初始表单来源）；它与自动授权使用的 `policy.allowed_domains` 是不同设置。
如果你明确指定了用于本任务的 Playwright 登录状态文件，可以通过
`browser.storage_state` 使用。该文件含凭据，不应加入 Git，也不会让不支持的控件
自动获得兼容性。助手不会复制你其他浏览器的个人资料来绕过登录。

`browser.executable_path` 默认为 `null`，使用 `playwright install chromium`
下载的匹配版本。若环境无法下载浏览器、只预装了另一份 Chromium（例如某些
云端容器），可以填写该可执行文件路径；相对路径同样按 YAML 所在目录解析。
它同时用于表单浏览器和 PDF 简历渲染。所填浏览器与 Playwright 版本不一致时
可能无法启动或行为不同，此时应以 `playwright install` 的版本为准。

## 操作者命令

普通使用交给技能即可。需要检查或调试时，可以在仓库根目录运行下列命令。
`JOB_ID`、`FACT_ID`、`PACKET_HASH` 都需要替换为真实命令结果。

```sh
# 状态、候选队列、合并待办和某个岗位的完整上下文
uv run applypilot-agent --config configs/agent.yaml status
uv run applypilot-agent --config configs/agent.yaml plan --limit 80
uv run applypilot-agent --config configs/agent.yaml inbox
uv run applypilot-agent --config configs/agent.yaml dashboard
uv run applypilot-agent --config configs/agent.yaml context JOB_ID

# 写输入文件前读取当前结构
uv run applypilot-agent --config configs/agent.yaml schema profile
uv run applypilot-agent --config configs/agent.yaml schema assessment
uv run applypilot-agent --config configs/agent.yaml schema packet
uv run applypilot-agent --config configs/agent.yaml schema browser

# 保存已有真实资料，导入已研究的岗位，或读取一个已核实的公开岗位板
uv run applypilot-agent --config configs/agent.yaml profile-set data/local/profile.json
uv run applypilot-agent --config configs/agent.yaml profile-export data/local/profile.json
uv run applypilot-agent --config configs/agent.yaml import-jobs data/local/researched-jobs.json
uv run applypilot-agent --config configs/agent.yaml discover lever leverdemo

# 基线用于筛选；自定义评估使用当前上下文里的版本和事实证据
uv run applypilot-agent --config configs/agent.yaml assess JOB_ID
uv run applypilot-agent --config configs/agent.yaml assess JOB_ID --path data/local/assessment.json

# 观察、生成确定性的材料、保存申请包、试填（不提交）
uv run applypilot-agent --config configs/agent.yaml inspect JOB_ID
uv run applypilot-agent --config configs/agent.yaml render JOB_ID --fact FACT_ID --fact OTHER_FACT_ID
uv run applypilot-agent --config configs/agent.yaml packet-set data/local/packet.json
uv run applypilot-agent --config configs/agent.yaml execute JOB_ID

# 只有当前包在自动授权范围内，或用户已经批准它时，才能实际提交
uv run applypilot-agent --config configs/agent.yaml execute JOB_ID --submit
uv run applypilot-agent --config configs/agent.yaml events --job-id JOB_ID
```

这里的 `leverdemo` 是 Lever 官方演示岗位板，只用于公开读取演示，不是用户的投递
目标。示例 JSON 随技能保存：

- [个人资料结构](../../.agents/skills/applypilot-onboard/references/profile-example.json)
- [手动研究的岗位](../../.agents/skills/applypilot-discover/references/manual-jobs-example.json)
- [匹配评估结构](../../.agents/skills/applypilot-discover/references/assessment-example.json)
- [申请包结构](../../.agents/skills/applypilot-prepare/references/packet-example.json)
- [审核、提交与核对的完整操作](../../.agents/skills/applypilot-prepare/references/packet-and-execution.md)

这些都是结构示例，包含虚构资料或待替换的版本值；不能把它们当作确认过的用户数据。
每个岗位的真实输入文件应保存到独立子目录，避免覆盖另一份正在审核的材料。
`dashboard` 返回本地可读页面的路径；在 Codex 中打开即可查看当前资料和待办。
这个页面不会自动批准或提交申请，状态变化后可以重新生成。

## 状态与异常处理

| 结果 | 含义与下一步 |
| --- | --- |
| `discovered` / `assessed` | 已入库或已评估，尚未等于准备完成或获准提交 |
| `needs_info` | 有问题需要澄清或执行受阻；查看待办与具体错误 |
| `review` | 有具体申请包需要审核 |
| `ready` | 当前包已准备，可由执行器重新检查授权和版本 |
| dry run 返回 `prepared` | 表单已试填并验证，**没有提交**；浏览器会关闭 |
| `submitting` | 已记录提交意图，可能仍在执行；先核实进程状态 |
| `unknown` | 是否成功不确定，禁止直接重试，先核对真实记录 |
| `submitted` | 执行器观察到回执，或有明确标记的用户核实记录 |
| `retryable` | 用户核实未提交后可重试；旧审核已失效 |
| `skipped` | 当前事实和要求下不应继续 |

不要通过修改数据库“解决”未知结果。`recover` 只用于确认旧执行器已经停止后的
中断恢复；不要在另一个活跃提交进程运行时调用。`reconcile` 需要用户核实后的
来源和观察记录，不是让模型填写一段看起来像成功的文字。没有收到邮件不等于未提交。

## 当前适用范围

- 直接读取 Greenhouse、Lever 全球实例、Ashby 公开岗位板；其他站点可通过 Codex
  研究后导入。当前不包含整个互联网的公司目录，也不保证自动识别所有招聘系统。
- 浏览器执行器使用隔离上下文，支持可观察的原生输入、单选下拉、勾选和单文件上传。
  动态控件、iframe、登录、验证码、跨域依赖或填写期间的后台写入可能需要专用适配。
  不能为了通过流程而跳过检查或使用其他浏览器直接提交。
- 自动提交必须有真实可验证的完成条件。当前没有为所有 ATS 预置完整的表单和回执
  适配表；无法建立完成条件时可以准备材料，但会留下具体交接事项。
- 内置材料生成器把选中的已确认记录/字段按档案分区排成简洁文档；证据 ID 来自
  `context.profile_evidence`，如 `contact.email`。它不自动创造经历，也不
  保证所有简历风格都适合；高匹配岗位应检查最终文档，并在需要时审核更丰富的改写。
- 事实引用和文件哈希能检查来源、版本和文件完整性，不能证明任意文字改写语义真实。
  模型评审是辅助信号，真实用户反馈、人工盲评和招聘结果仍需分别采集。
- 技能和本地工具面向协作式 Codex 使用；它们不是限制拥有完整 shell 权限的恶意代理
  的安全沙箱。维护代码或技能时仍须独立检查回归和行为边界。

开发时可另行运行 [当前档案更新实验](PROFILE_MEMORY_EXPERIMENT.md)，比较 Codex
直接更新与 LangMem Profile。它需要可选 `memory-eval` 依赖，只使用合成资料，
不初始化或修改个人档案；不属于日常求职流程。

工程验证可运行：

```sh
uv run python -m pytest tests/agent
uv run ruff check src/applypilot_agent tests/agent
```

浏览器测试使用受控表单；通过这些测试不代表已向真实公司提交申请，也不代表覆盖
了全部真实 ATS。评测里的合成样本用于验证工程逻辑，不能宣称是用户标注的招聘效果 GT。
