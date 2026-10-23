---
name: merge
description: 把开发完成的 worktree 分支合并回 main：跑验证、推送、清理 worktree 和分支。在用户请求或授权协调者安排合并时运行。
---

# /merge：把开发分支合并回 main

替用户做完合并的全部 git 操作，用户不需要自己敲 git 命令。授权协调者决定常规取舍；Claude Code 和 Codex 均可使用。任务明确“先不推送/合并”时不运行。

参数为分支名：取用户或协调者消息指定的分支（Claude Code 的 $ARGUMENTS；为空时按第 4 步找）

无论在哪一步停下，只要已经持锁，结束前都运行 `python3 .claude/hooks/code_owner.py release`，不把主目录的锁留在本会话。

## 1. 确认位置

运行 `python3 .claude/hooks/code_owner.py status`。标着"← 当前"的那一行不是主目录，说明本会话在 worktree 里：由协调者安排持锁主目录的合并执行；不能占用其他会话的检出。主目录不在 `main` 上时也停下，把情况告诉用户。

## 2. 持锁

`python3 .claude/hooks/code_owner.py claim 合并`

## 3. 主目录的未提交改动

`git status --short` 不为空时，列出这些文件；仅处理本次授权范围内且归属明确的改动，其他会话改动不提交、不丢弃，安排隔离执行或报告实际冲突。

## 4. 选分支

给了分支名就用它，先确认分支存在、并且有 `main` 上没有的提交。没给就看 status 里的 worktree 分支，找出有 `main` 上没有的提交的（`git log main..<分支> --oneline`），附上各自最近一条提交说明，由授权协调者选择本次交付范围内的分支，并说明合并后会归档它的开发会话、删除分支；另给一个选项"这次不合并，只推送"。没有可合并的分支，或者本次只安排推送，跳到第 7 步。

## 5. 合并并验证

1. `git merge --no-ff <分支> -m "Merge <分支>: <这个需求的一句话说明>"`
2. 有冲突时：两边意图清楚的（比如各自往同一个文件里加了不同内容），解决后把改法讲给用户，协调者检查后提交；牵涉设计决定或看不懂的，`git merge --abort`，由协调者安排在开发检出吸收 main 并解决；缺少需求事实时报告具体缺失。
3. 跑"验证"里的全部命令。不通过不推送、不清理；保留现场并由协调者安排修复，不用 reset --hard 丢弃其他改动。

## 6. 清理

1. 先看那个 worktree 里有没有未提交的改动（`git -C <worktree 路径> status --short`）。有就不清理，告诉用户。
2. 开发会话在桌面版里时，归档它（会一起移除它的 worktree）；做不到就 `git worktree remove <worktree 路径>`，不加 `--force`。
3. `git branch -d <分支>`；分支已经不在就跳过。

## 7. 推送

`main` 比 `origin/main` 新时运行 `git push origin main`。推送失败（没网、没登录）就把原因告诉用户：合并已经在本地，下次运行 /merge 会再推。

## 8. 释放并汇报

运行 `python3 .claude/hooks/code_owner.py release`，再用两三句话告诉用户：合并了什么、验证结果、是否已推送、还有哪些 worktree 在开发中。
