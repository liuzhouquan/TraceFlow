# TraceFlow

TraceFlow 是一个面向 GitHub 研发协作的 AI 助手。它整合仓库代码、Issue、Pull Request 和 CI 运行记录，帮助开发者理解项目上下文、审查变更、定位失败原因，并生成带证据的建议与操作草稿。

项目聚焦于让研发协作信息更容易检索、分析和跟进。功能范围、运行方式与当前限制见下文。

## 能力

- 同步 GitHub Issue、PR、变更文件、评论、Workflow Run、Job 与日志。
- Issue 分析、PR 审查、CI 故障诊断、工程周报。
- Agent 工具调用与 Planner → 专用分析 Agent → Observer → Synthesis 工作流。
- 工作区文件检索、RAG、对话记忆、Skill 加载、评估及操作草稿。

CI 诊断读取已有运行记录并给出建议；此版本没有自动修改代码并重跑 CI 的修复闭环。

## 目录

```text
backend/                 FastAPI、Agent、业务服务与测试
frontend/                Next.js UI
scripts/                 初始化、演示数据和同步脚本
docs/                    架构、API 与审查记录
examples/rag-evaluation/  独立 RAG 实验与历史评估数据
```

PostgreSQL 保存业务数据，Milvus 保存向量；etcd 和 MinIO 是 Milvus 依赖，Redis 可选。Compose 沿用带 pgvector 扩展的 PostgreSQL 镜像，但应用向量检索仍走 Milvus。

## 启动

需要 Python 3.12、Node.js 22+、Git 和 Docker Compose。以下命令从项目根目录开始：

```bash
cp .env.example .env
# 编辑 .env：配置 LLM、Embedding / Rerank 服务与 GitHub 凭据
# 使用本地 Qwen Embedding 时需另装匹配设备的模型依赖并下载模型
docker compose up -d postgres etcd minio milvus
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
cd backend
uvicorn app.main:app --reload
```

Windows 激活命令为 `backend\.venv\Scripts\Activate.ps1`；再进入 backend 启动服务。

另开终端，从项目根目录运行：

```bash
cd frontend
npm ci
npm run dev
```

访问 http://localhost:3000；API 文档 http://localhost:8000/docs。`.env` 的查找相对工作目录，请按上述方式在 `backend/` 启动后端。前端默认连接本机 8000 端口，远程部署需设置 `NEXT_PUBLIC_API_BASE_URL`。

轻量演示可参考 `scripts/start_rag.ps1`：SQLite + 确定性 Embedding 是降级流程，不能代表 Milvus/真实模型效果。

## 开发检查

```bash
# 项目根目录，激活后端环境后
pip install ruff==0.17.0
ruff format --config backend/pyproject.toml --check backend/app scripts
cd backend
DATABASE_URL=sqlite+pysqlite:///:memory: AUTO_SYNC_ENABLED=false MILVUS_ENABLED=false python -m pytest
cd ../frontend
npm run format:check
npm run lint
npm run typecheck
npm run build
```

后端依赖的 pytest 用于本项目自身回归测试，不意味着被分析的 GitHub 仓库必须使用 Python。

独立实验的数据结构和应用评估接口不同，不能直接混用。当前仓库未包含 `docs/datas/evals/rag_cases.json` 主评估用例文件，因此不将历史评估输出当作当前验证结果。当前审查与验证范围见 [review](docs/review.md)。

当前版本提供注册/登录、密码哈希、JWT 身份校验、仓库创建者与协作者权限控制，以及 Alembic 迁移和回滚。旧数据库只有在所需表、字段和外键均通过检查后才会纳管；无法识别的结构会拒绝自动升级。验证范围与当前限制见 [review](docs/review.md)。
