# 参考结果说明

这些 JSON 是课程写作时保存的原始运行结果，只用于核对文章中的失败原因和分数，不是新的评测输入。

| 文件 | 评测集 Hash | 结果 |
| --- | --- | --- |
| `00_before_rule_fix_top4_full.json` | `66a745eb...` | 修正规则前，Top-K=`4`，硬规则 `5/9`；不能与后三次直接比较 |
| `01_after_fix_top2_hard_rules.json` | `38c1b066...` | Top-K=`2`，硬规则 `8/9`，未运行 Judge |
| `02_after_fix_top3_full_passed.json` | `38c1b066...` | Top-K=`3`，硬规则 `9/9`，四项 RAGAS 全部过线 |
| `03_after_fix_top4_full_failed.json` | `38c1b066...` | Top-K=`4`，硬规则 `9/9`，Context Precision=`0.7882` 未过线 |

第一个结果使用的是修正规则前的评测集，因此它只能说明旧判分规则会误伤正确回答。后三个结果使用同一个评测集 Hash，才构成 Top-K=`2/3/4` 的对比。
