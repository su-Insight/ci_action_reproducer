from __future__ import annotations

from typing import Any

import yaml

from utils.log_parser import classify_and_sort_os


def rewrite_workflow(workflow_content: str, insights: dict) -> str:
    parsed = yaml.safe_load(workflow_content)
    if not isinstance(parsed, dict):
        raise ValueError("Workflow content is not valid YAML mapping content.")

    replacement_os: dict[str, str] = {}
    sorted_os = classify_and_sort_os(insights["operating_systems"])
    runtime_labels = find_key_recursively(parsed, "runs-on")

    for runtime_label in runtime_labels:
        if not isinstance(runtime_label, str) or "latest" not in runtime_label:
            continue
        family = runtime_label.split("-latest", 1)[0]
        candidates = sorted_os.get(family, [])
        if candidates:
            replacement_os[runtime_label] = candidates[0]

    new_content = workflow_content
    for action_name, sha in insights["action_shas"].items():
        action = action_name.split("@", 1)[0]
        new_content = new_content.replace(action_name, f"{action}@{sha}")
    for old_os, new_os in replacement_os.items():
        new_content = new_content.replace(old_os, new_os)
    return new_content


def find_key_recursively(data: Any, target_key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key == target_key:
                found.append(value)
            else:
                found.extend(find_key_recursively(value, target_key))
    elif isinstance(data, list):
        for item in data:
            found.extend(find_key_recursively(item, target_key))
    return list(dict.fromkeys(found))
