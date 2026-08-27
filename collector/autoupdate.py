"""取得してJSONを作り、変わっていれば push する。

GitHub Actions がアカウントのロックで動かないため、このPCから走らせる。
Actions が復活したら、こちらのタスクを解除すればいい（両方動いても
中身が同じなら片方が空振りするだけで壊れはしない）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOG = REPO / "build" / "autoupdate.log"
REMOTE = "https://github.com/youzoom64/keizai-board.git"
ACCOUNT = "youzoom64"
# 更新するのはこの2つだけ。作業中のコードを巻き込まないため。
TRACKED = ["docs/data", "docs/index.html"]


def log(message: str) -> None:
    line = "{} {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(args, **kwargs):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300, **kwargs)


def push_token() -> str | None:
    """push に使うトークン。アクティブなアカウントに左右されないよう明示的に取る。"""
    proc = subprocess.run(["gh", "auth", "token", "--user", ACCOUNT],
                          capture_output=True, text=True, timeout=30)
    token = (proc.stdout or "").strip()
    return token if proc.returncode == 0 and token else None


def main() -> int:
    log("=== 開始 ===")

    # 1) 取得して書き出す
    env = dict(os.environ, KEIZAI_NO_RAW="1", PYTHONUTF8="1")
    proc = subprocess.run([sys.executable, "-m", "collector.export"],
                          cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=1800, env=env)
    if proc.returncode != 0:
        log("取得に失敗: " + (proc.stderr or "")[-500:])
        return 1
    for line in (proc.stdout or "").splitlines():
        if "取得完了" in line or "警告" in line:
            log(line.strip())

    # 2) 変わっていなければ何もしない
    run(["git", "add"] + TRACKED)
    if run(["git", "diff", "--staged", "--quiet"]).returncode == 0:
        log("更新なし")
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    committed = run(["git", "-c", "user.name=youzoom64",
                     "-c", "user.email=youzoom64@users.noreply.github.com",
                     "commit", "-m", "データ更新 " + stamp])
    if committed.returncode != 0:
        log("commit に失敗: " + (committed.stderr or "")[-300:])
        return 1

    # 3) push。トークンはURLに一度だけ載せ、ディスクには残さない。
    token = push_token()
    if not token:
        log("push 用のトークンが取れない（gh auth status を確認）")
        return 1
    url = REMOTE.replace("https://", "https://x-access-token:{}@".format(token))

    result = run(["git", "push", url, "HEAD:main"])
    if result.returncode != 0:
        # 他所から更新されていた場合は取り込んでからもう一度だけ試す
        log("push が弾かれた。取り込んで再試行する")
        run(["git", "fetch", url, "main"])
        rebased = run(["git", "rebase", "FETCH_HEAD"])
        if rebased.returncode != 0:
            run(["git", "rebase", "--abort"])
            log("取り込みに失敗。手動で確認が要る")
            return 1
        result = run(["git", "push", url, "HEAD:main"])

    if result.returncode != 0:
        log("push に失敗: " + (result.stderr or "").replace(token, "***")[-300:])
        return 1

    log("更新して push 完了 " + stamp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
