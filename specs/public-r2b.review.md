# R2b 规格交审记录

对应[规格](public-r2b.md)、[计划](public-r2b.plan.md)、[模块决定](public-r2b.decisions.md)。本片只有文档，实施/测试代码未获批准。

## 基线和执行边界

新树public-r2b-plan，分支WItaZhang/public-r2b-plan，基线main23aa66bca4c40b69a78bad049f5681bea41522db。确认无人持锁后以真实Codex线程claim；ff-only main为Already up to date；uv sync --locked成功，CPython3.13.16，58包解析/54包安装。只写本片四文档，不改owner/main/R2、skills、依赖或根规格；不读profile/.env/key、不接账号或真实业务。R2固定04d629e及006仅按甲方给定范围验收，R2b不解除其GUI门槛。

## 证据来源与采纳界限

- `/tmp/public-r2b-runtime-proposal.md`：Claude只读代码映射与建议，可能继续修订，不自动变成规格。关键内容已落在仓库矩阵，不仅依赖tmp文件。
- `/tmp/public-r2b-provider-doc-evidence.json`：2026-10-23T12:44Z候选官方来源核对，SDK/model/price/account/live evidence均未验证。本会话按OpenAI Docs技能再次搜索并打开官方页面，仅核合同不选供应商，不作API请求。
- [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)：结构化文本与工具调用区分；不支持strict schema会失败，拒答/incomplete不能当有效领域结果。
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)：显式strict；对象additionalProperties=false、字段required、可空需显式；模型参数仍要本地校验。规格不照抄“所有默认值改nullable”的推论，避免改变原schema语义。
- [Background mode](https://developers.openai.com/api/docs/guides/background)：后台取消能力与响应ID/模式相关，服务端存在响应保留；不推导普通HTTP断开即远端取消。选型前默认不启用background，是否支持/数据告知按实际适配核对。
- 本树`llm.py`实际只有fake/host；`apply_bridge.py`的claude_command、MCP npx配置和`form_fill/runner.py`证明宿主依赖。`applypilot_bridge.configure/run_sources`已有require_external，setup_status在local_only提前返回；预研“绕过settings”不能作为事实采纳。独立环境凭据模型路径仍是迁移对象。
- 只读四个skills正文，对照规则形成规格§4映射；没有调用skills业务动作或改正文。既有“错误原样汇报”在公众流程改为固定脱敏错误，此为展示适配而非技能正文暗改。

## 甲方澄清落实

1. 编号依据为总决定27/§4.1，本模块决定1–4；决定30仅R2。不占根新编号，提交用public R2b plan。
2. 独立核心接口/fake可先行只是待批准建议。当前只规格，没有提前启动S1。
3. R3依赖R2b：R2b验收供R3消费的协议及合成消费者，不要求R3页面先完成；自身R2配置/模型/凭据状态/费用/停止必须接线实测。完整fake合成提交终态保持。
4. 不要求用户装Node不等于禁用已审核包内driver；合成sink计数与真实sink0分开，避免“完整提交但所有sink0”的虚假证据。
5. 取消多维记录、未知费用不作0、模型不授权、真实provider最低一家及受控实证均硬性保留。

## 当前交审状态

| 字段 | 记录 |
| --- | --- |
| 切片 | public R2b plan，仅四文档 |
| 产品行为 | 无运行行为变更；完整目标/样例/接口/依赖/责任与估时明确 |
| 状态 | 规格交审，未批准实施；未声称R2b或公众交付通过 |
| 提交证据 | 本文随四文档固定提交，最终HEAD在交审回复报告 |
| 验证结果 | uv sync --locked成功；ruff check src tests通过、ruff format --check src tests通过（98文件）；四文档本地链接存在性/编号/范围及diff检查通过。纯规格未重跑pytest；未执行新provider、浏览器或业务测试 |
| 阻塞 | 文档无技术阻塞；实施需甲方批准；S5账号/版本/费率/网络实证未取得，S6依赖R2集成与真实UI证据 |
| 下一步及估时 | 甲方/Claude审本规格；若批准S1，预计2–3有效工作日；完整实施15–26日另含规格0.5–1日，不含外部等待 |

根文件准确待安排差异见plan§4。本片不把协调者安排R2b只读预研当实施授权，不启动新agent，不推送合并。文档检查不等于新增契约已测试；未来每片先红后绿，整体退出仍须四项、制品及真实provider/可见配置证据。

## 固定b50bcec评审后的修订交审

依据`/tmp/public-r2b-plan-review.md`及甲方裁决采纳B1/C2/C1，模块决定5与原规格同时修改。B1采用强于评审“预留钩子”的要求：S1必须有逐节点白名单、不可省略的确定性过滤/打码、未声明类别拒绝和实际序列化哨兵反证，模型无个人文件读取能力；求职信不扩大既有材料授权。C2明确外挂测试注入合成生效意图/资料，R2草稿不自动生效；增加显式proposal/选择/decide、搜索候选选择及公众/个人授权来源区别。C1修U01并保留真实键盘/读屏要求。

同时纳入budget_exhausted、v1协议版本及兼容、测试确认/sink不进入公众包（fake provider仍允许）、独立runtime配置节及协调者合并顺序。仅原四文档改动，未实现、未改skills/根规格/R2，S1仍待修订复审后正式授权。本次按甲方要求固定修订HEAD后跑四项，具体结果随后追加；不将旧规格片未跑pytest记录改写成当时通过。

### 修订固定HEAD四项与后续配置建议

修订固定HEAD `d7a622e73a26f088a57b89f846bdde850ba4cdd8` 四项全通过：uv sync --locked、ruff check src tests、ruff format --check src tests（98文件）、pytest **1753 passed/89.47s**。日志分别为`/tmp/public-r2b-plan-four-{sync,lint,format,full}.log`。四文档本地链接与diff检查通过；这是main23aa66b代码基线的验证，不含未合R2的1906项，也不证明新R2b契约已实现。后续仅本证据文档提交，不改实现，无需为同代码重复全套。

甲方补充`/tmp/public-r2b-s1-config-review.md`仅作未来S1参考，不授权代码改动：独立runtime节，旧workspace缺节用包内默认资源唯一源，不回落legacy llm凭据；不碰R2 setup_drafts。实施前明确部分覆盖/嵌套合并语义并测试，禁止浅合并丢默认；避免config→runtime.config→config循环依赖，类型放置以实际依赖图验证。零重试等合法预算按语义允许0，不采纳机械所有字段正数的建议。合并顺序由协调者负责，不能承诺放在不同代码位置就一定无git冲突；必须实际集成验证。新字段对旧版读取的影响留R7兼容记录，不在此片实施迁移。S1具体范围仍待修订复审后正式授权。

## S1正式实施准入

规格cb05496已整合main d15f440（甲方合并四项1753/77.97s，CI39958072188由协调者跟进）。新树public-r2b-s1-runtime无人持锁，真实会话claim后ff-only main/sync locked成功。甲方正式批准S1，模块决定6/总决定31记录范围；未启动S2–S6。后续将分别记录首步语义红、实际实现绿、取消/过滤毒丸和完整四项/制品结果；当前不将获准当通过。

### S1首步反证与配置小步

`/tmp/public-r2b-s1-semantic-red.log`保留5项语义失败：旧FakeClient未脚本化返回默认空对象，以及故意不合格空过滤、布尔取消、缺usage填0、模型actor/next_state可信的四个负向实现。后四项是契约反例而非现有生产漏洞证明。配置新增测试初轮2失败/7通过（尚缺新模块，独立标为接口缺失，不取代上述语义红），随后递归覆盖/旧快照/未知拒绝及资源10项通过。配置类型独立，不import Settings；默认只来自包内default.yaml，旧llm不改。计划旧状态原文已同步此次授权。

### S1执行器与过滤稳定点

甲方配置裁决已落实为模块决定7：默认unconfigured；external仅用于未配置凭据/未支持状态，不加载任何secret或SDK；fake显式选择并标模拟。全部容量0合法、发送0，重试0允许首次请求；剩余总时限与单次时限取min。运行入口Runtime.load持验证类型，旧llm.py未改。独立双顺序import与嵌套配置测试通过。

实际节点过滤包括discovery/tagging/analysis/intent_edit/letter/form_fill，历史与tool结果同策略过滤；分析只取job_tags不传原岗位全文。真实序列化body、文件/网络毒丸、超限/未知类别、仅材料白名单、受限工具提议和旧接口兼容均覆盖。S1不执行业务工具，不携带合成提交/确认控制器。

反证性质更正：semantic-red中旧FakeClient是既有行为反证，其余4项仅硬编码合同示意，不计产品链路红；contract-red 9项/provider-red 18项是新接口缺失。现有tests/test_runtime_mutations.py对同一实际Runtime调用链注入透传assemble、伪造远端取消确认、缺usage归零、跳过输出校验；`R2B_MUTATION=filter/cancel/usage/authority uv run python -m pytest -q tests/test_runtime_mutations.py`各1 failed/3 passed，精确命中对应产品断言，日志`/tmp/public-r2b-s1-mutation-*.log`。开关仅在tests，不进入产品代码；撤掉后全绿。

中间38 passed的唯一warning为Pydantic Tool.schema遮蔽基类属性，已更名parameters，未过滤warnings。后续81项通过无warning。新增事件一致性校验时，Handle初始状态尚为failed无error导致53 failed/28 passed；修为真实running状态后81项通过，未放宽事件断言。迟到usage仅更新用量、不发布已取消/失效value；重试中未知用量不沿用上一轮总量，逐尝试记录保留。Event覆盖取消/失效/在途槽位/总时限短于单次，未证明远端实际取消（S5待验）。

基线main d15f440的CI39958072188由甲方确认全success（四项/产品browser/package/editable，日志`/tmp/public-r2b-plan-main-ci.log`），不当作本S1全量验证。当前实现稳定点先提交交Claude独立审阅，完整四项与安装包runtime资源证据继续执行，不进入S2–S6。

### 固定3513cb1全量结果及协议补齐

3513cb1fc683c454424ba1ea060745d1528fb2e0四项通过：sync、lint、format（109文件），pytest **1833 passed/96.80s**，日志`/tmp/public-r2b-s1-four-{sync,lint,format,full}.log`。`/tmp/public-r2b-s1-package.log`记录direct wheel和sdist→wheel均通过；包外运行验证独立runtime默认/旧快照/部分覆盖、实际fake请求打码，原47工具握手、full/scheduled真实副作用拒绝均通过，未上传。此结果仅该固定HEAD。

甲方发现S1命令协议缺口，确认原实现未满足；另consume(None/[]/'x'/1)泄漏AttributeError、completed+local stopped未拒绝。新增测试先红18项：前5项是实际consumer语义失败，其余命令模块尚缺，不混记；日志`/tmp/public-r2b-s1-commands-red.log`。修复加入commands.py封闭解析与可信引用/作用域校验，无动态导入或业务权限，Event跨字段验证；定向98项先通过，再补命令在途取消/revision和tool引用屏障。四项旧轮未中断或盲重跑。模块决定8记录，当前稳定修复继续固定交审。

### X1–X4执行器范围修复

a6f0bbc命令修复固定HEAD的中间四项1854 passed/83.59s，lint/format/sync及direct wheel/sdist通过（`/tmp/public-r2b-s1-final-*.log`），不作为后续X修复通过。根静态认可命令方向，普通完成roundtrip测试已准确改名，不再名为迟到usage实证。

依据`/tmp/public-r2b-s1-runtime-review.md`及甲方裁决，模块决定9同步现态：max_tool_calls=0仅禁tool_turn；单Runtime首次有效接纳时绑定scope，其他scope固定stale，不占预算；总时钟在成功启动的provider worker实际调用send前起算，同run间隔计入，构造空闲不计。两个线程层级启动失败均回滚未发送占位，internal/drained且发送计数0，后续可使用槽位。后续S2必须新run新Runtime，不提供命令重置预算。S5传输必须有自己的时限/结束机制，S1不强杀不合作线程；其槽位保留到实际返回，不宣称远端取消。

`/tmp/public-r2b-s1-x-red.log`为5项真实产品语义失败，非ImportError；相同断言修复后105项定向通过，再补首次scope并发竞争及非法输入不绑定的屏障。修复后新HEAD另跑全量/包契约，原中间结果保留。

## S1固定2f4d5c7最终本地交审

产品实现HEAD `2f4d5c78653e6d87e84fb795e6a56c5e7a2e155c`。runtime定向107项通过（`/tmp/public-r2b-s1-x-stable.log`），完整四项通过：sync locked、ruff check、format check（112文件）、**1860 passed/85.13s，无skip或warning**。日志`/tmp/public-r2b-s1-x-four-{sync,lint,format,full}.log`。`/tmp/public-r2b-s1-x-package.log`记录direct wheel及sdist→wheel均安装探针通过，runtime默认/缺节/部分覆盖/真实序列化fake、原资源闭包/47工具握手/full与scheduled local_only拒绝/第二入口通过；未上传或发行。

提交范围：独立runtime配置与唯一源默认、字段过滤/打码、协议v1事件及封闭命令、受限工具提议、显式fake、错误/预算/多维取消/迟到usage、单scope生命周期和线程启动失败回滚、旧接口兼容；根索引/排期和决定31按授权同步。llm.py/业务领域/skills/R2未改，不新增SDK、网络、真实凭据或S2编排。实际四组mutation红与旧FakeClient反证、接口缺失红、中间事件初始化失败和X红均已在上述历史记录区分，未以最终绿抹除。

| 字段 | 最终交审状态 |
| --- | --- |
| 切片 | 公众R2b S1 |
| 产品行为 | 显式fake可验证模型契约；默认未配置不发送；字段/协议/作用域/预算拒绝，过期value不发布、usage可迟到 |
| 状态 | 本地验证完成、代码冻结；待独立审阅及协调者整合决定，不代表R2b全量或公众发布通过 |
| 提交证据 | 产品2f4d5c7；本次后续仅证据文档提交，最终文档HEAD在回复中报告 |
| 验证结果 | 107定向、1860全套、wheel/sdist安装探针通过 |
| 阻塞/限制 | S1远端CI未运行；真实provider/传输结束机制S5待实证；S2–S6未授权，R2真实GUI门槛独立保留；不合作线程只能保留槽位，不能强杀或证明远端取消 |
| 下一步及估时 | 协调者/Claude独立窄复核本固定HEAD；未获新任务不改实现、不推进S2，评审等待无工程时长承诺 |

本证据提交不改产品代码或包，不重复同代码全套；工作区检查后释放锁，不推送合并。

## Y1：已接纳未发送身份保留（模块决定10）

固定2f4d5c7的无条件finally回滚经真实调用链Event屏障复现，非概率重跑：`/tmp/public-r2b-s1-y1-red.log` 6 failed / 8 passed。取消、invalidate/revision失效后的跨run及重复ID、发送前预算耗尽ID回退均纳入；invalidate同scope本就被invalid集合拒绝，保留该正例不冒称全部红。

回滚仅限外层执行线程/内层传输线程实际启动失败；普通收尾仅释放槽。两层失败仍internal/drained/发送0，并允许相同ID在新scope重新接纳。定向runtime验证114 passed，无warning；四项及package待本次固定实现HEAD验证。S1仍待独立签收，不进入S2–S6。
