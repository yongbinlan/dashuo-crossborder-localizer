#!/usr/bin/env python3
"""Explicit public-model installation, never called by media processing commands.

Uses official revision-pinned HF URLs and validates LFS SHA256/git blob identity.
No auth discovery, no credential persistence, no automatic retry or target upload.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request

MODELS={
    "chatterbox":("ResembleAI/chatterbox",["ve.pt","t3_mtl23ls_v2.safetensors","s3gen.pt","grapheme_mtl_merged_expanded_v1.json","conds.pt","Cangjie5_TC.json"]),
    "whisper-tiny":("Systran/faster-whisper-tiny",["config.json","model.bin","tokenizer.json","vocabulary.txt"]),
}


def check(path,entry):
    if not path.is_file() or path.stat().st_size!=entry["size"]:return False
    lfs=entry.get("lfs",{})
    hasher=hashlib.sha256() if lfs else hashlib.sha1()
    if not lfs:hasher.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as f:
        for block in iter(lambda:f.read(4*1024*1024),b""):hasher.update(block)
    expected=lfs.get("sha256") or entry["blobId"]
    return hasher.hexdigest()==expected


def main():
    p=argparse.ArgumentParser();p.add_argument("model",choices=MODELS);p.add_argument("--output",required=True)
    args=p.parse_args();repo,names=MODELS[args.model]
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}?blobs=true",timeout=30) as r:
        info=json.load(r)
    revision=info["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}",revision):raise RuntimeError("Invalid upstream revision")
    entries={x["rfilename"]:x for x in info["siblings"]}
    if any(n not in entries for n in names):raise RuntimeError("Required model file missing from official inventory")
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    def download(name):
        entry=entries[name];destination=output/name
        if check(destination,entry):return {"file":name,"status":"existing_identity_checked"}
        if destination.exists():raise RuntimeError(f"Existing file identity differs: {name}; preserve it before replacement")
        partial=destination.with_suffix(destination.suffix+".direct.part")
        offset=partial.stat().st_size if partial.exists() else 0
        req=urllib.request.Request(f"https://huggingface.co/{repo}/resolve/{revision}/{name}?download=true",
                                   headers={"Range":f"bytes={offset}-"} if offset else {})
        with urllib.request.urlopen(req,timeout=30) as response:
            if offset and response.status==206:
                if not response.headers.get("Content-Range","").startswith(f"bytes {offset}-"):
                    raise RuntimeError("Invalid partial response")
                mode="ab"
            else:offset=0;mode="wb"
            size=offset;last_report=size
            with partial.open(mode) as f:
                while block:=response.read(4*1024*1024):
                    f.write(block);size+=len(block)
                    if size-last_report>=128*1024*1024:
                        print(f"{name}: {size//(1024*1024)} MiB",flush=True);last_report=size
        if not check(partial,entry):raise RuntimeError(f"Incomplete/model identity mismatch: {name}")
        os.replace(partial,destination)
        return {"file":name,"status":"downloaded_identity_checked","bytes":entry["size"]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(download,names))
    receipt={"repo":repo,"revision":revision,"files":results,"model_loaded":False}
    (output/"download-receipt.json").write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(receipt,indent=2),flush=True)


if __name__=="__main__":main()
