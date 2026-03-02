import subprocess
from git import Repo, exc, GitCommandError


def run_command(cmd, cwd=None):
    """
    执行命令并实时输出到控制台，同时返回完整输出
    :param cmd: 命令列表，例如 ['git', 'clone', 'url']
    :param cwd: 工作目录，None表示当前目录
    :return: 返回命令完整输出字符串
    """
    try:
        # 使用Popen实现实时输出
        result_lines = []
        with subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 错误流也输出到stdout
            text=True
        ) as proc:
            for line in proc.stdout:
                print(line, end='')  # 实时打印
                result_lines.append(line)
            proc.wait()  # 等待命令执行完成
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, cmd, output=''.join(result_lines))
        return ''.join(result_lines)
    except subprocess.CalledProcessError as e:
        print("命令执行失败:", ' '.join(cmd))
        print("错误信息:", e.output)
        return None

def remote_fetch(repo_path, remote_repo, remote_name='upstream'):
    remote = remote_repository_add(repo_path, remote_repo, remote_name)

    if not remote:
        print("❌ 远程仓库添加失败")
        return None

    repo = Repo(repo_path)
    if repo.bare:
        print("该路径不是有效的 Git 仓库")

    try:
        # 获取远程对象
        # remote = repo.remote(remote_name)

        # 从远程仓库获取最新的所有分支和提交信息
        print(f"Fetching from {remote_name} ({remote_repo})...")
        fetch_info = remote.fetch(prune=True)  # prune=True 可以清理已经删除的远程分支
        for remote_ref in remote.refs:
            branch_name = remote_ref.name.replace(f"{remote_name}/", "")
            if branch_name not in repo.heads:  # 本地不存在该分支
                repo.create_head(branch_name, remote_ref).set_tracking_branch(remote_ref)
                print(f"本地分支 {branch_name} 已创建并跟踪 {remote_ref}")

        print("远程分支与提交信息已同步完成 ✅")
        return repo

    except GitCommandError as e:
        print(f"执行 Git 命令失败: {e}")
    except Exception as e:
        print(f"发生未知错误: {e}")

def remote_repository_add(repo_path, remote_repo, remote_name='upstream', use_ssh=False, ssh_name=""):
    """
    Add or update a remote repository for the given local repo.

    :param repo_path: 本地 Git 仓库路径
    :param remote_repo: 远程仓库 (例如 "user/repo")
    :param remote_name: 远程仓库名 (默认 'origin')
    :return: Remote 对象
    """
    if use_ssh:
        remote_url = f'git@{ssh_name}:{remote_repo}.git'
    else:
        remote_url = f'https://github.com/{remote_repo}.git'

    repo = Repo(repo_path)
    if repo.bare:
        print(f"❌ {repo_path}不是有效的 Git 仓库")
        return None

    if remote_name in [r.name for r in repo.remotes]:
        remote = repo.remote(remote_name)
        if remote.url != remote_url:
            print(f"⚠️ Remote '{remote_name}' 已存在，但 URL 不匹配，更新为 {remote_url}")
            remote.set_url(remote_url)
        else:
            print(f"✅ Remote '{remote_name}' 已存在: {remote.url}")
    else:
        remote = repo.create_remote(remote_name, remote_url)
        print(f"➕ 已添加远程仓库：{remote_name} -> {remote_url}")

    return remote


def reset_branch(repo_path, remote_repo, branch, commit_sha):
    repo = remote_fetch(repo_path, remote_repo, remote_repo.split('/')[0])
    if not repo:
        return None

    try:
        print(f"🔄 Checking out branch '{branch}'...")
        repo.git.checkout(branch)
        print(f"⚡ Resetting branch '{branch}' to commit '{commit_sha}' (hard reset)...")
        repo.git.reset('--hard', commit_sha)
        print(f"✅ Branch '{branch}' is now at commit '{commit_sha}'")
    except GitCommandError as e:
        print(f"执行 Git 命令失败: {e}")
        print(f"❌ Reset '{commit_sha}', Branch: {branch}")
        return None
    except Exception as e:
        print(f"发生未知错误: {e}")
        print(f"❌ Reset '{commit_sha}', Branch: {branch}")
        return None
    print(f"✅ Reset '{commit_sha}', Branch: {branch}")
    return repo
    # if branch == default_branch:
    #     pass
    #     # TODO: reset --hard
    # else:
    #

def push(repo_path, branch, remote_name):
    # remote = remote_repository_add(repo_path, remote_repo, "origin")

    repo = Repo(repo_path)
    origin = repo.remote(name=remote_name)

    # 推送到远程分支
    try:
        push_info = origin.push(refspec=branch, force=True)[0]

        if push_info.flags & push_info.ERROR:
            print(f"推送失败: {push_info.summary}")
            return False

        print(f"成功推送触发分支 {branch}")
        return True

    except GitCommandError as e:
        print(f"推送失败: {e}")
        return False