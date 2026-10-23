# R1b 决定记录

## 1（2026-10-23）：B1/B2与S1/S2收敛（总决定29）

甲方采纳/tmp/public-r1b-plan-review.md：四种身份失效结果明确，submit已launch结果不明held/interrupted，未launch无虚假attempt；stop不能覆盖安全优先级。旧或不同generation不接管旧tab，但必须保留从岗位地址重开并核查已提交的恢复路线。

S1有条件采用：先用锁定MCP0.0.83/Playwright1.56.0空profile本地探针证明精确WS、CDP browserPID、PID/start/profile核验；成立后不用强制lsof，失败报告不退HTTP裸端口。GUID/CDP自报不认证恶意同用户进程。

S2只做小型管理锁＋已有apply_running owner，不做lease/心跳；独立start_form_fill也纳入跨会话互斥。旧复制profile从不采用、迁移或删除，重新登录为接受的兼容变化。保留local_only，不扩R4资料权限/R5提交边界。

本次明确阶段门：先规格/计划/探针证据交甲方和Claude复核，再产品实现。授权的测试仅空专属profile/about:blank/127.0.0.1，无真实业务。

## 2（2026-10-23）：S1本地实证与MCP错误GUID对照（总决定29）

> 证据外推范围及必需校验组合已被决定3取代；历史实测结果保留。

同一MCP0.0.83正确WS列出about:blank合成标题；错误GUID实际调用browser_tabs返回工具错误且标题不可见，Python连接错误GUID也拒绝。PID/start/profile证据成立，当前macOS环境采用S1而不强制lsof；其他平台和产品迁移仍待验证。Python Playwright1.56.0与MCP自报及实际Node依赖1.64.0-alpha-1790635538000分开记录，没有升级依赖。探针不证明恶意同用户认证或所有未来重连场景。详见同名review及仓库JSON。

## 3（2026-10-23）：正式评审A/B收敛（总决定29）

甲方采纳A(a)：本profile端点记录+精确WS+OS PID/started/profile命令行+连接后SystemInfo browserPID；删除必需CDP命令行核验及enable-automation。已有headless Chromium探针保留但限于原模式；另跑系统Google Chrome有头模式的空profile本地探针，记录平台/OS/binary/headless/启动参数及Python/MCP正确错误GUID对照。不得宣称无webdriver标志或规避检测。尚未验证平台不外推。

B：独立填表不依赖领域数据，唯一例外是apply_running协调行与管理锁。kind=form_fill+runid与self._form_fill_run匹配；正常运行和收尾不误kill，真失联只清自己的进程及协调行，不走_close_interrupted，不写application/attempt/hold。other_session区分填表。三组事件屏障及nonowner元数据无attach/evaluate回归入计划。本次仍只规格/探针，交甲方/Claude窄复核后才产品实现。

## 4：绑定早审E1–E7（总决定29）

甲方采纳ab1063d早审：首次空专属profile后持久复用登录，generation为实例，不是每次丢资料；澄清原“每次空”歧义。只读SQLite协调查询不构造Store；绑定kind+唯一runid，比较调用方独立期望root/db；原子session、损坏不覆盖、有界管理锁内重读owner；同连接验PID再读白名单元数据。补真实边界fake与最终disconnect浏览器仍活/Linux包装PID实证，Windows不回落。

## 5：实现验收与平台证据（总决定29）

b2716f5阶段批准后已完成实现，系统Chrome有头与Playwright Chromium headless均采用产品launch_arguments的本地合成fixture；配置显式选择binary/headless、锁与启动上限，默认系统Chrome有头，任何配置都不豁免local_only或身份验证。LinuxCI增同一产品fixture命令且不上传制品；Windows未验证拒绝管理。固定Node顶层包和实际传递依赖分别记录，无升级。公开产品的授权/可靠提交仍归R4/R5，R1b不解除公众外发门槛。

## 6：最终评审T1/F1（总决定29）

甲方采纳141c582最终评审两项：普通超时与明确身份失败分型；在重核身份一致后ChromeWatch连续N次只报告超时，恢复清计数，ensure/connect仍不建立未验证授权。恢复规格所列vendor非归属参数，不恢复复制/固定端口/kill，不声称参数具有权限隔离保证。最终参数重跑两种产品fixture并更新证据，Linux支持仅按实际CI模式记录。决定5的“产品参数”证据只描述旧参数，最新兼容参数以本决定和新fixture为准。

## 7：Linux CI启动失败先取证（总决定29）

甲方报告141c582的CI39926377064：4 failed/1667 passed/17 errors，全部在ensure未产生端点，后续fixture/制品未执行。stderr原被丢弃，目前不能归因。当前Mac未发现docker/podman/colima/limactl/orb/nerdctl可用入口，先提交CI窄合成启动诊断：最终参数、空临时profile、有界stderr/退出码、相关内核状态只读；真实测试不跳过。Chromium官方AppArmor/userns文档仅排查线索，未授权修改用户机器策略，不照抄通配路径/全局禁用方案。

## 8：已证实无可用sandbox，限CI精确路径许可（总决定29）

CI39928342793明确stderr FATAL No usable sandbox、exit=-6，AppArmor enabled=Y、restrict_unprivileged_userns=1、unprivileged_userns_clone=1；pytest4 failed/1685 passed/17 errors。选择Chromium官方按路径userns许可方案，仅GitHub托管Linux临时runner，固定Playwright版本实际二进制路径，无通配workspace；root拥有的本次run专属AppArmor配置只含userns，按单profile加载/移除。flags=(unconfined)沿官方语义，不是关闭Chromium自己的sandbox。拒绝异常路径/符号链接/非文件，策略不写入产品配置、不进入用户安装流程；不设置no-sandbox、不全局sysctl、不改用户系统。精确路径仍不能抵御能替换该文件的同权限恶意进程，只适用临时CI测试范围。通过新CI前Linux修复保持待验证。

同时按甲方要求把public-release现状从ef8dbd3刷新：R1 P1/P2及CI引用8530523/39919661089；安装器/干净OS/签名仍缺，P5只按R1b实测平台记录，Linux待验证、R4凭据未完成。公众发布仍不通过，历史证据留review/decisions。

> 决定8补充中的sandbox页面字段假设已由决定9取代，历史取舍保留。

决定8补充：采纳Claude最新清单，Linux产品fixture用Chromium自身sandbox页加OS父链/Seccomp/NoNewPrivs的组合观测，缺证据不通过。历史Playwright自行launch测试的默认sandbox选项不替代产品ensure路径证据；R1b真实checkpoint/readback fixture现已通过product ensure启动，其他旧launch测试按各自原实现保留，不据其成功宣称产品sandbox通过。CI策略脚本从sdist排除，wheel无此入口；不作为用户安装步骤。

## 9：按CI实际sandbox表格解析（总决定29）

CI39929809213实际页面为Layer 1 Sandbox=Namespace、PID namespaces=Yes、Network namespaces=Yes、Seccomp-BPF sandbox=Yes，不存在假想的Namespace sandbox Yes标签。改为逐行tab字段精确解析，拒绝缺失/No/重复矛盾或一致重复，保留所有renderer父链/Seccomp2/NoNewPrivs1验证。实际三个renderer的pid/user namespace均与browser不同，留真实合成CI fixture，不当作完整CI通过。此次install/endpoint/remove及1720测试已成功，产品探针因解析错误失败，下游制品未执行；修复只在探针/测试/文档，不改产品或降低sandbox要求。
