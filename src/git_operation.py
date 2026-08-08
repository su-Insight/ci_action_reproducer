from __future__ import annotations

import subprocess
from pathlib import Path

from git import GitCommandError, Repo


class GitRepositoryManager:
    def run_command(self, cmd: list[str], cwd: Path | None = None) -> str | None:
        try:
            output_lines: list[str] = []
            with subprocess.Popen(
                cmd,
                cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            ) as process:
                assert process.stdout is not None
                for line in process.stdout:
                    print(line, end="")
                    output_lines.append(line)
                process.wait()
                if process.returncode != 0:
                    raise subprocess.CalledProcessError(
                        process.returncode,
                        cmd,
                        output="".join(output_lines),
                    )
            return "".join(output_lines)
        except subprocess.CalledProcessError as exc:
            print("Command failed:", " ".join(cmd))
            print("Output:", exc.output)
            return None

    def clone_if_missing(self, repository_url: str, destination: Path) -> None:
        if destination.exists():
            print(f"Clone skipped, destination exists: {destination}")
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        print(f"Cloning {repository_url} -> {destination}")
        self.run_command(["git", "clone", repository_url, str(destination)])

    def ensure_remote(
        self,
        repo_path: Path,
        remote_name: str,
        repository: str,
        *,
        use_ssh: bool = False,
        ssh_name: str = "",
    ) -> None:
        repo = Repo(repo_path)
        remote_url = (
            f"git@{ssh_name}:{repository}.git"
            if use_ssh
            else f"https://github.com/{repository}.git"
        )

        if remote_name in [remote.name for remote in repo.remotes]:
            remote = repo.remote(remote_name)
            if remote.url != remote_url:
                remote.set_url(remote_url)
            return

        repo.create_remote(remote_name, remote_url)

    def fetch(self, repo_path: Path, repository: str, remote_name: str) -> bool:
        self.ensure_remote(repo_path, remote_name, repository)
        repo = Repo(repo_path)

        try:
            remote = repo.remote(remote_name)
            print(f"Fetching {remote_name} in {repo_path}")
            remote.fetch(prune=True)
            for remote_ref in remote.refs:
                branch_name = remote_ref.name.replace(f"{remote_name}/", "")
                if branch_name not in repo.heads:
                    repo.create_head(branch_name, remote_ref).set_tracking_branch(remote_ref)
            return True
        except GitCommandError as exc:
            print(f"Fetch failed: {exc}")
            return False

    def reset_branch_to_commit(
        self,
        repo_path: Path,
        source_repository: str,
        branch: str,
        commit_sha: str,
    ) -> bool:
        remote_name = source_repository.split("/")[0]
        if not self.fetch(repo_path, source_repository, remote_name):
            return False

        repo = Repo(repo_path)
        try:
            remote_branch = f"{remote_name}/{branch}"
            print(
                f"Resetting {repo_path} from {source_repository} "
                f"remote_branch={remote_branch} to commit={commit_sha}"
            )
            repo.git.checkout("-B", branch, remote_branch)
            repo.git.reset("--hard", commit_sha)
            return True
        except GitCommandError as exc:
            print(f"Reset failed for {branch}@{commit_sha}: {exc}")
            return False

    def push(
        self,
        repo_path: Path,
        branch: str,
        remote_name: str,
        *,
        create_empty_commit: bool = False,
        empty_commit_message: str = "chore: trigger ci replay",
    ) -> bool:
        repo = Repo(repo_path)
        remote = repo.remote(name=remote_name)
        try:
            if create_empty_commit:
                print(f"Creating empty commit in {repo_path}: {empty_commit_message}")
                repo.git.commit("--allow-empty", "-m", empty_commit_message)
            print(f"Pushing {repo_path} branch={branch} remote={remote_name}")
            push_info = remote.push(refspec=branch, force=True)[0]
            if push_info.flags & push_info.ERROR:
                print(f"Push failed: {push_info.summary}")
                return False
            return True
        except GitCommandError as exc:
            print(f"Push failed: {exc}")
            return False

    def get_head_commit_sha(self, repo_path: Path, branch: str) -> str | None:
        repo = Repo(repo_path)
        try:
            sha = repo.git.rev_parse(branch).strip()
            print(f"Resolved head sha for {repo_path} branch={branch}: {sha}")
            return sha
        except GitCommandError as exc:
            print(f"Failed to resolve head sha for {branch}: {exc}")
            return None
