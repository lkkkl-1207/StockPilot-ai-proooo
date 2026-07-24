from __future__ import annotations

import base64
import json
import os
from typing import Any

import requests


GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPOSITORY = os.getenv(
    "GITHUB_REPOSITORY",
    "",
).strip()
GITHUB_BRANCH = os.getenv(
    "GITHUB_BRANCH",
    "main",
).strip()

WATCHLIST_PATH = "backend/watchlist.json"
GITHUB_API_BASE = "https://api.github.com"


def _validate_config() -> None:
    if not GITHUB_TOKEN:
        raise ValueError("没有配置 GITHUB_TOKEN。")

    if not GITHUB_REPOSITORY:
        raise ValueError("没有配置 GITHUB_REPOSITORY。")

    if "/" not in GITHUB_REPOSITORY:
        raise ValueError(
            "GITHUB_REPOSITORY 格式不正确，"
            "应为 owner/repository。"
        )


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _contents_url() -> str:
    return (
        f"{GITHUB_API_BASE}/repos/"
        f"{GITHUB_REPOSITORY}/contents/"
        f"{WATCHLIST_PATH}"
    )


def get_watchlist_file() -> tuple[dict[str, Any], str]:
    """读取 GitHub 仓库里的 watchlist.json 和文件 SHA。"""
    _validate_config()

    response = requests.get(
        _contents_url(),
        headers=_headers(),
        params={"ref": GITHUB_BRANCH},
        timeout=30,
    )

    if response.status_code == 401:
        raise PermissionError(
            "GitHub Token 无效或已经过期。"
        )

    if response.status_code == 403:
        raise PermissionError(
            "GitHub Token 没有读取仓库内容的权限。"
        )

    if response.status_code == 404:
        raise FileNotFoundError(
            f"仓库中找不到 {WATCHLIST_PATH}。"
        )

    response.raise_for_status()
    payload = response.json()

    encoded_content = payload.get("content")
    sha = payload.get("sha")

    if not isinstance(encoded_content, str) or not sha:
        raise ValueError(
            "GitHub 返回的观察名单文件格式不正确。"
        )

    decoded = base64.b64decode(
        encoded_content.replace("\n", "")
    ).decode("utf-8")

    settings = json.loads(decoded)

    if not isinstance(settings, dict):
        raise ValueError(
            "watchlist.json 顶层必须是 JSON 对象。"
        )

    return settings, str(sha)


def get_watchlist() -> list[str]:
    settings, _ = get_watchlist_file()
    raw_watchlist = settings.get("watchlist", [])

    if not isinstance(raw_watchlist, list):
        raise ValueError(
            "watchlist.json 中的 watchlist 必须是列表。"
        )

    return [
        str(symbol).strip().upper()
        for symbol in raw_watchlist
        if str(symbol).strip()
    ]


def update_watchlist(
    watchlist: list[str],
    sha: str,
    commit_message: str,
    current_settings: dict[str, Any],
) -> list[str]:
    """把观察名单更新回 GitHub。"""
    _validate_config()

    cleaned_watchlist = sorted(
        {
            str(symbol).strip().upper()
            for symbol in watchlist
            if str(symbol).strip()
        }
    )

    updated_settings = dict(current_settings)
    updated_settings["watchlist"] = cleaned_watchlist

    encoded_content = base64.b64encode(
        (
            json.dumps(
                updated_settings,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    ).decode("ascii")

    response = requests.put(
        _contents_url(),
        headers=_headers(),
        json={
            "message": commit_message,
            "content": encoded_content,
            "sha": sha,
            "branch": GITHUB_BRANCH,
        },
        timeout=30,
    )

    if response.status_code == 401:
        raise PermissionError(
            "GitHub Token 无效或已经过期。"
        )

    if response.status_code == 403:
        raise PermissionError(
            "GitHub Token 没有写入仓库内容的权限。"
        )

    if response.status_code == 409:
        raise RuntimeError(
            "观察名单刚刚被其他操作修改，"
            "请重新发送一次指令。"
        )

    response.raise_for_status()
    return cleaned_watchlist


def add_symbol(symbol: str) -> tuple[list[str], bool]:
    normalized = symbol.strip().upper()

    if not normalized:
        raise ValueError("股票代码不能为空。")

    settings, sha = get_watchlist_file()
    current = [
        str(item).strip().upper()
        for item in settings.get("watchlist", [])
        if str(item).strip()
    ]

    if normalized in current:
        return current, False

    current.append(normalized)

    updated = update_watchlist(
        watchlist=current,
        sha=sha,
        commit_message=(
            f"Add {normalized} to StockPilot watchlist"
        ),
        current_settings=settings,
    )

    return updated, True


def remove_symbol(symbol: str) -> tuple[list[str], bool]:
    normalized = symbol.strip().upper()

    if not normalized:
        raise ValueError("股票代码不能为空。")

    settings, sha = get_watchlist_file()
    current = [
        str(item).strip().upper()
        for item in settings.get("watchlist", [])
        if str(item).strip()
    ]

    if normalized not in current:
        return current, False

    updated = update_watchlist(
        watchlist=[
            item
            for item in current
            if item != normalized
        ],
        sha=sha,
        commit_message=(
            f"Remove {normalized} from StockPilot watchlist"
        ),
        current_settings=settings,
    )

    return updated, True