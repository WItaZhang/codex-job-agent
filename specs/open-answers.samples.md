# 开放题答案库：样例（synthetic）

规格见 `open-answers.md`，计划见 `open-answers.plan.md`。下面的申请人（Sam Example）、联系方式和答案都是编的（synthetic），不来自任何真实申请人或知识库。O1–O7 的数据在 `tests/fixtures/open_answers/samples.yaml`，以那份文件为准；O8–O11 写在测试里（`tests/test_open_answers.py`）。

共同设定：`profile.json` 里申请人的邮箱 `sam.synthetic@example.test`、电话 `555-0100`、地址 `1 Synthetic Way`、城市 `Austin`、领英 `https://www.linkedin.com/in/sam-synthetic`。`applicant.yaml` 写 `open_answers: open_answers.yaml`（O10 除外）。预算 `open_answers.max_chars: 8000`（O8 除外）。

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
