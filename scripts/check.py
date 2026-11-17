"""Run both offline suites from any working directory after creating demo data."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
env = dict(os.environ, FREIGHT_PYTHON=sys.executable, PYTHONUTF8='1')
subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'prototype/tests', '-t', 'prototype', '-v'], cwd=root, env=env, check=True)
node = shutil.which('node')
if not node:
    raise SystemExit('Install Node.js 22+ to run Harness tests.')
subprocess.run([node, '--test', 'prototype/harness/freight-plugin.test.mjs', 'prototype/agent/run.test.mjs'], cwd=root, env=env, check=True)
