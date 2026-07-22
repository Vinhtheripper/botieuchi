#!/usr/bin/env python3
"""Generate the scoring-free manifest bundled with the Vite frontend."""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.database import init_db
from app.main import public_manifest

init_db()
target=ROOT.parent/"khaosat-web"/"public"/"survey-manifest.json"
target.parent.mkdir(parents=True,exist_ok=True)
manifest=public_manifest()
serialized=json.dumps(manifest,ensure_ascii=False,separators=(",",":"))
for forbidden in ('"scores"','"scores_json"','"variables_json"','"weights"'):
    if forbidden in serialized:raise SystemExit(f"Refusing to export hidden field: {forbidden}")
target.write_text(serialized+"\n",encoding="utf-8")
print(json.dumps({"target":str(target),"version":manifest["version"],"questions":len(manifest["questions"]),"branches":len(manifest["branches"])},ensure_ascii=False))
