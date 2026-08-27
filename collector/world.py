"""他国比較のデータ。指標を1つ選んで国を並べるための取得と換算。

日本の盤面（series.py / analyze.py）とは完全に分けてある。
こちらは「指標1つ × 国複数」で、単位が揃うので変換モードが要らない。

出どころはFRED。OECD・IMF由来の系列を国コードだけ差し替えて取る。
取れない組み合わせは黙って落とす（国によって統計の整備状況が違うため）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

from . import sources

# (キー, 表示名, FREDの国コード, 色)
COUNTRIES = [
    ("jp", "日本", "JP", "#ef5350"),
    ("us", "米国", "US", "#4c9be8"),
    ("ez", "ユーロ圏", "EZ", "#ffa726"),
    ("de", "ドイツ", "DE", "#b47cf0"),
    ("gb", "英国", "GB", "#4caf50"),
    ("ch", "スイス", "CH", "#26c6da"),
    ("kr", "韓国", "KR", "#ec407a"),
    ("ca", "カナダ", "CA", "#8bc34a"),
    ("au", "豪州", "AU", "#ffca28"),
    ("nz", "ニュージーランド", "NZ", "#26a69a"),
    ("cn", "中国", "CN", "#ff7043"),
    ("in", "インド", "IN", "#9575cd"),
    ("id", "インドネシア", "ID", "#a1887f"),
]

# (キー, 表示名, FREDの系列パターン, 単位, 小数桁, 補足)
INDICATORS = [
    ("bond10y", "10年国債利回り", "IRLTLT01{}M156N", "%", 2,
     "長期金利。国ごとの資金の値段"),
    ("policy_rate", "政策金利", "IRSTCI01{}M156N", "%", 2,
     "中央銀行が決める短期の金利。定義は国ごとに異なる"),
    ("unemployment", "失業率", "LRHUTTTT{}M156S", "%", 1,
     "国際比較用に調整された失業率"),
]

# 名目GDPは自国通貨で来るので、為替で割ってドル建てにする。
GDP_PATTERN = "NGDPSAXDC{}Q"
GDP_UNIT_SCALE = 1e-6   # 百万自国通貨 → 兆単位へ
GDP_ANNUALIZE = 4        # 四半期額を年換算する

# 為替の向きが通貨ごとに逆になる。取り違えると桁が狂うのでここで明示する。
FX = {
    "jp": ("EXJPUS", "divide"),      # 円/ドル
    "gb": ("EXUSUK", "multiply"),    # ドル/ポンド
    "ez": ("EXUSEU", "multiply"),    # ドル/ユーロ
    "de": ("EXUSEU", "multiply"),
    "ch": ("EXSZUS", "divide"),
    "kr": ("EXKOUS", "divide"),
    "ca": ("EXCAUS", "divide"),
    "au": ("EXUSAL", "multiply"),
    "nz": ("EXUSNZ", "multiply"),
    "cn": ("EXCHUS", "divide"),
    "in": ("EXINUS", "divide"),
    "us": (None, "none"),            # 換算不要
}


def _safe_fetch(series_id):
    try:
        return sources.fetch_fred(series_id, full=True)
    except Exception:
        return None


def _quarter_key(iso: str) -> str:
    d = date.fromisoformat(iso)
    return "{}-{:02d}-01".format(d.year, (d.month - 1) // 3 * 3 + 1)


def _quarterly_average(points):
    """月次の為替を四半期平均にする。GDPが四半期なので合わせる。"""
    buckets = {}
    for iso, value in points:
        buckets.setdefault(_quarter_key(iso), []).append(value)
    return {q: sum(v) / len(v) for q, v in buckets.items()}


def collect_world(progress=None) -> dict:
    """全指標・全国を取ってきて、ドル建てGDPを計算した結果を返す。"""
    jobs = []
    for ikey, _, pattern, _, _, _ in INDICATORS:
        for ckey, _, code, _ in COUNTRIES:
            jobs.append((ikey, ckey, pattern.format(code)))
    for ckey, _, code, _ in COUNTRIES:
        jobs.append(("gdp_local", ckey, GDP_PATTERN.format(code)))
    seen = set()
    for ckey, (sid, _) in FX.items():
        if sid and sid not in seen:
            seen.add(sid)
            jobs.append(("fx", sid, sid))

    total = len(jobs)
    fetched = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_safe_fetch, sid): (ikey, ckey)
                   for ikey, ckey, sid in jobs}
        done = 0
        for fut in futures:
            ikey, ckey = futures[fut]
            done += 1
            if progress:
                progress(done, total, "{} / {}".format(ikey, ckey))
            points = fut.result()
            if points:
                fetched.setdefault(ikey, {})[ckey] = points

    data = {ikey: fetched.get(ikey, {}) for ikey, _, _, _, _, _ in INDICATORS}

    # ドル建てGDP = 自国通貨の四半期GDP ÷ その四半期の平均為替
    gdp_usd = {}
    fx_cache = {sid: _quarterly_average(pts) for sid, pts in fetched.get("fx", {}).items()}
    for ckey, points in fetched.get("gdp_local", {}).items():
        sid, op = FX.get(ckey, (None, None))
        if op is None:
            continue
        rates = fx_cache.get(sid) if sid else None
        if op != "none" and not rates:
            continue
        converted = []
        for iso, value in points:
            if op == "none":
                usd = value
            else:
                rate = rates.get(_quarter_key(iso))
                if not rate:
                    continue
                usd = value / rate if op == "divide" else value * rate
            converted.append((iso, usd * GDP_UNIT_SCALE * GDP_ANNUALIZE))
        if converted:
            gdp_usd[ckey] = converted
    data["gdp_usd"] = gdp_usd

    return data


def payload(data: dict) -> dict:
    """画面が読む形にまとめる。日付は文字列のまま持たせる（月次・四半期なので軽い）。"""
    indicators = [{"key": k, "name": n, "unit": u, "decimals": d, "note": note}
                  for k, n, _, u, d, note in INDICATORS]
    indicators.append({"key": "gdp_usd", "name": "名目GDP（ドル建て・年換算）",
                       "unit": "兆ドル", "decimals": 2,
                       "note": "自国通貨の名目GDPを、その四半期の平均為替でドルに換算したもの"})

    series = {}
    for ikey, per_country in data.items():
        series[ikey] = {ckey: {"d": [p[0] for p in pts], "v": [p[1] for p in pts]}
                        for ckey, pts in per_country.items()}

    return {
        "fetchedAt": date.today().isoformat(),
        "countries": [{"key": k, "name": n, "color": c} for k, n, _, c in COUNTRIES],
        "indicators": indicators,
        "series": series,
    }


if __name__ == "__main__":
    got = collect_world(progress=lambda i, n, l: None)
    for ikey, per in got.items():
        rows = sorted(per.items(), key=lambda kv: kv[1][-1][1], reverse=True)
        print("== {} ({}カ国)".format(ikey, len(rows)))
        for ckey, pts in rows:
            print("   {:4} {} {:>12,.2f}".format(ckey, pts[-1][0], pts[-1][1]))
