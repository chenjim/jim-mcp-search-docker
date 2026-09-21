#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jim-search 体检：服务健康 + token + 容器状态 + cookie 有效性。

    python3 doctor.py
    python3 doctor.py --no-cookie     # 跳过 cookie 探测（cookie 探测会消耗抓取额度）

cookie 探针只对内置表里的域名跑；未知域名自动跳过。
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from jimsearch import api, load_token  # noqa: E402

PROJECT = pathlib.Path("/vol1/1000/Tools/jim-search-docker")
COOKIE_DIR = PROJECT / "cookies"

# cookie.<name>.txt 对应的探针 URL
PROBES = {
    "toutiao": "https://www.toutiao.com/",
    "weibo": "https://s.weibo.com/top/summary",
    "baidu": "https://www.baidu.com/",
    "zhihu": "https://www.zhihu.com/",
}

WALL_HINTS = ("passport.", "login.", "sso.", "请登录", "登录后", "访客", "验证码")
MIN_CHARS = 3000

# 已知 cookie 无解的域名 —— 反爬是 JS 挑战，cookie 与 TLS 指纹都过不去，只能走 jina
KNOWN_UNFIXABLE = {"zhihu": "zse_ck JS 挑战，cookie 无效，固定走 jina"}


def check_health() -> bool:
    try:
        data = api("GET", "/health", timeout=5)
    except SystemExit:
        print("  ✗ 服务不可达 —— cd /vol1/1000/Tools/jim-search-docker && docker compose up -d")
        return False
    ok = data.get("status") == "ok"
    print(f"  {'✓' if ok else '✗'} /health → {data}")
    return ok


def check_token() -> bool:
    token = load_token()
    if token:
        print(f"  ✓ token 已解析（{len(token)} 字符）")
        return True
    print("  ✗ token 未找到 —— 查 ~/.hermes/.env 或 ~/.config/jim-search/token")
    return False


def check_container() -> None:
    try:
        proc = subprocess.run(
            ["docker", "compose", "ps"], cwd=str(PROJECT),
            capture_output=True, text=True, timeout=30,
        )
        text = (proc.stdout or proc.stderr).strip()
        print("  " + (text.replace("\n", "\n  ") if text else "（无输出）"))
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  ⚠ 查不到容器状态：{exc}")


def check_cookies() -> None:
    files = sorted(COOKIE_DIR.glob("cookie.*.txt"))
    if not files:
        print("  （cookies/ 下没有 cookie.*.txt）")
        return
    for path in files:
        name = path.name[len("cookie."):-len(".txt")]
        url = PROBES.get(name)
        if not url:
            print(f"  {path.name:22} 无内置探针，跳过")
            continue
        data = api("POST", "/scrape", body={"urls": [url]}, timeout=180)
        item = (data.get("results") or [{}])[0]
        status = item.get("status")
        source = item.get("scrape_source") or "-"
        text = item.get("markdown") or ""
        error = item.get("error") or ""
        low = (error + text[:2000]).lower()

        if name in KNOWN_UNFIXABLE:
            verdict = f"— 已知无解（{KNOWN_UNFIXABLE[name]}）"
        elif status != "ok":
            verdict = f"✗ 失败：{error[:60]}"
        elif any(hint.lower() in low for hint in WALL_HINTS):
            verdict = "✗ 疑似过期（撞到登录 / 访客墙）"
        elif len(text) < MIN_CHARS:
            verdict = f"⚠ 内容偏短（{len(text)} 字符），可能没吃到登录态"
        else:
            verdict = "✓ 有效"
        print(f"  {path.name:22} {source:9} {len(text):>8} 字符  {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="doctor.py", description="jim-search 体检")
    parser.add_argument("--no-cookie", action="store_true", help="跳过 cookie 探测（省额度）")
    args = parser.parse_args()

    print("[1] 服务健康")
    alive = check_health()

    print("\n[2] 鉴权 token")
    check_token()

    print("\n[3] 容器状态")
    check_container()

    print("\n[4] cookie 有效性")
    if args.no_cookie:
        print("  （--no-cookie，已跳过）")
    elif not alive:
        print("  （服务不可达，跳过）")
    else:
        check_cookies()


if __name__ == "__main__":
    main()
