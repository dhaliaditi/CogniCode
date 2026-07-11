"""
collectors/prospector_col.py
Collects: Issue  (total issue count per file)
"""

import json
import sys
import subprocess
from pathlib import Path
from collections import defaultdict


class ProspectorCollector:

    def collect_batch(self, repo_root: Path) -> dict[str, dict]:
        print("[prospector] Running ...")
        cmd = [
            sys.executable, "-m", "prospector",
            "--output-format", "json",
            "--strictness", "verylow",
            "--without-tool", "pyroma",
            "--without-tool", "vulture",
            str(repo_root),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  cwd=str(repo_root), timeout=1800)
            raw = proc.stdout.strip()
            if not raw:
                print("  [prospector] No output -- check prospector is installed.")
                return {}
            data = json.loads(raw)
        except subprocess.TimeoutExpired:
            print("  [prospector] Timed out (600 s)")
            return {}
        except json.JSONDecodeError as exc:
            print(f"  [prospector] JSON parse error: {exc}")
            return {}
        except Exception as exc:
            print(f"  [prospector] Error: {exc}")
            return {}

        by_file: dict[str, dict] = defaultdict(lambda: {"Issue": 0})
        for msg in data.get("messages", []):
            fpath = msg.get("location", {}).get("path", "")
            try:
                key = Path(fpath).relative_to(repo_root).as_posix()
            except ValueError:
                key = fpath
            by_file[key]["Issue"] += 1

        print(f"[prospector] Done. Issues across {len(by_file)} files.")
        return dict(by_file)
