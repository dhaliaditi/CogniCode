"""
collectors/sonarqube_col.py

Full SonarQube pipeline:
  1. Detect / start SonarQube via Docker if it is not already running.
  2. Wait until the server is healthy (can take 2-3 min on first boot).
  3. Bootstrap an API token (first run only; cached in .sonar_token).
  4. Create or verify the SonarQube project.
  5. Write sonar-project.properties and run sonar-scanner.
  6. Poll the background CE task until analysis is complete.
  7. Fetch per-file metrics via the Web API.

Collects: bugs, code_smells, cognitive_complexity, complexity,
          duplicated_blocks, duplicated_lines,
          effort_to_reach_maintainability_rating_a, functions,
          reliability_rating, reliability_remediation_effort,
          security_rating, security_remediation_effort,
          statements, sqale_index, sqale_debt_ratio,
          comment_lines_density, coverage
"""

import re
import shutil
import subprocess
import time
from pathlib import Path

import requests


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TOKEN_CACHE_FILE = ".sonar_token"
_SCANNER_WAIT_S   = 600      # max seconds to wait for sonar-scanner
_BOOT_WAIT_S      = 300      # max seconds to wait for SonarQube to boot
_TASK_WAIT_S      = 300      # max seconds to wait for CE task

METRIC_KEYS = [
    "bugs", "code_smells", "cognitive_complexity", "complexity",
    "duplicated_blocks", "duplicated_lines",
    "effort_to_reach_maintainability_rating_a", "functions",
    "reliability_rating", "reliability_remediation_effort",
    "security_rating", "security_remediation_effort",
    "statements", "sqale_index", "sqale_debt_ratio",
    "comment_lines_density", "coverage",
]


class SonarQubeCollector:
    """End-to-end SonarQube collection.  Call collect_batch()."""

    def __init__(
        self,
        host: str          = "http://localhost:9000",
        admin_user: str    = "admin",
        admin_password: str = "admin",
        token: str         = "",
        project_key: str   = "",
        compose_file: str  = None,
    ):
        self.host           = host.rstrip("/")
        self.admin_user     = admin_user
        self.admin_password = admin_password
        self._token         = token          # may be empty
        self.project_key    = project_key
        self.compose_file   = compose_file   # path to docker-compose.yml

        self._session: requests.Session | None = None  # built after auth

    # ===================================================================
    # Public entry point
    # ===================================================================

    def collect_batch(self, repo_root: Path) -> dict[str, dict]:
        """Full pipeline.  Returns {relative_file_path: {metric: value}}."""

        # Step 1 -- ensure SonarQube is running
        if not self._server_is_up():
            started = self._start_via_docker()
            if not started:
                print("[sonarqube] Cannot reach server and Docker start failed.")
                return {}

        # Step 2 -- wait for healthy status
        if not self._wait_for_healthy():
            print("[sonarqube] Server never became healthy.  Skipping SonarQube.")
            return {}

        # Step 3 -- authenticate (token or admin password)
        self._session = self._build_session()
        if self._session is None:
            return {}

        # Step 4 -- ensure project exists
        self._ensure_project()

        # Step 5 -- write properties + run scanner
        sonar_scanner = shutil.which("sonar-scanner")
        if not sonar_scanner:
            print("[sonarqube] sonar-scanner not found on PATH.")
            print("  Download from: https://docs.sonarsource.com/"
                  "sonarqube/latest/analyzing-source-code/scanners/sonarscanner/")
            return {}

        self._write_properties(repo_root)
        task_id = self._run_scanner(repo_root, sonar_scanner)
        if not task_id:
            return {}

        # Step 6 -- wait for CE task
        self._wait_for_task(task_id)

        # Step 7 -- fetch metrics
        return self._fetch_metrics(repo_root)

    # ===================================================================
    # Step 1: server detection
    # ===================================================================

    def _server_is_up(self) -> bool:
        try:
            r = requests.get(f"{self.host}/api/system/status", timeout=5)
            status = r.json().get("status", "")
            return status in ("UP", "DB_MIGRATION_NEEDED", "DB_MIGRATION_RUNNING")
        except Exception:
            return False

    # ===================================================================
    # Step 1b: start via Docker Compose
    # ===================================================================

    def _start_via_docker(self) -> bool:
        if not shutil.which("docker"):
            print("[sonarqube] Docker not installed -- cannot auto-start SonarQube.")
            return False

        compose_cmd = self._docker_compose_cmd()
        if compose_cmd is None:
            print("[sonarqube] docker compose / docker-compose not available.")
            return False

        compose_file = self.compose_file or self._find_compose_file()
        if compose_file is None:
            print("[sonarqube] docker-compose.yml not found.  "
                  "Place it in the project root or pass --compose-file.")
            return False

        print(f"[sonarqube] Starting SonarQube via Docker Compose ...")
        cmd = compose_cmd + ["-f", str(compose_file), "up", "-d"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [sonarqube] docker compose up failed:\n{result.stderr[:500]}")
            return False

        print("[sonarqube] Docker Compose started.  Waiting for server to boot ...")
        return True   # we'll confirm health in _wait_for_healthy()

    def _docker_compose_cmd(self) -> list[str] | None:
        """Return ['docker', 'compose'] or ['docker-compose'] depending on what's installed."""
        # Try Docker CLI plugin first
        r = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            return ["docker", "compose"]
        # Fallback to standalone docker-compose
        if shutil.which("docker-compose"):
            return ["docker-compose"]
        return None

    def _find_compose_file(self) -> Path | None:
        """Look for docker-compose.yml in common places."""
        candidates = [
            Path(__file__).parent.parent / "docker-compose.yml",
            Path.cwd() / "docker-compose.yml",
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

    # ===================================================================
    # Step 2: wait for healthy
    # ===================================================================

    def _wait_for_healthy(self, timeout: int = _BOOT_WAIT_S) -> bool:
        url = f"{self.host}/api/system/status"
        print(f"[sonarqube] Waiting up to {timeout}s for server to be UP ...")
        waited = 0
        while waited < timeout:
            try:
                r = requests.get(url, timeout=5)
                if r.json().get("status") == "UP":
                    print("[sonarqube] Server is UP.")
                    return True
            except Exception:
                pass
            time.sleep(10)
            waited += 10
            print(f"  ... {waited}s elapsed")
        return False

    # ===================================================================
    # Step 3: authentication
    # ===================================================================

    def _build_session(self) -> requests.Session | None:
        token = self._resolve_token()
        if token is None:
            return None
        s = requests.Session()
        s.auth = (token, "")
        return s

    def _resolve_token(self) -> str | None:
        # Priority: explicit token > cached token > generate from admin creds
        if self._token:
            return self._token

        cache = Path(_TOKEN_CACHE_FILE)
        if cache.exists():
            cached = cache.read_text().strip()
            if cached:
                print("[sonarqube] Using cached token from .sonar_token")
                return cached

        # Generate a new token using admin credentials
        print("[sonarqube] Generating API token with admin credentials ...")
        token = self._generate_token()
        if token:
            cache.write_text(token)
            print(f"[sonarqube] Token saved to {_TOKEN_CACHE_FILE}")
            return token

        print("[sonarqube] Could not obtain a token.  "
              "Check admin_user / admin_password in config.yaml.")
        return None

    def _generate_token(self) -> str | None:
        """
        Create a user token via the SonarQube API using admin credentials.
        On very fresh installs the admin password may need to be changed first;
        we attempt the default and the configured password.
        """
        url   = f"{self.host}/api/user_tokens/generate"
        token_name = "static_metrics_collector"

        for password in [self.admin_password, "admin"]:
            try:
                r = requests.post(
                    url,
                    auth=(self.admin_user, password),
                    data={"name": token_name},
                    timeout=15,
                )
                if r.status_code == 200:
                    data = r.json()
                    return data.get("token")
                if r.status_code == 400:
                    # Token with that name already exists -- revoke and retry
                    requests.post(
                        f"{self.host}/api/user_tokens/revoke",
                        auth=(self.admin_user, password),
                        data={"name": token_name},
                        timeout=10,
                    )
                    r2 = requests.post(
                        url,
                        auth=(self.admin_user, password),
                        data={"name": token_name},
                        timeout=15,
                    )
                    if r2.status_code == 200:
                        return r2.json().get("token")
            except Exception as exc:
                print(f"  [sonarqube] Token generation attempt failed: {exc}")

        return None

    # ===================================================================
    # Step 4: project creation
    # ===================================================================

    def _ensure_project(self):
        url = f"{self.host}/api/projects/search"
        try:
            r = self._session.get(url, params={"projects": self.project_key}, timeout=10)
            components = r.json().get("components", [])
            if components:
                print(f"[sonarqube] Project '{self.project_key}' already exists.")
                return
        except Exception:
            pass

        print(f"[sonarqube] Creating project '{self.project_key}' ...")
        try:
            r = self._session.post(
                f"{self.host}/api/projects/create",
                data={
                    "name":       self.project_key,
                    "project":    self.project_key,
                    "visibility": "public",
                },
                timeout=15,
            )
            if r.status_code in (200, 201):
                print(f"[sonarqube] Project created.")
            else:
                print(f"  [sonarqube] Project creation returned {r.status_code}: {r.text[:200]}")
        except Exception as exc:
            print(f"  [sonarqube] Project creation error: {exc}")

    # ===================================================================
    # Step 5: sonar-project.properties + scanner
    # ===================================================================

    def _write_properties(self, repo_root: Path):
        token = self._session.auth[0] if self._session else ""
        props = (
            f"sonar.projectKey={self.project_key}\n"
            f"sonar.projectName={self.project_key}\n"
            f"sonar.sources=.\n"
            f"sonar.host.url={self.host}\n"
            f"sonar.token={token}\n"
            f"sonar.language=py\n"
            f"sonar.python.version=3\n"
            f"sonar.sourceEncoding=UTF-8\n"
            # Exclude common non-source dirs to speed up analysis
            "sonar.exclusions=**/__pycache__/**,**/*.java,**/*.cpp,**/*.c,**/*.h,**/*.cs/**,**/*.pyc,"
            "**/venv/**,**/.venv/**,**/env/**,"
            "**/node_modules/**,**/dist/**,**/build/**\n"
        )
        props_file = repo_root / "sonar-project.properties"
        props_file.write_text(props)
        print(f"[sonarqube] Wrote {props_file}")

    def _run_scanner(self, repo_root: Path, scanner_bin: str) -> str | None:
        print("[sonarqube] Running sonar-scanner (this may take a few minutes) ...")
        result = subprocess.run(
            [scanner_bin],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=_SCANNER_WAIT_S,
        )
        # sonar-scanner prints the CE task URL in stdout
        for line in result.stdout.splitlines():
            if "task?id=" in line:
                tid = line.split("task?id=")[-1].strip().split()[0].rstrip('"')
                print(f"[sonarqube] Analysis task: {tid}")
                return tid
        if result.returncode != 0:
            print(f"[sonarqube] sonar-scanner failed (exit {result.returncode}).")
            print(result.stdout[-1500:])
        else:
            print("[sonarqube] Scanner finished but task ID not found in output.")
        return None

    # ===================================================================
    # Step 6: wait for CE task
    # ===================================================================

    def _wait_for_task(self, task_id: str, timeout: int = _TASK_WAIT_S):
        print(f"[sonarqube] Waiting for background analysis task ...")
        url = f"{self.host}/api/ce/task"
        waited = 0
        while waited < timeout:
            try:
                r = self._session.get(url, params={"id": task_id}, timeout=10)
                status = r.json().get("task", {}).get("status", "PENDING")
                if status == "SUCCESS":
                    print("[sonarqube] Analysis task completed successfully.")
                    return
                if status in ("FAILED", "CANCELED"):
                    print(f"[sonarqube] Analysis task ended with status: {status}")
                    return
                print(f"  ... task status: {status} ({waited}s elapsed)")
            except Exception as exc:
                print(f"  [sonarqube] Polling error: {exc}")
            time.sleep(10)
            waited += 10
        print("[sonarqube] Timed out waiting for analysis task.")

    # ===================================================================
    # Step 7: fetch per-file metrics
    # ===================================================================

    def _fetch_metrics(self, repo_root: Path) -> dict[str, dict]:
        print("[sonarqube] Fetching per-file metrics ...")
        url = f"{self.host}/api/measures/component_tree"
        by_file: dict[str, dict] = {}
        page = 1

        while True:
            params = {
                "component":   self.project_key,
                "metricKeys":  ",".join(METRIC_KEYS),
                "qualifiers":  "FIL",
                "ps":          500,
                "p":           page,
            }
            try:
                r = self._session.get(url, params=params, timeout=30)
                data = r.json()
            except Exception as exc:
                print(f"  [sonarqube] API error: {exc}")
                break

            for comp in data.get("components", []):
                path = comp.get("path", "")
                row: dict = {}
                for m in comp.get("measures", []):
                    row[m["metric"]] = _cast(m.get("value"))
                by_file[path] = row

            paging = data.get("paging", {})
            if page * paging.get("pageSize", 500) >= paging.get("total", 0):
                break
            page += 1

        print(f"[sonarqube] Fetched metrics for {len(by_file)} files.")
        return by_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cast(value):
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
