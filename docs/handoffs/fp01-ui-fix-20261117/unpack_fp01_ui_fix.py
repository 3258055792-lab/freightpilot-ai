# Unpack readable UTF-8 sources into a NEW directory; never overwrite existing work.
import hashlib, json
from pathlib import Path
src = Path(__file__).with_name("fp01-ui-fix-sources.json")
data = json.loads(src.read_text(encoding="utf-8"))
assert data["format"] == "utf8-source-bundle-v1"
allowed = {'dist/demo-rates.json', 'HANDOFF.md', 'dist/core.js', 'TEST-RESULTS.txt', 'tests/workflow.test.mjs', 'package.json', 'dist/style.css', 'fixtures.json', 'dist/app.js', 'README.md', 'SHA256SUMS.json', 'tests/ui-regression.test.mjs', 'dist/index.html'}
assert len(data["files"]) == len(allowed)
assert {f["path"] for f in data["files"]} == allowed
for f in data["files"]:
    if hashlib.sha256(f["content"].encode("utf-8")).hexdigest() != f["sha256"]:
        raise ValueError("Hash mismatch: " + f["path"])
root = Path(__file__).with_name("freight-agent-workbench-fp01-ui-fix")
root.mkdir(exist_ok=False)
for f in data["files"]:
    p = root / f["path"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(f["content"].encode("utf-8"))
print("Extracted verified sources to", root)
print("Read HANDOFF.md; then run: py -m http.server 4173 --bind 127.0.0.1 --directory dist")
