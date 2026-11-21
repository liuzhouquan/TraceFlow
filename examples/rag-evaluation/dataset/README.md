# 数据集目录

- `knowledge_base/`：依次导入五份 Markdown 文档，不要漏掉干扰资料和已废弃规则。
- `evaluation_set/held_out_v1.jsonl`：9 条固定评测 Case。

这五份资料故意同时包含正常事实、关键词干扰、无答案问题以及新旧规则冲突。删除任何一份资料，或者修改 Case 的参考答案与硬规则，都会改变实验条件。
