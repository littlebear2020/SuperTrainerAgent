# 360 智脑视频生成 API 官方文档本地副本（2026-08-18 抓取）

来源：https://ai.360.com/docs/（抓取需带浏览器 UA，裸 curl 被 openresty 403）。

## 关键事实（video-gen provider 开发的权威依据）

1. **MiniMax-H3 图生视频官方支持**（create-video-generation.md 示例 9/10/11）：
   - 图生视频（首帧）= content 单张 `image_url`，`extra_body` **无 role**（resolution 2K / duration 5 / ratio adaptive）；
   - 首尾帧 = 双图按序（「第一张传入的图像会被作为首帧，第二张传入图像作为尾帧」），无 role；
   - 参考生视频 = `extra_body.role: reference`。
2. **传图口径**：「`image_url.url` 可以是图片 URL（**确保可访问**）或图片 **Base64 编码**」——本地路径必须 data-URI 化（`seedream._to_data_uri` 先例）。
3. **role 是 extra_body 全局生成类型标识**（seedance 专文）：「会将所有的图像、视频和音频对象设置为参考类型」——**无 per-image role 字段**（与 Ark 逐图 first_frame/last_frame/reference_image 完全不同）。seedance 首帧 i2v = 单图不设 role（示例 2）。
4. **ratio 官方示例均用 `adaptive`**；H3 另支持 16:9/4:3/1:1/3:4/9:16/21:9（代码实测注释 qihu_video.py:54-59）。`ai_video.py` 现传任务画布比例，与 adaptive 的冲突行为需实网探针。
5. **查询响应仅 `url`**（24h 签名链，`X-Amz-Expires=86400`），无 last_frame_url/首尾帧字段——末帧/尾帧须本地抽帧（现状一致）。
6. H3 强制扁平 `extra_body.resolution`（仅 768P/2K，1080P 会 400）+ `extra_body.ratio`，缺任一 400（代码实测注释 qihu_video.py:346-364）。

关联：修复计划 `docs/two-feedback-diagnosis-fix-plan-2026-08-18.md` T3；字节系对照 `backend/docs/BYTEDANCE_ARK_API_REFERENCE.md`。
