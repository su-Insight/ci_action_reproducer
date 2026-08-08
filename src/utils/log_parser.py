from __future__ import annotations

import re
from collections import defaultdict


TIMESTAMP_PREFIX = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*"
ACTION_SHA_PATTERN = re.compile(
    r"Download action repository '([\w./-]+@\S+)' \(SHA:\s*([a-f0-9]+)\)"
)
IMMUTABLE_ACTION_PATTERN = re.compile(
    r"##\[group\]Download immutable action package '([^\n]*)'\s*"
    r"Version: \S+\s*Digest: sha256:\S+\s*Source commit SHA: ([^\n]*)(?:\n|$)"
)
RUNNER_IMAGE_PATTERN = re.compile(r"[a-z]+-[\d.]+")


def parse_logs(raw_logs: list[str]) -> dict:
    aggregated_action_shas: dict[str, str] = {}
    aggregated_os: list[str] = []

    for raw_log in raw_logs:
        action_shas, operating_systems = parse_log_text(raw_log)
        aggregated_action_shas.update(action_shas)
        aggregated_os.extend(operating_systems)

    unique_os = sorted(set(aggregated_os), key=parse_version, reverse=True)
    return {
        "action_shas": aggregated_action_shas,
        "operating_systems": unique_os,
    }


def parse_log_text(content: str) -> tuple[dict[str, str], list[str]]:
    stripped = re.sub(TIMESTAMP_PREFIX, "", content, flags=re.MULTILINE)

    action_shas: dict[str, str] = {}
    for action_name, sha in ACTION_SHA_PATTERN.findall(content):
        action_shas[action_name] = sha
    for action_name, sha in IMMUTABLE_ACTION_PATTERN.findall(stripped):
        action_shas[action_name] = sha

    operating_systems: list[str] = []
    lines = stripped.splitlines()
    for index, line in enumerate(lines):
        if "##[group]Runner Image" not in line or index + 1 >= len(lines):
            continue
        next_line = lines[index + 1].strip()
        if not next_line.startswith("Image:"):
            continue
        image_info = next_line.split("Image:", 1)[1].strip()
        match = RUNNER_IMAGE_PATTERN.match(image_info)
        if match:
            operating_systems.append(match.group(0))
            break

    return action_shas, operating_systems


def parse_version(os_name: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", os_name)
    return tuple(map(int, numbers)) if numbers else (0,)


def classify_and_sort_os(operating_systems: tuple[str, ...] | list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for item in operating_systems:
        match = re.match(r"([a-zA-Z]+)", item)
        if match:
            grouped[match.group(1)].append(item)

    for os_name, values in grouped.items():
        values.sort(key=parse_version, reverse=True)
        grouped[os_name] = values
    return dict(grouped)
