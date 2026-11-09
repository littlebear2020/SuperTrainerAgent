"""共享异常定义（2026-09-08 T12）。

QCRepairNeeded 原先定义在 worker.py——`python worker.py` 启动时 worker 模块同时以
__main__ 与 worker 两个实例存在，compose_remotion 里 `from worker import QCRepairNeeded`
拿到的是**另一个类**，process_task 的 except 永远接不住（E2E 实锤：黑帧任务被标
failed 而非 qc_repair）。抽到本共享模块消除双实例隐患。
"""


class QCRepairNeeded(Exception):
    """QC 发现异常场景，任务不标 ready、进 qc_repair 状态（D19：修复完再交付）。

    由 compose_remotion 在 QC 扫描发现黑帧/冻结/静音后抛出（worker 只上报不生成）；
    gateway 侧 watcher 发现 qc_repair → dirty_frames → 既有重渲染回路重生素材 →
    原任务复用回 composing 重渲（不新建任务）。ex.qc_issues 携带异常场景清单
    （[{scene, issue, detail}]），随 result 落库透出到详情页。
    """

    def __init__(self, qc_issues: list[dict], message: str = ""):
        self.qc_issues = qc_issues
        super().__init__(message or f"QC issues found in {len(qc_issues)} scene(s); entering qc_repair")
