# Setup logging to both file and console
import base64
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

from src.git_operation import run_command, reset_branch, push, remote_repository_add
from src.utils.github_client import request_github
from src.utils.github_response_filter import save_build_logs
from src.utils.log_parser import fetch_action_infos, find_key_recursively, classify_and_sort_os
from src.utils.metrics_aggregator import BuildCSVHandler

script_name = os.path.splitext(os.path.basename(__file__))[0]
logging.basicConfig(filename=f"{script_name}.log", level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)

def load_config(config_file='config.yml'):
    try:
        with open(config_file, 'r') as file:
            config = yaml.safe_load(file)
        return config
    except Exception as e:
        logging.error(f"Failed to load config file {config_file}: {e}")
        return {}
config = load_config()
# print(config)


def check_dirs():
    """
    Ensure that the specified directories exist. If they do not, create them.

    Args:
        dirs (list[str], optional): List of directory paths to check/create.
            Defaults to ['workflows'].
    """
    dirs = ['workflows', 'logs']

    for dir_path in dirs:
        try:
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
                print(f"Created directory: {dir_path}")
            else:
                print(f"Directory already exists: {dir_path}")
        except OSError as e:
            print(f"Failed to create directory {dir_path}: {e}")
            exit(1)
check_dirs()

def get_available_action_sha(action_name, sha):
    action = action_name.split("@")[0]
    return f"{action}@{sha}"

output_csv = "../data/builds.csv"
logs_path = "logs"
clone_path = "../clone"
csv_handler = BuildCSVHandler(output_csv)
main_token = config['main_token']
secondary_token = config['secondary_token']
ssh_name1, ssh_name2 = tuple(config['local_ssh_names'])
remote_name = "origin"

def reproduce_build(build):
    res = {
        "repository_name": build['repository_name'],
        "run_id": build['run_id'],
        "status": None,
        "exception": None
    }
    repo = build['repository_name']
    run_id = build['run_id']


    user = request_github(f"https://api.github.com/user", main_token, "GET")
    user_name = user["login"]
    main_fork_repo = f'{user_name}/{repo.split('/')[-1]}'
    # ================================= 前序环境准备工作 ================================

    run = request_github(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}", main_token, 'GET')
    commit_branch = run['head_branch']
    commit_sha = run['head_sha']
    # print(run)

    # 将build的所有日志保存到本地，如果有日志保存失败，直接退出程序（日志缺失后续无法实现复现流程）
    print("正在保存日志...", end='')
    save_status = save_build_logs(build, main_token, logs_path)
    # if not save_status:
    #     csv_handler.save("")
    print("Done!")

    print("正在解析日志信息...", end='')
    action_infos = fetch_action_infos(repo, run_id, logs_path)
    print("Done!")

    # 检查仓库是否已经被当前账户fork，如果没有则fork
    repo_satus = request_github(f"https://api.github.com/repos/{main_fork_repo}", main_token, 'GET')
    if not repo_satus:
        # fork仓库到当前token账户
        print("fork...")
        request_github(f"https://api.github.com/repos/{repo}/forks", main_token, 'POST')
        time.sleep(5)

    # 启用action
    # Payload: enable all workflows
    # data = {
    #     "enabled": True,
    #     "allowed_actions": "all"
    # }
    # request_github(f"https://api.github.com/repos/{repo}/actions/permissions", main_token, 'GET', data)


    repo_url = f"https://github.com/{main_fork_repo}.git"
    clone_repo_path = f"{clone_path}/{main_fork_repo}"
    if not os.path.exists(clone_repo_path):
        run_command(["git", "clone", repo_url, clone_repo_path])

    workflow = request_github(f"https://api.github.com/repos/{repo}/actions/workflows/{run['workflow_id']}", main_token, 'GET')
    if not workflow:
        return
    workflow_path = workflow['path']

    workflow_content = request_github(f"https://api.github.com/repos/{repo}/contents/{workflow_path}?ref={commit_sha}", main_token, 'GET')
    if not workflow_content:
        return
    workflow_content = base64.b64decode(workflow_content['content']).decode('utf-8')

    def update_workflow(workflow_content):
        yaml_workflow_content = yaml.safe_load(workflow_content)
        replace_os = {}
        sort_os = classify_and_sort_os(action_infos['os'])
        operation_systems = find_key_recursively(yaml_workflow_content, 'runs-on')
        for os in operation_systems:
            if 'latest' in os:
                pure_os = os.split('-latest')[0]
                replace_os[os] = sort_os[pure_os][0]
        for action_name, sha in action_infos['action_sha'].items():
            action_with_sha = get_available_action_sha(action_name, sha)
            workflow_content = workflow_content.replace(action_name, action_with_sha)
        for old_os, new_os in replace_os.items():
            workflow_content = workflow_content.replace(old_os, new_os)

        return workflow_content
    try:
        new_workflow_content = update_workflow(workflow_content)
    except Exception as e:
        print(e)
    print(new_workflow_content)
    exit()

    # ========================== 正式执行，分流：PR or Push =================================
    def reproduce_pull_request():
        secondary_fork_repo = main_fork_repo
        secondary_remote_repo = repo
        pull_requests = request_github(f"https://api.github.com/repos/{repo}/commits/{commit_sha}/pulls", main_token, 'GET')
        if not pull_requests:
            res['exception'] = "Could not find PR number"
            # TODO: save
            return
        else:
            pull_request = pull_requests[0]


        # 判断PR是否由两个仓库提交，如果PR来自其他fork仓库且配置了仿真模拟，则对副账户同样进行配置
        same_repos_tag = pull_request['head']['label'].split(':')[0] == pull_request['base']['label'].split(':')[0]
        if not same_repos_tag and config['simulated_commit']:
            secondary_user = request_github(f"https://api.github.com/user", secondary_token, "GET")
            secondary_user_name = secondary_user["login"]
            secondary_fork_repo = f"{secondary_user_name}/{repo.split('/')[-1]}"
            secondary_remote_repo = f"{pull_request['head']['label'].split(':')[0]}/{repo.split('/')[-1]}"

            # 检查仓库是否已经被当前账户fork，如果没有则fork
            repo_satus = request_github(f"https://api.github.com/repos/{secondary_fork_repo}",
                                        secondary_token,
                                        'GET')
            if not repo_satus:
                # fork仓库到当前token账户
                print("fork...")
                request_github(f"https://api.github.com/repos/{repo}/forks", secondary_token, 'POST')
                time.sleep(5)

            # 启用action
            # Payload: enable all workflows
            # data = {
            #     "enabled": True,
            #     "allowed_actions": "all"
            # }
            # request_github(f"https://api.github.com/repos/{repo}/actions/permissions", secondary_token, 'GET', data)

            repo_url = f"https://github.com/{repo}.git"
            clone_repo_path = f"{clone_path}/{secondary_fork_repo}"
            if not os.path.exists(clone_repo_path):
                run_command(["git", "clone", repo_url, clone_repo_path])

        # 对仓库执行reset，使其当前的代码处于run执行时的状态
        repo_path = f"{clone_path}/{secondary_fork_repo}"
        head_ref = pull_request['head']['ref']
        reset_branch(repo_path, secondary_remote_repo, head_ref, commit_sha)
        # 确保origin指向当前仓库，使用ssh认证方式，否则推送会失败
        remote_repository_add(repo_path, secondary_fork_repo, remote_name, True, ssh_name2)
        if not push(repo_path, head_ref, remote_name):
            return

        # repository = request_github(f"https://api.github.com/repos/{repo}", main_token, 'GET')
        # default_branch = repository['default_branch']

        # print(commit_sha)
        repo_path = f"{clone_path}/{main_fork_repo}"
        base_ref = pull_request['base']['ref']
        base_sha = pull_request['base']['sha']
        reset_branch(repo_path, repo, base_ref, base_sha)
        remote_repository_add(repo_path, main_fork_repo, remote_name, True, ssh_name1)
        if not push(repo_path, base_ref, remote_name):
            return

    def reproduce_push():
        repo_path = f"{clone_path}/{main_fork_repo}"
        repository = request_github(f"https://api.github.com/repos/{repo}", main_token, 'GET')
        default_branch = repository['default_branch']
        print(commit_sha)

        reset_branch(repo_path, repo, commit_branch, commit_sha)
        # TODO: 更新 workflow
        if not push(repo_path, main_fork_repo, commit_branch):
            return
        # push()

    if run['event'] == 'pull_request' or run['event'] == 'pull_request_target':
        new_run_id = reproduce_pull_request()
    if run['event'] == 'push':
        new_run_id = reproduce_push()

def main():
    # Read the list of builds that need to be reproduced
    builds = csv_handler.read_basic_build_info_as_dict()
    processing_builds = [build for build in builds if build['status'] != '1']
    print(processing_builds)

    # 根据repository分配进程，同一个仓库在同一时间不会被同时执行
    max_workers = min(1, len(processing_builds))  # 线程池大小按机器调整
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        pool.map(reproduce_build, processing_builds)



main()