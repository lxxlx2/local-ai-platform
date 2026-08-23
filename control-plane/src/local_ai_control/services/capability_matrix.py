"""The sole source of truth for capability statuses and coverage metrics."""
from dataclasses import dataclass
from enum import StrEnum

class CapabilityStatus(StrEnum):
    NOT_STARTED="NOT_STARTED"; FOUNDATION="FOUNDATION"; PARTIAL="PARTIAL"; FUNCTIONAL="FUNCTIONAL"; PRODUCTION_READY="PRODUCTION_READY"

@dataclass(frozen=True)
class Capability:
    name: str; status: CapabilityStatus; evidence: str

CAPABILITIES = (
    Capability("普通多轮对话", CapabilityStatus.FUNCTIONAL, "test_gateway_v02"),
    Capability("长上下文", CapabilityStatus.FUNCTIONAL, "8K/32K benchmarks"),
    Capability("记忆", CapabilityStatus.PARTIAL, "test_gateway_v02"),
    Capability("历史搜索", CapabilityStatus.FOUNDATION, "storage only"),
    Capability("文件分析", CapabilityStatus.FOUNDATION, "file safety tests"),
    Capability("PDF", CapabilityStatus.NOT_STARTED, "provider missing"),
    Capability("图片理解", CapabilityStatus.FUNCTIONAL, "Qwen3.8 owner-private vision runtime tests"),
    Capability("图片生成编辑", CapabilityStatus.FOUNDATION, "owner-only provider boundary"),
    Capability("音频转写", CapabilityStatus.FOUNDATION, "STT provider boundary"),
    Capability("音频理解", CapabilityStatus.FOUNDATION, "audio routing and quotas"),
    Capability("视频分析", CapabilityStatus.FOUNDATION, "video routing and media jobs"),
    Capability("Web Search", CapabilityStatus.FOUNDATION, "safe fetch/search adapters"),
    Capability("Deep Research", CapabilityStatus.FOUNDATION, "web evidence contracts"),
    Capability("Retrieval/RAG", CapabilityStatus.FOUNDATION, "embedding/rerank contracts"),
    Capability("代码生成", CapabilityStatus.PARTIAL, "Qwen coding agent not validated"),
    Capability("代码审核", CapabilityStatus.FOUNDATION, "independent-review process"),
    Capability("Tool Calling", CapabilityStatus.FUNCTIONAL, "tool regressions"),
    Capability("确定性任务", CapabilityStatus.FUNCTIONAL, "control tests"),
    Capability("项目上下文", CapabilityStatus.FOUNDATION, "registry only"),
    Capability("定时任务", CapabilityStatus.NOT_STARTED, "scheduler missing"),
    Capability("审批工作流", CapabilityStatus.FUNCTIONAL, "test_control"),
    Capability("Public/Private", CapabilityStatus.FUNCTIONAL, "isolation regressions"),
    Capability("数据导出删除", CapabilityStatus.FOUNDATION, "memory deletion"),
    Capability("多模型路由", CapabilityStatus.FUNCTIONAL, "test_runtime_integration"),
    Capability("Embeddings", CapabilityStatus.FOUNDATION, "Qwen3 8B providers registered"),
    Capability("长期存储", CapabilityStatus.PARTIAL, "SQLite"),
    Capability("Browser/Computer", CapabilityStatus.FOUNDATION, "owner-only browser boundary"),
    Capability("Structured outputs", CapabilityStatus.FUNCTIONAL, "JSON/tool tests"),
    Capability("Usage/rate limiting", CapabilityStatus.FUNCTIONAL, "rate limit regression"),
    Capability("Safety/secret firewall", CapabilityStatus.FUNCTIONAL, "security regressions"),
)

def summary(capabilities=CAPABILITIES):
    total = len(capabilities)
    functional = sum(item.status in {CapabilityStatus.FUNCTIONAL, CapabilityStatus.PRODUCTION_READY} for item in capabilities)
    production = sum(item.status is CapabilityStatus.PRODUCTION_READY for item in capabilities)
    return {"TOTAL_CAPABILITIES": total, "FUNCTIONAL_COUNT": functional, "PRODUCTION_READY_COUNT": production, "FUNCTIONAL_COVERAGE": functional / total, "PRODUCTION_READY_COVERAGE": production / total}

def render_metrics(capabilities=CAPABILITIES):
    values = summary(capabilities)
    return "\n".join(("<!-- CAPABILITY_MATRIX_METRICS: generated; do not edit manually -->", f"TOTAL_CAPABILITIES: {values['TOTAL_CAPABILITIES']}", f"FUNCTIONAL_COUNT: {values['FUNCTIONAL_COUNT']}", f"PRODUCTION_READY_COUNT: {values['PRODUCTION_READY_COUNT']}", f"FUNCTIONAL_COVERAGE: {values['FUNCTIONAL_COVERAGE']:.0%}", f"PRODUCTION_READY_COVERAGE: {values['PRODUCTION_READY_COVERAGE']:.0%}", "<!-- /CAPABILITY_MATRIX_METRICS -->"))

def render_document(capabilities=CAPABILITIES):
    rows = "\n".join(f"| {item.name} | {item.status} | {item.evidence} |" for item in capabilities)
    return "\n".join(("# Capability Matrix", "", "Generated solely from `capability_matrix.py`. FUNCTIONAL coverage includes PRODUCTION_READY; production coverage counts only PRODUCTION_READY.", "", "| Capability | Status | Evidence |", "|---|---|---|", rows, "", render_metrics(capabilities), ""))
