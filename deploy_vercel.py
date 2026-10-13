#!/usr/bin/env python3
"""
把 chengpin 目录部署到 Vercel —— 不需要 npm、不需要 vercel CLI，只用标准库。

用法：
    VERCEL_TOKEN=xxxx python3 deploy_vercel.py            # 默认部署到生产环境
    VERCEL_TOKEN=xxxx python3 deploy_vercel.py --name 我的项目名

token 从 https://vercel.com/account/tokens 生成（Scope 选你的个人账号即可）。
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.vercel.com/v13/deployments"
HERE = os.path.dirname(os.path.abspath(__file__))

# 要上传的文件：仓库里除 git 内部文件外的东西
SKIP_DIRS = {".git", ".vercel", "node_modules", "__pycache__"}
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", "deploy_vercel.py", "deploy-vercel.sh"}


def collect(root):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_FILES or fn.startswith("."):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as fh:
                data = fh.read()
            files.append({"file": rel, "data": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
    return files


def main():
    token = os.environ.get("VERCEL_TOKEN", "").strip()
    if not token:
        sys.stderr.write(
            "\n缺少 VERCEL_TOKEN。\n\n"
            "  1. 打开 https://vercel.com/account/tokens\n"
            "  2. Create Token，名字随便填（比如 deploy），Scope 选你的个人账号\n"
            "  3. 复制那串 token，然后运行：\n\n"
            "     VERCEL_TOKEN=粘贴在这里 python3 deploy_vercel.py\n\n"
        )
        return 2

    name = "college-orbit"
    argv = sys.argv[1:]
    if "--name" in argv:
        name = argv[argv.index("--name") + 1]

    files = collect(HERE)
    if not any(f["file"] == "index.html" for f in files):
        sys.stderr.write("当前目录里没有 index.html，确认你是在 chengpin 目录下运行\n")
        return 2

    payload = {
        "name": name,
        "files": files,
        "target": "production",
        "projectSettings": {"framework": None, "buildCommand": None, "outputDirectory": None},
    }

    print("要上传 %d 个文件：%s" % (len(files), ", ".join(f["file"] for f in files)))
    print("项目名：%s" % name)

    req = urllib.request.Request(
        API + "?forceNew=1&skipAutoDetectionConfirmation=1",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        sys.stderr.write("部署失败：HTTP %s\n%s\n" % (e.code, detail[:1200]))
        if e.code == 403:
            sys.stderr.write("\n提示：token 可能无效，或者没有权限。\n")
        return 1
    except Exception as e:
        sys.stderr.write("部署失败：%s\n" % e)
        return 1

    url = body.get("url") or ""
    alias = body.get("alias") or []
    print("\n✅ 部署已创建")
    print("   部署 ID : %s" % body.get("id"))
    print("   状态    : %s" % body.get("readyState") or body.get("status"))
    if url:
        print("   预览地址: https://%s" % url)
    for a in alias:
        print("   正式地址: https://%s" % a)
    print("\n（首次部署生成的是 *.vercel.app 地址；绑定自定义域名请在 Vercel 后台操作）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
