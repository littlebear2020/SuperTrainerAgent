## 基线

- tested_sha: `<本 PR 最终 HEAD 的完整 SHA>`
- deployed_sha: `<实际部署 release-manifest.json 的 source_sha；未部署填 N/A>`
- spec_sha256: `<执行计划或设计文档的 SHA-256；无计划填 N/A>`
- 分支 / worktree: `<名称>`

## 变更与影响

<一句话说明变更、用户行为和部署影响；不要粘贴真实主机、账号或内部任务 ID。>

## 验证

- [ ] 已在最终 `tested_sha` 上运行相关实现级测试。
- [ ] 已验证用户可感知行为，或明确标注不适用及原因。
- [ ] live 报告分别记录 `tested_sha`、`deployed_sha`、`spec_sha256`，未用本地 HEAD 冒充部署版本。

```text
<命令 → 实际关键输出>
```

## 发布与例外

- 部署动作：`<无 / 镜像重建 / config 同步 / 其他>`
- [ ] 最终行为已验证；或已在 `docs/findings-backlog.md` 登记未勾选 `[release-blocker]`，本 PR 不宣称可正式发布。
- [ ] 已核对 `origin/main` 最新状态，其他 Agent 的在途文件未混入本 PR。
