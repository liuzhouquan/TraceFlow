# 7.5 RAG 评测测试数据集

这是一套专门用于课程实验的合成测试数据，不是真实业务数据。它对应课程 7.5 中“课程实验使用了哪些资料”以及 Top-K=`2/3/4` 对比实验。

## 压缩包里有什么

- `dataset/knowledge_base/`：五份需要导入知识库的 Markdown 资料。
- `dataset/evaluation_set/held_out_v1.jsonl`：9 条固定评测 Case，包含参考答案、预期来源、必含事实和禁用事实。
- `reference_results/`：课程文章使用的原始运行结果，用于核对复现方向，不应直接当作你本地运行的新结果。
- `standalone_reproduction/`：产生课程实验结果的独立 RAG 工程源码。已经排除 `.env`、数据库、向量库、虚拟环境和运行缓存。

## 数据集指纹

`held_out_v1.jsonl` 的 SHA-256 是：

```text
38c1b06645b60f1e226aa790a9bdaf3fabd936612ee56927a8c4523cac5ae840
```

只有 Hash 相同，才说明使用的是修正判分规则后的同一套 9 条 Case。

## 怎样复现课程结论

进入 `standalone_reproduction/`，按照该目录中的 `README.md` 启动 Milvus、模型和服务。不要复制任何真实业务 `.env`；请从 `.env.example` 新建自己的 `.env`，再填写本地模型或兼容 API 配置。

导入五份示例资料并完成第一版发布后，依次运行：

```powershell
python scripts\evaluate_ragas.py --repo-id devflow-ai --top-k 2 --json-output eval_results\top2_hard_rules.json
python scripts\evaluate_ragas.py --repo-id devflow-ai --top-k 3 --judge --timeout 900 --json-output eval_results\top3_full.json
python scripts\evaluate_ragas.py --repo-id devflow-ai --top-k 4 --judge --timeout 900 --json-output eval_results\top4_full.json
```

课程原始实验中：Top-K=`2` 的硬规则通过 `8/9`；Top-K=`3` 的硬规则通过 `9/9`，四项 RAGAS 指标全部过线；Top-K=`4` 虽然硬规则通过 `9/9`，但 Context Precision=`0.7882`，低于课程门槛 `0.80`。

RAGAS 使用模型评分，同样的数据与参数也可能出现小幅波动，因此复现时应同时核对逐题回答、检索来源、评测集 Hash、Judge 模型、Ragas 版本和阈值，不要只比较最后四个小数。

## 与 7.8 当前主项目的区别

7.5 用这套五份资料和 9 条 Case 解释评测方法与课程实验结论；7.8 解读当前 DevFlow AI 主项目中的 ChatAgent 评测实现。当前主项目自带的 `docs/datas/evals/rag_cases.json` 是另一套 5 条真实项目回归基线，分数不能与本数据集直接比较。
