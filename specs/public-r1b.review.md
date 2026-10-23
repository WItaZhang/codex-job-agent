# R1b 实现与验证交审记录

状态：产品实现及本地验证完成，待独立代码复核和协调者推送后的Linux CI；不推送/合并，不宣称公众发布验收完成。总决定29；[规格](public-r1b.md)、[计划](public-r1b.plan.md)、[决定](public-r1b.decisions.md)。以下早期阶段记录为历史证据，其“未实现/等待批准”只描述当时状态，最终结果见末尾。

## 基线和历史

基线main `85305234c57713efe8738abf0dd64aed220aef3f`。甲方提供R1分支CI39918253389对应34221b1；main CI39919661089全success：1627 passed/122.49s、0skip，wheel/sdist/editable通过，原日志`/tmp/public-r1-main-ci-39919661089.log`。合并后本地四项1627 passed/66.13s。未改写R1旧报告，以上不意味着该HEAD包含R1b能力。

## 采纳与结果

- B1：submit/fill × 未launch/已launch四格与停止优先序已入原文；未知submit必须held/interrupted，确定未launch无虚假attempt，已知成功不回退。
- B2：旧/不同generation不接管旧tab，但保留安全重新打开岗位地址和核查已提交的恢复路径。
- S2：管理锁加已有owner，无lease；独立填表也互斥。旧复制profile不采用/迁移/删除，接受重新登录。
- S1：本机macOS能力成立，不要求强制lsof；不是恶意同用户进程认证，也没有证明Linux/Windows兼容或产品所有重连时序。

原headless实证对应f64140e脚本；其原始JSON保持不变，含当时的enable-automation及CDP命令行检查，不代表当前必需组合。当前系统Chrome复现命令见下方A/B补充。需已安装锁定Python Playwright浏览器及npx；脚本会下载固定MCP包。脚本只创建临时空profile和about:blank、设置合成标题，结束仅清自己的Popen进程；不读profile.json/.env，不用现有Chrome资料，不调用产品业务工具。浏览器后台网络削减参数不是网络沙箱，不宣称零网络数据包。

[原headless Chromium机器结果](evidence/public-r1b-browser-probe.json)和[脚本](../scripts/probe_browser_binding.py)入库，不能仅依赖/tmp。动态端点的正确WS连接成功，CDP browserPID等于Popen PID，OS started稳定，OS和CDP profile参数匹配。Python错误GUID连接拒绝。**同一MCP0.0.83**：正确WS的browser_tabs返回合成标题和about:blank；错误GUID实际browser_tabs返回is_error，错误文本为连接错误且不含合成标题。此对照证明本次调用未回退HTTP发现活浏览器，不外推为全部未来连接行为保证。

运行时分开记录：Python Playwright **1.56.0**，Chromium **141.0.7390.37**；npm实际安装`@playwright/mcp` **0.0.83**。MCP initialize的server_version为**1.64.0-alpha-1790635538000**；从此包安装树读到Node `playwright`和`playwright-core`也均为该版本。这不是Python版本，也未执行升级。固定顶层包与实际传递依赖版本的证据均保留。

探针早期三次脚手架失败留底：sync Playwright事件循环中直接asyncio.run失败，改专用线程；npm用户/全局配置同为/dev/null导致double-loading，改独立空临时配置；SDK用serverInfo而非server_info导致读取版本失败，修正Python字段。没有改产品或放宽能力断言。随后成功，补实际包版本及本轮MCP错误GUID对照再次成功。依赖升级提示未执行。

技术背景：[Playwright CDP接口](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)、[Chrome独立user-data-dir说明](https://developer.chrome.com/blog/remote-debugging-port)。锁定版本的实际探针为本阶段直接证据。

## 验证与交接

本阶段四项：uv sync --locked、ruff check src tests、ruff format --check src tests全部通过；pytest **1627 passed in 72.97s**。之后只补探针对照和文档；脚本独立ruff及真实合成探针通过，未重复无关全套。产品先红测试、完整调用迁移、事件屏障/恢复/跨会话互斥、受支持平台实证均尚未执行，按计划在阶段批准后开展。没有修改src/vendor/CI/依赖或local_only。

后续实现预估2.5–4.5有效工作日；当前阻塞是明确的规格/探针阶段复核门，而非能力失败。通过后按计划逐步先红实现，不推送/合并。R4资料跨目录及每用户授权、R5可靠提交、R6其余恢复/N2、R8安装发行等保留后续，不以本探针替代。

## 正式评审A/B窄复核补充

甲方采纳`/tmp/public-r1b-spec-review.md`两项。A选(a)，必需证据改为本profile精确WS、OS PID/start/profile命令行及连接后SystemInfo browserPID。不再调用Browser.getBrowserCommandLine，不加enable-automation；不承诺无webdriver标志或规避检测。旧headless Chromium141证据仍保留，仅证明原二进制/模式；未改写旧JSON为新行为。

实际系统Chrome有头实证一次通过：[完整JSON](evidence/public-r1b-system-chrome-probe.json)。命令`uv run python scripts/probe_browser_binding.py --system-chrome`。Darwin/macOS26.5.1、arm64，二进制`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`，版本155.0.8059.85，headless=false，动态端口0及全新临时空profile。全部相关启动参数在JSON，临时路径脱敏为占位符。没有enable-automation，没有CDP命令行读取；SystemInfo browserPID等于Popen PID，OS start稳定及profile参数匹配。Python正确WS成功/错误GUID拒绝；MCP0.0.83正确WS读到合成标题，错误GUID的browser_tabs返回错误且标题不可见。Python1.56.0与Node/MCP报告的1.64.0-alpha仍分别留证，没有升级。仅about:blank，未访问真实网站/日常profile；浏览器后台联网不在零流量保证范围。Linux/Windows仍未验证。

B已同步form-fill原文及R1b规格/计划/决定：不依赖领域数据，仅允许协调行/管理锁例外；kind=form_fill+runid识别self._form_fill_run。正常running及收尾不误kill；真正失联只清身份匹配的本运行进程并结束协调行，不走_close_interrupted、不写application/attempt/hold。other_session文案区分独立填表。三组事件屏障（running时apply_status kill0、正常结束恢复交错、真失联）和nonowner Target.attachToTarget/Runtime.evaluate调用0已入先红计划，**尚未实现或运行这些产品回归**。

本次针对验证：系统Chrome合成探针成功，脚本ruff check/format通过，git diff --check及文档相对链接检查通过。按甲方要求不重复1627全套；先前f64140e阶段全套记录仍有效于当时代码，不冒充本轮新增产品测试。src/依赖/CI未改；等待甲方和Claude对A/B窄复核后再产品实现。

## 产品实现阶段批准

甲方与Claude已批准b2716f5（/tmp/public-r1b-spec-review.md末尾），进入计划1–9的全部产品实现。唯一编辑树public-r1b-browser；每个绿步提交，不推送/合并。预计3–5有效工作日，仅规划估计。最终必须分别给出macOS产品参数本地fixture和Linux CI证据，Windows未验证不回落。以下后续记录逐步追加红绿及最终交审结果。

### 实现小步1：入口行为红与绑定基础

新接口test_browser_binding先因模块缺失collection error（1 error）；这仅是接口不存在证据。现有入口独立test_browser_entry_boundary有效行为红为3 failed：ensure_chrome返回foreign固定9222；setup_worker_profile命中复制毒丸；open_tabs返回foreign标签。前两次测试夹具误用settings参数/修改冻结Storage导致失败，修正后才取得上述行为红，不混作产品证据。迁移ensure_chrome和open_tabs后，与5项绑定基础测试共8 passed。现阶段只完成基础，其他消费者/协调/四格仍待后续小步；不能宣称全入口已经封堵。

### 基础早审E1–E7修正

ab1063d早审采纳。E1已改只读SQLite，再补ensure/owner/connect测试防Store构造；E2固定owned profile保留登录，实例generation更新；E3唯一协调runid；E4调用方expected root/db；E5原子replace/坏记录不覆盖；E6配置有界锁且锁内重读owner；E7白名单且同连接SystemInfo→Target。管理反例先6 failed（行为/缺接口），完成后基础+管理14 passed，包括合成cookie重启保留、跨workspace、旧run、真实文件锁超时、原子写和fake连接。仍需全消费者贯穿与最终真实fixture，未把基础通过等同片验收。

### 消费者、协调和启动仲裁小步

消费者8项行为红（HTTP重新发现/port-only checkpoint/MCP裸port）后统一绑定和独立expected root/db。四格8项行为红后通过，加4项真实线程启动/stop事件屏障；独立fill按kind/runid识别，running期间apply_status、正常收尾交错不kill，失联不写领域状态；同进程另一Toolbox也拒绝接管。只读SQLite显式closing，管理补活profile/损坏标记/缺目录/关闭PID复用测试。checkpoint身份失效Carry on反例先失败，再改STOP。

迁移过程中首组消费者回归86 failed/116 passed/16 errors（整数fake和旧checkpoint schema等），后续逐项迁移，未放宽业务断言。整套首次15 failed/1658 passed/76.13s：尚未迁移的readback裸port测试、ChromeRunner测试接口、恢复fixture缺generation；随后针对消费者236 passed/31.40s、checkpoint36 passed/16.88s、剩余197 passed/14.33s。新旧generation另有明确回归，旧记录不得靠补默认generation接管。完整最终验证仍在后面记录。

系统Chrome产品fixture已跑通：产品ensure/精确连接、metadata、readback、checkpoint、watchdog、Python/MCP错误GUID、disconnect后浏览器PID仍活、持久profile重启新generation。实际navigator.webdriver=true，未加enable-automation且不宣称规避检测；仅about:blank合成页面。最终平台/参数/Node依赖JSON随最终交付保存，LinuxCI尚待协调者推送。

### 真实fixture与收尾边界补充

最终产品路径系统Chrome与headless Chromium本地探针均通过（准确参数见仓库JSON）；Linux同命令已加入只读权限CI步骤与静态允许清单，禁止上传发行制品不变。CI契约先1 failed/13 passed，再14 passed。新探针增加版本校验时曾有局部变量version遮蔽导入导致启动前失败，改名后通过，未升级依赖。

活实例marker不符补测先1 failed/2 passed，调整先验profile所有权再adopt后15管理测试通过。Popen已返回但spawned身份记录失败补测先4 failed/16 passed，修复为已启动submit held/interrupted、fill待修复失败后20 passed；不会以未启动pending吞掉已启动的不确定结果。后续异常同样保守收尾。

## 最终本地交审结果

产品代码集中四项通过：`uv sync --locked`、`uv run ruff check src tests`、`uv run ruff format --check src tests`；`uv run python -m pytest -q` **1688 passed in 80.48s，无skip**。保留上述首次失败记录，不以最后一轮绿抹去迁移失败。此后仅把独立fill正常结束测试改为屏障内running→done，并给真失联增加显式线程屏障；相同5项定向测试全部通过，ruff两项再次通过，产品代码未再变。三组分别覆盖运行中apply_status不kill、正常收尾与恢复交错、失联确认前kill0及确认后只结束协调记录。

制品验证：`scripts/check_package.py` direct wheel及sdist→wheel均通过，安装后MCP47工具、full/scheduled、资源闭包、cwd独立及form-fill缺运行参数检查通过；local_only外发调用仍拒绝。`scripts/check_editable_resources.py`的修改配置、修改/新增/删除reference四种uv run/sync资源更新通过。无上传制品/Release/Pages/PyPI发布。

实际产品fixture：[系统Chrome有头](evidence/public-r1b-product-system.json)、[Chromium headless](evidence/public-r1b-product-headless.json)，复现脚本为`scripts/probe_product_browser.py`（系统Chrome加`--system-chrome`）。两者均使用产品启动参数、全新临时专属profile和about:blank合成页面，验证精确WS/OS PID-start-profile/CDP PID、metadata/readback/checkpoint/watchdog、Python及MCP正确/错误GUID、`browser.close()`仅disconnect且浏览器仍活、同profile重启保留合成标记且generation更新。实际webdriver=true，不承诺规避检测。Python1.56.0、MCP0.0.83及实际Node1.64.0-alpha-1790635538000分别留证，没有升级。

| 范围 | 验证入口/证据 |
| --- | --- |
| 旧ensure_chrome/open_tabs反证 | test_browser_entry_boundary：原行为3红，foreign9222不复用、vendor复制/端口kill毒丸 |
| 归属、原子记录、锁、关闭 | test_browser_management/binding：expected root/db、runid、损坏记录、锁超时、活profile不删ActivePort、marker不符/目录缺失不创建、SIGKILL前重验及PID复用kill0 |
| 连接及只读元数据 | test_browser_migration及真实fixture：同连接先PID后Target，非owner无attach/evaluate，checkpoint失效STOP，无整数端口回落 |
| 启动及恢复 | test_browser_identity_outcome（20项）、form_fill_owner（5项）、generation与既有stuck/gate回归：未launch不造attempt，已launch未知submit保守held，旧generation安全重开 |
| 隔离和发行 | 既有workspace回归与制品探针：local_only先拒绝；test_release_workflow允许产品合成fixture但禁止发行步骤 |

**尚待证据**：本机两种模式都是macOS，不能充当Linux包装PID/进程身份实证。相同产品fixture已加入Linux CI，需协调者推送新HEAD后取得结果；Windows未验证，管理入口明确拒绝。精确WS及CDP自报不是恶意同用户进程认证；浏览器后台网络削减参数不是网络沙箱。本片不构成保护公司最终提交技术边界或公众上市验收，R4授权/资料边界、R5可靠提交、R6其他恢复、R8发行仍保留。未访问真实招聘/邮箱，未读取个人profile.json/.env或复制日常Chrome。

本地实现阶段完成，交独立复核；不推送/合并。预计后续为一次独立代码复核及一次远端CI窗口，耗时取决于队列和发现的问题，不作上线承诺。

## 141c582 最终评审 T1/F1 修复（决定29）

甲方采纳 `/tmp/public-r1b-final-review.md`：主体已核实，T1超时分类/F1兼容参数为阻塞。本轮先改规格/决定，再新增有效行为红：3 failed/5 passed（一次普通socket超时误判身份失败、两种模式缺兼容参数）；修复后93项相关回归通过。扩展握手/PID/targets读取超时、恢复清计数、超时期间PID/端点/generation/run变化、拒绝/非101/断开、connect/verify失败关闭及参数正反契约。扩展帧断开补测先1 failed，发现struct.error被监视器吞掉；改为明确身份失败后16项通过。未放宽业务断言；旧报告文案改为只报告超时，不再推测崩溃或关闭。首次ruff发现socket.timeout别名规则，改成等价TimeoutError并移除未用import，复验通过。

最终参数的macOS系统Chrome155有头与Chromium141无头产品fixture均通过，更新同名JSON；Python/MCP正确错误GUID、断开客户端不关浏览器、同profile重启新generation全部保持。恢复的每个flag均列入参数契约，未逐项验证现代Chrome是否忽略flag或实际提示行为，因此不宣称密码/权限隔离保证，也不宣称webdriver=false。Linux仅待新HEAD的Playwright Chromium无头CI，系统Chrome发行版包装脚本不在已验证范围；Windows不变。旧CI39926377064由协调者跟踪，本会话未重跑/推送。

本轮第一套1703 passed/80.28s；在其运行期间新增扩展帧断开回归及异常分类，所以还需最终整套覆盖该新增项，结果在下方另记。制品首次wheel/sdist往返通过，同样在最终代码上复验。记录文档的第一次Python追加命令遇到UTF-8解析错误，未写入文件；改用直接文本追加，不影响产品测试。

最终四项全部通过：uv sync --locked、ruff check src tests、ruff format --check src tests；pytest **1704 passed in 78.83s，无失败/skip**。最终代码的 direct wheel / sdist→wheel再次通过，安装MCP47工具、full/scheduled、资源闭包/cwd独立及local_only拒绝保持。产品探针脚本另经ruff check/format通过；仅增加JSON限制说明，不改变已执行的fixture逻辑。没有改资源构建机制，本轮未重复无关editable变更探针，上一轮通过证据保留。交协调者安排Claude T1/F1窄复核与新HEAD CI；本会话不推送/合并。

## Linux CI 39926377064：窄启动诊断交接

甲方提供的141c582远端日志显示 **4 failed / 1667 passed / 17 errors**，失败均在浏览器未发布端点；pytest之后的产品fixture/制品未执行。此日志没有浏览器stderr，**根因尚未证实**，不能把AppArmor、缺依赖、sandbox或参数任一项写成既定原因。甲方随后确认Claude对4fe693的T1/F1窄复核通过，本轮不重做它们。

本机Darwin，PATH未找到docker/podman/colima/limactl/orb/nerdctl；未取得本地隔离Linux环境。新增`scripts/probe_browser_startup.py`，CI在pytest前运行：Playwright实际Chromium二进制、产品最终参数、全新临时空profile、about:blank、20秒启动上限；持续排空stderr，只保留末16KiB及总字节数，记录启动退出码/超时/端点和清理后的退出码，清理仅自己的Popen子进程。只读固定Linux kernel状态清单，不dump环境，不读取用户配置/既有profile，不改变策略。报告仅诊断，不以其退出成功冒充验证通过；原pytest及产品fixture仍是必须的门槛，无skip/fake替代，无制品上传。

先红：采集器两项因脚本缺失失败（接口缺失证据）；workflow新步骤先1 failed/13 passed。实现后16项定向通过（0.15s）：合成子进程100000字节stderr与exit7保留、有界截断；超时子进程清理；CI只读/禁止发行契约。ruff初次一处长行，经format后src/tests及新脚本check/format均通过，diff检查通过。macOS真实空profile诊断：endpoint=true、未超时、退出前仍运行、清理后exit0、stderr98字节、0.519秒；这仅证明诊断管道可用，不证明Linux根因。没有产品代码改动，沿用4fe693四项1704通过记录，本轮不重复无关全套。

后续由协调者推送新HEAD取得Linux stderr，优先判读明确错误文本及退出状态，再安排针对修复。官方[AppArmor/userns说明](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md)说明了可能机制及按路径策略，但不证明本次故障来源。若确证，评估仅ephemeral CI、精确实际二进制路径的最小必要许可或保留sandbox的helper；不照抄文档通配路径，不全局sysctl关闭限制，不在产品加no-sandbox，不在用户Mac/日常系统改策略。password-store=basic不构成磁盘秘密保护，R4仍需独立验收。

状态：诊断待Linux取证，R1b仍未签收；预计取得日志后一个诊断/修复小步，具体估时取决于实际原因。不重跑旧CI、不自行push/合并。

## CI39928342793确诊与精确userns许可交审

诊断实际二进制为`/home/runner/.cache/ms-playwright/chromium-1194/chrome-linux/chrome`，stderr明确`FATAL No usable sandbox!`、退出-6；AppArmor enabled Y/restrict_unprivileged_userns 1/unprivileged_userns_clone 1。该轮 **4 failed/1685 passed/17 errors**，fixture/制品未运行。按甲方决定采用官方按路径userns方案，而不是全局sysctl或no-sandbox。

`scripts/ci_browser_userns.py`仅在GitHub-hosted Linux准备/always清理步骤调用：Playwright固定1.56.0的实际executable_path必须规范realpath、所有祖先无symlink、可执行普通文件，路径语法只接收既定cache布局和数字revision，拒绝特殊字符/通配/转义/其他位置；双引号精确attachment。本次run/attempt数字构成固定前缀profile名，不从路径派生；不覆盖已有文件。root:root/0644安装后核对内容/权限，单profile加载；清理核对归属和内容后单profile移除。CLI用参数列表无shell；没有sysctl、停用AppArmor、no-sandbox或其他权限。路径许可仍不能阻止拥有相同文件写权限的人替换二进制，不外推到用户系统。脚本不进wheel，实际sdist检查也已证实排除；不提供用户安装步骤。

官方依据：[Chromium按路径userns说明](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md)、[Ubuntu单profile加载说明](https://documentation.ubuntu.com/server/how-to/security/apparmor/index.html)、[AppArmor parser](https://www.apparmor.net/man/master/apparmor_parser/)。Ubuntu manpage直接抓取曾超时，官方搜索结果及上述文档核对-r/-R语义；Linux实际加载仍须本轮CI验证，未冒称本机已执行策略。

新增Linux产品fixture组合证据：专属连接读取`chrome://sandbox`状态，CDP browser PID必须等于绑定PID，renderer必须沿OS父链属于该browser，并记录Seccomp/NoNewPrivs和可读namespace inode。必须有Chromium Namespace/Seccomp均Yes、renderer Seccomp2/NoNewPrivs1，缺任一项失败；单一Seccomp值或端点出现均不足。结果在验证前输出，namespace不可读明确记录错误。此真实Linux分支本机无法执行，目前只有合成验证器拒绝缺失/单项/foreign证据的测试，不能算Linuxsandbox已验证。产品参数禁no-sandbox/disable-setuid-sandbox/disable-namespace-sandbox有独立回归；旧Playwright launch测试默认行为不当作产品ensure证明。R1b真实checkpoint/readback fixture已走ensure，不泛称所有测试都自行launch。

状态规格同步：public-release §1/§3 P1/P2/P10更新R1 main8530523及CI39919661089；P5区分R1b待整合分支实测与Linux待验证，R4凭据未完成。公众不通过结论、R8安装器/干净OS/签名及用户Linux策略缺口不变。

红绿：新CI策略接口先10项因脚本不存在失败（接口缺失证据）；workflow新增步骤先1 failed/13 passed。实现后精确策略文本、注入/异常路径、runid、hosted环境、symlink/非可执行文件、sdist排除、禁止全局放宽、产品sandbox参数、组合证据及既有T1/诊断/CI护栏定向通过。没有修改产品src、跳过测试或放宽旧断言。构建direct wheel/sdist往返、安装MCP47工具/full/scheduled、local_only拒绝再次通过；独立检查sdist无CI策略安装脚本。新workflow只有install/remove两步，原诊断/pytest/fixture/制品顺序和只读权限、禁止发行保持。

本轮本地四项：uv sync --locked、ruff check/format均通过；完整pytest **1719 passed/78.49s，0skip**。此后仅补独立产品sandbox禁止参数测试及runid反例，策略文件13项定向通过（包含该新增测试）；合计46项相关定向先前通过，未改产品src。脚本独立ruff通过。Linux安装/卸载策略及sandbox实际观测仍未执行，必须等协调者push新HEAD后的完整CI，不能用本地绿代替。交审时保持工作区锁释放，不自行push/合并。
