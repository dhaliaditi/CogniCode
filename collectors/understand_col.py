"""
collectors/understand_col.py
Uses und command line. Metrics are written to /tmp/<dbname>.csv automatically.
"""

import shutil
import subprocess
from pathlib import Path
import pandas as pd

UNDERSTAND_METRICS = [
    "AvgCountLine", "AvgCountLineBlank", "AvgCountLineCode",
    "AvgCountLineComment", "AvgCyclomatic",
    "CCViolDensityCode", "CCViolDensityLine",
    "CountCCViol", "CountCCViolType",
    "CountClassBase", "CountClassCoupled", "CountClassCoupledModified",
    "CountClassDerived", "CountDeclClass", "CountDeclExecutableUnit",
    "CountDeclFile", "CountDeclFunction", "CountDeclInstanceMethod",
    "CountDeclInstanceVariable", "CountDeclMethod", "CountDeclMethodAll",
    "CountLine", "CountLineBlank", "CountLineCode", "CountLineCodeDecl",
    "CountLineCodeExe", "CountLineComment",
    "CountStmt", "CountStmtDecl", "CountStmtExe",
    "Cyclomatic", "MaxCyclomatic", "MaxInheritanceTree", "MaxNesting",
    "RatioCommentToCode", "SumCyclomatic",
]


class UnderstandCollector:

    def __init__(self, und_bin: str = ""):
        self.und_bin = und_bin if und_bin else (shutil.which("und") or "und")

    def collect_batch(self, repo_root: Path) -> dict[str, dict]:
        if not self._available():
            print(f"[understand] und binary not found at '{self.und_bin}'.")
            return {}

        udb = Path("/tmp/und_project.und")
        if udb.exists():
            shutil.rmtree(udb)

        # und metrics writes to /tmp/und_project.csv by default
        csv_out = Path("/tmp/und_project.csv")
        if csv_out.exists():
            csv_out.unlink()

        print(f"[understand] Using und binary: {self.und_bin}")

        if not self._run(["create", "-db", str(udb), "-languages", "python"], "Creating project database"):
            return {}

        if not self._run(["add", str(repo_root), str(udb)], "Adding source files"):
            return {}

        if not self._run(["analyze", str(udb)], "Analyzing", timeout=1800):
            return {}

        if not self._run(["metrics", str(udb)], "Exporting metrics"):
            return {}

        if not csv_out.exists():
            print(f"  [understand] Expected CSV not found at {csv_out}")
            return {}

        return self._parse(csv_out, repo_root)

    def _available(self) -> bool:
        return Path(self.und_bin).exists() or shutil.which(self.und_bin) is not None

    def _run(self, args: list, label: str, timeout: int = 600) -> bool:
        cmd = [self.und_bin] + args
        print(f"  [understand] {label} ...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            print(f"  [understand] {label} failed (exit {result.returncode}).")
            if result.stderr:
                print(f"  {result.stderr[:400]}")
            return False
        return True

    def _parse(self, csv_out: Path, repo_root: Path) -> dict[str, dict]:
        try:
            df = pd.read_csv(csv_out, low_memory=False)
        except Exception as exc:
            print(f"  [understand] Cannot read CSV: {exc}")
            return {}

        # Keep only File kind rows
        if "Kind" in df.columns:
            df = df[df["Kind"].astype(str).str.lower().str.contains("file")]

        # Find the file path column
        file_col = next(
            (c for c in df.columns if c.strip().lower() == "file"),
            next((c for c in df.columns if c.strip().lower() == "name"), None),
        )
        if file_col is None:
            print(f"  [understand] No file/name column found. Columns: {list(df.columns)}")
            return {}

        # Filter to Python files only
        df = df[df[file_col].astype(str).str.endswith(".py")]

        by_file: dict[str, dict] = {}
        for _, row in df.iterrows():
            raw_path = str(row[file_col])
            try:
                key = Path(raw_path).relative_to(repo_root).as_posix()
            except ValueError:
                key = raw_path

            record: dict = {"Kind": row.get("Kind", ""), "Name": row.get("Name", "")}
            for m in UNDERSTAND_METRICS:
                if m in df.columns:
                    val = row[m]
                    record[m] = None if (isinstance(val, float) and __import__("math").isnan(val)) else val

            by_file[key] = record

        print(f"[understand] Parsed metrics for {len(by_file)} Python files.")
        return by_file
