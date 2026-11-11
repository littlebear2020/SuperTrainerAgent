# 火山云个人服务器部署指南

> 适用场景：无堡垒机、可 SSH 直连、有外网的火山云 ECS 实例
> **最后更新**：2026-10-05（审计整改批 E5 整份刷新：五服务拓扑/.env 必填清单/模型示例换现行花名册/prune 红线接入）；2026-10-12 计数修正（六服务：remotion-worker 已入列）；2026-11-11 补充 Aio 自建镜像与完整 Agent 执行前提
> 部署文档选型见 `company-server-deployment-guide.md` 开头的对照表；离线/仅国内环境**不要用本文**。

---

## 〇、前提条件

| 项目 | 要求 |
|------|------|
| 服务器 | 火山云 ECS，已有公网 IP，可 SSH 登录 |
| 操作系统 | Ubuntu 20.04+ 或 CentOS 7+ |
| 内存 | 最低 8 GB（六服务全量）；4-8G 可减 worker/ppt 副本、且不要起 remotion-worker（见二） |
| 磁盘 | 最低 40 GB 可用空间（镜像 ~10G + 运行数据） |
| 网络 | 能访问外网（Docker Hub / pip / npm 源） |
| 本地电脑 | 已安装 ssh、scp、git（方式乙另需 Docker） |

---

## 一、服务器初始化（首次部署执行一次）

### 1. SSH 登录服务器

```bash
ssh root@<你的服务器IP>
# 如果端口不是默认 22：ssh -p 端口号 root@<你的服务器IP>
```

### 2. 安装 Docker

```bash
# 一键安装（Ubuntu / CentOS 通用）
curl -fsSL https://get.docker.com | sh
systemctl enable docker && systemctl start docker

# 验证安装结果
docker --version
docker compose version
```

> 如果 `docker compose` 命令不可用，手动安装插件：
> ```bash
> # Ubuntu
> apt-get update && apt-get install -y docker-compose-plugin
> # CentOS
> yum install -y docker-compose-plugin
> ```

### 3. 配置国内镜像加速（推荐）

```bash
mkdir -p /etc/docker
cat > /etc/docker/daemon.json << 'EOF'
{
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me"
  ]
}
EOF
systemctl daemon-reload && systemctl restart docker
```

### 4. 开放防火墙端口

**火山云安全组**（控制台操作）：入方向放行 TCP 2026 端口。

**服务器本机防火墙**：

```bash
# Ubuntu（ufw）
ufw allow 2026/tcp
# CentOS（firewalld）
firewall-cmd --permanent --add-port=2026/tcp && firewall-cmd --reload
```

---

## 二、服务拓扑（六服务）

`docker/docker-compose-deploy.yaml` 现行拓扑（2026-10-05 实况）：

| 服务 | 容器 | 作用 | 默认副本 |
|------|------|------|----------|
| nginx | deer-flow-nginx | 2026 端口入口反代 | 1 |
| frontend | deer-flow-frontend | Next.js 生产服务 | 1 |
| gateway | deer-flow-gateway | FastAPI 网关 + agent 运行时 | 1（**多 worker 会破坏会话粘性，勿加**） |
| ppt-converter | deerflow-deploy-ppt-converter-1..4 | LibreOffice PPT 转换 sidecar | 4 |
| production-worker | deerflow-deploy-production-worker-1..3 | ffmpeg 视频合成池 | 3 |
| remotion-worker | deerflow-deploy-remotion-worker-1 | 课程视频 Remotion 渲染池 | 1 |

> 小内存机器（4-8G）：把 `docker-compose.yaml` 里 ppt-converter `deploy.replicas` 4→1、production-worker 3→1 后再 `up -d`（compose 改动须随部署同步、只换镜像不生效——口径见 docs/production-server-ops-manual.md）。remotion-worker 在 compose 里带 128G 档硬前置警告（mem_limit 10g + shm 2g）——**4-8G 小内存档不要起 remotion-worker 副本**（确需功能验证可按测试机降级 override：mem 3500m / shm 1g，口径见 docs/company-server-deployment-guide.md L 节）。

---

## 三、构建镜像（两种方式二选一）

### 方式甲：服务器上直接构建（服务器 ≥4核8G 时推荐）

```bash
# 服务器上：取代码
mkdir -p ~/deerflow && cd ~/deerflow
git clone <你的仓库地址> .

# 五镜像全量构建（⚠️ 全部在仓库根执行；worker/remotion 上下文见注释）
docker build -t littlebear2016/deerflow-gateway:latest -f backend/Dockerfile .
docker build -t littlebear2016/deerflow-frontend:latest -f frontend/Dockerfile .
docker build -t littlebear2016/deerflow-ppt-converter:latest -f docker/ppt-converter/Dockerfile .
docker build -t littlebear2016/deerflow-production-worker:latest -f docker/production-worker/Dockerfile docker/production-worker
# remotion 的 FROM 是 trainer-ai-worker:latest 基座——先给 worker 打基座 tag 再建：
docker tag littlebear2016/deerflow-production-worker:latest trainer-ai-worker:latest
docker build -t littlebear2016/deerflow-remotion-worker:latest -f docker/production-worker/Dockerfile.remotion .
```

> build context 必须是仓库根 `.`（仅 production-worker 除外，其上下文为 `docker/production-worker`；remotion-worker 的 Dockerfile.remotion 同样必须仓库根上下文——上方命令里它用的就是 `.`）——context 传错会报 `"/frontend": not found` 之类。

### 方式乙：本地构建再传服务器（服务器配置一般时）

```bash
# 本地电脑：构建（同方式甲的 5 条 build 命令，Mac 需加 --platform linux/amd64）
# 导出 + 上传
docker save littlebear2016/deerflow-{gateway,frontend,ppt-converter,production-worker,remotion-worker}:latest \
  | gzip > /tmp/deerflow-images.tar.gz
scp /tmp/deerflow-images.tar.gz root@<服务器IP>:~/

# 服务器上加载
ssh root@<服务器IP> "gunzip -c ~/deerflow-images.tar.gz | docker load && rm ~/deerflow-images.tar.gz"
```

### 部署目录（两方式通用）

```bash
mkdir -p ~/deerflow-deploy/{nginx,frontend,skills,.deer-flow,extensions-config}
# 从仓库复制：
cp docker/docker-compose-deploy.yaml ~/deerflow-deploy/docker-compose.yaml
cp docker/nginx/nginx.conf ~/deerflow-deploy/nginx/
cp config.example.yaml ~/deerflow-deploy/config.yaml
cp .env.example ~/deerflow-deploy/.env
cp -r skills/public skills/custom ~/deerflow-deploy/skills/   # 技能目录（compose 挂载）
cp extensions_config.json ~/deerflow-deploy/extensions-config/  # MCP/skills 开关（compose 挂载该目录且 env 指向其中文件，缺失时 agent 运行路径炸 FileNotFoundError）
```

---

## 四、配置

```bash
ssh root@<服务器IP> && cd ~/deerflow-deploy
```

### 1. `.env` 必填项（compose `:?` 硬校验，缺失直接拒绝启动）

```env
# ========== 必填 ==========
# 部署目录绝对路径（用 pwd 实测输出填，勿照抄示例、勿用 ${PWD}；根因与排障见 docs/company-server-deployment-guide.md）
DEER_FLOW_HOST_BASE_DIR=/root/deerflow-deploy/.deer-flow
DEER_FLOW_HOST_SKILLS_PATH=/root/deerflow-deploy/skills
# PPT 转换 sidecar 共享密钥（生成：openssl rand -hex 32）
PPT_CONVERTER_SECRET=<openssl rand -hex 32 的输出>

# ========== 生产强烈建议（不属 compose `:?` 硬校验） ==========
# 认证 JWT 密钥：缺失时后端自动生成并持久化到 .jwt_secret，不阻断启动——但生产应显式设置
#（BETTER_AUTH_SECRET 已于 2026-10-05 退役删除）
AUTH_JWT_SECRET=<openssl rand -hex 32 的输出>

# ========== 模型 API Key（按实际使用填，示例为当前主链路双出口） ==========
ARK_API_KEY=你的火山方舟密钥      # 字节豆包系（Chat/生图/视频）+ ViMax
QIHU_API_KEY=你的360密钥          # 360 网关（LLM/图/TTS 降级链）

# ========== 搜索 MCP（按需） ==========
METASO_API_KEY=你的密钥
```

### 2. `config.yaml` 关键项

模型花名册以 `config.yaml` 顶部 `models:` 段 / 仓库 `config.example.yaml` 实际在册为准（名单随模型批次流动，勿抄死）——从 `config.example.yaml` 复制后按需删减即可，示例（360 通道，2026-10-12 起聊天一律走 360；Ark 直连聊天已撤册，Ark 通道仅剩 seedream/seedance 生图生视频）：

```yaml
models:
  - name: qwen3.8-flash
    display_name: 360·Qwen 3.8 Flash（默认）
    use: deerflow.models.patched_openai:PatchedChatOpenAI
    model: qwen/qwen3.8-flash
    api_key: $QIHU_API_KEY
    base_url: https://api.360.cn/v1
    # …完整字段见 config.example.yaml（勿手抄此缩略示例）
```

数据库默认 SQLite 即可。活跃默认 `LocalSandboxProvider + allow_host_bash: false` **不提供 bash**，不能据此宣称 PPT、Remotion、知识卡等完整 Agent 执行能力可用。需要这些能力时，显式切换现有 `deerflow.community.aio_sandbox:AioSandboxProvider`，保持 `allow_host_bash: false`，并使用仓库根上下文构建的自包含镜像：

```bash
# Apple Silicon 先在 Docker Desktop 启用 Rosetta；Linux amd64 不需要模拟。
docker build --platform linux/amd64 -t deerflow-sandbox:local -f docker/sandbox/Dockerfile .
```

`sandbox.image` 指向该镜像，不得在容器启动后安装依赖。每个本地 Aio 容器按计划需要约 3 GiB 内存与 2 GiB shm，默认 replicas=3 时使用回环地址 8080–8082；先检查 `/v1/sandbox`，再分别实测 `/v1/shell/exec` 与 `/v1/bash/exec`，并预检 PPT/Remotion/知识卡依赖。amd64 经 Rosetta 模拟可能更慢，长任务沿用技能的后台执行与日志检查，不盲目提高超时。本地 Docker 验证不代表公司服务器 provisioner 或部署拓扑已验证。

当前示例 `config_version` 为 **31**；升级时先运行 `make config-upgrade`，不要覆盖本地模型与密钥配置。`sandbox.environment` 保持 `{}`——密钥走技能 SKILL.md `required-secrets` 声明 + 请求级带外注入（见 `config.example.yaml` 的 Aio 注释块）；白名单仅为紧急回退杆。

### 3. 前端环境文件（可选）

```bash
cat > ~/deerflow-deploy/frontend/.env << 'EOF'
# 按需（通常留空——默认同源反代）
EOF
```

---

## 五、启动

```bash
cd ~/deerflow-deploy
docker compose up -d
docker compose ps          # 应见 nginx/frontend/gateway + 4×ppt + 3×production-worker + 1×remotion-worker
docker compose logs -f --tail=50
```

浏览器访问 **http://你的服务器IP:2026**。

> **worker 重建会等 drain（最长 ~7 分钟属正常）**：`up -d` 期间勿 Ctrl-C / `docker kill`——根因与口径见 docs/production-server-ops-manual.md。

---

## 六、日常运维

```bash
docker compose logs -f gateway          # 后端日志
docker compose logs -f --tail=200       # 全部服务
docker compose restart gateway          # 单服务重启
# 改 .env 后必须 --force-recreate（restart 不生效，原因见 docs/production-server-ops-manual.md）：
docker compose up -d --force-recreate --no-deps gateway
```

### 更新部署（方式甲）

```bash
cd ~/deerflow && git pull
# 重建有变更的镜像（命令同第三节）→ 同步 compose/nginx → 重启
cp docker/docker-compose-deploy.yaml ~/deerflow-deploy/docker-compose.yaml
cp docker/nginx/nginx.conf ~/deerflow-deploy/nginx/
cd ~/deerflow-deploy && docker compose up -d
```

### 备份 / 恢复数据

```bash
# 备份（SQLite + 上传文件 + 任务数据都在 .deer-flow；栈停止后的冷备份——部署前备份纪律与运行中热备见 docs/production-server-ops-manual.md）
tar czf ~/deerflow-backup-$(date +%Y%m%d).tar.gz -C ~/deerflow-deploy .deer-flow/
# 恢复
cd ~/deerflow-deploy && tar xzf ~/deerflow-backup-日期.tar.gz && docker compose restart
```

### 磁盘空间不足

```bash
df -h
# ⚠️ 绝不执行 `docker system prune -a`（红线完整口径见 docs/production-server-ops-manual.md）。空间紧张时：
docker image prune            # 只清 dangling（无 -a）
docker rmi <具体旧版本镜像名>   # 按名逐个删，保留当前版与上一版回退档
```

---

## 七、常见问题排查

- **访问不了页面**：`docker compose ps` + `ss -tlnp | grep 2026` 正常 → 查火山云安全组 2026/TCP 放行。
- **gateway 起不来**：`docker compose logs gateway | tail -100`；常见为 config.yaml 缩进错误、`.env` 必填项缺失（compose `:?` 会直接拒绝启动并点名变量）。
- **视频任务一直排队**：查 production-worker/remotion-worker 容器是否在跑、`.deer-flow/data/deerflow.db` 所在盘是否满。
- **PPT 导出 401**：`.env` 的 `PPT_CONVERTER_SECRET` 与 ppt-converter 容器内值不一致（compose 单源注入通常不会；手动改过单侧才会）。
