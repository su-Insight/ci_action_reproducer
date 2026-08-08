from __future__ import annotations

import argparse
import logging
from pathlib import Path

import yaml

from git_operation import GitRepositoryManager
from utils.github_client import GitHubClient
from utils.log_parser import parse_logs
from utils.storage import ArtifactStorage, BuildStorage, setup_logging
from utils.workflow_rewriter import rewrite_workflow


SUPPORTED_EVENTS = {"push", "pull_request", "pull_request_target"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare or trigger GitHub Actions build reproduction runs."
    )
    parser.add_argument(
        "--config",
        help="Path to config.yml. Defaults to repo-root config.yml.",
    )
    parser.add_argument(
        "--builds",
        help="Optional custom path to builds.csv.",
    )
    parser.add_argument(
        "--run-id",
        help="Only process a single run_id from the builds CSV.",
    )
    return parser


def resolve_config_path(repo_root: Path, explicit_path: str | None = None) -> Path:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_absolute() else repo_root / path

    for candidate in (repo_root / "config.yml", repo_root / "config.yaml"):
        if candidate.exists():
            return candidate
    return repo_root / "config.yml"


def load_config(path: Path) -> dict:
    logging.info("Loading config from %s", path)
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    if "main_token" not in config:
        raise ValueError("Missing required config key: main_token")

    config.setdefault("secondary_token", "")
    config.setdefault("simulated_commit", True)
    config.setdefault("local_ssh_name", "")
    config.setdefault("local_ssh_names", [])
    config.setdefault("preview_only", True)
    config.setdefault("del_traces", False)
    config.setdefault("enable_min_replaceable_os_version", False)
    config.setdefault("enable_min_replaceable_action_version", False)
    return config


def runtime_paths(repo_root: Path, builds_override: str | None = None) -> dict[str, Path]:
    data_dir = repo_root / "data"
    artifacts_dir = repo_root / "artifacts"
    builds_csv = data_dir / "builds.csv"
    if builds_override:
        path = Path(builds_override)
        builds_csv = path if path.is_absolute() else repo_root / path

    return {
        "repo_root": repo_root,
        "data_dir": data_dir,
        "clone_dir": repo_root / "clone",
        "artifacts_dir": artifacts_dir,
        "logs_dir": artifacts_dir / "logs",
        "workflows_dir": artifacts_dir / "workflows",
        "builds_csv": builds_csv,
        "run_log_file": artifacts_dir / "action_reproducer.log",
    }


def ensure_runtime_dirs(paths: dict[str, Path]) -> None:
    for key in ("data_dir", "clone_dir", "artifacts_dir", "logs_dir", "workflows_dir"):
        paths[key].mkdir(parents=True, exist_ok=True)


def clone_repo_path(clone_dir: Path, repository: str) -> Path:
    return clone_dir / repository.replace("/", "__")


def repository_slug(repository_name: str) -> str:
    return repository_name.split("/")[-1]


def primary_ssh_name(config: dict) -> str:
    ssh_names = config.get("local_ssh_names") or []
    if ssh_names:
        return ssh_names[0]
    return config.get("local_ssh_name", "")


def secondary_ssh_name(config: dict) -> str:
    ssh_names = config.get("local_ssh_names") or []
    if len(ssh_names) >= 2:
        return ssh_names[1]
    return config.get("local_ssh_name", "")


def ssh_name_for_repository(
    config: dict,
    target_repository: str,
    primary_repository: str,
) -> str:
    if target_repository == primary_repository:
        return primary_ssh_name(config)
    return secondary_ssh_name(config)


def process_builds(
    config: dict,
    paths: dict[str, Path],
    build_store: BuildStorage,
    artifact_storage: ArtifactStorage,
    git_manager: GitRepositoryManager,
    run_id: str | None = None,
) -> list[dict]:
    primary_client = GitHubClient(config["main_token"])
    secondary_client = GitHubClient(config["secondary_token"]) if config["secondary_token"] else None

    builds = build_store.read_pending_builds(run_id=run_id)
    logging.info("Found %s pending build(s).", len(builds))
    if run_id is not None:
        logging.info("Restricted to run_id=%s", run_id)
    results = [
        reproduce_build(
            build=build,
            config=config,
            paths=paths,
            artifact_storage=artifact_storage,
            primary_client=primary_client,
            secondary_client=secondary_client,
            git_manager=git_manager,
        )
        for build in builds
    ]
    return results


def reproduce_build(
    *,
    build: dict,
    config: dict,
    paths: dict[str, Path],
    artifact_storage: ArtifactStorage,
    primary_client: GitHubClient,
    secondary_client: GitHubClient | None,
    git_manager: GitRepositoryManager,
) -> dict:
    logging.info(
        "Start reproduction for %s#%s",
        build["repository_name"],
        build["run_id"],
    )
    result = {
        "repository_name": build["repository_name"],
        "run_id": build["run_id"],
        "status": "pending",
        "exception": "",
        "new_repository": "",
        "new_run_id": "",
        "conclusion": "",
        "workflow_preview_path": "",
        "head_commit_sha": "",
        "base_commit_sha": "",
        "pull_request_url": "",
    }

    user = primary_client.get_authenticated_user()
    if not user:
        return fail(result, "Unable to fetch GitHub user for main token.")
    logging.info("Authenticated as %s", user["login"])

    main_fork_repository = f"{user['login']}/{repository_slug(build['repository_name'])}"
    result["new_repository"] = main_fork_repository

    run = primary_client.get_run(build["repository_name"], build["run_id"])
    if not run:
        return fail(result, "Unable to fetch run metadata.")
    logging.info(
        "Run metadata loaded: event=%s workflow_id=%s head_branch=%s head_sha=%s",
        run["event"],
        run["workflow_id"],
        run["head_branch"],
        run["head_sha"],
    )

    raw_logs = download_logs(build, primary_client, artifact_storage)
    if raw_logs is None:
        return fail(result, "Unable to save build logs.")
    logging.info("Downloaded %s job log(s)", len(raw_logs))

    insights = parse_logs(raw_logs)
    if not insights["action_shas"] and not insights["operating_systems"]:
        logging.warning(
            "No action metadata parsed from logs for %s#%s",
            build["repository_name"],
            build["run_id"],
        )

    primary_client.ensure_fork_exists(build["repository_name"], main_fork_repository)
    logging.info("Using fork repository %s", main_fork_repository)

    main_clone_path = clone_repo_path(paths["clone_dir"], main_fork_repository)
    logging.info("Ensuring local clone at %s", main_clone_path)
    git_manager.clone_if_missing(primary_client.build_clone_url(main_fork_repository), main_clone_path)

    workflow_source = primary_client.get_workflow_source(
        build["repository_name"],
        run["workflow_id"],
        run["head_sha"],
    )
    if not workflow_source:
        return fail(result, "Unable to fetch workflow content.")

    rewritten_workflow = rewrite_workflow(workflow_source["content"], insights)
    preview_path = artifact_storage.save_workflow_preview(
        build["repository_name"],
        build["run_id"],
        rewritten_workflow,
    )
    result["workflow_preview_path"] = str(preview_path)
    logging.info(
        "Saved rewritten workflow preview for %s#%s to %s",
        build["repository_name"],
        build["run_id"],
        preview_path,
    )

    if config.get("preview_only", True):
        logging.info("preview_only=true, skipping trigger step")
        result["status"] = "previewed"
        return result

    event = run["event"]
    if event == "push":
        return reproduce_push(
            build,
            run,
            result,
            config,
            paths,
            main_fork_repository,
            git_manager,
            primary_client.get_commit_message(build["repository_name"], run["head_sha"]),
        )
    if event in {"pull_request", "pull_request_target"}:
        return reproduce_pull_request(
            build,
            run,
            result,
            config,
            paths,
            main_fork_repository,
            primary_client,
            secondary_client,
            git_manager,
            primary_client.get_commit_message(build["repository_name"], run["head_sha"]),
        )
    if event not in SUPPORTED_EVENTS:
        return fail(result, f"Unsupported event type: {event}")
    return result


def download_logs(build: dict, client: GitHubClient, artifact_storage: ArtifactStorage) -> list[str] | None:
    job_ids = client.list_job_ids(build["repository_name"], build["run_id"])
    if not job_ids:
        logging.warning(
            "No job ids found for %s#%s",
            build["repository_name"],
            build["run_id"],
        )
        return None

    raw_logs: list[str] = []
    slug = repository_slug(build["repository_name"])
    logging.info(
        "Downloading logs for %s#%s (%s job(s))",
        build["repository_name"],
        build["run_id"],
        len(job_ids),
    )
    for job_id in job_ids:
        log_content = client.download_job_log(build["repository_name"], job_id)
        if not log_content:
            logging.warning(
                "Failed to download job log for %s#%s job=%s",
                build["repository_name"],
                build["run_id"],
                job_id,
            )
            continue
        raw_logs.append(log_content)
        artifact_storage.save_job_log(slug, build["run_id"], job_id, log_content)
        logging.info("Saved job log job_id=%s", job_id)
    if not raw_logs:
        return None
    return raw_logs


def reproduce_push(
    build: dict,
    run: dict,
    result: dict,
    config: dict,
    paths: dict[str, Path],
    main_fork_repository: str,
    git_manager: GitRepositoryManager,
    commit_message: str | None,
) -> dict:
    logging.info(
        "Triggering push reproduction for %s branch=%s",
        build["repository_name"],
        run["head_branch"],
    )
    repo_path = clone_repo_path(paths["clone_dir"], main_fork_repository)
    if not git_manager.reset_branch_to_commit(
        repo_path,
        build["repository_name"],
        run["head_branch"],
        run["head_sha"],
    ):
        return fail(result, "Failed to reset branch for push-event reproduction.")
    git_manager.ensure_remote(
        repo_path,
        "origin",
        main_fork_repository,
        use_ssh=True,
        ssh_name=primary_ssh_name(config),
    )
    if not git_manager.push(
        repo_path,
        run["head_branch"],
        "origin",
        create_empty_commit=True,
        empty_commit_message=commit_message or "chore: trigger ci replay",
    ):
        return fail(result, "Failed to push branch for push-event reproduction.")
    result["head_commit_sha"] = git_manager.get_head_commit_sha(repo_path, run["head_branch"]) or ""

    logging.info("Push reproduction completed for %s#%s", build["repository_name"], build["run_id"])
    result["status"] = "triggered"
    return result


def reproduce_pull_request(
    build: dict,
    run: dict,
    result: dict,
    config: dict,
    paths: dict[str, Path],
    main_fork_repository: str,
    primary_client: GitHubClient,
    secondary_client: GitHubClient | None,
    git_manager: GitRepositoryManager,
    head_commit_message: str | None,
) -> dict:
    logging.info(
        "Triggering PR reproduction for %s head_sha=%s",
        build["repository_name"],
        run["head_sha"],
    )
    head_repository = primary_client.get_head_repository_from_run(
        build["repository_name"],
        run,
    )
    if head_repository:
        logging.info("Resolved head repository from run metadata: %s", head_repository)
    else:
        logging.warning(
            "Falling back to base repository for commit->pull lookup: %s",
            build["repository_name"],
        )

    pull_requests = primary_client.get_pull_requests_for_commit(
        head_repository or build["repository_name"],
        run["head_sha"],
    )
    if not pull_requests:
        return fail(result, "Could not find PR information for commit.")

    pull_request = pull_requests[0]
    secondary_fork_repository = main_fork_repository
    # secondary_source_repository = build["repository_name"]

    if is_cross_repository(pull_request) and config.get("simulated_commit", True):
        logging.info("Cross-repo PR detected, simulated_commit enabled")
        if secondary_client is None:
            return fail(
                result,
                "secondary_token is required for simulated cross-repo PR reproduction.",
            )

        secondary_user = secondary_client.get_authenticated_user()
        if not secondary_user:
            return fail(result, "Unable to fetch secondary GitHub user.")

        secondary_fork_repository = (
            f"{secondary_user['login']}/{repository_slug(build['repository_name'])}"
        )
        # secondary_source_repository = (
        #     f"{pull_request['head_label'].split(':')[0]}/{repository_slug(build['repository_name'])}"
        # )
        secondary_client.ensure_fork_exists(build["repository_name"], secondary_fork_repository)
        git_manager.clone_if_missing(
            primary_client.build_clone_url(build["repository_name"]),
            clone_repo_path(paths["clone_dir"], secondary_fork_repository),
        )
        logging.info("Prepared secondary fork %s", secondary_fork_repository)

    head_empty_commit_message = (
        head_commit_message or pull_request["head_ref"] or "chore: trigger ci replay"
    )
    head_ssh_name = ssh_name_for_repository(
        config,
        secondary_fork_repository,
        main_fork_repository,
    )
    secondary_repo_path = clone_repo_path(paths["clone_dir"], secondary_fork_repository)
    if not git_manager.reset_branch_to_commit(
        secondary_repo_path,
        head_repository,
        pull_request["head_ref"],
        run["head_sha"],
    ):
        return fail(result, "Failed to reset PR head branch.")
    git_manager.ensure_remote(
        secondary_repo_path,
        "origin",
        secondary_fork_repository,
        use_ssh=True,
        ssh_name=head_ssh_name,
    )
    if not git_manager.push(
        secondary_repo_path,
        pull_request["head_ref"],
        "origin",
        create_empty_commit=True,
        empty_commit_message=head_empty_commit_message,
    ):
        return fail(result, "Failed to push PR head branch.")
    result["head_commit_sha"] = (
        git_manager.get_head_commit_sha(secondary_repo_path, pull_request["head_ref"]) or ""
    )
    logging.info("Pushed PR head branch %s", pull_request["head_ref"])

    base_commit_message = primary_client.get_commit_message(
        build["repository_name"],
        pull_request["base_sha"],
    )
    base_empty_commit_message = (
        base_commit_message or pull_request["base_ref"] or "chore: trigger ci replay"
    )
    main_repo_path = clone_repo_path(paths["clone_dir"], main_fork_repository)
    if not git_manager.reset_branch_to_commit(
        main_repo_path,
        build["repository_name"],
        pull_request["base_ref"],
        pull_request["base_sha"],
    ):
        return fail(result, "Failed to reset PR base branch.")
    git_manager.ensure_remote(
        main_repo_path,
        "origin",
        main_fork_repository,
        use_ssh=True,
        ssh_name=primary_ssh_name(config),
    )
    if not git_manager.push(
        main_repo_path,
        pull_request["base_ref"],
        "origin",
        create_empty_commit=True,
        empty_commit_message=base_empty_commit_message,
    ):
        return fail(result, "Failed to push PR base branch.")
    result["base_commit_sha"] = (
        git_manager.get_head_commit_sha(main_repo_path, pull_request["base_ref"]) or ""
    )

    pull_request_title = pull_request["title"] or f"Replay {build['repository_name']}#{build['run_id']}"
    pull_request_body = pull_request["body"] or ""
    head_owner = secondary_fork_repository.split("/", 1)[0]
    if secondary_fork_repository == main_fork_repository:
        head_spec = pull_request["head_ref"]
    else:
        head_spec = f"{head_owner}:{pull_request['head_ref']}"
    pr_client = primary_client
    # if secondary_fork_repository != main_fork_repository and secondary_client is not None:
    #     pr_client = secondary_client
    #     logging.info("Creating replay pull request with secondary token")
    # else:
    #     logging.info("Creating replay pull request with primary token")
    created_pull_request = pr_client.create_pull_request(
        main_fork_repository,
        pull_request_title,
        head_spec,
        pull_request["base_ref"],
        pull_request_body,
    )
    if not created_pull_request:
        return fail(result, "Failed to create replay pull request.")
    result["pull_request_url"] = created_pull_request.get("html_url", "")
    logging.info(
        "Created replay pull request %s",
        result["pull_request_url"] or created_pull_request.get("url", ""),
    )

    logging.info("PR reproduction completed for %s#%s", build["repository_name"], build["run_id"])
    result["status"] = "triggered"
    return result


def is_cross_repository(pull_request: dict) -> bool:
    return pull_request["head_label"].split(":")[0] != pull_request["base_label"].split(":")[0]


def fail(result: dict, message: str) -> dict:
    result["status"] = "failed"
    result["exception"] = message
    logging.error(message)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    config_path = resolve_config_path(repo_root, args.config)
    if not config_path.exists():
        parser.error(
            f"Config file not found at {config_path}. "
            "Copy config.example.yml to config.yml and fill in your tokens."
        )

    config = load_config(config_path)
    paths = runtime_paths(repo_root, args.builds)
    ensure_runtime_dirs(paths)
    setup_logging(paths["run_log_file"])

    build_store = BuildStorage(paths["builds_csv"])
    artifact_storage = ArtifactStorage(paths["logs_dir"], paths["workflows_dir"])
    git_manager = GitRepositoryManager()
    results = process_builds(
        config=config,
        paths=paths,
        build_store=build_store,
        artifact_storage=artifact_storage,
        git_manager=git_manager,
        run_id=args.run_id,
    )

    if not results:
        logging.info("No pending builds found.")
        return 0

    build_store.save_results(results)
    for result in results:
        logging.info(
            "Build %s#%s finished with status=%s preview=%s",
            result["repository_name"],
            result["run_id"],
            result["status"],
            result["workflow_preview_path"] or "-",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
