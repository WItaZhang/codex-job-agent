# R2b 规格切片与后续独立实施计划

依据总决定27/公众§4.1及[本模块规格](public-r2b.md)，[模块决定](public-r2b.decisions.md)、[交审](public-r2b.review.md)。当前已由总决定31/模块决定6批准S1，S2–S6仍待分别批准。不改R2、owner、main或skills；本次根索引/排期与决定31已按授权同步。R2代码继续冻结，R2b不解除R2门槛。

## 1. 依赖与顺序

基线main23aa66b含R1/R1b。建议依次S1→S2→S3→S4；S5在S1后可独立安排，单开发者不隐含并行；S6依赖S1–S5及R2集成基线。R2等待GUI时可以先审批S1，再审批不触碰R2文件的S2–S5，当前仅S1获准。每片新分支/唯一持锁检出，合并与锁由协调者安排。

R2→S6→R2b整体出口→R3，没有R3页面反向依赖。R2b定义供R3展示的事件/命令/错误/usage/取消协议，以合成消费者验证，完整fake A/B由包外测试驱动器注入合成已生效意图/资料和受限替身，验证至合成提交终态，不从R2草稿自动生效；确认控制器/sink不入公众包，fake provider本身不因此禁止。S6仍必须把R2b自己的配置/费用/停止界面接入R2并真实实测。R4/R5约束真实业务，独立获准的开发provider合成探针不是公众权限；没有测试账号则S5实证待验证，S1–S4结果不能替代。

| 片 | 范围、先红顺序和出口 | 文件责任候选（批准实施时固定） | 依赖 | 有效工作日 |
| --- | --- | --- | --- | --- |
| S0 当前规格 | 四文档/样例/技能规则映射、依赖/预算、交审；不写实现/测试 | 仅public-r2b四文档 | 总决定27 | 0.5–1 |
| S1 接口与fake契约 | 现有make_client真实provider不支持、fake默认空值反证→必经节点白名单组装/打码及哨兵→请求/结果/schema转换→无key fake/错误/usage→Event取消/过期/越权→版本化合成消费者/预算耗尽；旧host兼容 | runtime/provider.py、runtime/contracts.py、runtime/providers/fake.py、llm.py最小兼容、tests/test_runtime_contract.py；config.py仅新增独立runtime配置节，不碰R2字段，合并顺序由协调者负责 | main基线；不依赖R2 UI；已批准 | 2–3 |
| S2 A确定性流程 | F01–03搜索候选选择→A01–05含显式意图proposal/选择/decide；失败抓取继续已有队列、预算唯一分派；today两步/真实来源/stale/同岗，统一发现模型注入；直接适配local_only毒丸 | runtime/orchestrator/fetch.py、today.py、apply_list.py、runtime/tools.py、applypilot_bridge.py适配；tests/test_runtime_a.py | S1；不改R2服务或领域算法；待批准 | 3–5 |
| S3 求职信与B协调 | B01→批次/保护守卫B02→恢复B04；fake材料、校验和打印替身；停止/错误不漏过校验 | runtime/orchestrator/cover_letter.py、apply.py；tools.py/apply_bridge.py仅接口适配；tests/test_runtime_b.py | S1/S2稳定事件；待批准 | 2–3 |
| S4 无宿主受限浏览器循环 | 现有claude/npx依赖反证→封闭工具和注入→包外受限替身与合成站点/精确binding→合成提交receipt/断链核查/重启→无宿主进程来源 | runtime/browser_agent.py、受限browser_tools.py、apply_bridge.py、form_fill/runner.py接口、合成fixtures与probe；不改vendor业务逻辑 | S1/S3/R1b；无真实网站；待批准 | 4–7 |
| S5 一个真实provider | 先官方版本/能力/许可/费率核对并记录选择→不合格adapter协议红例→真实适配→获准账号合成探针；受控事件取消/usage/错误证据分层 | runtime/providers/选定适配.py、schema转换、tests/test_provider_contract.py、独立probe脚本；依赖如需新增uv锁定 | S1；账号/网络/数据政策/费用预算明确才实测；待批准 | 2–4 |
| S6 R2可见配置与整体制品 | 合成消费者协议拒绝旧revision→R2配置/费用/停止页面真实输入→无宿主完整A/B fake+提交终态→真实adapter证据归档/四项/安装与清单 | R2整合后ui/service/controller/assets及壳最小接线、配置/打包资源、tests/probes；R3仅协议不写完整页面 | R2可集成签收基线及S1–S5；待批准 | 2–4 |

合计15.5–27有效工作日，含当前规格0.5–1日；实施15–26日，不含R2环境等待、账号许可、独立评审/CI与站点外部条件。此为按完整流程分解的规划估算，不是上线承诺；原6–10日已按总决定31同步根路线。S4单列最高不确定性，先窄探针；若不能安全保留契约，报告证据再改计划，不削掉提交/恢复终态。

## 2. 首个实施片的具体红绿合同（已批准）

输入：脚本fake返回`tags`，没有key；错误脚本返回额外`next_state=submit`；真实配置仅有provider/model没有credential；取消用Event停在发送前和回复前。输出：fake合法值经原schema；额外字段拒绝且工具执行0；真实配置missing_credential但fake仍可用；发送前停止不发请求，发送后本地停止/远端未知分开，迟到值不更新revision。注入进程环境中的合成key/宿主配置哨兵不得读取，不碰真实个人文件。

先让不合格实现（默认空响应、布尔cancelled、缺usage填0、信任模型actor）违反断言，再最小实现。测试不得只以新模块ImportError作为反证；用现有llm.make_client/FakeClient的实际行为说明缺口，并保留legacy调用原行为的兼容边界。S1不接UI、不改授权/Store生效规则、不装浏览器、不做网络请求。不新增网络SDK。所有阈值加入有来源的独立runtime配置节而非散落魔数；config.py不碰R2字段。合并顺序由协调者负责。

S1必选隐私反证C06：上游对象混入synthetic邮箱/电话/简历哨兵，逐节点验证实际送给fake provider的序列化请求；未声明类别/缺策略拒绝，空过滤实现必须红，合法最少字段正例通过，个人文件读取毒丸0。过滤覆盖重试/历史消息/tool结果，不提供raw prompt旁路。S1落实规格§2.1所有节点的类别上限与非可选边界，S2/S3再接具体材料服务；不能只留接口待以后补。求职信材料上限不扩大profile读取。

S1协议出口同时包含v1封套、缺版本/未知字段/不支持版本拒绝、明确适配的兼容规则、budget_exhausted停止新动作以及旧LLMClient/FakeClient/NoModelClient/MCP host行为回归。首片已获甲方正式授权。

## 3. 接口与运行时选型待证据项

- provider/SDK/model未选择；OpenAI只是有官方文档证据的候选。S5选择前核对精确版本、严格schema子集、工具/取消能力、usage、隐私留存与费率。不能把本片文档访问当API实证。
- S4优先验证现有Python Playwright+包内driver的小型封闭适配，备选包内固定MCP server；两者均可能使用Node，不运行npx下载。以现有readback/checkpoint语义和注入反例决定，不预先宣称可无损替换所有工具。
- 发现增强目标是统一provider并保留可选无模型跳过；vendor注入方式先验证，不依赖全进程环境切换串workspace凭据。
- 浏览器/PDF子进程环境最小白名单按实际必要变量验证；此处只治理运行时触达的sink，不借机重写R2已冻结PDF实现。
- 无GPU硬要求；fake/纯逻辑使用本机CPU，真实模型在供应商侧。S1预算测试可全fake，真实探针需限定调用/输出token预算。没有实测请求量/费率前不报货币成本。S4/S6实测浏览器/进程族内存与耗时，不能用R2静态启动采样代替。

## 4. 验证、交付及协调者待安排差异

S1完整交审：固定修订HEAD后跑仓库四项及链接/编号/范围/diff核对，结果记review；实现片均先红后绿、定向与仓库四项，必要制品/原生证据另列。完整退出按规格矩阵，不以单片测试绿放行。每个小步英文commit用public R2b及模块决定号，提交后交审、不推送合并。

本次已获授权并完成的根文件同步（后续R2集成由协调者安排）：

1. `specs/public-release.md` §4 R2b行：6–10日改为审阅后的分片估算，依赖区分S1–S5前置与R2集成出口；总串行估算相应重算（仅替换此行时45.5–77.5变为55–94，其余并行/已耗时不在本片重算）。
2. 同文件§4.1加本模块互链、明确供R3协议由合成消费者验证而非等待R3页面；可见配置必须R2实测。
3. `spec.md`模块索引增加public-r2b链接；根新决定31由协调者分配并已追加，决定30保留R2。
4. main公众规格仍有R1b“未合main”等历史状态，R2分支已有更新安排；本片不重写该状态。实际R1b合并23aa66b及CI39934310215证据不能误写成R2b新能力。

统一进度字段：切片、产品行为、状态、提交证据、验证结果、阻塞、下一步及估时。当前状态是S1实施；完整S1验证后冻结交独立评审，不自行进入S2。

## 5. S1执行步骤与唯一文件责任

1. 首步记录旧fake空响应及故意不合格过滤/取消/usage/actor行为的断言失败（非ImportError）；随后相同断言作用于真实runtime。
2. runtime/config.py独立模型/递归覆盖；config.py仅runtime新增节，configs/default.yaml唯一默认；旧workspace及包内资源回归。
3. runtime/contracts.py、privacy.py：v1封套/固定错误/字段策略/确定性打码和实际序列化；未知类别/额外字段失败关闭。
4. runtime/provider.py、providers/fake.py：schema/工具提议、配置状态、并发/预算/取消与迟到usage；无网络实现。
5. tests/test_runtime_*.py和合成fixture：Event屏障、毒丸、legacy兼容、版本消费及资源安装契约；每个通过小步英文提交，四项/制品探针后冻结。

llm.py除必要兼容不改；不触碰R2字段、业务Store或skills。根索引/排期本次按协调者授权更新，后续R2合并冲突由协调者安排实际验证。
