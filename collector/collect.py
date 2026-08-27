"""取得ラン。冪等で、途中で失敗しても他の系列は最後まで取り切る。"""
from __future__ import annotations

from . import boj, sources, store
from .series import CYCLE_DAILY, SERIES, Series


def _points_for(spec: tuple, full: bool, mof_cache: dict) -> list[tuple[str, float]]:
    source = spec[0]
    if source == "mof":
        if "data" not in mof_cache:
            # 全履歴CSVは前月末までしか入っていないので、当月分を必ず重ねる。
            merged: dict[str, dict[str, float]] = {}
            parts = [sources.fetch_mof(full=True)] if full else []
            parts.append(sources.fetch_mof(full=False))
            for part in parts:
                for tenor, points in part.items():
                    merged.setdefault(tenor, {}).update(dict(points))
            mof_cache["data"] = {t: sorted(v.items()) for t, v in merged.items()}
        return mof_cache["data"].get(spec[1], [])
    if source == "yahoo":
        return sources.fetch_yahoo(spec[1], full=full)
    if source == "fred":
        return sources.fetch_fred(spec[1], full=full)
    if source == "dashboard":
        return sources.fetch_dashboard(spec[1], spec[2], spec[3], full=full)
    raise ValueError("未知の取得元: {}".format(source))


def collect(full: bool | None = None, only: list[str] | None = None,
            cycles: list[str] | None = None, with_boj: bool = True,
            progress=None) -> list[dict]:
    """全系列を取得してDBへ入れる。

    full=None なら DBが空の時だけ全履歴、それ以外は差分。
    cycles で日次だけ／月次だけの取得もできる。
    戻りは系列ごとの結果リスト。GUI側はこれをそのまま状況表示に使う。
    """
    conn = store.connect()
    if full is None:
        full = store.is_empty(conn)

    targets: list[Series] = [s for s in SERIES
                             if (not only or s.id in only)
                             and (not cycles or s.cycle in cycles)]
    total = len(targets) + (1 if with_boj else 0)
    mof_cache: dict = {}
    report: list[dict] = []

    for i, series in enumerate(targets, 1):
        if progress:
            progress(i, total, series.label)

        errors: list[str] = []
        done = False
        for spec in series.sources:
            try:
                points = _points_for(spec, full, mof_cache)
                if not points:
                    raise ValueError("データが0件")
                if series.scale != 1.0:
                    points = [(d, v * series.scale) for d, v in points]
                rows = store.upsert(conn, series.id, points)
                store.log_fetch(conn, series.id, spec[0], rows, "ok")
                report.append({"id": series.id, "label": series.label, "status": "ok",
                               "source": spec[0], "rows": rows,
                               "latest": points[-1][0], "message": ""})
                done = True
                break
            except Exception as exc:
                errors.append("{}: {}: {}".format(spec[0], type(exc).__name__, exc))
                # 代替元があるなら次を試す。ここで止めない。
                continue

        if not done:
            message = " / ".join(errors)
            store.log_fetch(conn, series.id, "-", 0, "error", message)
            report.append({"id": series.id, "label": series.label, "status": "error",
                           "source": "-", "rows": 0, "latest": "", "message": message})

    conn.close()

    if with_boj:
        if progress:
            progress(total, total, "日銀会合日程")
        try:
            meetings = boj.refresh()
            report.append({"id": "boj_meetings", "label": "日銀会合日程", "status": "ok",
                           "source": "boj", "rows": len(meetings),
                           "latest": meetings[-1]["start"] if meetings else "", "message": ""})
        except Exception as exc:
            report.append({"id": "boj_meetings", "label": "日銀会合日程", "status": "error",
                           "source": "boj", "rows": 0, "latest": "",
                           "message": "{}: {}".format(type(exc).__name__, exc)})
    return report


def summarize(report: list[dict]) -> str:
    ok = [r for r in report if r["status"] == "ok"]
    ng = [r for r in report if r["status"] != "ok"]
    parts = ["取得完了 {}/{} 系列".format(len(ok), len(report))]
    latest = [r["latest"] for r in ok if r["id"] != "boj_meetings" and r["latest"]]
    if latest:
        parts.append("最新 " + max(latest))
    for r in ng:
        parts.append("失敗: {} ({})".format(r["label"], r["message"][:120]))
    return " / ".join(parts)


if __name__ == "__main__":
    import sys

    full = "--full" in sys.argv
    cycles = None
    if "--daily" in sys.argv:
        cycles = [CYCLE_DAILY]
    elif "--monthly" in sys.argv:
        cycles = ["M", "Q"]

    rep = collect(full=full or None, cycles=cycles,
                  progress=lambda i, n, l: print("[{}/{}] {}".format(i, n, l)))
    for r in rep:
        print("{:5} {:22} src={:9} rows={:6} latest={} {}".format(
            r["status"], r["label"], r["source"], r["rows"], r["latest"], r["message"][:70]))
    print(summarize(rep))
