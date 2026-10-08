# Codex Job Agent

[English](README.md) · **简体中文**

**让 LLM Agent 安全执行真实世界不可撤销操作的运行时，以求职投递为案例。**

**[回放一次真实运行](https://witazhang.github.io/codex-job-agent/demo/#zh)**（无需安装）：三份申请经过同一个执行器，其中一份始终没有出现提交确认。页面由一次真实的演示运行生成，源文件为 [`docs/demo/index.html`](docs/demo/index.html)。

## 要解决的问题

Agent 擅长判断，不擅长保证。申请、邮件、付款一旦发出就收不回来，真正要防的是机械性的失败：未经允许就执行、审核后材料又变了却照样提交、重复提交、把没看到的成功当成成功。

这个项目按这条线分工。

| | Codex（技能） | 运行时（Python） |
| --- | --- | --- |
| 负责 | 匹配判断、选用哪些已确认事实、答案和措辞 | 授权、版本、状态、浏览器执行、回执、重试规则 |
| 出错时 | 由抽样的独立评审发现 | 不允许出错：由代码保证，并有测试覆盖 |

模型说"已提交"不算数。只有执行器能提交，只有观察到的回执才算成功。

## 一、执行边界

- **批准绑定整份申请包。** 答案、附件哈希、岗位和档案版本、浏览器计划合成一个哈希；任何一项变化，批准都失效。
- **由策略决定走哪条路，默认需要审核。** 自动提交只限用户授权的匹配档位、域名和公司，并受每日提交尝试上限约束。
- **先试填。** 到提交那一步之前，浏览器拦截所有写请求和跨域加载。
- **岗位内容是不可信输入。** 它不能授权操作，也不能修改策略。

## 二、故障恢复

- **先记录意图，再执行。** 点击提交前先持久化提交意图，并持有每个岗位的租约锁，两个执行器不能同时提交同一份申请。
- **不确定就保持不确定。** 回执缺失或进程中断时状态记为 `unknown`，禁止自动重试；只有带用户核实证据的对账，才能改成 `submitted` 或 `retryable`。
- **辅助流程不能覆盖结果。** 质量抽检失败会留在待办里，不会改动已确认的提交。

## 三、评测

- **抽检实际发出的材料。** 提交的材料被冻结，按可配置比例（默认每 10 份抽 1 份）交给两个全新上下文：一个判断招聘相关性，一个逐条核对事实依据。每条发现都要引用来源 ID 和原文。
- **评审器本身也要检验。** 六组合成用例，每组按 A/B 两种顺序各评一次，共 12 次判断，检查冗余扩写偏好、顺序偏差、等长劣化和有用扩写。该次运行中冗余扩写偏好 0/4，顺序不一致 0/6。在植入缺陷的测试里，事实评审在没有提示的情况下指出了一个无来源的"收入增长 80%"。
- **设计选择用实验决定。** 档案更新用 Codex 直接修改和用 LangMem，在合成的多轮修改上都是 24/24，所以保留更简单的直接更新。见[实验记录](docs/agent/PROFILE_MEMORY_EXPERIMENT.md)。
- **245 项测试**，包括用真实 Chromium 对本地模拟 ATS 的测试，在 Windows 和 Ubuntu CI 上运行。

## 回放展示了什么

| 场景 | 过程 | 招聘方收到 | Agent 观察到 | 最终状态 |
| --- | --- | --- | --- | --- |
| 需要审核 | 批准前尝试提交被拒绝；试填 0 次 POST；批准绑定申请包哈希 | 1 次 POST | 回执 | `submitted` |
| 在自动授权范围内 | 策略允许自动提交 | 1 次 POST | 回执 | `submitted` |
| 回执没有出现 | 策略允许自动提交 | 1 次 POST | 无 | `unknown`，拒绝重试 |

每个场景中，招聘方收到的简历哈希都与批准的申请包一致。回放使用合成的人和岗位，其中没有运行语言模型，检验的是运行时。

## 案例：个人求职 Agent

<picture>
  <source media="(max-width: 700px)" srcset="docs/assets/architecture.zh-CN.compact.svg">
  <img src="docs/assets/architecture.zh-CN.svg" alt="你的求职目标指导广泛发现机会与定制申请材料；投递规则决定自动投递或由你审核，进展与反馈回到你手中。">
</picture>

这套运行时在 Codex 里驱动一个求职流程。用户先确认自己的经历、偏好，以及哪些申请可以自动发出。之后 Codex 从 Greenhouse、Lever、Ashby 岗位板找岗位，带依据地判断匹配度，只用已确认的事实准备材料，并在保存的策略范围内提交。需要用户回答的问题和审核的材料集中呈现。

| 技能 | 职责 |
| --- | --- |
| `$applypilot` | 协调会话、队列、申请和质量复核 |
| `$applypilot-onboard` | 确认事实、求职偏好、限制和投递授权 |
| `$applypilot-discover` | 发现岗位、研究要求并记录有依据的匹配判断 |
| `$applypilot-prepare` | 准备申请包、观察表单、试填并执行获准的提交 |

技能位于 [`.agents/skills/`](.agents/skills/)。为兼容已有用法，保留 `applypilot*` 技能名、`applypilot-agent` CLI 和 `applypilot_agent` 模块名。日常用法见[使用指南](docs/agent/USAGE.md)。

## 运行

需要 [uv](https://docs.astral.sh/uv/)，依赖版本由 `uv.lock` 锁定。

```sh
git clone https://github.com/WItaZhang/codex-job-agent.git
cd codex-job-agent
uv sync --locked --extra dev
uv run playwright install chromium

# 在隔离的本地模拟 ATS 上跑三个场景，再生成回放页
uv run python -m applypilot_agent.demo --config configs/demo.yaml
uv run python docs/demo/build_replay.py logs/<run_id>

# 工程检查
uv run ruff check src/applypilot_agent tests/agent
uv run ruff format --check src/applypilot_agent tests/agent
uv run python -m pytest tests/agent -q
uv run python -m applypilot_agent.evaluation --config configs/evaluation.yaml
```

如果 Playwright 无法下载浏览器，可在 YAML 配置里把 `browser.executable_path` 设为已安装的 Chromium。

要用于真实求职，把目录作为 Codex 项目打开，然后说：

> 用 $applypilot-onboard 配置我的求职助手。我会提供简历，请先整理经历、求职方向和限制，再与我确认哪些岗位可以自动投递、哪些需要先审核材料。

[`configs/agent.yaml`](configs/agent.yaml) 管理预算、策略和浏览器设置。个人状态保存在被 Git 忽略的 `data/local/`，运行产物写入 `logs/`。

## 适用范围与限制

- **浏览器支持：** 面向可观察的原生表单。登录、验证码、iframe 和复杂控件需要适配或交给人工，许多大型 ATS 尚未覆盖。
- **申请材料：** 来源引用和文件哈希检查出处与完整性，不能证明每一处改写都符合事实。
- **证据性质：** 模型评审是代理判断，合成标签是工程用例。目前没有实测的面试率、录用率或成本数据。
- **隔离程度：** 工具保证受支持的执行路径，不是针对拥有完整 shell 权限的 Agent 的安全沙箱。
- **运行方式：** 只在 Codex 会话运行期间工作，没有后台调度。

详见[质量设计](docs/agent/QUALITY.md)和[验证记录](docs/agent/VERIFICATION.md)。

## 项目导航

```text
src/applypilot_agent/   运行时：契约、策略、持久化、浏览器、执行、质量、评测
.agents/skills/         Codex 技能及操作参考
tests/agent/           单元、集成与本地浏览器测试
evals/                 明确标注来源的合成评测用例
configs/               运行、演示和评测配置
docs/                  设计、使用、验证文档与演示回放
```

| 文档 | 内容 |
| --- | --- |
| [架构设计](docs/agent/ARCHITECTURE.md) | 系统组成、职责划分与设计取舍 |
| [使用指南](docs/agent/USAGE.md) | 安装、命令与操作边界 |
| [实现契约 · English](docs/agent/IMPLEMENTATION.md) | 数据契约与模块职责 |
| [质量评测](docs/agent/QUALITY.md) | 抽样复核、评审器校准与受控迭代 |
| [评测数据 · English](evals/README.md) | Ground truth、来源与数据集约定 |
| [验证记录 · English](docs/agent/VERIFICATION.md) | 已完成的检查及适用范围 |

## 许可与致谢

由 **WItaZhang** 开发和维护。项目来源与致谢见 [NOTICE.md](NOTICE.md)，采用 [AGPL-3.0-only](LICENSE) 许可证。
