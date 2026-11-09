# Video-Gen 半成品修复收口补丁 · Live 验证 Runbook

> **状态：PENDING** — 本开发环境无 `ARK_API_KEY`，以下 live 项无法端到端实跑。
> 代码 + 单测均已实现并绿，仅 live E2E 待用户在部署前（有 `ARK_API_KEY` 的环境）跑通。
> 关联计划：`docs/superpowers/plans/2026-07-13-video-gen-halfbaked-fix-followup-plan.md`（14 task 收口补丁）。

## 前置

- 环境变量：`ARK_API_KEY`（ByteDance Ark 直连）、`ARK_BASE_URL`（默认 `https://ark.cn-beijing.volces.com/api/v3`）。
- seedream 模型：`doubao-seedream-5-0-pro-260628`；seedance-ark：`doubao-seedance-2-0-260128`。
- 不回退 ByteDance：seedream/seedance-ark 默认首选，360 降级。

---

## 1. Task 1 / Task 14 — character_sheet 绝对路径 i2i E2E（A1）

**验证目标**：方式 A character_sheet 现存**绝对**路径 → `_to_data_uri` 的 `os.path.exists` 返 True → 转 data-URI → Ark `image` 字段有效 → side/back i2i 不 4xx；跨视频复用 24h+ 不 4xx（修前相对路径致 CWD 解析失败 → 4xx 不降级 → 整角色失败）。

**步骤**：
1. `POST /api/video/projects/{project_id}/character-sheets/generate`（body: `{"description":"...", "name":"...", "features":"..."}`）→ 返回 CharacterSheet。
2. 确认 `front_url` 是绝对路径（`os.path.isabs` True）且文件存在。
3. 跑 `python -c "from app.gateway.video_gen.providers.seedream import _to_data_uri; print(_to_data_uri('<front_url>')[:40])"` → 须输出 `data:image/png;base64,...`（非原样相对串）。
4. 生成一个绑该项目的视频（ai_image）→ 确认 side/back i2i 参考图解析成功（日志无 Ark 4xx、无 fallback 退化）。
5. 等 24h+ 后再生成一个绑同项目的视频 → 确认 `front_url` 仍可解析（绝对路径不随 TOS URL 过期失效）。

**通过判据**：步骤 4/5 无 Ark 4xx；`_to_data_uri` 输出 data-URI。

---

## 2. Task 6 — seedream i2i（image:[data_uri_list] 多参考图编辑）

**验证目标**：seedream `generate_image` 消费 `reference_image_paths` → 经 `_to_data_uri` 转 data-URI → 加入 body `image` 字段 → Ark 接受并返图（§4.2 实测可用的 Ark 原生多参考图编辑形式）。

**步骤**：
```bash
# 准备一张参考图的 data-URI（用 Task1 产出的 front.png）
B64=$(base64 -w0 /path/to/front.png)
curl -X POST "$ARK_BASE/images/generations" \
  -H "Authorization: Bearer $ARK_API_KEY" -H "Content-Type: application/json" \
  -d "{\"model\":\"doubao-seedream-5-0-pro-260628\",\"prompt\":\"a girl, side view\",\"size\":\"1152x2048\",\"image\":[\"data:image/png;base64,$B64\"]}"
```
**通过判据**：HTTP 200 + `data[0].url` 或 `data[0].b64_json` 非空（返图）。
**失败处置**：若 4xx 且 stderr 提示 image 字段格式错 → 复核 `_to_data_uri`（须 data-URI，非相对/绝对路径原样塞入）；若内容审查 4xx → 正常（不降级，设计如此）。

---

## 3. Task 13 Step 0 — seedream size 集合（阻塞合入生产）

**验证目标**：`_aspect_to_size` 映射的 size（如 `2048x1152`、`1152x1152`）须被 Ark 接受；ViMax aspect_ratio honors it（非硬编码 9:16）。**计划标注：此项阻塞合入生产。**

**步骤**：
```bash
# 16:9 横屏
curl -X POST "$ARK_BASE/images/generations" -H "Authorization: Bearer $ARK_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"doubao-seedream-5-0-pro-260628","prompt":"a landscape","size":"2048x1152"}'
# 1:1 方形
curl -X POST "$ARK_BASE/images/generations" -H "Authorization: Bearer $ARK_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"doubao-seedream-5-0-pro-260628","prompt":"a square","size":"1152x1152"}'
```
**通过判据**：两者均 200 + 返图。
**失败处置**：若 4xx `size not supported` → 查 ByteDance Ark 文档确认 seedream 接受的 size 集合 → 修 `provider_bridge._aspect_to_size`（STRING 版）+ 同步 `service.py`（tuple 版）+ 更新 `tests/test_vimax_seedream_aspect.py` 的期望 size。

---

## 4. Task 14 — TOS→local→i2i 全链路 E2E

**验证目标**：方式 A 真生角色图 → `front_url` 绝对、`_to_data_uri` 转 data-URI、跨视频复用 24h+ 不 4xx（综合 Task 1 绝对路径 + Task 6 i2i + Task 14 本地化）。

**步骤**：见第 1 项（同一链路：方式 A 生成 → 绝对路径 → data-URI → 跨视频 i2i 复用）。重点观察 24h+ 后的复用不因 TOS URL 过期失败（本地化前会失败；本地化后绝对路径不失效）。

**通过判据**：24h+ 后再生成绑同项目的视频，front/side/back 参考图 i2i 全部成功（无 4xx、无退化）。

---

## C 局限 UI 诚实化（可选 · N/A）

> 计划列「gpt-image-2/gemini-flash-edit/kling/seedance-360 在前端 provider 选择器标注实验性/未验证/未接入」。
> **核实结论：N/A** — video-gen 前端**无 provider 选择器 UI**（`ai_image_provider`/`gpt-image-2`/`kling` 等是 config.yaml 后端配置，非用户可选）。故无 UI 需诚实化。
> 各 provider 的已知局限已在 `backend/app/gateway/video_gen/providers/registry.py` 注释自承（gpt-image-2 i2i 身份漂移、gemini-flash-edit 未验证、kling Phase-3 stub、seedance-360 仅 mock 测过 wire 格式）—— 代码层已标注，无需 UI 层重复。

---

## 部署提示（关联收口补丁）

- **worker 改动（Task 4/5/6）** → 随下次离线包重建 worker 镜像。
- **gateway/前端改动（Task 1/2/3/7/8 + Phase 4 Task 10-14）** → 重启 gateway + 前端重构建。
- **最高 ROI 先上救急**：Task 11 nginx `proxy_read_timeout 300s`（preview 504）+ Task 14 E5a 每帧进度上报（75% stall）。
- **本 runbook 的 live 项须在部署前跑通**（尤其第 3 项 size 集合，阻塞合入生产）。
