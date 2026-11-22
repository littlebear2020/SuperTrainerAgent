# 服务器实测问题修复与 TTS 单通道收敛

> **TL;DR**：本批按四个主 PR 串行修复实测问题；当前提交覆盖停止生成保留草稿与 Task 0 取证，后续 PR 继续处理播客 URL、TTS 单通道和前端/live 断言；不自动部署、不修改真实配置、不执行未授权付费调用。

## 当前已落地

- P1：InputBox 在表单捕获阶段拦截 streaming 停止，保留文本、附件与 localStorage 草稿；正常发送仍清空。
- Task 0：确认实现代码相对计划基准无漂移；P6/P7 证据材料位于 `temp/live-test-remediation-20261122/`，均不满足条件 PR 门禁。
- PR 3 已按最新决策固定为四笔本地提交、全部验证后一次性 push、同一 PR 合入与回滚。

## 后续固定范围

- PR 2：播客 URL 抓取 socket 超时，保留完整 SSRF 安全链。
- PR 3：只保留 4 个中文豆包音色与既有克隆，移除 Edge/360 TTS。
- PR 4：动态默认徽标、用量标题占位、首渲文案与 live 断言。
- Writer、P4、P8、P12、P15 不加回；P6/P7 未取证不开 PR。

## 验收记录

- `git diff 0e84b148..origin/main --stat` → 14 个文档文件，无实现代码漂移。
- 基线 `cd frontend && pnpm test` → 279 files / 1806 tests passed。
- 基线 `cd backend && uv run pytest tests/ -q` → 10273 passed, 95 skipped。
- P1 RED：`pnpm exec vitest run --project dom tests/unit/components/workspace/input-box-stop-draft.dom.test.tsx` → 两条 streaming 用例因停止后文本变空失败，正常发送对照通过。
- P1 GREEN：同一命令 → 3 passed；移除旧 streaming 早退后相关 InputBox 回归 → 8 files / 33 tests passed。
- `cd frontend && pnpm check && pnpm test` → check 通过；280 files / 1809 tests passed。
- `python3 scripts/check_docs_convention.py` → OK 仓库治理约定全过。

## 阻塞与未执行

- P6：目标 run 终止记录与 checkpoint 未保留，无法确认免费恢复；外部阻塞、未重跑、未开 PR。
- P7：只有单次异常和后续未复现材料，缺少稳定数值序列；证据不足、未开 PR。
- 本地 OCR：`ocr llm test` 因 `auth_token_cmd` 空输出未运行；创建的 PR 仍须通过 GitHub AI Code Review，未通过不合入。
- 真实字节配音：`LIVE_NOT_VERIFIED`；未获付费授权，不执行。
- 部署后 L1/L2：未部署、未执行，不作为主 PR 合并门禁。
