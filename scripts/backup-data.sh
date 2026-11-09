#!/usr/bin/env bash
# =============================================================================
# 数据备份轮换脚本(存储止血 A3,2026-09-02;2026-09-03 对齐 A3 口径)
#
# 口径(对齐 docs/plans/2026-09-02-storage-and-security-bleeding-plan.md §A3):
#   - 每周全量,不做每日增量(SQLite 无可靠文件级增量;RPO < 1 天另行立项)。
#     建议 crontab 周日低峰 03:30(A1 的 checkpoint prune 每日 03:00 之后):
#       30 3 * * 0  /path/to/scripts/backup-data.sh >> logs/backup.log 2>&1
#   - 紧凑化:先 .backup 出在线一致性副本,再对副本 VACUUM INTO 出紧凑快照
#     (副本兼作全量备份,一举两得;紧凑化在副本上做,不阻塞在线写入)。
#   - 保留:本地最近 2 份;第 2 份建议归档到外部存储(运维另行挂接,本脚本只管本地)。
#   - 磁盘峰值:运行期间 TMP(≈活库体积)+ 紧凑快照 + 既有保留份同时在盘,
#     执行前 df 预检(不足即退出,不写半截)。
# 恢复:  停 gateway → 用快照文件替换 data/deerflow.db → 启动。
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_DIR="${ROOT}/backend/.deer-flow/data"
BACKUP_DIR="${ROOT}/backend/.deer-flow/backups"
DB="${DATA_DIR}/deerflow.db"
KEEP=2

mkdir -p "${BACKUP_DIR}"

# 清理上次中途失败残留的临时副本(.tmp-* 是点文件,轮换 glob 与 ls 都看不见)
rm -f "${BACKUP_DIR}"/.tmp-*.db

if [[ ! -f "${DB}" ]]; then
  echo "[backup-data] ERROR: 未找到数据库 ${DB}" >&2
  exit 1
fi

# 磁盘预检:峰值 ≈ TMP(≈活库)+ 紧凑快照(≤活库),再留 20% 余量
DB_KB="$(du -k "${DB}" | cut -f1)"
AVAIL_KB="$(df -k "${BACKUP_DIR}" | awk 'NR==2 {print $4}')"
NEED_KB=$(( DB_KB * 2 * 12 / 10 ))
if (( AVAIL_KB < NEED_KB )); then
  echo "[backup-data] ERROR: 磁盘余量不足(可用 ${AVAIL_KB} KB < 需要 ~${NEED_KB} KB,活库 ${DB_KB} KB)" >&2
  exit 1
fi

TS="$(date +%Y%m%d-%H%M%S)"
DEST="${BACKUP_DIR}/deerflow-${TS}.db"
TMP="${BACKUP_DIR}/.tmp-${TS}.db"

echo "[backup-data] 在线快照 ${DB} → ${TMP} ..."
# SQLite .backup:在线一致快照,无需停服(WAL 模式安全)
sqlite3 "${DB}" ".backup '${TMP}'"

echo "[backup-data] 紧凑化 ${TMP} → ${DEST} ..."
# VACUUM INTO 在副本上做,不碰在线库;目标已存在会报错——TS 唯一性保证
sqlite3 "${TMP}" "VACUUM INTO '${DEST}'"
rm -f "${TMP}"

SIZE="$(du -h "${DEST}" | cut -f1)"
echo "[backup-data] 完成:${DEST}(${SIZE})"

# 轮换:保留最近 KEEP 份,删除更旧的
ls -1t "${BACKUP_DIR}"/deerflow-*.db 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  echo "[backup-data] 轮换删除旧快照:${old}"
  rm -f "${old}"
done

echo "[backup-data] 当前快照列表:"
ls -1t "${BACKUP_DIR}"/deerflow-*.db 2>/dev/null | head -"${KEEP}" || true
