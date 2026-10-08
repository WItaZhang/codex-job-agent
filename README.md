# 岗位挖掘 Agent（意图维护）

每天给你 7 个推荐岗位和 3 个探索岗位。你在对话里给每个岗位打 label（想投 / 不想投 / 读错了），
agent 分析哪里没对上，给出几个修改选项；你选了，系统才精确修改你的求职意图里对应的那一条。

需求和规则见 [spec.md](spec.md)，开发约定见 [CLAUDE.md](CLAUDE.md)。

## 在 Claude Code 里使用

1. 安装依赖：`uv sync`
2. 把个人文件放进 `data/local/`（已被 git 忽略）：
   - 意图初始文件：参照 [docs/examples/intent.example.yaml](docs/examples/intent.example.yaml)，存为 `data/local/intent.yaml`
   - 岗位：参照 [docs/examples/jobs.example.json](docs/examples/jobs.example.json)，存为 `data/local/jobs.json`
3. 在仓库目录启动 Claude Code。第一次会询问是否启用项目里的 `intent-agent` MCP 服务，选择启用。
4. 对 agent 说，例如：
   - "用 data/local/intent.yaml 初始化我的意图"
   - "导入 data/local/jobs.json，给岗位打标签，然后选出今天的岗位"
   - "第 3 个不要，因为要坐班；第 5 个想投"
   - "选 2"

## 关于确认弹窗（重要）

记录 label、确认修改、修正岗位标签、初始化意图这四个工具，**每次都会弹窗请你批准**。这是你本人做决定的唯一凭证：

- 项目的 `.claude/settings.json` 把它们设为"每次询问"；服务端也给它们加了"每次都需要用户操作"的标记。
- 请不要为这几个工具添加"总是允许"的规则，也不要在 dontAsk 模式下使用（该模式会直接拒绝它们）。
- 确认修改时，弹窗里会显示选项编号和修改内容原文，例如 `option=2, summary="company_type.big_tech：回避 → 强烈回避"`。内容和你看到的不一致时，代码会拒绝。

## 开发

```text
uv sync --locked
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m pytest -q
```
