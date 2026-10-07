# 当前档案更新实验

本实验回答：连续修改偏好时，Codex 直接修订完整档案与 LangMem Profile
更新机制，哪一种更可靠地保持一份完整、自洽的当前档案？

这是开发期、公开合成用例上的小规模比较，不是生产功能，也不比较 Codex
原生后台 Memories。后者包含异步提炼和跨会话注入，不能与本次即时画像
更新的结果混称。

## 两个实验组

| 项目 | Codex 直接更新 | LangMem Profile |
| --- | --- | --- |
| 当前档案 | 同一个 Pydantic schema | 同一个 Pydantic schema |
| 新输入 | 当前一条用户指令或明确标记的不可信岗位内容 | 相同 |
| 模型 | YAML 中同一个 Codex 模型与推理档位 | 相同 |
| 更新协议 | 一次 Profile 工具调用，返回完整当前状态 | 真正调用 `langmem.create_memory_manager` 和 Trustcall |
| 插入画像 | 返回一份完整档案 | `enable_inserts=False`，更新固定画像 ID |
| 后续轮次 | 读取上轮实际保存的状态 | 相同 |
| 历史聊天、参考答案 | 不提供 | 不提供 |

两组通过同一个 `BaseChatModel` 适配器调用 `codex exec`，用 JSON envelope
承载文本和工具调用，复用已有 Codex 登录。无需读取认证文件或额外配置模型
API key。它测试的是 LangMem 经这个适配器工作的效果，不是某个模型供应商
原生工具调用 API 的表现。

模型选择记录为 requested model；如果 CLI 没有报告实际后端快照，保持未知，
不能把选择器字符串当成固定模型快照。默认实验使用本机已配置的模型系列
`gpt-6-astra`，两组统一使用 `medium` 推理档位，配置和 CLI 版本随运行冻结。

## 用例和评分

`evals/synthetic/profile_memory_cases.jsonl` 明确标注 synthetic。每个场景有
初始档案、连续输入和每轮完整参考档案，覆盖偏好反转、关联字段清理、公司
例外、无关内容保留、事实纠正、歧义、不可信岗位内容及一轮多项修改。

生产模型只收到 schema、当前预测档案和新输入。参考档案、场景标签和检查点
保留在评估侧。评分由纯 Python 完成，不使用模型意见作为真实用户标签：

- 每轮完整档案是否与参考一致；工作方式集合忽略顺序，缺失和 null 不混同。
- 顶层字段正确率、未要求修改的叶字段保留率。
- 只读当前档案的检查点通过率；这是确定性状态检查，不是新 LLM 会话问答。
- 关联字段冲突轮数，以及整条场景链是否始终正确。
- 实际 CLI 调用数、报告的 token 用量和耗时；不推算未测量的费用。

一个场景的后续轮次继续使用该方案自己的预测状态，绝不注入正确答案重置。
执行或解析失败会记录 error，后续轮次标记 skipped；不把供应商错误转换为
业务分数，也不根据剩余成功子集宣布胜者。LangMem 可用配置的调用上限进行
其原生格式修复；直接完整档案组只调用一次，两组实际开销分别报告。

## 运行

在仓库根目录运行：

```sh
uv sync --locked --extra dev --extra memory-eval
uv run --extra dev --extra memory-eval python -m applypilot_agent.evaluation.profile_memory --config configs/profile_memory_smoke.yaml
uv run --extra dev --extra memory-eval python -m applypilot_agent.evaluation.profile_memory --config configs/profile_memory.yaml
```

smoke 只验证真实调用链和格式，不计入正式比较。完整配置默认 8 个场景、每个
3 轮、两组各一次，共 48 次状态更新；最多 120 次底层模型调用，最多两个
并行场景链。所有路径、模型参数、超时和预算都在 YAML 中。`--config` 仅选择
实验配置，不覆盖其中参数。`seed` 只固定场景链的执行顺序，不固定模型采样；
默认每组仅一次运行，不能据此推断统计显著性。

依赖在 `memory-eval` optional extra，随 `uv.lock` 固定，不加入生产所需依赖。
已有 Codex 登录是调用前提；本实验不读取密钥，也不更改个人记忆设置。

## 隔离和证据

每次底层模型调用使用 fresh、ephemeral、read-only Codex 会话，关闭该次
调用的后台记忆读写、网络检索、应用、插件及 shell 工具。独立调用目录只放
必要输入和协议记录，不向模型提供仓库参考答案。read-only 本身不是读取隔离
沙箱；若 JSONL 记录出现真实工具调用，适配器将该次调用标记为污染并拒绝结果。

输出位于 `logs/YYYYMMDD_HHMMSS_<experiment>_<id>/`：

- `inputs/`：原配置、解析后的配置、合成用例、schema、实验代码和锁文件快照。
- `manifest.json`：输入 SHA256、包和 CLI 版本、执行顺序、调用上限。
- `trials/`：每条链的 `current.json`，每轮输入、输出和底层调用证据。
- `run.log`：运行过程的 stdout/stderr；`run.jsonl`、`results.json`：完整结果和错误记录。
- `metrics.json`、`report.md`：统计与可读报告。

这次比较不初始化个人 `data/local`、不更改真实用户档案、不执行浏览器投递。
可见用例是开发用例；检查失败后再改设计，必须保留原运行并标注新实验版本。
不能把重跑后的提升称为未见过用例上的泛化收益。

## 首轮结果：2026-10-06（America/Los_Angeles）

正式运行 ID：`20261007_024239_profile_memory_comparison_4c5d6fde`（目录时间为 UTC）。
使用 `codex-cli 0.160.1`、LangMem `0.0.30`、Trustcall `0.0.39`，模型选择和
推理档位为 `gpt-6-astra / medium`；CLI 没有报告实际模型快照。

| 指标 | Codex 直接更新 | LangMem Profile |
| --- | ---: | ---: |
| 当前档案完全正确 | 24/24 | 24/24 |
| 全部三轮均正确的场景 | 8/8 | 8/8 |
| 未要求修改的叶字段保留 | 190/190 | 190/190 |
| 关联字段冲突 / 执行错误 / 污染调用 | 0 / 0 / 0 | 0 / 0 / 0 |
| 实际模型调用 | 24 | 24 |
| 每轮中位耗时 | 6.33 秒 | 6.89 秒 |
| CLI input tokens（含报告的缓存部分） | 323,930 | 343,530 |
| CLI cached input tokens | 73,728 | 98,304 |
| CLI output tokens | 2,221 | 3,002 |

两组的 168 个顶层字段检查和 73 个状态检查点也全部通过。耗时包含 CLI 启动和
并发调度，token 包含共享的 Codex harness 开销；缓存比例不同，不能据此直接
推算费用。此次没有 LangMem 修复重试，因此也没有测出其修复机制的收益。

这轮支持的判断是：在明确的结构、更新规则和这些合成场景下，两种方法都能
维护一份正确的当前档案。没有观察到引入 LangMem 的准确率收益，因此当前
可以优先采用更简单的直接更新方案，并保留这一评测作为回归基线。它不能证明
两种方案普遍等价，也不能验证生产接入、长对话、大型档案或后台 Memories。

首次 smoke 发现 CLI 启动警告被误判为工具污染；修正日志解析并通过测试后，
第二次 smoke 完成。两次 smoke 的原始证据均保留，未计入正式结果；正式运行
开始后未调整用例、指令或评分。完整报告、48 次调用原始证据和冻结输入位于
上述 ID 的 `logs/` 目录。项目 lint、格式、单元测试和隔离本地 ATS 演示也已
通过，实验没有创建个人 `data/local`。

## 一手资料

- [LangMem Profile 模式](https://langchain-ai.github.io/langmem/guides/manage_user_profile/)
- [LangMem Memory API](https://langchain-ai.github.io/langmem/reference/memory/)
- [Codex 非交互调用与结构化输出](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex 原生 Memories：不属于本次比较](https://learn.chatgpt.com/docs/customization/memories)
