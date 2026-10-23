# R1b：浏览器归属与空专属资料

本规格定义R1b目标，已获甲方/Claude阶段批准并完成产品实现，本地及限定Linux CI验证已通过，等待Claude最终签收和协调者整合。[计划](public-r1b.plan.md)、[决定](public-r1b.decisions.md)、[证据](public-r1b.review.md)互链；总决定29。

## 1. 范围与已交付基线

R1已由甲方最终签收，main 85305234c57713efe8738abf0dd64aed220aef3f，分支CI39918253389对应34221b1；main远端CI39919661089全success（1627 passed/122.49s、0skip，wheel/sdist/editable通过，日志/tmp/public-r1-main-ci-39919661089.log）。合并后本地四项1627 passed/66.13s，均为甲方提供。此事实不表示R1已有本规格的浏览器归属能力，旧切片报告不改写。

R1b只修浏览器实例归属、空专属profile、必要跨会话互斥及身份丢失结果。不盲连9222，不读取/复制日常Chrome或其他worker；旧复制目录不采用、不迁移、不删除，包括legacy也不提供采用一次的开关。作者需在新的空专属profile重新登录是接受的行为变化。PDF渲染另用一次性空profile，不与CDP归属混同。

新workspace local_only原样拒绝真实执行，无新白名单/测试授权字段。合成浏览器实证仅在scripts/tests显式运行、临时空profile、about:blank或127.0.0.1 fixture，不访问招聘/邮箱/私人资料，不成为wheel产品入口。

## 2. 归属证据与接口

绑定至少含协调kind和唯一runid（step另保留batch/phase）、canonical工作区/data root、专属profile真实路径、generation、browser PID+稳定started、精确browser WebSocket端点及控制owner(pid+started)。首次创建空的非默认专属profile，之后同工作区持久复用并保留登录；generation只标识浏览器实例。每次启动使用动态端口0；只从本次启动profile的DevToolsActivePort读取完整WS路径，不扫描/探活固定端口。拒绝过期/损坏/软链/越界记录，不覆盖不明文件，不按端口kill。

S1证据按二进制/模式分别记录（见[证据](public-r1b.review.md)），不能把headless Chromium外推为系统Google Chrome有头模式；Linux限定CI实证见下文；未列平台待验证。系统Google Chrome155有头、无enable-automation的macOS26.5.1探针已通过；该环境不要求强制lsof。必需证据组合为：本profile的端点记录＋精确WS实例＋OS进程PID/started及profile启动参数＋连接后的SystemInfo.getProcessInfo browser PID核验。GUID仅防实例串接，CDP自报不是恶意同用户进程认证；不宣称抵御完全控制本用户文件/进程的攻击者。若锁定MCP0.0.83/Python Playwright1.56.0不支持这些能力，报告具体限制，不能退HTTP裸端口；探针成功才去除lsof强制条件。

不要求CDP Browser.getBrowserCommandLine，不为归属验证引入--enable-automation。OS命令行中的专属profile仍是必需核验项。不承诺navigator.webdriver为false或规避网站检测；动态端口/自动化本身的可观察行为如实说明。

所有入口以调用方Settings或子进程独立配置的期望root/db比对，不能信binding自报；session用临时文件+原子replace，损坏诊断失败关闭、不自动覆盖。管理锁等待有配置上限，锁内重读owner；同profile重启仅在已证明本工作区自有且无活实例后处理陈旧ActivePort，不触碰旧复制目录。只读RPC限SystemInfo/Target元数据，同一连接先验PID再取targets。

所有新连接/重连都使用准确WS，并在连上后再次核验PID/start/profile；断开则绑定失效，不按原端口重新发现。长连接无法在agent每次工具调用前插入校验：ChromeWatch旁路及checkpoint负责发现身份变化；不把轮询称为每次调用硬拦截。身份失效触发第4节结果与受控停止，不吞成普通ChromeDown后继续。

迁移apply_bridge、ChromeRunner._tabs/_held_tab、MCP配置、form_fill runner/checkpoint子进程、readback后台线程、watchdog及所有tools open_tabs提醒/prepare_resume。产品接口传绑定对象，不接受裸整数端口作为归属依据。旧port-only checkpoint执行配置失败关闭，旧离线report不受影响。

## 3. 最小管理锁和控制owner

工作区内一把小型跨进程管理锁，仅用于启动、接管、关闭及owner原子检查；进程退出自动释放。控制身份复用Store.apply_running的owner(pid+started)，不新增心跳/续租系统。独立start_form_fill也必须登记同一owner并与start_applying/resume_applying互斥，跨Toolbox/进程同时竞争只能一个成功；退出和启动失败必须清理本owner，不能清别人行。

独立填表协调行明确为kind=form_fill和runid；_recover按runid匹配self._form_fill_run，正常running及尚未完成收尾的已知运行由本运行负责，不能误kill。真正失联时只清理身份仍匹配的本运行agent/driver并结束协调行，不走_close_interrupted，不写applications/apply_attempts/holds。_refuse_other_session和other_session文案区分独立填表与投递，展示运行类型及runid，不要求去处理不存在的投递批次。

不是owner的会话仅可列已验证实例的标签元数据（如Target.getTargets），回归必须断言Target.attachToTarget及Runtime.evaluate调用为0；不能attach到页面、读回执行JS或启动agent。只读inspect/open_tabs不启动浏览器。当前owner发起的checkpoint子进程持有受限绑定引用，重新核验同一owner及generation，不独占第二份控制权。readback退出只disconnect，不关闭浏览器；agent进程树结束不能误杀独立进程组的浏览器。关闭仅针对仍符合绑定身份的本次浏览器，不按端口kill foreign进程。

## 4. 身份失效四种结果与停止优先序

launch指agent已可能启动：Popen已成功或无法排除已经启动；仅有准备状态、尚未调用Popen或能证明Popen失败且没有子进程，不算已launch。启动许可与停止信号必须由单一受控点仲裁，不能先无锁检查再Popen。测试用事件屏障覆盖两侧。

| 模式/时点 | 岗位结果 | 记录和后续 |
| --- | --- | --- |
| submit，确定未launch | 保持原状态：新岗位pending，恢复岗位held | 不造interrupted/投递attempt；清resuming/本次占用，报告browser_identity_lost，停止本步；不能声称已访问/已提交 |
| fill（review/careful），确定未launch | 同上保持原状态 | 无虚假attempt；停止本步，修复身份后沿现有流程重新准备 |
| submit，已launch且结果不明 | held/interrupted，可能已提交 | 停止本owner启动的agent/driver；不得pending自动重投；保留身份失效原因，沿既有恢复的已提交核查流程处理 |
| fill，已launch | failed/browser_identity_lost，要修复失败 | 停止自己的agent/driver，不记成功；不转换自动提交。若已观察到提交，则按maybe_submitted保守held并停批，优先于本行 |

已持久化且可靠的成功证据不回退pending/失败；“已提交/可能已提交”证据优先。身份失效与用户stop同时发生：确定未launch则两者都保持原状态、无attempt，沿R1正常收尾，不造中断；已launch的submit且结果不明，身份失效优先于普通stop的pending逻辑，必须held/interrupted；已launch fill的身份失效优先于普通stop，按要修复失败处理。正常身份下的普通停止语义不变。页面多出/关闭但归属仍有效只报告、不操作页面；归属无法确认或连接断开不能与此混为“有人在用”的提示。

## 5. held兼容与generation

held记录保存browser generation。同一已验证generation沿用id、再唯一地址匹配；旧记录没有generation、不同generation、同profile重启的新generation，均不按id或地址接管旧标签页。

这些情况不堵死“接着做”：沿既有安全路线从岗位地址重新打开。submit恢复首先核查是否已提交/已经申请，结果不明仍保守held，不能无条件再交；fill由填表模块重填且永不提交。旧标签页不点击、不导航、不关闭。报告明确“旧实例不接管，走重新打开及核查”，不以任意source参数授权提交。不是重写整个恢复状态机。

## 6. 验收与范围外

事件屏障：四种失效、各与stop竞争、真崩溃held、成功不回退、正常停止无虚假attempt；旧/不同generation恢复重开；fake9222、PID复用、陈旧DevToolsActivePort、重连换主、同工作区双会话/独立填表竞争、foreign kill0、日常profile读取/复制函数调用即失败；全库非vendor裸端口生产调用迁移清单及回归。真实探针验证准确WS、MCP、PID/start/profile，仅空本地页。

R4：legacy applicant跨目录资料权限、每用户授权/账号凭据；R5：保护公司可靠提交；R6：其余恢复/重复风险闭环及N2；R8：独立平台安装、升级发行、许可/完整公众验收。R1通过及本片探针通过均不能宣称公众自动业务可发布。

## 最终评审 T1/F1 行为约定

普通探测的 socket 超时单独分类：超时后重核 OS/PID/start/profile/端点，ChromeWatch 再核当前 generation，控制中的监视还核 kind/runid/owner；任一变化优先身份失效。身份仍一致时按 down_after 连续计数，达到 N 只报告普通探测超时，不推断关闭/崩溃、不升级身份失效；成功响应清连续计数。连接拒绝/断开、非101或错误GUID、PID/profile/run/generation不符立即身份失效。超时不能成为新控制授权：ensure/verify/connect 仍失败关闭，不返回未经验证的连接。

启动保留原vendor非归属行为参数：window-size=1024,768、profile-directory=Default、disable-session-crashed-bubble、disable-features=InfiniteSessionRestore,PasswordManagerOnboarding、hide-crash-restore-bubble、noerrdialogs、password-store=basic、disable-save-password-bubble、disable-popup-blocking、use-fake-device-for-media-stream、use-fake-ui-for-media-stream、deny-permission-prompts、disable-notifications。它们是兼容性参数，不是秘密/权限硬边界；现代Chrome是否忽略某flag不能仅凭启动成功断言。保留新专属profile/动态端口0/精确WS及既有后台联网削减参数，不恢复复制资料/固定端口/端口kill，不加enable-automation。

平台证据只覆盖实测二进制/模式：HEAD429a735的CI39930613311已实证GitHub托管ubuntu-latest、锁定Playwright1.56.0/Chromium141 build1194无头加精确路径CI userns许可，不代表发行版系统Chrome包装脚本（尤其fork包装）已支持；无法核对PID的启动仍失败关闭。

## Linux启动诊断及限定CI实证

CI在真实pytest前单独用产品最终launch_arguments、Playwright实际二进制、全新临时空profile和about:blank做启动诊断。限时并有界捕获stderr与退出码、端点是否出现；只读记录OS及相关userns/AppArmor状态，不修改系统策略。诊断报告不是测试通过证明，即使未启动也继续原有pytest，以保留真实失败；不跳测试、不用fake替代。诊断仅scripts/CI入口，不让普通产品浏览器输出潜在私人stderr。没有根因日志前不判定sandbox、AppArmor或依赖问题；不加入no-sandbox或全局sysctl变更。若确证环境策略原因，另按证据评估短生命周期CI中精确二进制路径许可，不扩展产品权限/支持范围。

已取得CI39928342793的No usable sandbox/-6及AppArmor限制证据。CI准备步骤只在GitHub托管Linux临时runner对锁定Playwright实际Chromium的精确规范路径安装本次run专属userns AppArmor规则；拒绝通配符、转义、变量插值、符号链接及非预期目录。配置root:root/0644，单profile加载，always清理且核对内容，不改全局sysctl、不加no-sandbox、不向公众安装器传播。Chrome自身sandbox保持开启；按路径许可不证明同权限文件防篡改。HEAD429a735b4a2cfe0c02d1c2ca6da589c5662be2a9的CI39930613311已完整通过：1747 tests/126.40s，0失败/错误/skip；启动诊断、产品fixture、wheel/sdist、editable及策略移除均执行通过。该限定范围已有实证，R1b仍待最终签收/整合；不等于公众Linux安装支持。

Linux产品fixture另读取本专属浏览器的chrome://sandbox内部状态，结合SystemInfo的browser/renderer PID、/proc父链、renderer Seccomp=2和NoNewPrivs=1验证；namespace inode可读时一并记录，不可读则明确标注原因。必须按chrome://sandbox实际逐行tab分隔字段验证Layer 1 Sandbox=Namespace、PID namespaces=Yes、Network namespaces=Yes、Seccomp-BPF sandbox=Yes；各字段唯一，缺失/No/重复（包括一致重复）或非精确值均拒绝，并保留进程组合证据，不把单一Seccomp值/端点出现当全部sandbox证明。缺证据使fixture失败，结果在断言前输出便于诊断；只访问内部页/合成本地页。此证据仅锁定CI构建headless，公众Linux安装仍由R8验证。
