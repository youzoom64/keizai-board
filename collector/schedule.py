"""このPCのタスクスケジューラに、1日2回の自動更新を登録する。

GitHub Actions がアカウントのロックで動かないための代替。
常駐は増やさず、OSに叩かせる。

    python -m collector.schedule register     登録する
    python -m collector.schedule status       状態を見る
    python -m collector.schedule unregister   解除する
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TASK_NAME = "KeizaiBoardUpdate"
REPO = Path(__file__).resolve().parents[1]
PYTHONW = Path(r"J:\system_tools\venvs\py310-common\Scripts\pythonw.exe")
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
# 朝は前日の米国市場の確定値、夕方はその日の国債金利と株価を拾う。
TIMES = ("08:10", "18:40")
NEVER_RUN = 267011  # 一度も実行されていない時の結果コード


def _run(script: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True, text=True, timeout=90,
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def status() -> dict:
    script = (
        "$t = Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue; "
        "if ($null -eq $t) {{ '{{\"registered\": false}}' }} else {{ "
        "$i = Get-ScheduledTaskInfo -TaskName '{name}'; "
        "[pscustomobject]@{{ registered = $true; state = [string]$t.State; "
        "last = [string]$i.LastRunTime; result = $i.LastTaskResult; "
        "next = [string]$i.NextRunTime }} | ConvertTo-Json -Compress }}"
    ).format(name=TASK_NAME)
    code, out, err = _run(script)
    if code != 0:
        return {"registered": False, "error": err or out}
    try:
        return json.loads(out) if out else {"registered": False}
    except ValueError:
        return {"registered": False, "error": out}


def register() -> dict:
    if not PYTHONW.exists():
        return {"ok": False, "error": "pythonw が見つからない: {}".format(PYTHONW)}
    triggers = ", ".join(
        "(New-ScheduledTaskTrigger -Daily -At '{}')".format(t) for t in TIMES)
    script = (
        "$a = New-ScheduledTaskAction -Execute '{py}' "
        "-Argument '-m collector.autoupdate' -WorkingDirectory '{cwd}'; "
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-DontStopIfGoingOnBatteries -AllowStartIfOnBatteries "
        "-ExecutionTimeLimit (New-TimeSpan -Minutes 30); "
        "Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger {trg} "
        "-Settings $s -Description '経済ボードのデータ更新とpush' -Force | Out-Null; 'ok'"
    ).format(py=PYTHONW, cwd=REPO, name=TASK_NAME, trg=triggers)
    code, out, err = _run(script)
    if code != 0:
        return {"ok": False, "error": err or out}
    return {"ok": True, "times": list(TIMES)}


def unregister() -> dict:
    script = ("Unregister-ScheduledTask -TaskName '{name}' -Confirm:$false "
              "-ErrorAction SilentlyContinue; 'ok'").format(name=TASK_NAME)
    code, out, err = _run(script)
    return {"ok": code == 0, "error": err or ""}


def status_text() -> str:
    info = status()
    if info.get("error"):
        return "自動更新: 状態を取れない（{}）".format(info["error"][:80])
    if not info.get("registered"):
        return "自動更新: 未登録"
    parts = ["自動更新: 登録済み {}".format("・".join(TIMES))]
    if info.get("next"):
        parts.append("次回 {}".format(info["next"]))
    last, result = info.get("last") or "", info.get("result")
    if result == NEVER_RUN or last.startswith("11/30/1999"):
        parts.append("まだ実行されていない")
    elif last:
        parts.append("前回 {}（{}）".format(
            last, "成功" if result == 0 else "結果コード {}".format(result)))
    return " / ".join(parts)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "register":
        print(json.dumps(register(), ensure_ascii=False))
    elif action == "unregister":
        print(json.dumps(unregister(), ensure_ascii=False))
    print(status_text())
