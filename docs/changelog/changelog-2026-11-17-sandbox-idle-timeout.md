# 沙箱空闲回收超时调整为 2 小时

> **TL;DR**：`sandbox.idle_timeout` 显式设为 7200 秒（默认 600），避免长视频渲染在沙箱工具调用空窗期被 10 分钟空闲回收误杀；关键决策 = 只调配置值、保留回收机制、不改代码；影响面 = `config.example.yaml` 示例与部署侧 `config.yaml`，需重启 gateway 生效。

## 变更摘要

- `config.example.yaml` 的 AIO sandbox 示例块新增 `idle_timeout: 7200`，注明默认 600 秒、仅在 gateway 启动时加载。
- 本机 `config.yaml`（已被 `.gitignore` 排除）同步设为 `7200`，不随 PR 提交。
- 未改代码、schema 与 `config_version`；未动前端、镜像、技能与回收逻辑。

## 验收记录

- YAML 校验 → `YAML OK; local sandbox.idle_timeout=7200`
- `cd backend && CI=true PYTHONPATH=packages/harness:. pytest tests/test_aio_sandbox_provider.py tests/test_local_sandbox_encoding.py -q` → `26 passed, 1 skipped`
- `cd backend && CI=true PYTHONPATH=packages/harness:. pytest tests/ -q -n auto --dist loadfile` → `10035 passed, 89 skipped`
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`

## 部署注意

- 生产部署目录的 `config.yaml` 需同步 `idle_timeout: 7200`，否则线上仍按默认 600 秒回收。
- 该值仅在 provider 初始化时读取，普通配置热重载无效，必须重启 gateway。
