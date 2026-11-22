"""Check the byte hashes of the tracked workbench handoff files."""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
workbench = root / "frontend/workbench"
manifest = workbench / "SHA256SUMS.json"
expected = json.loads(manifest.read_text(encoding="utf-8"))
tracked = subprocess.check_output(["git", "ls-files", "-z", "--", "."], cwd=workbench)
names = {name for name in tracked.decode().split("\0") if name} - {manifest.name}
errors = []
for name in sorted(names | expected.keys()):
    if name not in names:
        errors.append(f"{name}: stale manifest entry")
    elif name not in expected:
        errors.append(f"{name}: missing manifest entry")
    elif not (workbench / name).is_file():
        errors.append(f"{name}: missing file")
    elif hashlib.sha256((workbench / name).read_bytes()).hexdigest() != expected[name]:
        errors.append(f"{name}: SHA-256 mismatch")
if errors:
    raise SystemExit("\n".join(errors))
print(f"Workbench manifest: {len(names)} files verified")
