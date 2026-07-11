#!/usr/bin/env python3
"""
collect.py  --  Static Analysis Metrics Collector
==================================================

Clones a GitHub repository, runs five static analysis tools, and
produces a single CSV with one row per Python file.

Quick start (pure-Python tools only):
  python collect.py --repo https://github.com/scikit-learn/scikit-learn

Full run with SonarQube + Understand:
  python collect.py --repo https://github.com/scikit-learn/scikit-learn \\
      --sonar-project scikit_learn \\
      --understand-bin /opt/scitools/bin/linux64/und

All settings can be put in config.yaml so you do not have to repeat
flags every time.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Optional: PyYAML for config loading
# ---------------------------------------------------------------------------
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# ---------------------------------------------------------------------------
# Our collectors
# ---------------------------------------------------------------------------
from collectors import (
    RadonASTCollector,
    PylintCollector,
    ProspectorCollector,
    SonarQubeCollector,
    UnderstandCollector,
)


# ===========================================================================
# Configuration
# ===========================================================================

def load_config(path: str = "config.yaml") -> dict:
    defaults = {
        "sonarqube": {
            "host":           "http://localhost:9000",
            "admin_user":     "admin",
            "admin_password": "admin",
            "token":          "",
        },
        "understand": {"und_bin": ""},
        "output":    {"csv": "metrics.csv", "keep_clone": False},
    }
    if not HAS_YAML:
        return defaults

    cfg_path = Path(path)
    if not cfg_path.exists():
        return defaults

    try:
        with open(cfg_path) as f:
            user_cfg = yaml.safe_load(f) or {}
        # Deep merge user values over defaults
        for section, values in user_cfg.items():
            if section in defaults and isinstance(values, dict):
                defaults[section].update(values)
        return defaults
    except Exception as exc:
        print(f"[config] Could not load {path}: {exc}")
        return defaults


# ===========================================================================
# Git helpers
# ===========================================================================

def clone_repo(url: str, dest: str) -> Path:
    dest_path = Path(dest)
    print(f"[clone] Cloning {url} ...")
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"[clone] git clone failed:\n{exc.stderr}")
        sys.exit(1)
    print(f"[clone] Done -> {dest_path}")
    return dest_path


def find_python_files(root: Path) -> list[Path]:
    skip = {
        ".git", "__pycache__", ".tox", ".venv", "venv", "env",
        "node_modules", "dist", "build", ".eggs", ".mypy_cache",
    }
    files = sorted(
        p for p in root.rglob("*.py")
        if not any(part in skip for part in p.parts)
    )
    print(f"[discover] {len(files)} Python files found.")
    return files


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def slugify(url: str) -> str:
    """Turn a GitHub URL into a safe project key for SonarQube."""
    name = url.rstrip("/").split("/")[-1]
    name = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    return name[:100]


# ===========================================================================
# Merge
# ===========================================================================

KEEP_COLUMNS = [
    "File", "ProjectName", "Issue",
    "ClassName", "CyclomaticComplexity", "ComplexityRank", "MaintainabilityIndex",
    "Comments", "Multi", "Blanks", "PublicMethodsCount", "InstanceAttributesCount",
    "CommentRatio", "HalsteadEffort", "HalsteadDifficulty", "HalsteadVocabulary",
    "PylintIssues_C", "PylintIssues_R", "PylintIssues_W",
    "PylintIssues_E", "PylintIssues_F",
    "MissingDocstrings", "DeadCodeWarnings", "DuplicateCodeWarnings",
    "bugs", "code_smells", "cognitive_complexity",
    "duplicated_blocks", "duplicated_lines",
    "effort_to_reach_maintainability_rating_a",
    "reliability_rating", "reliability_remediation_effort",
    "security_rating", "security_remediation_effort",
    "statements", "sqale_index", "sqale_debt_ratio",
    "comment_lines_density", "coverage",
    "Kind", "Name",
    "AvgCountLine", "AvgCountLineBlank", "AvgCountLineCode", "AvgCountLineComment",
    "AvgCyclomatic", "CCViolDensityCode", "CCViolDensityLine",
    "CountCCViol", "CountCCViolType",
    "CountClassBase", "CountClassCoupled", "CountClassCoupledModified", "CountClassDerived",
    "CountDeclExecutableUnit", "CountDeclInstanceVariable",
    "CountDeclMethod", "CountDeclMethodAll",
    "Cyclomatic", "MaxCyclomatic", "MaxInheritanceTree", "MaxNesting",
    "RatioCommentToCode", "SumCyclomatic",
]


def merge_all(
    py_files:         list[Path],
    repo_root:        Path,
    project_name:     str,
    radon_rows:       dict,
    pylint_rows:      dict,
    prospector_rows:  dict,
    sonar_rows:       dict,
    understand_rows:  dict,
) -> pd.DataFrame:

    # Understand uses absolute paths -- build a lookup by filename only as fallback
    und_by_basename = {}
    for k, v in understand_rows.items():
        und_by_basename[Path(k).name] = v

    records = []
    for f in py_files:
        key = rel(f, repo_root)
        row: dict = {"File": key, "ProjectName": project_name}
        row.update(radon_rows.get(key, {}))
        row.update(pylint_rows.get(key, {}))
        row.update(prospector_rows.get(key, {}))
        row.update(sonar_rows.get(key, {}))
        # Try exact key first, then basename fallback for Understand
        if key in understand_rows:
            row.update(understand_rows[key])
        elif Path(key).name in und_by_basename:
            row.update(und_by_basename[Path(key).name])
        records.append(row)

    df = pd.DataFrame(records)

    for col in KEEP_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[KEEP_COLUMNS]

    return df


# ===========================================================================
# CLI
# ===========================================================================

def parse_args(cfg: dict):
    sq  = cfg["sonarqube"]
    und = cfg["understand"]
    out = cfg["output"]

    p = argparse.ArgumentParser(
        description="Collect static analysis metrics from a GitHub repository.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--repo", required=True,
                   help="GitHub URL, e.g. https://github.com/user/project")
    p.add_argument("--output", default=out["csv"],
                   help=f"Output CSV (default: {out['csv']})")
    p.add_argument("--clone-dir", default=None,
                   help="Directory to clone into (default: auto temp dir)")
    p.add_argument("--keep-clone", action="store_true", default=out["keep_clone"],
                   help="Keep the cloned repo after collection")
    p.add_argument("--config", default="config.yaml",
                   help="Config file (default: config.yaml)")

    sq_g = p.add_argument_group("SonarQube")
    sq_g.add_argument("--sonar-host",     default=sq["host"])
    sq_g.add_argument("--sonar-user",     default=sq["admin_user"])
    sq_g.add_argument("--sonar-password", default=sq["admin_password"])
    sq_g.add_argument("--sonar-token",    default=sq["token"],
                      help="Pre-existing SonarQube token (skips admin auth)")
    sq_g.add_argument("--sonar-project",  default=None,
                      help="SonarQube project key (auto-derived from repo name if absent)")
    sq_g.add_argument("--compose-file",   default=None,
                      help="Path to docker-compose.yml for SonarQube")
    sq_g.add_argument("--skip-sonarqube", action="store_true")

    ud_g = p.add_argument_group("Understand")
    ud_g.add_argument("--understand-bin", default=und["und_bin"],
                      help="Path to the `und` binary")
    ud_g.add_argument("--skip-understand", action="store_true")

    skip_g = p.add_argument_group("Skip other tools")
    skip_g.add_argument("--skip-pylint",     action="store_true")
    skip_g.add_argument("--skip-radon",      action="store_true")
    skip_g.add_argument("--skip-prospector", action="store_true")

    return p.parse_args()


# ===========================================================================
# Main
# ===========================================================================

def main():
    cfg  = load_config()
    args = parse_args(cfg)

    # Derive project key from repo URL if not given
    project_key = args.sonar_project or slugify(args.repo)
    project_name = slugify(args.repo)

    # Clone
    use_tmp    = args.clone_dir is None
    clone_root = tempfile.mkdtemp(prefix="static_metrics_") if use_tmp else args.clone_dir

    print("\n" + "=" * 60)
    print("  Static Analysis Metrics Collector")
    print("=" * 60)

    try:
        repo_root = clone_repo(args.repo, clone_root)
        py_files  = find_python_files(repo_root)

        if not py_files:
            print("No Python files found. Exiting.")
            sys.exit(1)

        # ----------------------------------------------------------------
        # Radon + AST
        # ----------------------------------------------------------------
        radon_rows: dict = {}
        if not args.skip_radon:
            print("\n--- Radon + AST ---")
            col = RadonASTCollector()
            for i, f in enumerate(py_files, 1):
                if i % 50 == 0 or i == len(py_files):
                    print(f"  {i}/{len(py_files)} files processed")
                radon_rows[rel(f, repo_root)] = col.collect(f)

        # ----------------------------------------------------------------
        # Pylint
        # ----------------------------------------------------------------
        pylint_rows: dict = {}
        if not args.skip_pylint:
            print("\n--- Pylint ---")
            pylint_rows = PylintCollector().collect_batch(py_files, repo_root)

        # ----------------------------------------------------------------
        # Prospector
        # ----------------------------------------------------------------
        prospector_rows: dict = {}
        if not args.skip_prospector:
            print("\n--- Prospector ---")
            prospector_rows = ProspectorCollector().collect_batch(repo_root)

        # ----------------------------------------------------------------
        # SonarQube
        # ----------------------------------------------------------------
        sonar_rows: dict = {}
        if not args.skip_sonarqube:
            print("\n--- SonarQube ---")
            sonar_rows = SonarQubeCollector(
                host           = args.sonar_host,
                admin_user     = args.sonar_user,
                admin_password = args.sonar_password,
                token          = args.sonar_token,
                project_key    = project_key,
                compose_file   = args.compose_file,
            ).collect_batch(repo_root)

        # ----------------------------------------------------------------
        # Understand
        # ----------------------------------------------------------------
        understand_rows: dict = {}
        if not args.skip_understand:
            print("\n--- Understand ---")
            understand_rows = UnderstandCollector(
                und_bin=args.understand_bin,
            ).collect_batch(repo_root)

        # ----------------------------------------------------------------
        # Merge + save
        # ----------------------------------------------------------------
        print("\n--- Merging results ---")
        df = merge_all(
            py_files, repo_root,
            project_name,
            radon_rows, pylint_rows, prospector_rows,
            sonar_rows, understand_rows,
        )

        out_path = Path(args.output)
        df.to_csv(out_path, index=False)

        print("\n" + "=" * 60)
        print(f"  Done!")
        print(f"  Files analyzed : {len(df)}")
        print(f"  Metrics columns: {len(df.columns) - 1}")
        print(f"  Output CSV     : {out_path.resolve()}")
        print("=" * 60)

        # Print tool coverage summary
        print("\nTool coverage (files with at least one metric):")
        radon_coverage      = sum(1 for k in radon_rows     if radon_rows[k])
        pylint_coverage     = sum(1 for k in pylint_rows    if pylint_rows[k])
        prospector_coverage = sum(1 for k in prospector_rows if prospector_rows[k])
        sonar_coverage      = len(sonar_rows)
        understand_coverage = len(understand_rows)
        print(f"  Radon/AST   : {radon_coverage}/{len(py_files)}")
        print(f"  Pylint      : {pylint_coverage}/{len(py_files)}")
        print(f"  Prospector  : {prospector_coverage}/{len(py_files)}")
        print(f"  SonarQube   : {sonar_coverage}/{len(py_files)}")
        print(f"  Understand  : {understand_coverage}/{len(py_files)}")

    finally:
        if use_tmp and not args.keep_clone:
            print(f"\n[cleanup] Removing cloned repo: {clone_root}")
            shutil.rmtree(clone_root, ignore_errors=True)


if __name__ == "__main__":
    main()
