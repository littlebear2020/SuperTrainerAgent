# PPT 美化长课件未交付：研究交接

> **状态**：建议，待核实。本文不是实施计划，核实完成前不要改代码。
> **TL;DR**：46 页美化在两次确认之后没有写出规格和 PPTX；模型把预算花在重读上，最后又重问已确认的方案。建议只修「确认已落盘就禁止重问」和「同一条对话里分段续写」。不要恢复跨会话 `resume-execute`，也不要把 46 页手写 SVG 改成脚本批量生成。

交给 Kimi 做深入核实。结论以仓库代码和下面这条真实对话为准，不要只复述本文。

## 1. 已观察到的失败

对话 `4fc235e0-b0b9-49c3-b517-4ca1777d5768`，库表时间 2026-11-11 16:08–16:33 UTC。用户上传《店长招聘与汰换 · 乐刻运动区域经理培训.pptx》（8.3 MB，46 页，16:9），要求文字不动、只美化排版，并交付可编辑 PPTX。模型是 `alibaba/qwen/qwen3.8-flash`。

三次用户消息依次是：提出美化；「按以上建议确认，进入 Stage 2 完整方案」；「B · 乐刻橙清晰栅格（推荐）」。

项目目录：

`backend/.deer-flow/users/2f450796-95f1-4a79-b922-6eed658b27d2/threads/4fc235e0-b0b9-49c3-b517-4ca1777d5768/user-data/workspace/projects/leike_dianzhang_beautify_ppt169_20261112/`

已存在：源 PPTX、逐页 Markdown、`analysis/*.identity.json`、`slide_library.json`、`beautify_inventory.json`、图片、`confirm_state.json`（`stage: final`，`status: confirmed`，方向为 B）。

不存在：`design_spec.md`、`spec_lock.md`、`svg_output/`、`exports/` 里的 PPTX。

第三次运行 `c685805c-944f-4760-8e36-e708f20d42f5`：68 次模型调用，输入约 652 万 token，状态记为 success。写文件只发生过两次，都是 `confirm_state.json`。上下文压缩出现在 `run_events` seq 1056 和 1227；第二次摘要写明方案 B 已确认，不要重做 Stage 1/2，下一步写设计规格。运行结束时模型调用 `ask_clarification`，重新询问沟通契约和正文字号。记录在 `backend/.deer-flow/data/deerflow.db` 的 `runs` 与 `run_events`。

同日小文件的 Edit Native 对话 `2b764024-4045-40b5-b783-8874b908f716` 导出了 PPTX。上传和 ppt-master 脚本能跑通。失败的是 46 页整套重排。

`validation/workflow.log` 里有一次 `pptx_to_svg.py` 把输出目录写成了位置参数，报 `unrecognized arguments: svg_output`。美化流程把这一步标成可选的视觉参考，不是交付前提。

## 2. 当前判断

两层原因叠在一起。

确认落盘之后，规格文件还不存在。`skills/public/ppt-master/SKILL.md` 覆盖段第 9 条（P20）只在 `confirm_state.json` 与 `design_spec.md` / `spec_lock.md` 互证一致时禁止重问。规格还没写出来，这条续跑条件不成立。模型随后又读了 `workflows/profiles/beautify-pptx.md`，该流程把确认写成硬停。它忽略压缩摘要，再次发起确认，这一轮结束。

即便没有重问，46 页也写不完。`backend/seed/agents/ppt-master/SOUL.md` 的 D3 要求同一个 agent 逐页手写 SVG，禁止脚本批量生成，禁止子代理。SKILL.md 覆盖段第 4 条禁用 `generation_mode: split` 与 `resume-execute`，并写「长 deck 走连续模式」。SOUL 末尾又写超过 10 页时建议分批，但注明 split 不可用。一次上下文装不下重读整份技能、46 页原文和 46 张 SVG。Flash 把预算耗在重读上，只是让这个缺口提前暴露。

同一条对话的项目目录当时还在。后两次运行的用户消息里带有该项目的 workspace 清单。`backend/packages/harness/deerflow/sandbox/tools.py` 把 `/mnt/user-data` 映射到 `threads/{thread_id}/user-data/`。这次失败不是中间文件丢了。

## 3. 建议的修法

只改续跑约定，让长课件在同一条对话里分段做完。

1. `confirm_state.json` 已是 `stage: final` 且 `status: confirmed` 时，禁止再为 Stage 1 或 Stage 2 调用 `ask_clarification`，即使 `design_spec.md` 还不存在。下一动作是写当前缺失的规格或下一页 SVG。
2. 超过 10 页时，规格和 SVG 按每轮大约 8 页往下写。一轮结束时报告「已完成 P01–P08 / 46」，用户回复「继续」后从已有文件接着写。不重跑解析，不重读整份技能。
3. 继续禁用上游的 `resume-execute`、`confirm_ui/server.py`、`generation_mode: split` 的跨会话语义，以及脚本批量生成 SVG。分段续写发生在同一条对话里，依据的是已经持久化的 `/mnt/user-data`。

8 页只是建议起点。核实 token、压缩和 recursion 之后可以改这个数字。换一个更大的模型可以减轻重读，但不能代替分段：46 页手写 SVG 仍然放不进一轮。

## 4. 核实清单

- 同一 `thread_id` 的下一次用户消息，沙箱是否重新挂上原来的 `user-data/workspace`。用这次对话的三次运行和 `aio` 沙箱挂载代码核对。区分「同一条对话的下一轮」和「新开一条对话」。
- P20 的互证条件是不是重问的直接原因，还是模型只是没有遵守压缩摘要。看 seq 1227 之后到 2135 的工具序列。
- 压缩摘要由哪段代码生成，能否在 `confirm_state.json` 为最终确认时固定带上「禁止重问，下一缺失文件是什么」。只改 SOUL 一句，压缩后可能仍然丢失。
- D3 第 4 条「禁止分批」与建议 2 是否冲突。分段续写仍是顺序逐页，不是并行分批。核实原文意图后再改措辞。
- `skills/public/ppt-master/scripts/attribution_guard.py` 对 SKILL.md 的约束：frontmatter 不动，覆盖段内不得重复 gate marker 字面量。改覆盖段后跑现有 ppt-master guard 测试。
- 偏离要登记 `skills/public/ppt-master/TRAINERAI_PATCHES.md`。该文件属于 vendored 技能树，补丁必须登记。

## 5. 不要做的事

- 不要把本次失败归到密钥注入、360 生图通道或 `QIHU_API_KEY`。这次运行没有走到生图和导出。
- 不要并进 `docs/plans/2026-11-16-agent-media-channel-and-sandbox-secrets-plan.md`。
- 不要恢复 `confirm_ui`，不要把 `resume-execute` 当成跨会话恢复。
- 不要用脚本按模板填 46 张 SVG 来绕过 D3，除非核实后证明同一条对话里的分段续写仍然无法交付。
- 不要把 `pptx_to_svg.py` 的参数错误当成这次没交付的原因。
- 不要读取或引用 `.env`、数据库里的密钥和用户正文以外的私人内容。上面的路径和计数足够复核。

## 6. 核实通过后的交付

另写一份实施计划，放在 `docs/plans/`，不要把本文改成计划。计划里写明改哪些段落、如何用测试钉住「最终确认之后不得再问 Stage 1/2」，以及用一个短课件验证分段续写不会重问、不会重跑解析。46 页真实美化的端到端交付单独列为人工验收，不放进默认 CI。
