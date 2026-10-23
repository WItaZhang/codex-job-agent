# GitHub 源码双宿主交付

依据[总决定32](../spec.decisions.md)。本片已获协调者实施授权；[计划](github-host-release.plan.md)、[决定](github-host-release.decisions.md)、[证据](github-host-release.review.md)。

## 1. 目标与边界

用户clone仓库，按README准备uv/Python及宿主、配置自己的数据，在Codex或Claude Code使用共用skills与同一个stdio MCP。不是独立桌面应用、自带模型运行时、安装器、SaaS或上市验收。保留已有代码/历史和冻结R2/S2分支，不合入整个S2，不新增宿主或发布框架。§9业务指标仍独立判断，不用合成测试冒充真实投递达标。

基础发现推荐依赖uv锁定Python环境、用户自己的Codex或Claude Code访问权限；浏览器增强还需匹配Playwright的Chromium。可选抓取模型key由用户本机配置，缺失行为据代码说明。投递额外依赖Claude Code CLI、Google Chrome、Node/npx及用户核对的申请资料，即使主宿主是Codex也是如此。邮箱依赖可选且按实际配置说明，不承诺免配置联网投递。

新用户须在自己的宿主明确授权业务范围；作者决定25只授权本项目开发及作者会话，不传递给clone使用者。项目工具allow/approve不等于个人授权。label必须来自真实用户判断，代理选择有来源和proposal校验；不能编造背景或解除名单。保护公司网页及邮件真正提交仍须本人确认，现有只填不交/本人手动提交边界保留；未构成浏览器工具层绝对硬拦须如实说明。不得读取真实profile/.env或复制日常浏览器。

## 2. 验收矩阵与具体样例

| ID | 实际输入/行为 | 必需证据与失败样例 |
| --- | --- | --- |
| G01 | 无data/local的干净git clone或固定archive，按README uv sync --locked | 固定HEAD、命令/exit、锁文件不改；源码根和含空格目录；缺依赖提示可操作 |
| G02 | 两宿主项目stdio启动配置 | .mcp.json与.codex/config.toml实际命令启动initialize/list_tools；本机已有用户MCP/配置不读不改；根/子目录入口差异测试；项目信任/首次MCP批准及本机版本分开实证 |
| G03 | 共用skills/agents和相对链接 | 四个skills可定位、角色指向唯一源，移动检出有效；静态/协议测试不冒称真实宿主已派生agent；无可用宿主交互则明确未验 |
| G04 | 用户按例创建合成legacy配置并初始化意图，导入合成岗位 | 实际MCP初始化/读取/校验错误；不从旧data/local复制；标记workspace保持local_only，不能为验收降级 |
| G05 | fetch→today→apply-list→apply路径 | 包外/测试内合成collector、结构化宿主参数、runner与页面，真实领域Store/Toolbox完整结果；无网络毒丸；明确skill指令映射与未调用真实模型，不把测试替身植入用户入口 |
| G06 | 保护公司/未知公司/缺名单，普通及恢复路径 | 原有保守名单/快照/邮件/只填保护回归；无真实提交，宿主强制审批不绕过 |
| G07 | main旧host跨Store resume/report | 先复现真实resume生命周期（合成runner/页面），已开始与排队窗口分别证据；只有确实影响旧路径才最小修，不移植新runtime/完整R6 |
| G08 | README逐项承诺 | 浏览器专属profile/无日常复制；子agent工具清单是行为边界还是强制隔离分别写；升级先停进程备份、无完整回滚保证；准确OS/版本/未测项 |
| G10 | 新用户含糊/apply、已有明确委托、保护公司 | 含糊调用只填并展示具体范围一次；明确自动投递当前清单即授权，后续沿用至撤销；allow/approve不是业务许可，名单不代理解除 |
| G11 | 普通clone与本地开发模式 | 普通使用无需claim即可改配置/升级；enable-dev或claim显式启用开发锁，现有受管理共享仓库和release后仍保护；不改用户全局配置 |
| G09 | 最终交付 | 四项完整检查与固定HEAD干净检出验收，依赖/许可/外发/费用来源告知；不推送/合并，不增加wheel下载渠道 |

基础合成闭环例：初始化合成ML求职意图→导入至少20合成岗位→宿主显式参数打标签→7+3→实际合成反馈及原因/方案选择→JSON/CSV清单→合成申请资料与runner的普通、保护、登录门槛/停止结果。测试会生成标明synthetic的profile fixture，仅测试进程使用，不读取任何用户资料。普通host处理不能把模拟提交说成真实提交。

AR样例：同workspace两独立Store，真实resume线程处理a、b排队；第二Toolbox报告b后释放事件屏障，核对是否整步异常、状态是否覆盖或重复启动。已开始a必须受持久记录保护；无关岗位仍可报告。现有协调行持久关联本次恢复选中的岗位，报告事务内同时保护该集合和已开始的 resuming 标记（含同岗连带写入）；排队不提前标已开始。启动锁内核对 hold，结束解除队列保护。旧活动缺少集合时保守保护 held，不假设无人恢复。无需新增恢复框架或改变本人确认规则。

## 3. 支持范围与证据口径

首要本机实测macOS arm64；Linux已有CI仅证明其所运行的合成/浏览器/包检查，不等于用户环境安装或Claude/Codex交互实证。Windows未验证，不宣称支持POSIX shell/flock/浏览器进程身份路径。最终README列实际版本和分层证据：配置解析、MCP协议、合成领域闭环、实际宿主交互、真实业务分别记录，缺哪层就写待验证。

不扩真实站点、账号、密钥、个人资料或投递授权；不为了完成合成闭环登录宿主账号。没有真实宿主交互证据不能仅凭list_tools声称全交互已验收。
