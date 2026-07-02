#!/usr/bin/env python3
"""One-shot model fetcher for Ash.

Downloads (or reuses) GGUFs from the *shared* HuggingFace hub cache via
huggingface_hub, so Ash shares models with any other HF-ecosystem app on the
machine (LM Studio, other llama.cpp tools, …) instead of keeping private copies
under <userData>/models. If a requested file is already in the cache the
"download" returns instantly — that's the reuse win.

electron/main.js spawns this directly with the sidecar venv's python; it is not
part of the FastAPI server, so it runs during onboarding before the stack is up.

Usage:
  python download_models.py cache-dir
      Print the resolved HF hub cache directory (one line) and exit.

  python download_models.py download '<spec-json>'
      spec = {"roles": [{"key","label","repo","quant","mmproj": bool}, ...]}
      Streams line-delimited JSON on stdout:
        {"cache": "<hub cache dir>"}                         (once, first)
        {"model": "<label>", "fraction": 0|1}                (per file)
        {"result": {key: {"repo","main","mmproj"|null}}}     (once, last)
      Progress is coarse (0 before a file, 1 after) — hf_hub_download exposes no
      per-byte callback. Non-result chatter/warnings go to stderr.
"""

import json
import sys

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.constants import HF_HUB_CACHE
from huggingface_hub.utils import disable_progress_bars

# tqdm would clutter the logs; we report our own coarse progress instead.
disable_progress_bars()


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def pick_file(files, quant, mmproj):
    """Mirror electron/main.js resolveHfFile: choose the GGUF for a role."""
    ggufs = [f for f in files if f.lower().endswith(".gguf")]
    if mmproj:
        return next((f for f in ggufs if "mmproj" in f.lower()), None)
    q = quant.lower()
    return (
        next((f for f in ggufs if q in f.lower() and "mmproj" not in f.lower()), None)
        or next((f for f in ggufs if "mmproj" not in f.lower()), None)
    )


def fetch(repo, filename, label):
    emit({"model": label, "fraction": 0})
    path = hf_hub_download(repo_id=repo, filename=filename)  # reuses cache if present
    emit({"model": label, "fraction": 1})
    return path


def download(spec):
    emit({"cache": HF_HUB_CACHE})
    api = HfApi()
    result = {}
    for role in spec["roles"]:
        repo, quant = role["repo"], role["quant"]
        files = api.list_repo_files(repo)
        main_file = pick_file(files, quant, mmproj=False)
        if not main_file:
            raise SystemExit(f"no GGUF matching quant {quant} in {repo}")
        entry = {"repo": repo, "main": fetch(repo, main_file, role["label"]), "mmproj": None}
        if role.get("mmproj"):
            mmproj_file = pick_file(files, quant, mmproj=True)
            if not mmproj_file:
                raise SystemExit(f"no mmproj GGUF in {repo}")
            entry["mmproj"] = fetch(repo, mmproj_file, role["label"] + " (vision projector)")
        result[role["key"]] = entry
    emit({"result": result})


def main(argv):
    if len(argv) >= 2 and argv[1] == "cache-dir":
        print(HF_HUB_CACHE)
        return
    if len(argv) >= 3 and argv[1] == "download":
        download(json.loads(argv[2]))
        return
    raise SystemExit("usage: download_models.py cache-dir | download <spec-json>")


if __name__ == "__main__":
    main(sys.argv)
