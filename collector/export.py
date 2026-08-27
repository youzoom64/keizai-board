"""取得したデータを、静的サイトが読むJSONへ書き出す。

GitHub Pages では Python が動かないので、取得と計算はここ（CI）で済ませ、
ブラウザには出来上がったJSONだけを渡す。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from . import analyze, boj, collect as collect_mod, store, world
from .series import CYCLE_MONTHLY, CYCLE_QUARTERLY, DAILY, MONTHLY, SERIES

DOCS = Path(__file__).resolve().parents[1] / "docs"
OUT = DOCS / "data"
EPOCH = date(1970, 1, 1)

SOURCES = [
    {"name": "財務省 国債金利情報", "url": "https://www.mof.go.jp/jgbs/reference/interest_rate/",
     "series": ["日本2年国債", "日本10年国債", "日本30年国債"]},
    {"name": "統計ダッシュボード（総務省ほか）", "url": "https://dashboard.e-stat.go.jp/",
     "series": ["消費者物価", "企業物価", "輸出入物価", "賃金", "雇用", "GDP"]},
    {"name": "日本銀行 金融政策決定会合", "url": "https://www.boj.or.jp/mopo/mpmsche_minu/",
     "series": ["会合日程"]},
    {"name": "FRED（セントルイス連銀）", "url": "https://fred.stlouisfed.org/",
     "series": ["米10年国債", "WTI原油", "ドル円", "日経平均"]},
]


def _day_number(iso: str) -> int:
    return (date.fromisoformat(iso) - EPOCH).days


def _series_meta() -> list[dict]:
    out = []
    for s in SERIES:
        out.append({
            "id": s.id, "label": s.label, "short": s.short_label,
            "group": s.group, "unit": s.unit, "suffix": s.suffix,
            "decimals": s.decimals, "color": s.color, "cycle": s.cycle,
            "diff": s.diff_style, "note": s.note,
            "alwaysRaw": s.always_raw,
        })
    return out


def _row_json(row: dict) -> dict:
    series = row["series"]
    if row.get("empty"):
        return {"id": series.id, "empty": True}
    return {
        "id": series.id,
        "value": row["value"],
        "valueText": row["value_text"],
        "date": row["date"],
        "period": row.get("period_text") or row["date"],
        "changes": row["changes"],
        "raw": row["raw"],
        "sigma": row.get("sigma"),
        "z": row.get("z"),
        "staleDays": row.get("stale_days"),
        "monthsBehind": row.get("months_behind"),
    }


def stamp_assets() -> str:
    """app.js と style.css のURLに中身のハッシュを付ける。

    付けないと、更新してもブラウザが古いJSを掴んだままになる。
    中身が変わった時だけURLが変わるので、無駄な再取得も起きない。
    """
    index = DOCS / "index.html"
    html = index.read_text(encoding="utf-8")
    digest = hashlib.sha1()
    for name in ("app.js", "style.css"):
        digest.update((DOCS / name).read_bytes())
    version = digest.hexdigest()[:8]

    # 既に版が付いていれば剥がしてから付け直す。正規表現は使わない。
    for name, before, after in (
        ("style.css", '<link rel="stylesheet" href="', '">'),
        ("app.js", '<script src="', '"></script>'),
    ):
        for old in (name, "{}?v=".format(name)):
            i = html.find(before + old)
            if i < 0:
                continue
            j = html.index(after, i)
            html = html[:i] + before + name + "?v=" + version + html[j:]
            break
    index.write_text(html, encoding="utf-8")
    return version


def export() -> dict:
    """DBの内容を docs/data/ 以下のJSONにする。"""
    OUT.mkdir(parents=True, exist_ok=True)
    conn = store.connect()
    try:
        data = store.load_all(conn)
        rows = analyze.board(conn)
        mrows = analyze.monthly_board(conn)
    finally:
        conn.close()

    # 日付は 1970-01-01 からの日数にして小さくする。ブラウザ側で戻す。
    series_payload = {}
    for sid, points in data.items():
        series_payload[sid] = {
            "t": [_day_number(d) for d, _ in points],
            "v": [v for _, v in points],
        }

    diag = analyze.diagnose(rows, mrows=mrows)
    meetings = boj.load()

    board_payload = {
        "generatedAt": date.today().isoformat(),
        "daily": [_row_json(r) for r in rows],
        "monthly": [_row_json(r) for r in mrows],
        "horizons": {
            "daily": [h for h, _ in analyze.HORIZONS],
            "monthly": [h for h, _ in analyze.MONTHLY_HORIZONS],
            "quarterly": [h for h, _ in analyze.QUARTERLY_HORIZONS],
        },
        "diagnosis": {
            "headline": diag["headline"], "reason": diag["reason"],
            "facts": diag["facts"], "context": diag.get("context", []),
            "horizon": diag["horizon"], "level": diag["level"],
        },
        "alerts": analyze.alerts(rows) + analyze.alerts(mrows),
        "stale": analyze.stale_notes(rows) + analyze.stale_notes(mrows),
        "sigmaAlert": analyze.SIGMA_ALERT,
        "staleDays": analyze.STALE_DAYS,
        "staleMonths": analyze.STALE_MONTHS,
    }

    meta_payload = {
        "generatedAt": date.today().isoformat(),
        "epoch": EPOCH.isoformat(),
        "series": _series_meta(),
        "dailyIds": [s.id for s in DAILY],
        "monthlyIds": [s.id for s in MONTHLY],
        "cycles": {"monthly": CYCLE_MONTHLY, "quarterly": CYCLE_QUARTERLY},
        "sources": SOURCES,
    }

    written = {}
    for name, payload in (("meta.json", meta_payload),
                          ("series.json", series_payload),
                          ("board.json", board_payload),
                          ("boj.json", {"meetings": meetings})):
        path = OUT / name
        path.write_text(json.dumps(payload, ensure_ascii=False,
                                   separators=(",", ":")), encoding="utf-8")
        written[name] = path.stat().st_size

    # 他国比較。日本の盤面とは別ファイルにして、既存の読み込みに影響させない。
    try:
        world_payload = world.payload(world.collect_world())
        path = OUT / "world.json"
        path.write_text(json.dumps(world_payload, ensure_ascii=False,
                                   separators=(",", ":")), encoding="utf-8")
        written["world.json"] = path.stat().st_size
    except Exception as exc:
        # 他国が取れなくても日本の盤面は出す。
        print("警告: 他国比較の取得に失敗 {}: {}".format(type(exc).__name__, exc))

    written["index.html(版)"] = stamp_assets()

    md = analyze.board_text(rows, diag, mrows)
    (OUT / "board.md").write_text(md, encoding="utf-8")
    written["board.md"] = (OUT / "board.md").stat().st_size
    return written


def main(full: bool = True) -> None:
    report = collect_mod.collect(full=full,
                                 progress=lambda i, n, l: print("[{}/{}] {}".format(i, n, l)))
    failed = [r for r in report if r["status"] != "ok"]
    for r in report:
        print("  {:5} {:24} {:>7}行 {}".format(r["status"], r["label"], r["rows"], r["latest"]))
    print(collect_mod.summarize(report))

    written = export()
    for name, size in written.items():
        print("  書き出し {:12} {:>9,} バイト".format(name, size))
    if failed:
        # 一部が取れなくても、取れた分でサイトは更新する。落ちたことは記録に残す。
        print("警告: {} 系列が取得できなかった".format(len(failed)))


if __name__ == "__main__":
    import sys
    main(full="--diff" not in sys.argv)
