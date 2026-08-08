# CI Action Reproducer

CI Action Reproducer replays the important context of a historical GitHub Actions run and,
when needed, pushes reconstructed head/base branches to your fork and creates a replay pull
request.

This repository is a local CLI tool. It is intended for manual research and replay workflows,
not for unattended production automation.

## When To Use This Tool

Use this tool when you already know a specific historical GitHub Actions run that you want to
replay or study.

Typical cases:

- reproduce a historical CI failure
- reconstruct a PR-triggered workflow in your own fork
- inspect which exact action SHAs and runner image versions were used at the time
- trigger a fresh run using a branch state that matches the original run as closely as possible

This tool is not a generic GitHub Actions migration framework. It assumes you already know the
target `repository_name` and `run_id`.

## Structure

```text
README.md
requirements.txt
config.example.yml
data/
  builds.csv
src/
  main.py
  git_operation.py
  utils/
    github_client.py
    log_parser.py
    workflow_rewriter.py
    storage.py
```

## What The Tool Does

For each `repository_name + run_id` pair, the tool:

1. Reads the original GitHub Actions run metadata
2. Downloads available job logs
3. Extracts action SHAs and runner image versions from the logs
4. Fetches the original workflow file
5. Rewrites the workflow into a local preview
6. Reconstructs the head/base branches in your fork
7. Creates fresh empty commits so GitHub sees new commits
8. Creates a replay pull request when the original event is a PR

The replay behavior is different depending on the original event type:

- `push`
  - rebuilds the target branch in your fork
  - creates one fresh empty commit
  - force-pushes the branch

- `pull_request` / `pull_request_target`
  - rebuilds the original PR head branch
  - rebuilds the original PR base branch
  - creates one fresh empty commit on each side
  - pushes both sides
  - creates a new replay PR through GitHub API

## Local Prerequisites

You need these before running the tool:

- Python 3.11+
- `git`
- A GitHub account that can create forks
- SSH keys configured for the GitHub account(s) used by this tool
- Network access to GitHub API and GitHub git remotes

Install dependencies:

```bash
pip install -r requirements.txt
```

If you use Conda on Windows, a typical setup is:

```powershell
conda create -n ci-action-reproducer python=3.11 -y
conda activate ci-action-reproducer
pip install -r requirements.txt
```

Verify the interpreter:

```powershell
python --version
```

## Required GitHub Preparation

Before running the tool, prepare GitHub in this order.

### 1. Fork the target repository

You must fork the repository you want to replay.

Example:

- original repo: `apache/tinkerpop`
- your fork: `su-Insight/tinkerpop`

If you plan to simulate cross-repo PRs, you need:

- a primary fork for the base side
- a secondary fork for the head side

Example:

- original repo: `apache/tinkerpop`
- primary fork: `su-Insight/tinkerpop`
- secondary fork: `your-second-account/tinkerpop`

### 2. Enable GitHub Actions in the fork

After forking, open the fork in GitHub and enable Actions manually if GitHub shows the usual
"Actions are disabled for this fork" banner.

The tool can push reconstructed branches, but GitHub will not run workflows unless Actions are
enabled in the target fork repository.

Do this for every fork that may receive pushed replay branches.

### 3. Configure local SSH

The tool pushes branches through SSH. That means your local machine must be able to push to the
target fork(s) without interactive prompts.

At minimum, this must work:

```bash
ssh -T git@github.com
```

If you use two GitHub accounts, configure two SSH hosts in `~/.ssh/config`.

Example:

```sshconfig
Host github-main
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_main

Host github-second
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_second
```

Then make sure these work:

```bash
ssh -T git@github-main
ssh -T git@github-second
```

Also make sure your fork repositories are writable with those identities.

Useful checks:

```bash
git ls-remote git@github-main:su-Insight/tinkerpop.git
git ls-remote git@github-second:your-second-account/tinkerpop.git
```

## GitHub Token Permissions

This tool calls GitHub REST APIs for:

- run metadata
- workflow content
- job logs
- PR lookup
- fork creation
- replay PR creation

### Main token

`main_token` should belong to the primary GitHub account.

It needs permission to:

- read repository metadata
- read Actions runs and logs
- read commits and workflows
- create forks
- create pull requests in the primary fork

For a fine-grained personal access token, the practical minimum is:

- Repository access to the target repository and your fork
- Actions: read
- Contents: read
- Pull requests: read and write
- Metadata: read

If you use a classic personal access token, it should cover the equivalent repository scopes for:

- repo access to the fork you will create PRs in
- workflow/actions read access

### Secondary token

`secondary_token` is only needed when:

- the original run came from a cross-repo PR
- and `simulated_commit: true`

It should belong to the secondary GitHub account and needs permission to:

- read repository metadata
- create or access the secondary fork
- create branches in the secondary fork
- possibly create the replay PR when the head side belongs to the second account

Practical rule:

- if the head replay branch is pushed to the second fork, the second token may be used for PR
  creation
- if the PR is created in the primary fork, the token used must still have access to that target
  repository

## Configuration

Copy the example file first:

```bash
cp config.example.yml config.yml
```

or on PowerShell:

```powershell
Copy-Item config.example.yml config.yml
```

Example `config.yml`:

```yaml
main_token: "ghp_xxx_primary"
secondary_token: "ghp_xxx_secondary"

del_traces: false
simulated_commit: true

local_ssh_name: "github-main"
local_ssh_names:
  - "github-main"
  - "github-second"

enable_min_replaceable_os_version: false
enable_min_replaceable_action_version: false

preview_only: true
```

### Single-account example

If you only use one GitHub account and do not simulate cross-repo PRs:

```yaml
main_token: "ghp_xxx_primary"
secondary_token: ""

simulated_commit: false

local_ssh_name: "github-main"
local_ssh_names:
  - "github-main"

preview_only: true
```

### Two-account example

If you want to simulate cross-repo PR behavior:

```yaml
main_token: "ghp_xxx_primary"
secondary_token: "ghp_xxx_secondary"

simulated_commit: true

local_ssh_names:
  - "github-main"
  - "github-second"

preview_only: false
```

### Config meanings

- `main_token`
  - Required
  - Used for the primary account and main fork operations

- `secondary_token`
  - Optional
  - Required only for simulated cross-repo PR replay

- `simulated_commit`
  - When `true`, cross-repo PR replay uses a second account/fork for the head branch

- `local_ssh_name`
  - Backward-compatible single SSH host alias

- `local_ssh_names`
  - Preferred SSH host aliases
  - Index 0 = primary account
  - Index 1 = secondary account

- `preview_only`
  - When `true`, stops after logs + workflow preview
  - No branch push and no replay PR creation

- `main_token` + `local_ssh_names[0]`
  - Should refer to the same GitHub account in normal use

- `secondary_token` + `local_ssh_names[1]`
  - Should refer to the same second GitHub account when `simulated_commit: true`

## Input CSV Format

The tool reads `data/builds.csv`.

Minimal useful format:

```csv
repository_name,run_id,status
apache/tinkerpop,123456789,0
```

Real example shape:

```csv
repository_name,run_id,status,exception,new_repository,new_run_id,conclusion
apache/tinkerpop,20345678901,0,,,,
apache/kafka,20345678902,0,,,,
```

Current storage also supports these columns:

```csv
repository_name,run_id,status,exception,new_repository,new_run_id,conclusion
```

### Field meanings

- `repository_name`
  - GitHub repository in `owner/repo` format

- `run_id`
  - GitHub Actions run ID

- `status`
  - Any value other than `success` is treated as pending by the current code

### How To Find `run_id`

From GitHub Actions UI:

1. Open the repository
2. Open the target workflow run
3. The URL will contain the run ID

Example:

```text
https://github.com/apache/tinkerpop/actions/runs/20345678901
```

Here:

- `repository_name = apache/tinkerpop`
- `run_id = 20345678901`

## Run

Run directly as a script:

```bash
python src/main.py
```

Dry run first:

```bash
python src/main.py --help
```

Run a specific build:

```bash
python src/main.py --run-id 17728568623
```

Specify custom config and CSV paths:

```bash
python src/main.py --config config.yml --builds data/builds.csv --run-id 17728568623
```

### Recommended First Run

For the first execution on a new repository:

1. set `preview_only: true`
2. run one known `run_id`
3. inspect generated logs and workflow preview
4. switch to `preview_only: false`
5. run the same `run_id` again if the preview looks correct

## Recommended Operating Procedure

Use this order when replaying a run:

1. Prepare fork(s)
2. Enable Actions in the fork(s)
3. Verify SSH push access
4. Fill in `config.yml`
5. Fill in `data/builds.csv`
6. Start with `preview_only: true`
7. Verify generated workflow preview and logs
8. Change `preview_only: false`
9. Run the replay

### Detailed PowerShell Sequence

```powershell
cd C:\Users\17554\PycharmProjects\Paper\ci_action_reproducer
conda activate ci-action-reproducer
Copy-Item config.example.yml config.yml
notepad config.yml
notepad data\builds.csv
python src\main.py --help
python src\main.py --run-id 17728568623
```

If preview mode is successful, change:

```yaml
preview_only: false
```

Then run:

```powershell
python src\main.py --run-id 17728568623
```

## Output

The tool writes runtime artifacts to:

- `artifacts/logs/`
- `artifacts/workflows/`
- `artifacts/action_reproducer.log`
- `clone/`

Important values generated during replay may include:

- new head replay commit SHA
- new base replay commit SHA
- replay pull request URL

The replay result may also update CSV result fields such as:

- `status`
- `exception`
- `new_repository`
- `pull_request_url` if your local result structure records it

## Notes About PR Replay

For PR-based runs, the tool currently:

- resolves the original head repository from run metadata
- queries PRs by `head repo + head commit`
- rebuilds both head and base branches locally
- creates a fresh empty commit on both branches
- pushes both branches
- creates a replay PR through GitHub API

This means replay PR creation depends on:

- valid SSH access for branch pushes
- valid PAT permissions for PR creation
- correct fork layout for primary and secondary accounts

### Important PR Replay Details

- The tool queries PRs by `head repo + head commit`
- The tool creates a new empty commit on the replayed head branch
- The tool creates a new empty commit on the replayed base branch
- The empty commit message tries to reuse the original commit message
- The tool records the new replay head/base SHAs after push
- The tool then creates a fresh replay PR through GitHub API

### What `preview_only` Actually Skips

When `preview_only: true`, the tool still does:

- run metadata lookup
- workflow download
- job log download
- workflow rewrite preview

But it does not do:

- branch push
- empty commit creation
- replay PR creation