from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timezone
from typing import Any

import requests


class GitHubClient:
    def __init__(self, token: str, timeout_seconds: int = 10) -> None:
        self.token = token
        self.timeout_seconds = timeout_seconds

    def get_authenticated_user(self) -> dict | None:
        payload = self.request("https://api.github.com/user")
        if not payload:
            return None
        return {"login": payload["login"]}

    def get_run(self, repository: str, run_id: str) -> dict | None:
        payload = self.request(f"https://api.github.com/repos/{repository}/actions/runs/{run_id}")
        if not payload:
            return None
        head_repository = payload.get("head_repository") or {}
        return {
            "event": payload["event"],
            "workflow_id": int(payload["workflow_id"]),
            "head_branch": payload["head_branch"],
            "head_sha": payload["head_sha"],
            "head_repository": head_repository.get("full_name", ""),
            "pull_requests": payload.get("pull_requests") or [],
        }

    def get_commit_message(self, repository: str, commit_sha: str) -> str | None:
        payload = self.request(f"https://api.github.com/repos/{repository}/commits/{commit_sha}")
        if not payload:
            return None
        commit = payload.get("commit") or {}
        message = commit.get("message")
        return message if isinstance(message, str) and message.strip() else None

    def list_job_ids(self, repository: str, run_id: str) -> list[str]:
        payload = self.request(f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/jobs")
        if not payload or "jobs" not in payload:
            return []
        return [str(job["id"]) for job in payload["jobs"]]

    def download_job_log(self, repository: str, job_id: str) -> str | None:
        return self.request(
            f"https://api.github.com/repos/{repository}/actions/jobs/{job_id}/logs",
            require_text=True,
        )

    def ensure_fork_exists(self, source_repository: str, fork_repository: str) -> None:
        if self.request(f"https://api.github.com/repos/{fork_repository}") is not None:
            return
        logging.info("Forking %s into %s", source_repository, fork_repository)
        self.request(f"https://api.github.com/repos/{source_repository}/forks", method="POST")
        time.sleep(5)

    def get_workflow_source(self, repository: str, workflow_id: int, commit_sha: str) -> dict | None:
        workflow = self.request(
            f"https://api.github.com/repos/{repository}/actions/workflows/{workflow_id}"
        )
        if not workflow:
            return None

        workflow_path = workflow["path"]
        contents = self.request(
            f"https://api.github.com/repos/{repository}/contents/{workflow_path}?ref={commit_sha}"
        )
        if not contents:
            return None

        decoded = base64.b64decode(contents["content"]).decode("utf-8")
        return {"path": workflow_path, "content": decoded}

    def get_pull_request(self, url: str) -> dict | None:
        payload = self.request(url)
        if not payload:
            return None
        return payload

    def get_head_repository_from_run(self, repository: str, run: dict) -> str | None:
        head_repository = run.get("head_repository")
        if isinstance(head_repository, str) and head_repository.strip():
            return head_repository

        pull_requests = run.get("pull_requests") or []
        for item in pull_requests:
            head = item.get("head") or {}
            repo_info = head.get("repo") or {}
            full_name = repo_info.get("full_name")
            if isinstance(full_name, str) and full_name.strip():
                return full_name

            pr_url = item.get("url")
            if isinstance(pr_url, str) and pr_url.strip():
                pr_payload = self.get_pull_request(pr_url)
                if not pr_payload:
                    continue
                pr_head = pr_payload.get("head") or {}
                pr_head_repo = pr_head.get("repo") or {}
                full_name = pr_head_repo.get("full_name")
                if isinstance(full_name, str) and full_name.strip():
                    return full_name

        logging.warning(
            "Unable to resolve head repository from run metadata for %s head_sha=%s",
            repository,
            run.get("head_sha", ""),
        )
        return None

    def get_pull_requests_for_commit(self, repository: str, commit_sha: str) -> list[dict]:
        logging.info(
            "Querying pull requests by commit: repository=%s commit_sha=%s",
            repository,
            commit_sha,
        )
        payload = self.request(
            f"https://api.github.com/repos/{repository}/commits/{commit_sha}/pulls"
        )
        if not payload:
            return []
        return [
            {
                "number": item["number"],
                "title": item["title"],
                "body": item.get("body") or "",
                "head_label": item["head"]["label"],
                "head_repo": item["head"]["repo"]["full_name"] if item["head"].get("repo") else "",
                "base_label": item["base"]["label"],
                "base_repo": item["base"]["repo"]["full_name"] if item["base"].get("repo") else "",
                "head_ref": item["head"]["ref"],
                "base_ref": item["base"]["ref"],
                "base_sha": item["base"]["sha"],
            }
            for item in payload
        ]

    def create_pull_request(
        self,
        repository: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
    ) -> dict | None:
        logging.info(
            "Creating pull request in %s head=%s base=%s title=%s",
            repository,
            head,
            base,
            title,
        )
        return self.request(
            f"https://api.github.com/repos/{repository}/pulls",
            method="POST",
            data={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
            },
        )

    @staticmethod
    def build_clone_url(repository: str) -> str:
        return f"https://github.com/{repository}.git"

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        require_text: bool = False,
    ) -> Any:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/115.0.0.0 Safari/537.36 Edg/115.0.1901.188"
            ),
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {self.token}",
            "Connection": "close",
        }

        attempt = 0
        max_attempts = 5
        while attempt < max_attempts:
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data,
                    timeout=self.timeout_seconds,
                )
                remaining = int(response.headers.get("X-RateLimit-Remaining", 1))
                reset_time = response.headers.get("X-RateLimit-Reset")

                if remaining == 0 and reset_time:
                    self._sleep_until(reset_time, f"Rate limit hit for {url}")
                    continue

                if response.status_code in (200, 201, 202):
                    return response.text if require_text else response.json()

                if response.status_code == 403 and reset_time:
                    self._sleep_until(reset_time, f"403 forbidden for {url}")
                    continue

                if response.status_code in (500, 502, 503, 504):
                    wait_time = min(2**attempt, 60)
                    logging.warning(
                        "GitHub server error %s for %s. Retrying in %s seconds.",
                        response.status_code,
                        url,
                        wait_time,
                    )
                    time.sleep(wait_time)
                    attempt += 1
                    continue

                logging.warning("GitHub request failed: %s %s", response.status_code, url)
                return None
            except requests.exceptions.ConnectionError:
                wait_time = min(2**attempt, 60)
                logging.error("Connection error for %s. Retrying in %s seconds.", url, wait_time)
                time.sleep(wait_time)
                attempt += 1
            except requests.exceptions.Timeout:
                wait_time = min(2**attempt, 60)
                logging.error("Request timeout for %s. Retrying in %s seconds.", url, wait_time)
                time.sleep(wait_time)
                attempt += 1
            except requests.exceptions.RequestException as exc:
                logging.error("Unexpected request error for %s: %s", url, exc)
                return None

        logging.error("Max retry attempts reached for %s.", url)
        return None

    @staticmethod
    def _sleep_until(reset_time: str, reason: str) -> None:
        wait_seconds = max(
            0,
            (
                datetime.fromtimestamp(int(reset_time), timezone.utc)
                - datetime.now(timezone.utc)
            ).total_seconds()
            + 10,
        )
        logging.warning("%s. Sleeping for %.0f seconds.", reason, wait_seconds)
        time.sleep(wait_seconds)
