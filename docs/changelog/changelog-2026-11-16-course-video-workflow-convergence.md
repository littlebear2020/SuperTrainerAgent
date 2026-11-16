# 视频课程专家工作流收敛与文档输入优化

> **TL;DR**：course-video 白名单收缩为 `deep-research + auto-video-flow`，SOUL 增加固定文档读取、固定稿免调研和原生 Remotion 图表规则；本机 reseed、文档探针、后端全量与静态门禁通过。TTS 根因定位为宿主已配置但 AUTOBIND 未开启，进程级临时授权后，固定稿和原生动态图表两个真实 MP4 均已完成并独立复核。

## 变更摘要

- `course-video` 移除 `corporate-video`、`tech-explainer-video`、`chart-visualization`、`trainerai-imagine` 绑定；四项工具组不变，不补 `video-generation`。
- SOUL 在写稿前增加固定读取步骤：DOCX 同时遍历段落与表格，PDF 使用 `pdftotext -layout`，XLSX 使用只读 `openpyxl` 且保留公式文本；文本落 workspace 后分段读取，失败停止，禁止运行期安装。
- 固定稿不改写、不扩写、不主动联网；生成稿只在确需公开资料时使用 deep-research。
- 数据画面要求原生 Remotion 数据元素动画，不接受静态 PNG 淡入冒充动态图表；沙箱渲染并发统一为 `--concurrency=1`。
- `SEED_VERSION` 升至 `2026-11-16`。未修改其他 Agent、前端、案例库、主技能、模板、Docker 配置或课程创建 API，未新增依赖或生产脚本。

## 验收记录

- RED：`cd backend && uv run pytest tests/test_seed_agents.py -q` → `3 failed, 20 passed`，分别命中旧六项白名单、旧种子版本和缺失 SOUL 规则。
- GREEN：`cd backend && uv run pytest tests/test_seed_agents.py tests/test_skill_whitelist_resolution.py -q` → `23 passed, 1 warning`。
- 本机重启 reseed → 运行副本 `.seed_version=2026-11-16`，skills 精确为两项，SOUL 新锚点存在；旧目录形成新备份，原本无 course-video memory。评审修复后的最终 SOUL 已同步到本机运行副本。
- 实际本地镜像 `deerflow-sandbox:local-cee995a7` 的文档小样例 → `DOCX paragraph/table order PASS`、`PDF readable text PASS`、`PDF blank text detected PASS`、`XLSX two sheets and unrecalculated formula PASS`；无安装命令、无全文 stdout。
- TTS 根因复核 → 配置加载后两个 TTS 变量均存在，但 AUTOBIND 关闭且默认注入为 0；进程级临时开启后目标变量进入注入集合，真实豆包男声与字级时间轴生成成功。未读取、复制或输出任何密钥值。
- 固定 DOCX 真实 Agent → 实际执行 `iter_inner_content` 并生成分镜；纠正表格文字新增标点后，TTS/字幕与抽取文本逐字一致。最终 `npx remotion render` 一次成功，`Encoded 300/300`，`present_files` 已调用；独立 ffprobe 为 H.264/AAC、1920×1080、30fps、300 帧、10.026667s、2,637,948 bytes，未调用 web 或四个已移除技能。
- 原生动态图表真实 Agent → 仅使用甲=0/乙=5/丙=10，柱体为 SVG rect + spring 生长和数值 count-up，无 chart-visualization、静态图片资源或安装命令。最终 `Encoded 330/330`，`present_files` 已调用；独立 ffprobe 为 H.264/AAC、1920×1080、30fps、330 帧、11.029333s、1,090,138 bytes，早晚帧确认数据元素确实变化。
- 本地环境修复：`docker build -t deerflow-sandbox:local-cee995a7 -f docker/sandbox/Dockerfile .` → 构建成功；新镜像 ID `sha256:63ea0f202c483a4e24b1ea848ea3416286460190bcc98dc08d5b5b7f4d2028c7`，模板版本由旧值修复为 `18cc3712bf06ffc6041cfa94f4704722`，文档依赖复核通过；本机服务已重启。
- 独立审查修复 → 抽取产物改为工程外 `<name>-input/<原始文件名>.txt`，PDF 先建父目录，XLSX 以真实单元格内容判空；新增断言先 RED 后 GREEN。
- 修复后真实探针 → `DOCX -> template copy: project root PASS`、新鲜 PDF 写入成功、输入文件分别落盘、空白 XLSX 非零且无输出；既有 DOCX/PDF/XLSX 探针继续通过。
- 最终后端全量：`cd backend && uv run pytest tests/ -q` → `10038 passed, 31 skipped, 421 warnings in 584.65s`。
- 静态门禁：根 `make check` → 退出 0；ESLint `0 errors, 121 warnings`，Prettier/knip/i18n/文档治理均通过。
- `git diff --check` → 无输出；`python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。

## 后续事项与限制

- 密钥注入的产品接线问题已单独立项交接：`docs/2026-11-16-sandbox-secret-injection-investigation.md`，并在 findings backlog 登记。AUTOBIND 仅用于本地验收进程，未写入 `.env` 或持久化配置。
- 两次 Agent 驱动曾触及递归上限，最终均在已有工程上完成真实渲染与交付；成片有效性以独立 ffprobe、实际媒体读取和工具调用证据为准，没有把驱动退出状态误当成产物失败或成功。
- 本次仅本机修改与验收，不部署、不建 PR、不主动 commit。用户既有前端改动和根目录媒体文件未修改；findings backlog 仅追加上述密钥注入研究项。
