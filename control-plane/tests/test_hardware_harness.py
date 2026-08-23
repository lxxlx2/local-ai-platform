from pathlib import Path
import ast

def test_qwen38_hardware_harness_is_static_safe_and_metrics_only():
    source=Path("/Users/jerson/AI/scripts/qualify-qwen38.py").read_text()
    ast.parse(source)
    assert "shell=True" not in source and "0.0.0.0" not in source
    assert "MODEL_SNAPSHOT_INCOMPLETE" in source and "CURRENT_OMLX_IS_RUNNING_REFUSE_SECOND_HEAVY_MODEL" in source
    assert '"tests": metrics' in source
    report_section=source[source.index("report = {"):]
    assert '"output_chars"' not in report_section and '"output"' not in report_section
