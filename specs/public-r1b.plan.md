# R1b 最小切片计划

[现行规格](public-r1b.md) · [模块决定](public-r1b.decisions.md) · [交审证据](public-r1b.review.md)。基线main8530523；唯一编辑检出public-r1b-browser。b2716f5已获甲方及Claude阶段批准，现按1–9测试先行产品实现；不推送，不改owner/main或旧R1树。

## 当前阶段

1. 读取甲方B1/B2/S1/S2取舍，写原规格与决定（总决定29）。
2. `scripts/probe_browser_binding.py`：锁定Python Playwright1.56.0、MCP0.0.83，分别记录此前headless Chromium证据及实际系统Google Chrome有头模式，均为自己的全新临时空profile，只about:blank。验证DevToolsActivePort精确WS、SystemInfo browserPID=Popen PID、OS started稳定、OS命令行profile参数一致，不调用CDP Browser.getBrowserCommandLine、不加enable-automation、MCP列到同一合成标题，错误GUID拒绝。只处理自己Popen，不kill端口。
3. 保存可复现脚本和精简JSON证据；故障区分探针脚手架问题与能力不支持，不因失败退HTTP裸端口。能力成立后不用强制lsof；不把GUID/CDP自报当恶意同用户身份认证。
4. 更新基线记录、规格互链、未实现状态，交甲方/Claude；本轮不修改src产品代码、业务权限或CI，不为local_only加例外。

## 已批准产品实现责任

| 文件/模块 | 改动责任与不可遗漏调用者 |
| --- | --- |
| 新browser_session小模块 | 绑定数据、空profile、动态端点0、启动/验证/inspect/关闭；最小工作区管理锁，无心跳lease |
| apply_bridge.py | ensure_chrome/cdp_alive替换；ChromeRunner.__call__前置_tabs/_held_tab；run_application与MCP精确WS；PDF用临时空profile；不再调用vendor复制/端口kill |
| tools.py / store.py | 复用apply_running owner；start_applying/resume_applying/独立start_form_fill跨会话互斥；open_tabs提醒及prepare_resume只校验不启动；held generation保存和安全重开 |
| form_fill/runner.py | ensure/targets/open_tabs/fill/read/tab_state一致绑定；run_job及write_checkpoint_config传版本化引用；身份失效四格传播 |
| form_fill/checkpoint.py | 子进程核验owner/generation/PID/start/profile与精确WS；LiveBrowser连接及fallback不接受任意port；身份失效不得吞为carry on |
| form_fill/readback.py | list_targets、_read_live、_glance、tab_state、read_final_page及有界线程贯穿同一绑定，实际连接时重验；仅清自己的driver |
| watchdog.py | cdp_targets/ChromeWatch验证及身份失效硬停止；保留非身份页面变动只报告；agent/driver与browser独立进程组，不能误杀foreign |
| config.py/configs/default.yaml/workspace.py | 专属目录/必要超时配置与路径约束；旧chrome_profile_strip仅兼容解析，不再是复制资料的保证；local_only仍先拒绝 |
| tests及文档 | 更新所有裸port fake；迁移test_slice6/form_fill runner/checkpoint/readback/stuck_run/gate_handoff/workspace/apply_fakes；src之外探针不成为产品入口；不改vendor正文 |

产品连接只接收BrowserBinding或受限序列化引用。精确端点来自本次profile记录，核验OS PID/start/profile并连接后检查SystemInfo browserPID；每次新连接/重连验证，长连接断开即失效，不按端口重新发现。只读非owner最多列标签元数据，不页面attach/执行JS；owner及其checkpoint受限子服务才读回。

旧profile不采用/迁移/删除，不允许legacy临时选择采用；重新登录为已接受兼容变化。新generation不接管旧tab，但继续既有重开地址→核查已提交/安全重填路线，不把接着做堵死。已launch submit结果不明held/interrupted；确定未launch保持原状态无attempt；fill已launch身份失效要修复失败，提交证据优先。详细stop优先序以规格四格为准。

## 产品先红测试顺序

1. **入口反例**：foreign9222响应不复用；日常profile读取、复制其他worker、vendor.setup/launch/_kill_on_port设为调用即失败；local_only所有入口sink0。先证明当前实现不满足。
2. **绑定/管理**：空profile、损坏/陈旧/软链DevToolsActivePort、PID复用、端口换主、错误GUID、profile不符、两工作区交错、同工作区双进程启动、owner仍活/已死。管理锁只覆盖原子管理，其他会话不能控制；无法证明归属失败关闭。
3. **调用迁移**：checkpoint子进程、延迟readback、ChromeWatch重连、MCP参数均核同一generation；AST/静态清单检查非vendor产品CDP连接无裸整数端口回落，不能仅改fake端口为随机数骗绿。
4. **四格事件屏障**：submit/fill × 确定未launch/已launch，各与用户stop交错；未launch无interrupted，已launch submit结果不明held；fill失败；已有成功不回退、maybe_submitted保守held。R1正常收尾竞态、真实owner失联held和close幂等全部保留。
5. **恢复/互斥**：旧无generation、不同generation、同profile重启都不操作旧tab而走安全重开；同generation沿id/唯一URL匹配。独立start_form_fill与apply/resume跨会话竞争只一个能启动，启动失败正确清自己的owner。
6. **独立填表协调三组事件屏障**：kind=form_fill+runid匹配self._form_fill_run；running期间调用apply_status等恢复入口，kill0且运行继续；正常结束与_recover交错，收尾完成前不误kill、不抢清记录；真失联才清理身份匹配的本运行进程并结束协调行。三组都断言_close_interrupted调用0、applications/apply_attempts/holds无写入。其他会话展示独立填表类型/runid；非owner只取元数据，Target.attachToTarget/Runtime.evaluate调用0。
7. **清理**：结束agent树不杀独立browser；PID变更/foreign port不kill；PDF临时空profile；reader退出只disconnect；旧离线报告可读、旧port-only执行配置拒绝。
8. **真实本地fixture**：批准后空profile、about:blank/127.0.0.1；正确绑定完成列标签/checkpoint/readback/watchdog，错误实例拒绝，退出只处理自己进程。无招聘/邮箱/真实申请。导航限制不等同于浏览器后台零联网，不虚称网络隔离。
9. 四项、必要制品及独立评审。若CI增加探针命令，须更新已批准静态workflow契约，不上传制品/Release/Pages/PyPI。实现阶段可按既有发行护栏加入必要Linux合成验证。

## 依赖、估时及缺证据项

预计实现3–5有效工作日（新增独立填表协调屏障及平台证据余量）：绑定/启动0.5–1，所有调用者及子进程迁移1–1.5，四格/恢复/互斥0.5–1，真实本地fixture及交审0.5–1。不是已实现承诺。当前探针先解决S1关键能力；macOS证据不能代替Linux/Windows，平台覆盖及MCP重连/TOCTOU仍需产品对抗验证。若锁定版本不支持准确WS或PID/profile，报告具体阻塞，不放宽归属证明。

不扩范围：R4 legacy资料跨目录及每用户授权、R5可靠最后提交、R6其余恢复闭环/N2、R8原生安装/发行/许可等仍各归其片。主规格§9业务标准保留，不把本片浏览器探针当完整B或公众验收。
