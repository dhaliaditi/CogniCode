"""
collectors/pylint_col.py
Collects: PylintIssues_C/R/W/E/F, MissingDocstrings,
          DeadCodeWarnings, DuplicateCodeWarnings
"""

import json
import sys
import subprocess
from pathlib import Path
from collections import defaultdict


class PylintCollector:

    _DOCSTRING_IDS = {"C0114", "C0115", "C0116", "W0107"}
    _DEADCODE_IDS  = {"W0611", "W0612", "W0401", "W0614"}
    _DUPLICATE_IDS = {"R0801"}

    def collect_batch(self, py_files: list[Path], repo_root: Path) -> dict[str, dict]:
        if not py_files:
            return {}

        print(f"[pylint] Running on {len(py_files)} files ...")
        cmd = [
            sys.executable, "-m", "pylint",
            "--output-format=json",
            "--disable=all",
            "--enable=C,R,W,E,F",
            "--jobs=0",
            "--score=no",
        ] + [str(f) for f in py_files]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  cwd=str(repo_root), timeout=600)
            raw = proc.stdout.strip()
            if not raw:
                return {}
            messages = json.loads(raw)
        except subprocess.TimeoutExpired:
            print("  [pylint] Timed out (600 s)")
            return {}
        except json.JSONDecodeError as exc:
            print(f"  [pylint] JSON parse error: {exc}")
            return {}
        except Exception as exc:
            print(f"  [pylint] Unexpected error: {exc}")
            return {}

        by_file: dict[str, dict] = defaultdict(lambda: {
            "PylintIssues_C": 0, "PylintIssues_R": 0,
            "PylintIssues_W": 0, "PylintIssues_E": 0,
            "PylintIssues_F": 0,
            "MissingDocstrings":    0,
            "DeadCodeWarnings":     0,
            "DuplicateCodeWarnings": 0,
        })

        col_map = {"C": "PylintIssues_C", "R": "PylintIssues_R",
                   "W": "PylintIssues_W", "E": "PylintIssues_E",
                   "F": "PylintIssues_F"}

        for msg in messages:
            fpath = msg.get("path", "")
            try:
                key = Path(fpath).relative_to(repo_root).as_posix()
            except ValueError:
                key = fpath

            cat = msg.get("type", "").upper()[:1]
            mid = msg.get("message-id", "").upper()

            if cat in col_map:
                by_file[key][col_map[cat]] += 1
            if mid in self._DOCSTRING_IDS:
                by_file[key]["MissingDocstrings"] += 1
            if mid in self._DEADCODE_IDS:
                by_file[key]["DeadCodeWarnings"] += 1
            if mid in self._DUPLICATE_IDS:
                by_file[key]["DuplicateCodeWarnings"] += 1

        print(f"[pylint] Done. Issues found across {len(by_file)} files.")
        return dict(by_file)
