import logging

from src.utils.github_client import request_github


def save_build_logs(build, token, output_path):
    repo = build['repository_name']
    run_id = build['run_id']

    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
    jobs = request_github(url, token, 'GET')
    # print(jobs)
    job_ids = [job['id'] for job in jobs['jobs']]
    for job_id in job_ids:
        save_status = save_job_logs(build, job_id, token, output_path)
        if not save_status:
            return False
    return True


def save_job_logs(build, job_id, token, output_path):
    repo = build['repository_name']
    run_id = build['run_id']

    url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"
    log = request_github(url, token, 'GET', require_text=True)
    if not log:
        logging.error(f"Job log not found: {repo}_{run_id}_{job_id}")
        return False
    # print(f"{output_path}/{repo}_{run_id}_{job_id}.log")
    with open(f"{output_path}/{repo.split('/')[1]}_{run_id}_{job_id}.log", 'w', encoding='utf-8') as log_file:
        log_file.write(log)
    return True

def cancel_all_runs():
    pass
    # statuses = ["in_progress", "queued"]
    # all_runs = []
    #
    # for status in statuses:
    #     params["status"] = status
    #     resp = requests.get(url, headers=headers, params=params)
    #     runs = resp.json().get("workflow_runs", [])
    #     all_runs.extend(runs)