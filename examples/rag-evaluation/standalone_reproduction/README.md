# 7.5 RAG 评测独立复现实验

这是产生示例 7.5 中五份合成资料、9 条固定 Case 和 Top-K=`2/3/4` 对比结果的独立实验工程。它来自示例早期版本中的 RAG 工程化示例，因此源码注释和默认知识库名称里仍可能出现旧章节号；这些历史文字不影响实验数据、运行逻辑和结果核对。

> Milvus 是本项目唯一的向量与检索数据库。SQLite 只保存文档目录、期望状态、Generation、Job、Manifest 和事件等控制面数据，不保存向量，不执行 FTS、BM25 或任何检索降级。

## RAG 工程化增加了什么

| 能力 | 进阶版 RAG | RAG 工程化 |
| --- | --- | --- |
| 写入方式 | 原地更新一个 Collection | 构建不可变候选 Collection |
| 在线读流量 | 构建时需要串行保护 | 始终通过 Milvus Alias 查询旧 Active |
| 发布前验证 | 手工运行评测 | 构建后自动执行完整性检查和固定质量门禁 |
| 发布 | 重建完成即生效 | 只有门禁通过才切换 Alias |
| 回滚 | 再次重建 | Alias 指回保留的旧 Generation |
| 增量成本 | 全部重新 Embedding | 相同 `chunk_id + content_hash + 维度` 复用旧向量 |
| 故障恢复 | 单次请求状态 | SQLite Job/Event 持久化并在启动时标记中断任务 |
| 一致性 | 依赖写入流程 | Manifest 与 Active Collection 对账 |
| 复杂问题 | 单轮检索 | 受证据合同和查询预算约束的有限补证据流程 |
| 答案质量 | 只看检索结果 | 固定答案 Case + 硬规则 + Ragas 四指标 + 异步报告 |

RAG 工程化阶段没有声称实现完整 Self-RAG、CRAG、GraphRAG 或通用 Agentic RAG。当前仓库的 `/investigate` 是一个可解释的工程闭环：先定义必须找到的证据槽位，再在固定轮数和查询数内补查；证据仍不足就拒答，证据冲突就把冲突交给显式规则或人工裁决。

## 架构职责

```text
期望语料
  -> SQLite 文档目录（staged / ready / deleted）
  -> 冻结构建快照与 build_key
  -> Milvus 候选物理 Collection
       -> 结构感知切片
       -> 复用未变化的旧向量
       -> 只为变化 Chunk 生成 Embedding
       -> Dense + BM25 Schema 与索引
  -> Manifest / Schema / 实际行数据验证
  -> 固定 JSONL 评测集质量门禁
  -> Milvus Alias 原子切换
  -> SQLite Active 镜像与事件记录

在线查询
  -> Milvus Alias
  -> 当前 Active 物理 Collection
  -> Dense / Hybrid / Hybrid + Rerank
  -> Answer Gate
  -> 带引用答案

持续答案评测
  -> 固定 held-out JSONL
  -> 同一个 pipeline.ask()
  -> 关键事实 / 拒答 / 冲突硬规则
  -> Ragas Collections：Precision / Recall / Faithfulness / Relevancy
  -> Repository 级异步 Run、阈值与逐题 JSON
```

Milvus Alias 是服务流量的事实来源，SQLite 中的 Active 记录只是控制面镜像。应用重启时会读取 Alias 并执行对账，而不是反过来用 SQLite 猜线上究竟指向哪个 Collection。

## 一次发布怎样运行

1. 上传、删除或导入示例资料只改变“下一版期望语料”，不会立刻修改 Active Collection。
2. `POST /api/repositories/{repo_id}/releases/build` 根据文档校验和与索引配置计算确定性的 `build_key`，创建当前仓库的 Generation 和持久化 Job。
3. 后台任务构建独立物理 Collection，例如 `rag_studio_production_v_a1b2...`。查询仍通过 Alias 访问旧版本，不会被构建锁阻塞。
4. 系统扫描候选 Collection，校验文档数、Chunk 数、Hash、Schema 和实际行数据。
5. 系统用 `eval_cases/held_out_v1.jsonl` 计算 Recall@K、MRR、nDCG@K、无答案拒答率，并验证冲突资料是否同时召回。
6. 绝对阈值和相对旧版本回归检查都通过后，Generation 进入 `passed`。
7. 自动发布时先让 Milvus Alias 指向候选 Collection，再更新 SQLite Active 镜像。任何前置步骤失败都不会移动 Alias。
8. 旧 Collection 保留，因此回滚只需再次切换 Alias，不需要现场重新 Embedding。
9. 发布后可以对 Serving Alias 运行独立答案评测；它不改动 Alias，也不把 Judge 波动混进当前确定性发布门禁。

同一个幂等键重复提交相同请求会返回原 Job；相同幂等键对应不同构建请求会返回 `409`。相同语料和索引配置会得到同一个 `build_key`，避免重复构建相同 Generation。

## 模型与检索

推荐的真实本地配置仍沿用进阶版 RAG：

- Embedding：Ollama `qwen3-embedding:0.6b`，默认 1024 维，适合中英文技术资料；
- 生成：Ollama `qwen3:4b-instruct`；
- 重排：兼容 `/reranks` 的 `qwen3-rerank` API；
- 检索：Milvus Dense、BM25 Function、RRF，按请求选择 `dense`、`hybrid` 或 `hybrid_rerank`。

`MODEL_PROVIDER=deterministic` 只是 384 维离线测试替身。它能验证发布状态机和 API，却不能代表真实语义检索质量。没有配置 Rerank Key 时，`hybrid_rerank` 会明确标记为 `fallback_hybrid`，不会用启发式规则冒充真实模型。

## 首次运行

所有命令都在本目录执行：

```powershell
cd standalone_reproduction
```

### 1. 启动 Milvus Standalone

```powershell
docker compose -f docker-compose.milvus.yml up -d
docker compose -f docker-compose.milvus.yml ps
Invoke-WebRequest http://127.0.0.1:9091/healthz -UseBasicParsing
```

RAG 工程化依赖 Milvus Alias，因此不能使用不支持 Alias 的 Milvus Lite。若第七、八章的 Standalone 已监听 `19530`，直接复用它，不要再启动第二套容器；三个阶段通过不同的 Collection/Alias 名隔离。

### 2. 准备模型

```powershell
ollama pull qwen3-embedding:0.6b
ollama pull qwen3:4b-instruct
ollama serve
```

### 3. 创建环境并启动

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python run.py
```

打开：

- 工作台：[http://127.0.0.1:8077](http://127.0.0.1:8077)
- API 文档：[http://127.0.0.1:8077/docs](http://127.0.0.1:8077/docs)

macOS 或 Linux 使用 `python3 -m venv .venv`、`source .venv/bin/activate` 和 `cp .env.example .env`，其余命令相同。

## 跑通第一版发布

先暂存五份示例资料：

```powershell
Invoke-RestMethod -Method Post `
  http://127.0.0.1:8077/api/repositories/traceflow/seed
```

此时资料是 `staged`，Active Alias 尚未改变。提交构建任务：

```powershell
$body = @{
  idempotency_key = "chapter-09-first-release"
  auto_promote = $true
} | ConvertTo-Json

$job = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8077/api/repositories/traceflow/releases/build `
  -ContentType "application/json" -Body $body
$job
```

查询任务进度和事件：

```powershell
Invoke-RestMethod http://127.0.0.1:8077/api/repositories/traceflow/jobs/$($job.id) |
  ConvertTo-Json -Depth 12
```

任务成功后查看 Generation、Active Alias 与门禁报告：

```powershell
Invoke-RestMethod http://127.0.0.1:8077/api/repositories/traceflow/releases |
  ConvertTo-Json -Depth 12
Invoke-RestMethod http://127.0.0.1:8077/api/repositories/traceflow/status |
  ConvertTo-Json -Depth 12
```

`report.build.reused_chunk_count` 与 `report.build.embedded_chunk_count` 能看出复用了多少向量、重新计算了多少向量。`report.quality_gate` 保存固定评测集的指标、阈值和失败原因。

## 手动发布、回滚与对账

若构建时设置 `auto_promote=false`，门禁通过后手动发布：

```powershell
Invoke-RestMethod -Method Post `
  http://127.0.0.1:8077/api/repositories/traceflow/releases/<generation_id>/promote
```

回滚到仍保留的旧 Generation：

```powershell
Invoke-RestMethod -Method Post `
  http://127.0.0.1:8077/api/repositories/traceflow/releases/<old_generation_id>/rollback
```

对账 Active Alias、控制面 Manifest 与 Milvus 实际数据：

```powershell
Invoke-RestMethod -Method Post `
  http://127.0.0.1:8077/api/repositories/traceflow/reconcile |
  ConvertTo-Json -Depth 12
```

`clean=true` 表示没有发现缺失 Chunk、孤儿 Chunk 或 Hash 不一致。对账发现内容漂移时只报告问题，不会偷偷原地修改 Active Collection；正确修复方式是从可信源构建一个新 Generation，再经过门禁发布。

## 有限补证据

查看内置证据合同：

```powershell
Invoke-RestMethod http://127.0.0.1:8077/api/evidence-contracts
```

验证当前发布手册与已废弃备忘中的阈值冲突：

```powershell
$body = @{
  question = "新旧发布资料中的核心接口错误率回滚阈值是否冲突？"
  contract = "release_conflict"
  mode = "hybrid"
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8077/api/repositories/traceflow/investigate `
  -ContentType "application/json" -Body $body |
  ConvertTo-Json -Depth 15
```

正常结果是 `decision=evidence_conflict`，并保留 `2%` 与 `5%` 两条来源。工作流不会替模型擅自挑一个数字。

## 答案级 RAGAS 评测

发布门禁验证候选索引的确定性底座；RAGAS Run 验证用户实际经过的 Serving 问答链。评测器直接调用 `pipeline.ask()`，保存回答、真实有序证据和 Generation Mode，再组合四项 Ragas 指标与关键事实硬规则。

先在 `.env` 启用 Judge。下面示例使用本地 OpenAI 兼容接口，也可以替换成你自己的兼容服务：

```dotenv
RAGAS_ENABLED=true
RAGAS_EVALUATION_SET=eval_cases/held_out_v1.jsonl
RAGAS_JUDGE_BASE_URL=http://127.0.0.1:11434/v1
RAGAS_JUDGE_API_KEY=local-judge
RAGAS_JUDGE_MODEL=qwen3:8b
RAGAS_PROMPT_VERSION=ragas-0.4.3-default
RAGAS_INPUT_LANGUAGE=zh-CN
RAGAS_JUDGE_MAX_TOKENS=8192
RAGAS_TIMEOUT_SECONDS=180

RAGAS_CONTEXT_PRECISION_MIN=0.80
RAGAS_CONTEXT_RECALL_MIN=0.85
RAGAS_FAITHFULNESS_MIN=0.90
RAGAS_ANSWER_RELEVANCY_MIN=0.75
```

这些是当前中文样本的试运行门槛，不是 Ragas 通用标准。完整报告要求四项分别达标、9 条硬规则全部通过且指标计算没有错误。没有启用 Judge 时只返回 `partial`，不会用假 0 分或假通过掩盖缺失检查。

运行：

```powershell
# 硬规则冒烟
python scripts\evaluate_ragas.py `
  --repo-id traceflow `
  --top-k 3 `
  --json-output eval_results\ragas_traceflow_top3_hard_rules.json

# 硬规则 + Ragas 0.4.3 四指标，并保存完整逐题报告
python scripts\evaluate_ragas.py `
  --repo-id traceflow `
  --top-k 3 `
  --judge `
  --timeout 900 `
  --json-output eval_results\ragas_traceflow_top3_full.json
```

也可以在工作台「答案质量评测」区域点击「运行」。页面按「分数 / 门槛」显示 Context Precision、Context Recall、Faithfulness 和 Answer Relevancy，并显示硬规则通过数、Top-K、Judge、Ragas 版本与失败 Case。点击「明细」可查看每道题的回答、来源、硬规则和四项分数。

Run 状态含义：

| 状态 | 含义 |
| --- | --- |
| `passed` | 硬规则和四项 Ragas 指标均完整通过 |
| `failed` | 评测跑完，但至少一个规则或指标未达标 |
| `partial` | 只运行硬规则，不能当作完整 RAGAS 通过 |
| `error` | Judge、Embedding、超时或结构化指标输出失败 |

`eval_results/ragas_traceflow_full_before_fix.json`、`ragas_traceflow_full_after_fix.json` 与 `ragas_traceflow_top3_full.json` 分别保存修复前失败、修复后仍有检索噪声、Top-K 3 最终通过的端到端现场。比较报告前先核对评测集 Hash、物理 Collection、Judge 和模型配置。

## 验证

离线单元测试不需要 Ollama 或 Milvus：

```powershell
python -m pytest -q --basetemp .test-tmp-unit
```

真实 Milvus 集成测试会创建临时 Collection 和 Alias，并验证切换与回滚：

```powershell
$env:MILVUS_TEST_URI = "http://127.0.0.1:19530"
python -m pytest -q tests\test_live_milvus.py --basetemp .test-tmp-live
```

针对已启动的独立实例运行端到端冒烟和三模式评测：

```powershell
python scripts\smoke_test.py
python scripts\smoke_test.py --ask
python scripts\evaluate_retrieval.py --repo-id isolation-lab
```

评测脚本默认使用预置的 `isolation-lab` 仓库，并要求语料恰好是五份示例资料。它有独立 SQLite 控制面、上传目录、Milvus Alias 与物理 Collection 前缀；不要混入未标注资料。

## 目录职责

| 文件 | 职责 |
| --- | --- |
| `rag_store.py` | SQLite 文档目录、RAG 工程化 Schema、Alias 与检索 |
| `control_plane.py` | Generation、Job、Event、Manifest 和 Active 镜像 |
| `rag_pipeline.py` | 切片、向量复用、检索、Answer Gate 与问答 |
| `release_service.py` | 构建、验证、质量门禁、发布、回滚与对账编排 |
| `quality_gate.py` | 固定 JSONL 评测集及绝对/回归阈值 |
| `ragas_evaluation.py` | 答案级 Case、硬规则、Ragas Collections 适配与异步报告存储 |
| `evidence_workflow.py` | 证据合同、补查预算、停止条件和冲突识别 |
| `app.py` | FastAPI、后台 Job 和工作台 API |
| `repositories.py` | Repository 清单、`repo_id` 目录与 Alias/Collection 命名作用域 |
| `static/` | RAG 工程化发布、调查与答案质量逐题报告工作台 |
| `scripts/evaluate_ragas.py` | 创建评测 Run、轮询结果、打印阈值并输出 CI 退出码 |
| `eval_results/` | 真实校准与端到端评测报告；不存 API Key |

## 与 TraceFlow 主应用的关系

这套 RAG 工程化工作室是一个可以独立运行和验证的示例架构。它使用 `Repository / repo_id` 隔离仓库数据，以 Milvus 作为唯一向量数据库，并把文档处理、检索、引用和索引重建放在同一条主线上。

为了让工作室可以独立运行，当前实现明确控制了能力范围：

- `repositories.py` 使用预置逻辑仓库，TraceFlow 主应用使用 GitHub 同步后写入数据库的 Repository UUID；
- 工作室使用每仓库独立 SQLite 控制面，集成时需要把 Generation、Job、Manifest 和事件迁入统一关系数据库；
- 工作室的 Build Job 在 FastAPI 进程内执行，集成时还需要正式任务队列、分布式锁、重试和监控；
- RAG 工程化工作室已实现 Milvus BM25 + RRF、Generation、质量门禁、Alias、回滚和对账，这些能力需要与主应用进一步集成；
- 工作室没有复制主应用的用户权限、GitHub 同步、Chat Agent、MCP 和会话记忆体系。

把 RAG 工程化工作室接入主应用时，核心算法和发布约束可以保留，主要工作是替换 Repository 来源、迁移控制面模型、统一检索实现、接入任务系统，并补齐权限、审计、告警和持续评测。完成这条工程链路，意味着可以构成 TraceFlow RAG 功能的工程基础；把它变成正式产品能力，还需要完成这些外围集成。

## 当前工程限制

这是一条可运行、可验证的工程化闭环，但它没有覆盖所有生产问题：

- 构建锁只在单个 Python 进程内生效，没有分布式队列和多 Worker 选主；
- 没有租户权限、审计授权、备份恢复和 Collection 自动保留策略；
- 后台 Job 重启后会被明确标记失败，不会自动从断点续建；
- 固定评测集规模有限，只能作为当前发布门禁，不能替代线上反馈、人工标注和持续扩充；
- RAGAS Run 当前读取 Serving Alias，不读取候选 Generation；正式发布前生成门禁需要增加候选 Shadow Alias；
- Judge 分数依赖模型、Prompt、语言与 Embedding，升级任何一项都要重新做人工校准；
- `investigate` 使用预定义证据合同，不是能规划任意工具调用的通用 Agent；
- 真实质量验收必须使用真实 Embedding/Rerank 配置，确定性替身只验证工程链路。

相关官方资料：

- [Milvus Manage Aliases](https://milvus.io/docs/manage-aliases.md)
- [Milvus BM25 Function](https://milvus.io/docs/bm25-function.md)
- [Milvus RRF Reranking](https://milvus.io/docs/reranking.md)
