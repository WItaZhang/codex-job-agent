# R1b 规格与能力探针交审记录

状态：规格/计划/独立合成探针完成，等待甲方及Claude正式复核；**未实现产品代码**。本次不是R1b最终验收或公众发布验收。总决定29；[规格](public-r1b.md)、[计划](public-r1b.plan.md)、[决定](public-r1b.decisions.md)。

## 基线和历史

基线main `85305234c57713efe8738abf0dd64aed220aef3f`。甲方提供R1分支CI39918253389对应34221b1；main CI39919661089全success：1627 passed/122.49s、0skip，wheel/sdist/editable通过，原日志`/tmp/public-r1-main-ci-39919661089.log`。合并后本地四项1627 passed/66.13s。未改写R1旧报告，以上不意味着该HEAD包含R1b能力。

## 采纳与结果

- B1：submit/fill × 未launch/已launch四格与停止优先序已入原文；未知submit必须held/interrupted，确定未launch无虚假attempt，已知成功不回退。
- B2：旧/不同generation不接管旧tab，但保留安全重新打开岗位地址和核查已提交的恢复路径。
- S2：管理锁加已有owner，无lease；独立填表也互斥。旧复制profile不采用/迁移/删除，接受重新登录。
- S1：本机macOS能力成立，不要求强制lsof；不是恶意同用户进程认证，也没有证明Linux/Windows兼容或产品所有重连时序。

可复现命令：`uv run python scripts/probe_browser_binding.py`。需已安装锁定Python Playwright浏览器及npx；脚本会下载固定MCP包。脚本只创建临时空profile和about:blank、设置合成标题，结束仅清自己的Popen进程；不读profile.json/.env，不用现有Chrome资料，不调用产品业务工具。浏览器后台网络削减参数不是网络沙箱，不宣称零网络数据包。

[机器结果](evidence/public-r1b-browser-probe.json)和[脚本](../scripts/probe_browser_binding.py)入库，不能仅依赖/tmp。动态端点的正确WS连接成功，CDP browserPID等于Popen PID，OS started稳定，OS和CDP profile参数匹配。Python错误GUID连接拒绝。**同一MCP0.0.83**：正确WS的browser_tabs返回合成标题和about:blank；错误GUID实际browser_tabs返回is_error，错误文本为连接错误且不含合成标题。此对照证明本次调用未回退HTTP发现活浏览器，不外推为全部未来连接行为保证。

运行时分开记录：Python Playwright **1.56.0**，Chromium **141.0.7390.37**；npm实际安装`@playwright/mcp` **0.0.83**。MCP initialize的server_version为**1.64.0-alpha-1790635538000**；从此包安装树读到Node `playwright`和`playwright-core`也均为该版本。这不是Python版本，也未执行升级。固定顶层包与实际传递依赖版本的证据均保留。

探针早期三次脚手架失败留底：sync Playwright事件循环中直接asyncio.run失败，改专用线程；npm用户/全局配置同为/dev/null导致double-loading，改独立空临时配置；SDK用serverInfo而非server_info导致读取版本失败，修正Python字段。没有改产品或放宽能力断言。随后成功，补实际包版本及本轮MCP错误GUID对照再次成功。依赖升级提示未执行。

技术背景：[Playwright CDP接口](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)、[Chrome独立user-data-dir说明](https://developer.chrome.com/blog/remote-debugging-port)。锁定版本的实际探针为本阶段直接证据。

## 验证与交接

本阶段四项：uv sync --locked、ruff check src tests、ruff format --check src tests全部通过；pytest **1627 passed in 72.97s**。之后只补探针对照和文档；脚本独立ruff及真实合成探针通过，未重复无关全套。产品先红测试、完整调用迁移、事件屏障/恢复/跨会话互斥、受支持平台实证均尚未执行，按计划在阶段批准后开展。没有修改src/vendor/CI/依赖或local_only。

后续实现预估2.5–4.5有效工作日；当前阻塞是明确的规格/探针阶段复核门，而非能力失败。通过后按计划逐步先红实现，不推送/合并。R4资料跨目录及每用户授权、R5可靠提交、R6其余恢复/N2、R8安装发行等保留后续，不以本探针替代。
