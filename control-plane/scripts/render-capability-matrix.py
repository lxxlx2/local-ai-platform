#!/usr/bin/env python3
from pathlib import Path
from local_ai_control.services.capability_matrix import render_document

Path(__file__).parents[2].joinpath("docs", "CAPABILITY_MATRIX.md").write_text(render_document())
