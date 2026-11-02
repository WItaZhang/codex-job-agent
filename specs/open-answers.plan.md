# 开放题答案库切片 1 计划：答案库和注入（2026-11-02，总规格决定 35）

规格：`specs/open-answers.md`；决定记录：`specs/open-answers.decisions.md`；样例：`specs/open-answers.samples.md`、`tests/fixtures/open_answers/samples.yaml`。用户全权委托（决定 25），按规格先行、测试先行直接实施。vendor 文件不改。

## 1. 做什么

1. **申请人字段**：`Applicant` 加可选的 `open_answers: Path | None`（照 `chrome_user_data_dir` 的加法，`extra="forbid"` 不变），路径相对申请人目录解析；配置了时 `missing_files()` 包括它。
2. **模型和加载函数**（新模块 `src/intent_job_agent/open_answers.py`）：
   - 题型封闭集合 `QUESTION_TYPES`（顺序即注入顺序）、状态 `draft | accepted | retired`、条目和答案库的 pydantic 模型。
   - `load(path, redactor, max_chars)`：照 `form_fill/inputs.py` 的 `load_checklist`，用同一套 YAML 读取（重复键、按文字读标量），一次收集全部问题；检查 3.1/4.2 的每一项；有 `redactor` 时检查联系方式（`Redactor.hits`，只报字段和种类）；渲染注入段并检查预算。
   - `section(bank)`：确定性地渲染注入段；没有已接受条目时返回空字符串。
3. **配置**：`configs/default.yaml` 加 `open_answers.max_chars: 8000`；`Settings.open_answers` 有同值默认，旧工作区配置不用改。
4. **注入**：`form_fill.instructions(answers, checklist, open_answers=None)` 在要求清单后接上注入段；`Inputs`、`RunContext` 各加一个默认为空的 `open_answers`；`load_inputs` 加载答案库并把问题并入同一条错误；`start_applying`、`resume_applying` 的 `sheet` 和 `run_job` 的 `extra_prompt` 都传它。
5. **投递前检查**：`_answer_sheet`（`get_apply_setup` 用）也加载答案库，问题进 `answer_sheet_problems`（打码）；配置了时 `get_apply_setup` 多返回 `open_answers: {file, accepted}`。
6. **示例和文档**：`docs/examples/applicant/open_answers.example.yaml`（合成、带注释），`applicant.example.yaml` 加 `open_answers` 一行，`docs/usage.md` 投递一节加一小段。

## 2. 不做什么

- 不做冷启动采访、草稿/定稿对、归纳回放、指标（切片 2–5）。
- 不给求职信模型、打标签或分析模型；不进数据库；工具不返回原文。
- 不改 `apply_bridge.OPEN_ANSWERS` 和 ApplyPilot 原句替换；不改停下规则；不截断超预算的答案库。
- 不读任何 `profile.json` 或 `.env`（测试只用合成的）；不建个人数据，不从作者知识库复制；不启动任何真实抓取、填表或投递。

## 3. 测试（先写，先失败）

`tests/test_open_answers.py`：

| 测试 | 样例 | 看什么 |
| --- | --- | --- |
| 样例逐个加载 | O1–O7 | 问题按 `expect.problems` 出现；已接受的 id；注入段按顺序含有 / 不含 |
| 联系方式不回显 | O2 | 错误信息里没有任何联系方式的值 |
| 预算 | O8 | 超了报字符数和上限，不截断；刚好等于上限通过 |
| 注入不变 | O4、O5、无答案库 | `instructions()` 和不带答案库时逐字相同 |
| 注入段的规则 | O1 | 使用规则里有：学取舍和口吻、这个岗位和这道题、题目相同才复用、不添加事实、否掉的说法、五条停下规则和验证码；注入段在 `== CHECKLIST ==` 之后 |
| 申请人字段 | O10、O11 | 没配置为 None；相对路径按申请人目录解析；配置了文件不存在算缺文件 |
| /apply | O1、O2、O10、O11 | 自动提交的 `sheet` 和填表模块的 `extra_prompt` 带同一段；没配置时逐字不变；不合格时 `start_applying` 不开始；`get_apply_setup` 的 `open_answers` 和 `answer_sheet_problems` |
| 填表模块 | O1、O2 | `start_form_fill` 的 `extra_prompt` 带注入段；不合格时不开始 |
| 示例文件 | — | `open_answers.example.yaml` 按示例 profile 加载通过，有已接受的条目 |
| 不进打标签 | O1 | `get_tagging_rules` 的输出里没有答案库的文字 |

## 4. 验证

CLAUDE.md "验证" 的四条命令全部通过；`git merge main` 后再跑一遍。
