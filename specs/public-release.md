# 原公众交付路线：当前已暂停

## 1. 当前交付目标

[总决定32](../spec.decisions.md)将本轮目标改为[GitHub源码双宿主可用](github-host-release.md)。用户clone后按README配置，在Codex或Claude Code运行共用skills/MCP。独立桌面、自带runtime、安装器、公网SaaS、非开发者UI与上市验收不再是本轮门槛。不继续公众F01/完整UI旅程，不整体合入S2。

这不是宣布原公众矩阵通过，也不是删除已有工作。R2冻结分支、S2的8e5c06d及全部提交/stash保留，S1已合main3fe87bc；旧规格内容可在git历史3fe87bc的本文件及同名decisions/review查询。原估时和R2→R2b→R3等排期不再生效，重新启用需另行明确决策。

## 2. 继续有效的边界

- 作者委托不等于其他用户授权；新用户在自己的宿主明确授予业务范围，模型actor/source不是认证。真实label/个人事实不得伪造，意图只走proposal及领域校验入口。
- 保护公司网页及邮件提交仍须本人确认；当前由填表模块停在提交前、本人亲手提交。未知公司/名单不可读取不自动提交，不增解锁参数。现有工具/prompt/事后检测不是绝对最终提交硬拦，README必须如实告知。
- 专属Chrome不复制日常profile；隐私、验证码、仲裁/SSN/支付及宿主强制审批规则保留。实际投递仍依赖Claude Code/Chrome/Node，即使主宿主为Codex。
- 既有R1 local_only保持，只读新workspace不能暗降级为legacy以通过验收。现行源码配置使用显式legacy；合成测试不授权真实业务。
- §9每周200、成功率、人工时间和连续两周目标仍独立；不以工程绿色或源码可用宣称完整B达标。

## 3. 当前证据与缺口如何记录

源码验收用github-host-release矩阵，分别记录依赖安装、配置解析、MCP握手、skills/角色链接、合成领域闭环、实际宿主交互和真实业务。缺环境如实待验证，不制造整体通过。Windows与Linux用户宿主体验不因已有CI自动算通过；R1b原限定Linux sandbox证据也不代表所有发行版。

## 4. 当前排期

仅执行[GitHub交付最小计划](github-host-release.plan.md)。不新增发布框架、provider、UI或SaaS；AR-1/排队窗口先在main旧host真实合成生命周期复现，必要才最小修。已合R1/R1b/S1继续保留，未合分支不在本片整合。下列R1既有契约继续有效，但不是重启整个公众路线的授权。

### 4.1 R2b状态

[原R2b规格](public-r2b.md)与计划为暂停路线记录；S1核心/fake已合main3fe87bc，S2停在独立分支8e5c06d，完整R2b未验收。既有代码不删除、不作为当前host使用的前置；不继续其实现或20+公众旅程。

## 5. 已交付 R1：持续验收契约、样例及文件责任

**范围**：脱离源码的初始化、运行资源定位、用户数据目录隔离、实际制品回归及CI。初始化为本地操作，不访问网络、不启动浏览器/MCP外部工具/投递、不读作者档案；不设计 consent.json 或实现授权系统。R1新workspace在R4/R5发布门槛完成前仅本地初始化/本地读/合成测试可用；服务端对真实模型外发、网站/浏览器、邮件、投递及恢复入口明确拒绝，直接调用也必须拒绝，不能仅靠review_first、提示或隐藏UI。显式legacy config保持开发者兼容而非公众默认，不能由新workspace自动降级到legacy绕过；外部legacy配置的data_dir/db_path及直接适配入口实际路径若解析到标记workspace或其后代也拒绝。CI产物标内部预发布，不上用户下载渠道。R1通过仍不能公开自动业务。

**已实现接口**（R1持续回归契约）：`intent-job-agent init [--workspace PATH]` 创建空工作区并报告绝对路径及“尚未配置个人资料/业务权限”；省略路径使用平台用户应用数据目录下默认工作区。MCP新增 `--workspace PATH`，不带定位参数使用同一默认工作区；未初始化时报可操作的初始化提示、非零退出，无 traceback/静默回落。form_fill report 同样支持 `--workspace`；无对应录制只报缺失，不访问网站。界面未来复用这些初始化/定位服务，用户不需掌握命令行。

**兼容语义**：保留显式 `--config` 旧入口，config 参数相对路径仍相对调用 cwd，旧配置里的相对路径仍保留既有 cwd 意义，不隐式迁移。新 workspace 模式全部路径锚定工作区，与 cwd 无关；生成配置使用绝对可写路径，包内默认资源只读。`--config` 与 `--workspace` 同传报歧义错误。已有 form_fill `--data-dir` 显式覆盖保留原语义，测试说明其优先级；用户主动指定的外部目录不能被称为默认隔离保证。不得凭目录名猜测作者 data/local；不修改显式旧配置。运行时写入包目录禁止。

初始化保留已有文件，重复运行幂等；损坏、不兼容配置、无权限或目录冲突明确失败，不覆盖、不换工作区。新建不植入真实个人资料/意图/密钥/登录态，也不生成表示同意的授权记录；名单缺失与显式空名单不能混为一谈，资料未配置时准备投递仍拒绝。包内通用默认不应含作者的搜索偏好或绝对路径。

资源须覆盖完整运行引用：default配置所需数据、通用求职信 SKILL.md 及 references 等引用文件、现有vendor YAML；需要由运行时消费的其他说明先列清单。skill唯一源仍在 .claude/skills，只允许构建时派生资源并验证一致，不复制第二份人工维护正文。宿主个人允许策略不能随资源一并作为新用户授权。资源加载可用包资源接口，不能依赖 site-packages 父级的 .claude。

| case | 合成输入/动作 | 必须观察到的输出及反例 |
| --- | --- | --- |
| R1-01 制品真实握手 | 干净构建wheel，独立venv安装，源码外init；真实MCP initialize/list_tools | 成功列出当前协议工具（基线47，后续数量按清单校验），无cwd configs报错；不能只验证help/EOF；不调用投递工具 |
| R1-02 资源闭包 | 默认求职信，无用户自定义skill，安装后加载SKILL及引用文件 | 文件可读、内容来自唯一源；默认不查venv父级.claude；合成materials可列出资源，不生成真实信 |
| R1-03 cwd/工作区 | W1在cwd A/B启动；W2另启动 | W1同一数据库；W1/W2数据库、letters、logs、form输出不串；cwd/源码/包目录无新data目录 |
| R1-04 幂等/失败 | 二次init；已有合成名单/资料/授权哨兵；损坏配置、只读目录 | 哨兵字节不变；错误非零且指明修复对象，不覆盖、不回落其他目录；不据哨兵宣称获得授权 |
| R1-05 两入口/兼容 | MCP及form_fill report；显式legacy config，--data-dir；冲突参数 | 共享workspace解析；legacy相对语义不变；合成报告写正确工作区；参数冲突拒绝；显式--data-dir优先，主动覆盖不属于默认workspace隔离保证 |
| R1-06 无私人状态/副作用 | 全新空目录；网络/Chrome探针；直接调用真实外发、浏览器、邮件、投递/恢复入口 | init及本地读取成功；所有真实副作用入口服务端拒绝，调用次数0；不生成许可、不读私人文件、不回落legacy；fake合成流程由测试直接种Store验证，公开意图初始化仍关闭；缺资料显示文件缺失/未初始化，不读profile/env或探测程序；允许读名单追加实际保护审计快照（决定25），不生成授权/反馈/投递状态 |
| R1-07 唯一源/包清单 | 构建两次、检查资源哈希及引用 | SKILL+references与源一致；没有个人数据、宿主作者授权配置；内部预发布标记和禁止用户渠道发布校验；资源清单足够运行而非只default.yaml；editable通过uv缓存键跟踪完整资源树，临时源码副本实证修改/新增/删除刷新，wheel无源码回落 |
| R1-08 防源码污染 | 实际wheel在repo外执行，去除PYTHONPATH/源码搜索路径 | import来自安装环境，禁访问源码仍通过；只用临时合成目录，CI能复现R1-01至07 |

先失败证据应覆盖默认配置缺失、默认skill缺失、cwd分散及form_fill默认入口，再实现。此前显式合成config可握手是正向基线，不应被误写成包完全不能运行。制品安装所需下载在CI准备阶段完成；初始化本身离线可用，不拿外部网络失败掩盖本地定位测试。

| 文件/模块责任（下一片唯一编辑者） | 拟改动 |
| --- | --- |
| pyproject.toml、构建资源配置/脚本（按支持的构建机制选最小方式） | 新init入口、只读资源打包、唯一源派生，不打包作者私有/授权配置 |
| src/intent_job_agent/config.py 及最小workspace/resource模块 | 共用定位/初始化服务；隔离新workspace语义与旧config语义，不改领域规则 |
| src/intent_job_agent/mcp_server.py、form_fill/cli.py | 两入口参数和可操作错误；接入新workspace的真实副作用拒绝；不设计consent/身份认证 |
| src/intent_job_agent/tools.py 的 DEFAULT_LETTER_SKILL、资源读取及副作用入口 | 改包资源定位，不重写求职信逻辑；只读检查依赖路径闭包；新workspace模式服务端真实副作用统一拒绝，保留legacy兼容 |
| configs/default.yaml、.claude/skills/cover-letter/ | 源资源；优先不改skill正文，若必须改按skill-creator验证，不能维护第二份 |
| tests/test_package_resources.py、test_workspace.py、制品smoke脚本（拟新增） | 上述八组回归、两个实际入口、合成资源输出、临时目录 |
| .github/workflows/ 下制品CI（拟新增）、README、相关规格/决定 | 四项检查与实际wheel安装探针、明确开发者入口及剩余发布门槛 |

退出条件：八组回归、四项检查和实际wheel CI验证；交审记录说明支持环境与未测平台，不宣称独立OS安装器完成。提交后等待独立评审再整合，R1本片修复CI阻塞并通过后整合，再立即R1b。

R1实施契约与入口清单见[public-r1.plan.md](public-r1.plan.md)，具体取舍记录于总决定28。R1禁止发行的静态回归限定workflow只读权限及已批准动作/验证命令，并拒绝合成上传或发布步骤；这是防意外变更的护栏，不是恶意代码或任意shell沙箱。新workspace模式沿Settings传至直接Toolbox/适配器，local-only不等于用户授权；显式legacy配置仅兼容既有开发调用。资源唯一源通过构建映射进入安装包，并验证sdist往返。

R1b详细规格、四格失效优先序、旧generation恢复及探针阶段见[public-r1b.md](public-r1b.md)。R1已签收main8530523，main CI39919661089通过；本片文档不把新能力记入旧HEAD。
