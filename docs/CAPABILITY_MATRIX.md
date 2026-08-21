# Capability Matrix

Generated solely from `capability_matrix.py`. FUNCTIONAL coverage includes PRODUCTION_READY; production coverage counts only PRODUCTION_READY.

| Capability | Status | Evidence |
|---|---|---|
| 普通多轮对话 | FUNCTIONAL | test_gateway_v02 |
| 长上下文 | FUNCTIONAL | 8K/32K benchmarks |
| 记忆 | PARTIAL | test_gateway_v02 |
| 历史搜索 | FOUNDATION | storage only |
| 文件分析 | FOUNDATION | file safety tests |
| PDF | NOT_STARTED | provider missing |
| 图片理解 | NOT_STARTED | provider missing |
| 图片生成编辑 | NOT_STARTED | provider missing |
| 音频转写 | NOT_STARTED | provider missing |
| 音频理解 | NOT_STARTED | provider missing |
| 视频分析 | NOT_STARTED | provider missing |
| Web Search | NOT_STARTED | approved tool missing |
| Deep Research | NOT_STARTED | workflow missing |
| Retrieval/RAG | NOT_STARTED | embedding missing |
| 代码生成 | PARTIAL | Qwen coding agent not validated |
| 代码审核 | FOUNDATION | independent-review process |
| Tool Calling | FUNCTIONAL | tool regressions |
| 确定性任务 | FUNCTIONAL | control tests |
| 项目上下文 | FOUNDATION | registry only |
| 定时任务 | NOT_STARTED | scheduler missing |
| 审批工作流 | FUNCTIONAL | test_control |
| Public/Private | FUNCTIONAL | isolation regressions |
| 数据导出删除 | FOUNDATION | memory deletion |
| 多模型路由 | FOUNDATION | test_quality_governance |
| Embeddings | NOT_STARTED | provider missing |
| 长期存储 | PARTIAL | SQLite |
| Browser/Computer | NOT_STARTED | scope missing |
| Structured outputs | FUNCTIONAL | JSON/tool tests |
| Usage/rate limiting | FUNCTIONAL | rate limit regression |
| Safety/secret firewall | FUNCTIONAL | security regressions |

<!-- CAPABILITY_MATRIX_METRICS: generated; do not edit manually -->
TOTAL_CAPABILITIES: 30
FUNCTIONAL_COUNT: 9
PRODUCTION_READY_COUNT: 0
FUNCTIONAL_COVERAGE: 30%
PRODUCTION_READY_COVERAGE: 0%
<!-- /CAPABILITY_MATRIX_METRICS -->
