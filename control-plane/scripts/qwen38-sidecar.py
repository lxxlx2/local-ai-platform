#!/usr/bin/env python3
from pathlib import Path
import argparse
import sys

ROOT=Path("/Users/jerson/AI"); sys.path.insert(0,str(ROOT/"control-plane/src"))
from local_ai_control.services.qwen38_runtime import MODEL_PATH,PRIVATE_SPOOL_ROOT,serve

parser=argparse.ArgumentParser(); parser.add_argument("--port",type=int,default=8001); parser.add_argument("--model-path",type=Path,default=MODEL_PATH); parser.add_argument("--spool-root",type=Path,default=PRIVATE_SPOOL_ROOT)
args=parser.parse_args()
if not 1024<=args.port<=65535: raise SystemExit("invalid port")
try:
    serve(args.port,model_path=args.model_path,spool_root=args.spool_root)
except KeyboardInterrupt:
    pass
