"""取得元。全てプレーンなHTTP取得で完結させ、ブラウザ自動化には依存しない。"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import date, datetime

from .store import RAW_DIR

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) market_watch/1.0"
TIMEOUT = 30

MOF_ALL = "https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv"
MOF_CURRENT = "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"

# 和暦の元号開始年-1。元号n年 = base + n
_ERA_BASE = {"M": 1867, "T": 1911, "S": 1925, "H": 1988, "R": 2018}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        return res.read()


def _save_raw(name: str, blob: bytes) -> None:
    """再解釈できるように生データを残す。パーサを直した時に取り直さずに済む。

    CI では毎回まっさらに取り直すので保存しない（KEIZAI_NO_RAW=1）。
    """
    if os.environ.get("KEIZAI_NO_RAW") == "1":
        return
    day = RAW_DIR / date.today().isoformat()
    day.mkdir(parents=True, exist_ok=True)
    (day / name).write_bytes(blob)


def wareki_to_iso(text: str) -> str | None:
    """'R8.8.26' -> '2026-08-26'"""
    m = re.fullmatch(r"\s*([MTSHR])(\d{1,2})\.(\d{1,2})\.(\d{1,2})\s*", text)
    if not m:
        return None
    era, y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    try:
        return date(_ERA_BASE[era] + y, mo, d).isoformat()
    except (KeyError, ValueError):
        return None


def fetch_mof(full: bool) -> dict[str, list[tuple[str, float]]]:
    """財務省 国債金利情報。1本のCSVに1〜40年の全年限が日次で入っている。

    full=True で全履歴(1974〜)、False で当月分のみ。戻りは {'10年': [(date, value)...]}。
    """
    url = MOF_ALL if full else MOF_CURRENT
    blob = _get(url)
    _save_raw("jgbcm_all.csv" if full else "jgbcm.csv", blob)
    text = blob.decode("cp932", errors="replace")

    rows = list(csv.reader(io.StringIO(text)))
    header_idx = next(i for i, r in enumerate(rows) if r and r[0].strip() == "基準日")
    tenors = [c.strip() for c in rows[header_idx][1:]]

    out: dict[str, list[tuple[str, float]]] = {t: [] for t in tenors if t}
    for row in rows[header_idx + 1:]:
        if not row or not row[0].strip():
            continue
        iso = wareki_to_iso(row[0])
        if not iso:
            continue
        for tenor, cell in zip(tenors, row[1:]):
            if not tenor:
                continue
            cell = (cell or "").strip()
            if not cell or cell == "-":
                continue
            try:
                out[tenor].append((iso, float(cell)))
            except ValueError:
                continue
    return out


def fetch_yahoo(symbol: str, full: bool) -> list[tuple[str, float]]:
    """Yahoo Finance の日足。当日ぶんも取れるので朝の盤面に間に合う。"""
    rng = "10y" if full else "3mo"
    url = YAHOO.format(symbol=urllib.parse.quote(symbol, safe=""), range=rng)
    blob = _get(url)
    _save_raw(f"yahoo_{re.sub(r'[^A-Za-z0-9]', '_', symbol)}.json", blob)
    result = json.loads(blob)["chart"]["result"][0]

    stamps = result.get("timestamp") or []
    closes = result["indicators"]["quote"][0].get("close") or []
    tz_offset = result["meta"].get("gmtoffset", 0)

    out: list[tuple[str, float]] = []
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        iso = datetime.utcfromtimestamp(ts + tz_offset).date().isoformat()
        out.append((iso, float(close)))

    # 同日に複数バーが来た場合(当日の速報バー)は後勝ちで1本に畳む
    merged: dict[str, float] = {}
    for iso, value in out:
        merged[iso] = value
    return sorted(merged.items())


def fetch_fred(series_id: str, full: bool) -> list[tuple[str, float]]:
    """FRED のCSV。APIキー不要。Yahooが落ちた時の代替に使う。"""
    blob = _get(FRED.format(sid=series_id))
    _save_raw(f"fred_{series_id}.csv", blob)
    text = blob.decode("utf-8", errors="replace")

    out: list[tuple[str, float]] = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)
    for row in reader:
        if len(row) < 2:
            continue
        raw = row[1].strip()
        if not raw or raw == ".":
            continue
        try:
            out.append((row[0].strip(), float(raw)))
        except ValueError:
            continue
    return out


DASHBOARD = "https://dashboard.e-stat.go.jp/api/1.0/Json/getData"

_MONTH_TIME = re.compile(r"^(\d{4})(\d{2})00$")
_QUARTER_TIME = re.compile(r"^(\d{4})(\d)Q00$")


def dashboard_time_to_iso(text: str) -> str | None:
    """統計ダッシュボードの時点表記を月初/四半期初のISO日付にする。

    '20260700' -> '2026-07-01'、'20262Q00' -> '2026-04-01'
    """
    m = _MONTH_TIME.match(text)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return date(year, month, 1).isoformat()
        return None
    q = _QUARTER_TIME.match(text)
    if q:
        year, quarter = int(q.group(1)), int(q.group(2))
        if 1 <= quarter <= 4:
            return date(year, quarter * 3 - 2, 1).isoformat()
    return None


def fetch_dashboard(code: str, cycle: str, seasonal: str, full: bool) -> list[tuple[str, float]]:
    """統計ダッシュボードAPI。appId登録が要らないので鍵の管理が要らない。

    full は使わない。月次・四半期は件数が少なく、毎回全期間取っても軽い。
    """
    params = urllib.parse.urlencode({
        "Lang": "JP", "IndicatorCode": code, "RegionCode": "00000",
        "Cycle": cycle, "IsSeasonalAdjustment": seasonal,
    })
    blob = _get(DASHBOARD + "?" + params)
    _save_raw("dashboard_{}.json".format(code), blob)

    root = list(json.loads(blob).values())[0]
    result = root.get("RESULT", {})
    if result.get("status") != "0":
        raise ValueError(result.get("errorMsg", "統計ダッシュボードが応答しない"))

    objs = root["STATISTICAL_DATA"]["DATA_INF"]["DATA_OBJ"]
    objs = objs if isinstance(objs, list) else [objs]

    merged: dict[str, float] = {}
    for obj in objs:
        value = obj.get("VALUE", {})
        iso = dashboard_time_to_iso(str(value.get("@time", "")))
        raw = value.get("$")
        if not iso or raw in (None, "", "-"):
            continue
        try:
            merged[iso] = float(raw)   # 同一時点は後勝ち(速報→確報)
        except ValueError:
            continue
    return sorted(merged.items())
