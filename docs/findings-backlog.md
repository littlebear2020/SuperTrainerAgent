# Findings Backlog（范围外发现登记处）

> 任务中发现但不顺手改的问题登记在此，一行一条：`- [ ] YYYY-MM-DD 一句话 + 指针`。完成后勾掉并链接处理批次。历史内部记录未纳入公开快照。

- [ ] 2026-11-10 `vimax-video` 的 `skills: []` 被新会话 `SuggestionList` 回退为全站已启用技能列表，与后端“未绑定技能”语义可能不一致；指针：`frontend/src/components/workspace/input-box.tsx:1298`、`backend/seed/agents/vimax-video/config.yaml`。
- [ ] 2026-11-11 旧全量模型探针仍硬编码 Terra 上游 `openai/gpt-5.6-terra`，且缺新增 DeepSeek；本批只使用新 opt-in 探针，不顺手改旧脚本；指针：`backend/scripts/model_connectivity_check.py:46-66`、`backend/tests/test_model_roster_live.py`。
- [ ] 2026-11-12 Doubao 字幕版 TTS 只记录 status、未记录 model/char_count，导致该路径无法按字符估算；本批不顺手改，后续独立小修；指针：`backend/app/gateway/voice_twin/doubao_tts_client.py:252-260`、`:325-334`。
- [ ] 2026-11-12 ViMax 直记桥未透传 amount/usage，计价器也未优先 completion_tokens；普通 throttle 已有 amount/total_tokens 传递，后续只修真实缺口；指针：`backend/app/gateway/video_gen/engines/vimax/provider_bridge.py:95-105`、`:714-718`、`backend/app/gateway/_shared/media_pricing_resolver.py:188-192`。
- [ ] 2026-11-16 宿主已配置密钥但沙箱默认未注入，需研究 run/Agent/技能级授权与诊断闭环；指针：`docs/2026-11-16-sandbox-secret-injection-investigation.md`。
- [ ] 2026-11-17 核对 qwen/Doubao `usage_metadata`、流式累计与近似 token 的统计口径，本批不修 provider/token 统计；指针：`backend/packages/harness/deerflow/agents/middlewares/token_attention_middleware.py:107-143`、`backend/packages/harness/deerflow/runtime/journal.py:379-388`。
- [ ] 2026-11-19 执行包写盘改用 `NamedTemporaryFile` 后文件权限由 0644 变为 0600（同 umask 022 受控对比），未在跨 uid 的 Docker/AIO 挂载环境验证；默认 LocalSandboxProvider 同 uid 判断低风险，需要时按 umask 恢复 0o644 并补测试；指针：`skills/public/ppt-master/scripts/project_management/execution_context.py:394-416`、`docs/changelog/changelog-2026-11-19-ppt-beautify-executor-context-pack.md` §6.5。
- [x] 2026-11-19 `Native-ready` 多键 map（`a=no; b=yes`）只按首个条目判定，该页不加载 native_table 规则，与文档语法 `<key>=yes|no; ...` 不符；OCR 同日也指出此点，复核后认定落在本批自己重写的解析函数内，已在 PR #29 的 `9d9cef4a` 先 RED 后修复（页内任一 `=yes` 即加载，全部 `no` 才为 False）；指针：`skills/public/ppt-master/scripts/project_management/execution_context.py:216`、`skills/public/ppt-master/references/plan-core.md:142`、`docs/changelog/changelog-2026-11-19-ppt-beautify-executor-context-pack.md` §6.2。
- [ ] 2026-11-20 `deploy_on_server.sh pack --no-sandbox` 会 `--exclude=docker/sandbox/cache`，连同新增的 `python-wheels/`、`tiktoken/` 一起排除，用这种包无法重建沙箱镜像（原有行为：只在服务器已有完整缓存时适用）；缓存目录现含 chrome/fonts/wheels/BPE 四类，排除粒度过粗，需决定是否按子目录排除（`deploy_to_test_server.sh` 已只排除 `cache/chrome-linux`）；指针：`deploy_on_server.sh:28-32`、`deploy_to_test_server.sh:60-63`、`docs/changelog/changelog-2026-11-20-sandbox-tiktoken-vendoring.md` §4。
- [ ] 2026-11-20 沙箱镜像基座无 tiktoken 以外的编码缓存，且 `TIKTOKEN_CACHE_DIR` 对任务用户只读：`get_encoding('cl100k_base')` 等非 o200k_base 编码会 `PermissionError`（不是联网下载）；当前技能树只用 o200k_base，无影响，将来出现其它编码需求需另行预烘焙；指针：`docker/sandbox/Dockerfile`（tiktoken 区块）、`docs/changelog/changelog-2026-11-20-sandbox-tiktoken-vendoring.md` §3。
- [ ] 2026-11-20 ppt-master `prompt_audit` 预算失守且无人执行：manifest 声明 `failure-recovery.md` 预算 2750，o200k 实测 origin/main=2799（基线已超）→ #27 HEAD=2908 → first-read 修复批=2912，同一审计另报 `SKILL.md` 4073>2500、`topic-research.md` 2100>2000（存量）；审计手册自述不接 CI/pre-commit，全仓无任何测试/CI 执行它，且本仓布局下直接运行报 `AUDIT_SETUP_ERROR`（需 `--root` 指向上游布局副本），预算声明形同虚设；需决定接入门禁并重设预算，或废弃 manifest 的预算声明；指针：`skills/public/ppt-master/scripts/prompt_audit_manifest.json`（`file_budgets`）、`skills/public/ppt-master/scripts/docs/prompt_audit.md`、`docs/changelog/changelog-2026-11-20-ppt-master-first-read-order.md` §3。
- [ ] 2026-11-21 main 基线三条 video-gen 详情页 Playwright 用例在 `page.waitForLoadState("networkidle")` 30s 超时，works-assets 全量/隔离复跑与 main 树对照均复现，本批不顺手修；指针：`frontend/tests/e2e/video-gen.spec.ts:439`、`:452`、`:465`。
- [ ] 2026-11-21 summarization 的 trim 只保留旧消息尾部，被移出主窗口且未获救的最早历史不会进入摘要输入；已离线复现，质量影响待基准；指针：`backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py:528-556`、`docs/plans/2026-11-21-mode-specific-summarization-plan.md` §2.7。
- [ ] 2026-11-21 模式菜单项点击后业务状态更新但外层 Radix 菜单不关闭，根因是菜单项外误嵌套第二个 `PromptInputActionMenu` root；基线与 #37 均存在，非本批回归，后续删除内层 root 并补关闭行为断言；指针：`frontend/src/components/workspace/input-box.tsx:515-614`、`frontend/src/components/ai-elements/prompt-input.tsx:1039-1075`。
- [ ] 2026-11-21 [OCR #37] 模型清单未加载或持久化模型未命中时 `thinking_enabled=false`，需复核短暂空清单下的标准/Ultra 思考行为，不能只按 UI 默认档推断；指针：`frontend/src/core/threads/hooks.ts:500-535`、PR #37 review comment `4651993985`/`4652058013`。
- [ ] 2026-11-21 [OCR #37] `reasoning_effort` 按 flash/非 flash 内联映射，OCR 质疑未参考模型能力位与 `mode_defaults`；需对照已定稿模式语义复核，不在本批照改；指针：`frontend/src/core/threads/hooks.ts:526-530`、PR #37 review comment `4652058016`。
- [ ] 2026-11-21 [OCR #37] 旧 `reasoning_effort` 仅读取时剔除、未立即回写 localStorage，运行时异常字段仍可能再次持久化，后续评估一次性迁移写回；指针：`frontend/src/core/settings/local.ts:95-119`、PR #37 OCR summary `7061233375`。
- [ ] 2026-11-21 [OCR #37] `thinking/pro` 合并为标准后 `is_plan_mode` 对标准统一开启属于行为扩大，计划虽已定稿为标准开规划，仍需确认用户可见说明与长期口径一致；指针：`frontend/src/core/threads/hooks.ts:526-529`、`docs/plans/2026-11-18-model-mode-ladder-plan.md` §1.2、PR #37 review comment `4652088712`。
- [ ] 2026-11-21 [OCR #37] 后端未配置 `modes` 时可能返回 `[]`，前端旧后端回退只识别 `undefined`，需用混合花名册实测并明确空数组契约，不能直接把所有缺省值改判为全档可用；指针：`frontend/src/core/models/modes.ts:15-20`、`backend/packages/harness/deerflow/config/model_config.py`、PR #37 review comment `4652088707`。
- [ ] 2026-11-21 [OCR #37] `defaultModelForMode` 的全列表首项三级兜底可能跨档且与选择器高亮不一致；定稿明确要求该兜底，后续若改空态需先修订产品契约而非直接删除；指针：`frontend/src/core/models/modes.ts:22-33`、PR #37 review comment `4652058011`。
- [ ] 2026-11-21 [OCR #37] `reasoningEffortForMode` 已无生产调用且与提交 hooks 内联映射重复，后续删除或统一单一出口；指针：`frontend/src/components/workspace/input-mode.ts:11-13`、`frontend/src/core/threads/hooks.ts`、PR #37 OCR summary `7061233375`。
- [ ] 2026-11-21 [OCR #37] `input-mode.ts` 已退化为 `InputMode` 薄 re-export 与 `resolveMode` 小函数，后续评估并入 `core/models/modes.ts`，避免双出口漂移；指针：`frontend/src/components/workspace/input-mode.ts:1-9`、PR #37 OCR summary `7061233375`。
- [ ] 2026-11-22 P2 Writer 版本回滚后继续编辑出现伪“保存冲突”，不属于本批六卡核心范围；指针：`docs/plans/2026-11-22-live-test-remediation-plan.md`。
- [ ] 2026-11-22 P4 档内模型价格后续由管理端手工维护，本批不做自动计价改造；指针：`docs/plans/2026-11-22-live-test-remediation-plan.md`。
- [ ] 2026-11-22 P12 账户设置与登录失败文案的中英文混杂整项退出本批；指针：`docs/plans/2026-11-22-live-test-remediation-plan.md`。
- [ ] 2026-11-22 Writer 分享页英文文案不属于本批六卡核心范围，未修改；指针：`docs/plans/2026-11-22-live-test-remediation-plan.md`。
- [ ] 2026-11-22 P14 Writer 模块入口保留或下线仍属产品决策，本批不增加入口或删除模块；指针：`docs/plans/2026-11-22-live-test-remediation-plan.md`。
- [ ] 2026-11-22 P15 普通用户“我的渠道/凭据”入口按既定决策不开放，本批不实现；指针：`docs/plans/2026-11-22-live-test-remediation-plan.md`。
- [ ] 2026-11-23 `display_name=null` 仍为未处理候选，本批未改用户/模型命名逻辑；指针：`docs/plans/2026-11-23-six-core-live-defects-remediation-plan.md`「只观察、不在本批修实现」。
- [ ] 2026-11-23 用量标题国际化仍为未处理候选，本批未改前端或 i18n；指针：`docs/plans/2026-11-23-six-core-live-defects-remediation-plan.md`「只观察、不在本批修实现」。
- [ ] 2026-11-23 ppt-master audio-only 规则仍为未处理候选，本批未改音频工作流；指针：`docs/plans/2026-11-23-six-core-live-defects-remediation-plan.md`「只观察、不在本批修实现」。
- [ ] 2026-11-23 `clone:` 空后缀仍为未处理候选，本批未改音色解析或克隆入口；指针：`docs/plans/2026-11-23-six-core-live-defects-remediation-plan.md`「只观察、不在本批修实现」。
- [ ] 2026-11-23 PPT 导出后文字行预筛仅接入 Default/Beautify，`quick-generate.md` 与 `edit-native-pptx.md` 独立导出路径未覆盖，不得宣称全路径已上门；指针：`docs/plans/2026-11-23-six-core-live-defects-remediation-plan.md` Task 3。
- [ ] 2026-11-23 `TRAINERAI_PATCHES.md` 补丁表仍有空行拆表、P32 在 P31 前、缺 P30 行的格式问题，本批只追加 P34、未整理；指针：`docs/plans/2026-11-23-six-core-live-defects-remediation-plan.md`「明确不做」。
