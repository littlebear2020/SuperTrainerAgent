# Findings Backlog（范围外发现登记处）

> 任务中发现但不顺手改的问题登记在此，一行一条：`- [ ] YYYY-MM-DD 一句话 + 指针`。完成后勾掉并链接处理批次。历史内部记录未纳入公开快照。

- [ ] 2026-11-10 `vimax-video` 的 `skills: []` 被新会话 `SuggestionList` 回退为全站已启用技能列表，与后端“未绑定技能”语义可能不一致；指针：`frontend/src/components/workspace/input-box.tsx:1298`、`backend/seed/agents/vimax-video/config.yaml`。
- [ ] 2026-11-11 旧全量模型探针仍硬编码 Terra 上游 `openai/gpt-5.6-terra`，且缺新增 DeepSeek；本批只使用新 opt-in 探针，不顺手改旧脚本；指针：`backend/scripts/model_connectivity_check.py:46-66`、`backend/tests/test_model_roster_live.py`。
- [ ] 2026-11-12 Doubao 字幕版 TTS 只记录 status、未记录 model/char_count，导致该路径无法按字符估算；本批不顺手改，后续独立小修；指针：`backend/app/gateway/voice_twin/doubao_tts_client.py:252-260`、`:325-334`。
- [ ] 2026-11-12 ViMax 直记桥未透传 amount/usage，计价器也未优先 completion_tokens；普通 throttle 已有 amount/total_tokens 传递，后续只修真实缺口；指针：`backend/app/gateway/video_gen/engines/vimax/provider_bridge.py:95-105`、`:714-718`、`backend/app/gateway/_shared/media_pricing_resolver.py:188-192`。
- [ ] 2026-11-16 宿主已配置密钥但沙箱默认未注入，需研究 run/Agent/技能级授权与诊断闭环；指针：`docs/2026-11-16-sandbox-secret-injection-investigation.md`。
- [ ] 2026-11-20 `deploy_on_server.sh pack --no-sandbox` 会 `--exclude=docker/sandbox/cache`，连同新增的 `python-wheels/`、`tiktoken/` 一起排除，用这种包无法重建沙箱镜像（原有行为：只在服务器已有完整缓存时适用）；缓存目录现含 chrome/fonts/wheels/BPE 四类，排除粒度过粗，需决定是否按子目录排除（`deploy_to_test_server.sh` 已只排除 `cache/chrome-linux`）；指针：`deploy_on_server.sh:28-32`、`deploy_to_test_server.sh:60-63`、`docs/changelog/changelog-2026-11-20-sandbox-tiktoken-vendoring.md` §4。
- [ ] 2026-11-20 沙箱镜像基座无 tiktoken 以外的编码缓存，且 `TIKTOKEN_CACHE_DIR` 对任务用户只读：`get_encoding('cl100k_base')` 等非 o200k_base 编码会 `PermissionError`（不是联网下载）；当前技能树只用 o200k_base，无影响，将来出现其它编码需求需另行预烘焙；指针：`docker/sandbox/Dockerfile`（tiktoken 区块）、`docs/changelog/changelog-2026-11-20-sandbox-tiktoken-vendoring.md` §3。
