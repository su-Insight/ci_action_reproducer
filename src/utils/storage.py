from __future__ import annotations

import csv
import logging
from pathlib import Path


class BuildStorage:
    fieldnames = [
        "repository_name",
        "run_id",
        "status",
        "exception",
        "new_repository",
        "new_run_id",
        "conclusion",
    ]

    def __init__(self, csv_path: Path) -> None:
        self.csv_path = csv_path

    def read_builds(self) -> list[dict[str, str]]:
        if not self.csv_path.exists():
            return []

        items: list[dict[str, str]] = []
        with self.csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                repository_name = (row.get("repository_name") or "").strip()
                run_id = str(row.get("run_id") or "").strip()
                if not repository_name or not run_id:
                    continue
                items.append(
                    {
                        "repository_name": repository_name,
                        "run_id": run_id,
                        "status": (row.get("status") or "").strip(),
                    }
                )
        return items

    def read_pending_builds(self, run_id: str | None = None) -> list[dict[str, str]]:
        builds = [build for build in self.read_builds() if build["status"] != "triggered"]
        if run_id is None:
            return builds
        return [build for build in builds if build["run_id"] == str(run_id)]

    def save_results(self, results: list[dict]) -> None:
        if not results:
            return

        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.fieldnames)
            writer.writeheader()
            for result in results:
                writer.writerow(
                    {
                        "repository_name": result["repository_name"],
                        "run_id": result["run_id"],
                        "status": result["status"],
                        "exception": result["exception"],
                        "new_repository": result["new_repository"],
                        "new_run_id": result["new_run_id"],
                        "conclusion": result["conclusion"],
                    }
                )


class ArtifactStorage:
    def __init__(self, logs_dir: Path, workflows_dir: Path) -> None:
        self.logs_dir = logs_dir
        self.workflows_dir = workflows_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.workflows_dir.mkdir(parents=True, exist_ok=True)

    def save_job_log(self, repository_slug: str, run_id: str, job_id: str, content: str) -> Path:
        path = self.logs_dir / f"{repository_slug}_{run_id}_{job_id}.log"
        path.write_text(_truncate_lines(content, max_lines=500), encoding="utf-8")
        return path

    def save_workflow_preview(self, repository: str, run_id: str, content: str) -> Path:
        safe_name = repository.replace("/", "__")
        path = self.workflows_dir / f"{safe_name}_{run_id}.yml"
        path.write_text(content, encoding="utf-8")
        return path


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def _truncate_lines(content: str, max_lines: int) -> str:
    lines = content.splitlines()
    truncated = lines[:max_lines]
    return "\n".join(truncated)
