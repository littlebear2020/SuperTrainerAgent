# video_gen 360 通道切换——部署验收套件（2026-08-24）

计划：`docs/plans/2026-08-21-video-gen-360-switch-impl-plan.md` §五（AC1~AC10）。
对象：**仅 360 通道模型**；Ark 通道不在验收范围（仅 AC4 降级演练中作为被降落对象验证可达）。

## 前置

- 代码已部署到**测试服**（gateway 重建 + frontend 重建同窗口）；
- 测试服 config.yaml 已按 changelog「部署事项」节落位（`ai_video_provider: seedance`、
  `script_model`、`models[]` 追加等）；
- `ffmpeg`/`ffprobe` 在运行脚本的机器上可用；
- 环境：`VG_ACCEPT_BASE`（网关基址）、可选 `VG_ACCEPT_TOKEN`、`VG_ACCEPT_USER`、
  `VG_ACCEPT_ADMIN_EMAIL`/`VG_ACCEPT_ADMIN_PASSWORD`（鉴权部署下脚本自动走
  initialize→login→CSRF 引导；已初始化环境须传入既有管理员账密，默认账密只对
  首次 initialize 生效）。

## 执行序（严格按此顺序）

```bash
cd backend

# 0) AC8 第一步——部署「前」在旧版本上就要做（见下节）
# 1) AC9：单测零回归（也可在部署前本地跑）
uv run python scripts/acceptance/video_gen_360/run_acceptance.py --ac 9

# 2) AC1/AC2：分镜失败率 + 总耗时（20 次真实分镜，~10-30 分钟）
uv run python scripts/acceptance/video_gen_360/run_acceptance.py --ac 1,2

# 3) AC5：逐帧提示词质量（2 题材 × 8 帧）
uv run python scripts/acceptance/video_gen_360/run_acceptance.py --ac 5

# 4) AC6/AC7：预览同模型 + 参考图不丢（seedream-360 任务）
uv run python scripts/acceptance/video_gen_360/run_acceptance.py --ac 6,7

# 5) AC3：三档比例 ffprobe 100% 正确（真实出片，慢）
uv run python scripts/acceptance/video_gen_360/run_acceptance.py --ac 3

# 6) AC10：>15s 旁白单帧续写（真实出片，慢）
uv run python scripts/acceptance/video_gen_360/run_acceptance.py --ac 10

# 7) AC4：降级演练（需服务器侧注入坏 key，见下）
uv run python scripts/acceptance/video_gen_360/run_acceptance.py --ac 4
```

全部 PASS 才允许上生产；任一 FAIL 回到对应 E 任务修复（对照 changelog 的任务映射）。

## AC8 跨模块隔离（部署前后各拍一次快照）

```bash
# 部署前（旧版本）：
uv run python scripts/acceptance/video_gen_360/run_acceptance.py --snapshot pre.json
# 部署后（新版本）：
uv run python scripts/acceptance/video_gen_360/run_acceptance.py --snapshot post.json
uv run python scripts/acceptance/video_gen_360/run_acceptance.py --diff pre.json post.json
```

判据：models 名单与顺序零差异（允许**尾部追加**）、默认模型（models[0]）不变、
video providers 允许追加 seedance-fast/seedance-mini（E2 型号选项）、不允许摘除任何项。

## AC4 降级演练 runbook

0. ⚠️ **前置依赖已失效（2026-10-12 花名册批）**：原设计用任务级 `script_model`
   覆盖走 Ark 直连 LLM `doubao-seed-2.1-pro-ark`（坏 qihu key 下分镜 LLM 仍可用，
   只让「生视频」通道处于故障注入态）——豆包聊天删册后**非 360 通道的注册聊天
   模型已不存在**（Ark 仅剩生图/生视频通道），覆盖值改在册名 `qwen3.8-max` 后
   坏 qihu key 下分镜 LLM 与生视频通道**同步故障**，AC4 语义退化为「通道级全
   故障」用例（任务预计在分镜阶段即失败，「全部成功」断言口径随之失效）。若
   需恢复原隔离语义，须另议注册非 360 通道聊天模型（超出花名册批范围）；
1. 测试服 `config.yaml` 把 `video_gen.qihu_api_key` 改成无效值（`ark_api_key` 保留）→
   `sudo docker compose up -d --force-recreate gateway`（或对应重启方式）；
2. 跑 `--ac 4`：并发 8 个短 ai_video 任务，**退化口径断言**（2026-10-12 起通道级
   全故障）：全部任务在分镜阶段快速失败且 error 可见（= 分镜 LLM 与生视频通道
   同步故障，验证 fail-fast 而非静默挂死）。原「100% 落 Ark」隔离断言已失效——
   seedance→Ark 兜底行为的覆盖由单测 `test_seedance_360_fallback.py` 承担；
   - Ark 提交突发由 `seedance_ark._inflight_gate` 平滑——并发提交 ≤3；闸不覆盖
     分钟级轮询窗口，Ark 端在途任务数由既有 429→重试机制兜底（单测
     `tests/test_seedance_360_fallback.py` 锁死；服务端可用
     `docker logs <gateway> 2>&1 | grep -c 'seedance-ark task submitted'` 观察节奏）；
3. 恢复有效 key → 重启 → 再跑一个小任务并确认日志出现 360 主力提交记录
   （= 恢复后切回）：
   `docker logs <gateway> 2>&1 | grep 'Seedance task submitted' | tail`
   （注意：`sink_provider` 是落库参数不打日志，别 grep 它）。

## AC6 产物来源核验（服务端侧）

脚本断言预览生成成功与帧 visual_url 就位；「确为 seedream-360 所出」在服务端核验：

```bash
docker logs <gateway> 2>&1 | grep "providers.qihu_image" | tail
# （logger 名即模块路径，seedream-360 走 QihuImageProvider；代码中不存在
# "provider_usage" 日志串，别 grep 它）
# 或查 /api/console/usage 的 provider 维度（record_external_api_call 落库，
# provider 列为真实来源，如 seedream-360）
```

## 已知边界

- AC1 的失败率统计以「storyboard 就绪且全帧有 image_prompt」为成功口径（与线上
  28% 失败率的同口径：分镜阶段完成）；
- AC10 的静止段检测用 ffmpeg `freezedetect`（阈值 n=0.003, d=2s），两判据口径
  不同：判据②（>15s 续写）查**尾部** ≥2s 冻结（续写段之间的极短过渡不计）；
  判据①（≤15s 超发裁剪，2026-08-25 起覆盖）要求**全片**无 ≥2s 冻结段，且
  产物时长 ≈ storyboard 帧 duration（TTS 对账值）±1s；
- 长旁白单帧构造用 `n_scenes=1` + 内置长 script（自然 TTS ≈32s > 15s 单段上限，
  触发帧内续写；产物时长以 ffprobe 实测为准）。`seconds_per_frame` 越界会被 422
  拒绝，脚本不再使用该参数。
