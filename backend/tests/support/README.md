# tests/support/

共享测试基础设施目录——只放跨文件复用的测试 helper / 探测器，**不放
`test_*.py` 测试文件**（pytest 会按 `test_*.py` 收集，放这里语义混乱）。

当前内容：

- `detectors/blocking_io_runtime.py` —— Blockbuster 阻塞 IO 严格门的运行时
  探测器（`scanned_modules` 限定 app/deerflow），由 `tests/blocking_io/`
  的 conftest 挂载。门控目前只罩 `tests/blocking_io/` 目录内的元测试；
  扩面时在此扩展，不要复制粘贴。
