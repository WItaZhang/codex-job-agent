# 开放题答案库：样例（synthetic）

规格见 `open-answers.md`，计划见 `open-answers.plan.md`。下面的申请人（Sam Example）、联系方式和答案都是编的（synthetic），不来自任何真实申请人或知识库。O1–O7 的数据在 `tests/fixtures/open_answers/samples.yaml`，以那份文件为准；O8–O11 写在测试里（`tests/test_open_answers.py`）。

共同设定：`profile.json` 里申请人的邮箱 `sam.synthetic@example.test`、电话 `555-0100`、地址 `1 Synthetic Way`、城市 `Austin`、领英 `https://www.linkedin.com/in/sam-synthetic`。`applicant.yaml` 写 `open_answers: open_answers.yaml`（O10 除外）。预算 `open_answers.max_chars: 16000`（O8 除外）。

## O1 典型答案库

六条：`why_role` 一条已接受（带中文原话、一条否掉的说法和说明）、`about_me` 一条已接受（两行）、`why_role` 一条草稿、`anything_else` 一条已停用和一条已接受（两条否掉的说法）、`other` 一条已接受。

- 校验通过，已接受的四条按题型顺序进注入段：`why_role`、`about_me`、`anything_else`、`other`；组内按文件顺序。
- 每条写原题、定稿（多行照原样分行缩进）、`Rejected wording (never use): "…"; "…"`、`Note: …`。
- 草稿、已停用的、原话（中文）和 `from` 都不进注入段；没有条目的题型（如 `origin`）不出现。
- 注入段前面是使用规则：学取舍和口吻；为这个岗位和这道题重写，题目相同且合适才贴近复用；不添加事实；不用否掉的说法；不压过仲裁、SSN、支付、已经申请过、不许用 AI 写和验证码这些停下规则。

注入段的样子（节选）：

```text
== OPEN ANSWER EXAMPLES ==
The applicant wrote or approved these answers to open questions in earlier applications. Use them for every open
question (why this role, tell us about yourself, anything else and the like):
...
-- why_role: why this domain, role or company --
[why-role-ranking] Question: Why do you want to work on search ranking at our company?
  Answer:
    Ranking is where I learned that a model is only as good as the question it answers.
    On my last team I chose which signals to trust before I tuned anything.
    Your team owns that choice for the whole product, and that is the work I want.
  Rejected wording (never use): "I am passionate about building the future of search."
  Note: Say what the team owns, never praise the company in general.
```

## O2 条目里有联系方式

一条已接受的 `about_me`：原题里有申请人的领英链接，定稿里有申请人的邮箱，说明里有申请人的电话，否掉的说法里有别人的邮箱。

- 校验不通过，四个问题：`question` 有 `url`、`text` 有 `email`、`rejected[0]` 有 `other_email`、`notes` 有 `phone`。
- 错误信息里没有任何一个值（邮箱、电话、链接都不出现）。

## O3 重复的 id

两条都叫 `why-role`。校验不通过：`entries[1] (why-role): duplicate id 'why-role'`。

## O4 只有草稿和已停用的

校验通过，没有已接受的条目，不加注入段：`instructions()` 的结果和没配置时逐字相同。

## O5 空答案库

`entries: []`。校验通过，不加注入段，结果和没配置时逐字相同。

## O6 格式错误一次列全

`version: 2`、多余的顶层字段 `colour`、`id` 不合格（有大写和空格）、题型 `hobbies` 不在封闭集合里、已接受的没有 `from`、`text` 只有空白、状态 `final`、多余字段 `score`、`rejected` 里有空字符串、一条不是映射。全部问题在一条错误里列出。

## O7 原话不是英文

`origin` 一条已接受，`source_words` 有中文和日文。校验通过；注入段只有英文定稿，原话不出现。

## O8 超出预算

O1 的答案库、预算设成 200。校验不通过：`the accepted entries make an OPEN ANSWER EXAMPLES section of N characters, over open_answers.max_chars 200`，请缩短或改成 `retired`。注入段没有被截断，也不开始投递。

## O9 否掉的说法

O1 里 `anything-else-gap` 有两条否掉的说法，注入段写成一行 `Rejected wording (never use): "I am a fast learner and a team player."; "Thank you for considering my application."`；使用规则写明不用它们，也不用相似的说法。

## O10 没配置

`applicant.yaml` 没有 `open_answers`。`get_apply_setup` 没有 `open_answers` 这个键；`start_applying` 自动提交的 `sheet` 和填表模块的 `extra_prompt` 和原来逐字相同。

## O11 接进投递前的检查

- 配置了、文件不存在：`get_apply_setup` 的 `missing` 列出这个路径，`ready` 为 false；`start_applying` 不开始。
- 文件不合格（例如 O2）：`get_apply_setup` 的 `answer_sheet_problems` 写出问题（打码，不回显联系方式），`ready` 为 false；`start_applying`、`start_form_fill` 都不开始，什么都没交给填表 agent。
- 文件合格（O1）：`get_apply_setup` 返回 `open_answers: {file, accepted: 4}`，不返回原文；自动提交的 `sheet` 和填表模块的 `extra_prompt` 都带同一段 `== OPEN ANSWER EXAMPLES ==`，在 `== CHECKLIST ==` 之后。

## 切片 3：草稿/定稿对（synthetic）

规格见 4.6，计划见 `open-answers.slice3.plan.md`。草稿和定稿的文字在 `tests/fixtures/open_answers/pairs.yaml`（以那份文件为准），其余写在测试里（`tests/test_open_answer_pairs.py`）。

共同设定：申请人还是 Sam Example（联系方式同上）。岗位 `acme-ml-01`（Acme Robotics，ML Engineer），表单只有一页、在 `jobs.example.test` 上：p1.1 First Name、p1.2 Email、p1.3 "Why do you want to join Acme Robotics?"（多行文本）、p1.4 "Is there anything else you would like us to know?"（多行文本）。填表 agent 填完调用 `checkpoint("filled", open_answers=[{p1.3, why_role}, {p1.4, anything_else}])`，停在提交前。配置用默认值：每 5 秒读一次，不限时，界线 0.5；要看到时限的样例（如 P8）把时限设成 20 秒。

### P1–P3 分类

| 样例 | 定稿 | 分类 | 问什么 |
| --- | --- | --- | --- |
| P1 原样用 | 和草稿只差首尾和连续空白 | `unchanged`，改动 0 | 什么都不问 |
| P2 改过 | 改了一句里的几个词 | `edited`，改动不超过 0.5 | 只问收不收进答案库 |
| P3 重写 | 换了一套说法和事实 | `rewritten`，改动超过 0.5 | 问原因，再问收不收 |

另外：定稿是空的算重写；同一对草稿和定稿每次算出的改动都一样。

### P4–P8 读回

- **P4 交上**：第 1 次读是草稿，第 2 次读是申请人改过的，第 3 次读页面写着 "Thank you for applying!"。定稿是第 2 次读到的，`ended: received`；第 3 次读到的值不用。
- **P5 关掉**：读到改过的值以后标签页没了：定稿是最后读到的，`ended: closed`。
- **P6 离开**：读到改过的值以后，标签页换到 `mail.example.org`：定稿是最后读到的，`ended: left`；那个页面一次都不读。
- **P7 改完了**：申请人改完说"改完了"（`open_answer_pairs(read_now=true)`）：马上读一次，`ended: user_done`；另一个岗位的标签页接着看。
- **P8 没在看的时候交了**：第一次看到时限，对还在等；之后申请人交了；下一次看时页面写着已经交上：`no_final`（最后读到的是上一次看的），不分类。开始看时标签页已经不在，也是 `no_final`。

### P9 记草稿时不收的

- `open_answers` 里写了 p1.9（这一页没有）、p1.2（Email，对到联系方式条目）：都不记，回复里按栏号说明，p1.3 照常记。
- 题型写成 `hobbies`：整次调用不收（schema 校验），这一页没有记。
- 草稿和原题来自代码的读回；agent 回复里说的文字不用。

### P10–P13 选择题

- **P10 原因**：P3 那对的原因题选 5 "其他（我自己说）"，申请人说 "I would never call myself a self-starter." → 逐字记下；选 0 跳过 → `{"skipped": true}`；不带 `user_report` → 不收。
- **P11 收进**：P2 那对选 1 → 答案库末尾多一条：`status: accepted`、`type: why_role`、原题、定稿原文、`from: choice <对的编号>:promote#1`、`added` 是当天；原来的条目和注释一字不变，重读校验通过。选 2 并给 `type: origin` → 题型是 `origin`。选 3 不收 → 只记下选择，答案库不变。
- **P12 收不进去**：定稿里有申请人的电话（读回时打成了 `[phone]`）→ 不收，问题变成"收不进去（含联系方式）"，只有 3 / 4 / 0；加上这一条超出预算 → "加上这一条有 N 字符，上限 M"，同样三项；答案库不变，文字不被截断。
- **P13 我改一下再收**：选 4，申请人写英文 → 逐字收进，`from: own words <对的编号>:promote#4`，`source_words` 是他写的原话；写中文、协调者给了两个译法 → 先出一道译法选择题，选 2 → 收进第 2 个译法，`source_words` 是中文原话，`from: choice <对的编号>:render#2 (rendering of own words)`。

### P14–P16 边界

- **P14 自动提交的岗位**：没有对；它的指令里没有 `open_answers` 的说明。
- **P15 打码**：申请人在定稿里写了自己的邮箱：对的文件里只有 `[email]`；所有对的文件按 profile 扫不出联系方式；`open_answer_pairs` 的返回里没有草稿和定稿的文字；数据库里也没有。
- **P16 让路**：看的时候开始新的填表运行：先停下不看，对接着等；别的会话的看（`watch-…` 那一行）让出来，新运行照常开始；用户说"停下"也停下不看。
