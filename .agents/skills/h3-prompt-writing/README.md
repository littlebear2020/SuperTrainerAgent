# h3-prompt-writing（上游原样引入）

- 来源：https://github.com/MiniMax-AI/MiniMax-H3 （`skills/h3-prompt-writing/`，main 分支）
- 引入日期：2026-09-13
- 引入方式：SKILL.md / references/base-en.txt / references/ref-en.txt / agents/openai.yaml 逐字节原样拷贝，未做本地改动——便于后续 `diff` 上游跟进官方修订。
- 许可：上游仓库未附标准开源协议，模型权重与仓库整体适用 MiniMax H3 Community License（HuggingFace `MiniMaxAI/MiniMax-H3`）。仅作内部技术参考使用，不再分发。
- 本项目消费方：`.zcode/agents/h3-prompt-doctor.md`（离线 H3 提示词医生 agent）；
  线上 wire 提示词仍由 `backend/app/gateway/video_gen/prompt_translators/h3.py` + `vocab.py` 确定性产出（2026-08-16 计划拍板：翻译归代码，不进 LLM 热路径）。
- 项目约束与官方指南的已知分歧清单（诊断 checklist）见上述 agent 定义。
