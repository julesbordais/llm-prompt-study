"""Checks the key (remaining credit) and that the model slugs in .env exist on OpenRouter."""
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("OPENROUTER_API_KEY") or sys.exit("OPENROUTER_API_KEY missing in .env")
h = {"Authorization": f"Bearer {key}"}
info = requests.get("https://openrouter.ai/api/v1/key", headers=h, timeout=20)
print("Key status:", info.status_code, info.json().get("data", info.text))
models = {m["id"]: m for m in requests.get("https://openrouter.ai/api/v1/models", timeout=20).json()["data"]}
for spec in [*os.getenv("GEN_MODELS", "").split(","), os.getenv("JUDGE_MODEL", "")]:
    slug = spec.strip().split(":", 1)[-1]
    if not slug:
        continue
    m = models.get(slug)
    if m:
        p = m.get("pricing", {})
        print(f"OK   {slug}  in ${float(p.get('prompt', 0))*1e6:.2f}/Mtok  out ${float(p.get('completion', 0))*1e6:.2f}/Mtok")
    else:
        near = [k for k in models if slug.split("/")[0] in k][:8]
        print(f"MISSING {slug} -> try one of: {near}")
