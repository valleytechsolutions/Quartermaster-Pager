"""Copy Quartermaster's core into the payload from a Quartermaster commit or tag, read-only.

    python3 tools/sync_core.py [REF]

REF defaults to the one in core.lock. Node firmware images are left out: Hak5's library takes no binaries.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "core.lock"
TARGET = ROOT / "payload" / "quartermaster" / "quartermaster"
SOURCE = "https://github.com/valleytechsolutions/Quartermaster.git"
SKIP = ("__pycache__", "*.pyc", "*.bin")


def main(argv):
    ref = argv[1] if len(argv) > 1 else LOCK.read_text().split()[0]
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init", "-q", tmp], check=True)
        subprocess.run(["git", "-C", tmp, "fetch", "-q", "--depth", "1", SOURCE, ref], check=True)
        subprocess.run(["git", "-C", tmp, "checkout", "-q", "FETCH_HEAD"], check=True)
        commit = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"], check=True, capture_output=True,
                                text=True).stdout.strip()
        version = (Path(tmp) / "quartermaster" / "__init__.py").read_text().split('__version__ = "')[1].split('"')[0]
        if TARGET.exists():
            shutil.rmtree(TARGET)
        shutil.copytree(Path(tmp) / "quartermaster", TARGET, ignore=shutil.ignore_patterns(*SKIP))
    LOCK.write_text("%s %s\n" % (commit, version))
    print("Quartermaster %s (%s) copied" % (version, commit[:7]))


if __name__ == "__main__":
    main(sys.argv)
