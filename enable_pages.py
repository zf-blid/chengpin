#!/usr/bin/env python3
"""
给 GitHub 仓库开启 Pages —— 不需要 npm、不需要 gh CLI，只用标准库。

用法：
    GITHUB_TOKEN=xxxx python3 enable_pages.py
    GITHUB_TOKEN=xxxx python3 enable_pages.py 用户名 仓库名 分支

token 从 https://github.com/settings/tokens/new?scopes=repo&description=chengpin-pages 生成，
勾 repo 一项就够了。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

OWNER = sys.argv[1] if len(sys.argv) > 1 else "zf-blid"
REPO = sys.argv[2] if len(sys.argv) > 2 else "chengpin"
BRANCH = sys.argv[3] if len(sys.argv) > 3 else "main"
API = "https://api.github.com"


def call(method, path, token, payload=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "chengpin-pages-setup",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body


def main():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        sys.stderr.write(
            "\n缺少 GITHUB_TOKEN。\n\n"
            "  1. 打开 https://github.com/settings/tokens/new?scopes=repo&description=chengpin-pages\n"
            "  2. 拉到最下面点 Generate token\n"
            "  3. 复制那串 ghp_ 开头的，然后运行：\n\n"
            "     GITHUB_TOKEN=粘贴在这里 python3 enable_pages.py\n\n"
        )
        return 2

    print("仓库：%s/%s，分支：%s" % (OWNER, REPO, BRANCH))

    # --- 先自检：token 是谁、对这个仓库有没有写权限 ---
    c, me = call("GET", "/user", token)
    if c == 200:
        print("token 身份: %s" % me.get("login"))
    else:
        sys.stderr.write("token 无效或已过期：HTTP %s %s\n" % (c, me))
        return 1

    c, repo = call("GET", "/repos/%s/%s" % (OWNER, REPO), token)
    if c == 404:
        sys.stderr.write(
            "\n❌ 这个 token 看不到 %s/%s 仓库。\n"
            "   原因：生成 token 时 Repository access 没把 %s 选进去。\n"
            "   修法：重新生成，Repository access 选 “Only select repositories”，勾上 %s。\n\n"
            % (OWNER, REPO, REPO, REPO)
        )
        return 1
    if c != 200:
        sys.stderr.write("读取仓库失败：HTTP %s %s\n" % (c, repo))
        return 1

    perms = repo.get("permissions") or {}
    print("仓库权限: admin=%s push=%s pull=%s | 可见性=%s"
          % (perms.get("admin"), perms.get("push"), perms.get("pull"), repo.get("visibility")))
    if not perms.get("push"):
        sys.stderr.write(
            "\n❌ 找到原因了：这个 token 对 %s 只有【只读】权限，没有写权限。\n"
            "   开启 Pages 属于写操作，所以被拒（那个 403 就是这么来的）。\n\n"
            "   你多半是在 Repository access 里选了 “Public Repositories”——\n"
            "   对公开仓库，fine-grained token 只给只读。\n\n"
            "   修法（二选一）：\n"
            "   A. 重新生成 fine-grained token：Repository access 选\n"
            "      “Only select repositories” → 勾上 %s；\n"
            "      权限保持 Pages: Read and write、Contents: Read-only、Metadata: Read-only。\n"
            "   B. 直接用 classic token（基本必成）：\n"
            "      https://github.com/settings/tokens/new?scopes=repo&description=chengpin-pages\n\n"
            % (REPO, REPO)
        )
        return 1

    # 先看有没有开过
    code, cur = call("GET", "/repos/%s/%s/pages" % (OWNER, REPO), token)
    payload = {"source": {"branch": BRANCH, "path": "/"}}

    if code == 200:
        print("Pages 之前已开启，正在更新配置…")
        code, res = call("PUT", "/repos/%s/%s/pages" % (OWNER, REPO), token, payload)
    elif code == 404:
        print("Pages 未开启，正在开启…")
        code, res = call("POST", "/repos/%s/%s/pages" % (OWNER, REPO), token, payload)
    else:
        sys.stderr.write("查询失败：HTTP %s %s\n" % (code, cur))
        if code in (401, 403):
            sys.stderr.write("提示：token 无效或权限不足，需要 repo 权限。\n")
        return 1

    if code not in (200, 201, 204):
        sys.stderr.write("开启失败：HTTP %s %s\n" % (code, res))
        if code == 403:
            sys.stderr.write(
                "\n提示：仓库写权限通过（push=true），但 Pages 接口仍被拒。\n"
                "      这种情况 fine-grained token 常常不行，直接用 classic token 最稳：\n"
                "      https://github.com/settings/tokens/new?scopes=repo&description=chengpin-pages\n"
            )
        return 1

    url = (res or {}).get("html_url") or "https://%s.github.io/%s/" % (OWNER, REPO)
    print("✅ 已开启")
    print("   地址: %s" % url)

    # 等构建
    print("\n等待构建…")
    for i in range(20):
        time.sleep(15)
        c, b = call("GET", "/repos/%s/%s/pages/builds/latest" % (OWNER, REPO), token)
        if c == 200:
            st = b.get("status")
            print("   [%02d] 状态: %s" % (i + 1, st))
            if st in ("built", "errored"):
                if st == "errored":
                    print("\n构建报错：%s" % b.get("error", {}).get("message"))
                    return 1
                break
        else:
            print("   [%02d] 查询构建状态失败 HTTP %s" % (i + 1, c))

    print("\n去浏览器打开看看：%s" % url)
    print("（首次生效可能要等一两分钟）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
