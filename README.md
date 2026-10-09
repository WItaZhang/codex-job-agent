# 岗位挖掘 Agent（意图维护）

每天给你 7 个推荐岗位和 3 个探索岗位。你在对话里给每个岗位打 label（想投 / 不想投 / 读错了），
agent 分析哪里没对上，给出几个修改选项；你选了，系统才精确修改你的求职意图里对应的那一条。

需求和规则见 [spec.md](spec.md)，开发约定见 [CLAUDE.md](CLAUDE.md)。

## 在 Claude Code 里使用

1. 安装依赖：`uv sync`，然后 `uv run playwright install chromium`（补全原文要用浏览器）
2. 把个人文件放进 `data/local/`（已被 git 忽略）：
   - 意图初始文件：参照 [docs/examples/intent.example.yaml](docs/examples/intent.example.yaml)，存为 `data/local/intent.yaml`
   - 搜索设置：参照 [docs/examples/searches.example.yaml](docs/examples/searches.example.yaml)，存为 `data/local/searches.yaml`
     （按关键词 × 地点搜索，与 ApplyPilot 相同；也可以让 agent 根据你的意图生成）
   - 可选：`employers.yaml`（Workday 雇主，见 [示例](docs/examples/employers.example.yaml)）、`sites.yaml`（smartextract 站点）、
     `boards.yaml`（Greenhouse / Lever / Ashby，见 [示例](docs/examples/boards.example.yaml)）
   - 其他来源的岗位：参照 [docs/examples/jobs.example.json](docs/examples/jobs.example.json)，存为 `data/local/jobs.json` 后导入
3. 在仓库目录启动 Claude Code。第一次会询问是否启用项目里的 `intent-agent` MCP 服务，选择启用。
4. 对 agent 说，例如：
   - "用 data/local/intent.yaml 初始化我的意图"
   - "检查一下岗位发现的设置"（agent 会问你要不要配 LLM key，并帮你生成 searches.yaml）
   - "开始找岗位，找完给新岗位打标签，然后选出今天的岗位"
   - "导入 data/local/jobs.json"
   - "第 3 个不要，因为要坐班；第 5 个想投"
   - "选 2"

## 岗位从哪里来

岗位发现复刻自 [ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot)：用 JobSpy 按关键词和地点搜索 Indeed、LinkedIn、
Glassdoor、ZipRecruiter、Google Jobs，另有 Workday 雇主搜索和 smartextract（解析招聘页面），最后打开岗位页面补全原文。

- **LLM key（可选）**：只有 smartextract 和补全原文的最后一步需要。要用的话，自己在 `data/local/.env` 写一行
  `GEMINI_API_KEY=...`（或 `OPENAI_API_KEY=...`、`LLM_URL=...`），不要把 key 贴进对话。不写也能用，这两步会跳过。
- **代理（可选）**：被限流或封 IP 时，设置环境变量 `INTENT_AGENT_PROXY=host:port`（或 `host:port:user:pass`）。
- **风险**：这些网站的服务条款禁止自动抓取，可能被限流或封 IP，网站改版后可能失效。仅供个人求职使用。
- 没有自己的 `employers.yaml`、`sites.yaml` 时使用 ApplyPilot 自带的列表（以加拿大雇主和站点为主）；
  只想搜美国岗位的话，可以写 `employers: {}` 和 `sites: []` 把它们关掉。

## 关于确认弹窗（重要）

记录 label、确认修改、修正岗位标签、初始化意图这四个工具，**每次都会弹窗请你批准**。这是你本人做决定的唯一凭证：

- 项目的 `.claude/settings.json` 把它们设为"每次询问"；服务端也给它们加了"每次都需要用户操作"的标记。
- 请不要为这几个工具添加"总是允许"的规则，也不要在 dontAsk 模式下使用（该模式会直接拒绝它们）。
- 确认修改时，弹窗里会显示选项编号和修改内容原文，例如 `option=2, summary="company_type.big_tech：回避 → 强烈回避"`。内容和你看到的不一致时，代码会拒绝。

## 升级

`git pull` 只更新代码，`data/local/` 里的意图、label 和历史不受影响。pull 之后运行 `uv sync`，并在 Claude Code 里用 `/mcp` 重连服务（或重启）。

## 许可证

AGPL-3.0-only，来源说明见 [NOTICE.md](NOTICE.md)。

## 开发

```text
uv sync --locked
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m pytest -q
```
