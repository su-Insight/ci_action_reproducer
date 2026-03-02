import os
import re
from collections import defaultdict

import yaml


def fetch_action_infos(repo, run_id, log_path):
    pattern = f"{repo.split('/')[-1]}_{run_id}"
    action_infos = {}
    for file_name in os.listdir(log_path):
        if pattern in file_name:
            try:
                action_info = fetch_action_info(f"{log_path}/{file_name}")
                action_infos.update(action_info)
            except Exception as e:
                print(e)

    print(action_infos)
    return action_infos

def fetch_action_info(log_path):
    timestamp_pattern = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*'
    action_info = {}

    # 解析所有的action下载过程，获取日志运行时的哈希值
    action_sha_pattern = r"Download action repository '([\w/-]+@\S+)' \(SHA:\s*([a-f0-9]+)\)"

    action_sha = {}
    action_sha_start = False
    with open(log_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()  # 读取所有行
        for line in lines:
            match = re.search(action_sha_pattern, line)
            if match:
                action_sha_start = True
                action = match.group(1)
                sha = match.group(2)
                action_sha[action] = sha
            elif action_sha_start:
                break

    with open(log_path, 'r', encoding='utf-8') as file:
        content = file.read()
        content = re.sub(timestamp_pattern, '', content, flags=re.MULTILINE)

        pattern = r"##\[group\]Download immutable action package '([^\n]*)'\s*Version: \S+\s*Digest: sha256:\S+\s*Source commit SHA: ([^\n]*)(?:\n|$)"
        matches = re.findall(pattern, content)
        for name, sha in matches:
            # action_name = name.split('@')[0]
            action_sha[name] = sha

    action_info['action_sha'] = action_sha

    # 解析日志所使用的os镜像版本
    image_pattern = re.compile(r"[a-z]+-[\d.]+")
    with open(log_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()  # 读取所有行
        for i, line in enumerate(lines):
            line = re.sub(timestamp_pattern, "", line)
            if "##[group]Runner Image" in line:  # 检测到目标行
                if i + 1 < len(lines):  # 确保下一行存在
                    next_line = re.sub(timestamp_pattern, "", lines[i + 1].strip())  # 获取下一行并去除两端空格
                    if next_line.startswith("Image:"):  # 判断下一行是否以 "Image:" 开头
                        image_info = next_line.split("Image:")[1].strip()
                        match = re.match(image_pattern, image_info)
                        action_info['os'] = [match.group(0)]
                        break



    return action_info

def parse_version(os_str):
    """
    提取版本号并转为 tuple 以便比较
    macOS-14  -> (14,)
    ubuntu-24.04 -> (24, 4)
    windows-2022 -> (2022,)
    """
    # 匹配数字部分
    nums = re.findall(r"\d+", os_str)
    return tuple(map(int, nums)) if nums else (0,)

def classify_and_sort_os(found):
    result = defaultdict(list)

    for item in found:
        # 提取操作系统名称（非数字部分）
        match = re.match(r"([a-zA-Z]+)", item)
        if match:
            os_name = match.group(1)
            result[os_name].append(item)

    # 每个操作系统内部按版本号排序（新到旧）
    for os_name in result:
        result[os_name].sort(key=parse_version, reverse=True)

    return dict(result)

def find_key_recursively(data, target_key):
    """
    Recursively search for all values of target_key in nested dict/list structures.
    Returns a list of found values.
    """
    found = []

    if isinstance(data, dict):
        for key, value in data.items():
            if key == target_key:
                found.append(value)
            else:
                found.extend(find_key_recursively(value, target_key))
    elif isinstance(data, list):
        for item in data:
            found.extend(find_key_recursively(item, target_key))

    # return classify_and_sort_os(list(set(found)))
    return list(set(found))

# Find all 'runs-on'
content = '''
# This workflow will build a Java project with Maven
# For more information see: https://help.github.com/actions/language-and-framework-guides/building-and-testing-java-with-maven

name: Java CI with Maven

on:
  push:
    branches: [ master ]
  pull_request:
    branches: [ master ]

jobs:
  build:
    timeout-minutes: 60
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v2
    - name: Set up JDK 17
      uses: actions/setup-java@v1
      with:
        java-version: 17
    - name: Set up Maven
      uses: stCarolas/setup-maven@v4.5
      with:
        maven-version: 3.8.4
    - uses: actions/cache@v4
      with:
        path: ~/.m2/repository
        key: ${{ runner.os }}-maven-${{ hashFiles('**/pom.xml') }}
        restore-keys: |
          ${{ runner.os }}-maven
    - name: Compilation and Installation
      run: bash scripts/build.sh install
    - name: publish coverage report
      run: bash <(curl -s https://codecov.io/bash)
'''

a = find_key_recursively(yaml.safe_load(content), 'runs-on')
print(a)