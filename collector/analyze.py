"""盤面の指標計算とパターン判定。

数値を並べるだけでは「何が起きたか」は分からないので、
(1) 平常時の変動に対して今日はどれだけ外れているか(σ)
(2) 金利・為替の動きの組み合わせが何を示唆するか
(3) 物価・賃金・日銀会合という背景がそれと整合するか
をここで機械的に出す。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import boj, store
from .series import (CYCLE_MONTHLY, CYCLE_QUARTERLY, DAILY, DIFF_BP, DIFF_PCT,
                     DIFF_PT, MONTHLY)

# 判定に使うしきい値。曖昧なままにせず全て実数で決め打つ。
BP_MOVE = 10.0        # 「金利が動いた」と見なす変化幅(bp)
BP_FLAT = 5.0         # 「ほぼ横ばい」と見なす上限(bp)
FX_MOVE = 0.5         # 「円安/円高が進んだ」と見なす変化(%)
GLOBAL_GAP = 8.0      # 日米の金利変化がこれ以内なら世界同時とみなす(bp)
SIGMA_ALERT = 2.0     # 異常扱いする前日比のσ
SIGMA_WINDOW = 250    # σを取る営業日数(およそ1年)
CPI_MOVE = 0.2        # 物価が「加速/減速した」と見なす3ヶ月での変化(ポイント)

# 鮮度の警告。日次は日数、月次・四半期は「何ヶ月遅れているか」で見る。
# 公的統計は公表まで1〜2ヶ月かかるのが普通なので、そこは警告しない。
STALE_DAYS = 4
STALE_MONTHS = {CYCLE_MONTHLY: 3, CYCLE_QUARTERLY: 7}

HORIZONS = [("前日比", 1), ("1週", 7), ("1ヶ月", 30), ("3ヶ月", 91)]
MONTHLY_HORIZONS = [("前月差", 1), ("3ヶ月前差", 3), ("1年前差", 12)]
QUARTERLY_HORIZONS = [("前期差", 3), ("2期前差", 6), ("1年前差", 12)]


def horizons_for(cycle: str):
    if cycle == CYCLE_QUARTERLY:
        return QUARTERLY_HORIZONS
    if cycle == CYCLE_MONTHLY:
        return MONTHLY_HORIZONS
    return HORIZONS


def _months_before(iso: str, months: int) -> str:
    d = datetime.fromisoformat(iso).date()
    year, month = d.year, d.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1).isoformat()


def format_period(iso: str, cycle: str) -> str:
    """時点の表記。月次は「2026年7月」、四半期は「2026年4-6月期」にする。"""
    d = datetime.fromisoformat(iso).date()
    if cycle == CYCLE_QUARTERLY:
        return "{}年{}-{}月期".format(d.year, d.month, d.month + 2)
    if cycle == CYCLE_MONTHLY:
        return "{}年{}月".format(d.year, d.month)
    return iso


def _value_on_or_before(points, target):
    hit = None
    for d, v in points:
        if d <= target:
            hit = (d, v)
        else:
            break
    return hit


def _change(series, latest_v, past_v):
    """金利は bp、率や倍の水準は ポイント、それ以外は 変化率% で返す。"""
    if series.diff_style == DIFF_BP:
        diff = (latest_v - past_v) * 100.0
        return diff, "{:+.1f}bp".format(diff)
    if series.diff_style == DIFF_PT:
        diff = latest_v - past_v
        return diff, "{:+.{p}f}pt".format(diff, p=max(series.decimals, 1))
    if past_v == 0:
        return 0.0, "-"
    pct = (latest_v - past_v) / past_v * 100.0
    return pct, "{:+.2f}%".format(pct)


def change_text(series, latest_v, past_v):
    """2時点の差を、その系列の流儀(bp / % / pt)で文字にする。"""
    return _change(series, latest_v, past_v)[1]


def _sigma(points, diff_style):
    tail = points[-(SIGMA_WINDOW + 1):]
    if len(tail) < 30:
        return None
    diffs = []
    for (_, a), (_, b) in zip(tail, tail[1:]):
        if diff_style == DIFF_BP:
            diffs.append((b - a) * 100.0)
        elif diff_style == DIFF_PT:
            diffs.append(b - a)
        elif a:
            diffs.append((b - a) / a * 100.0)
    if len(diffs) < 30:
        return None
    mean = sum(diffs) / len(diffs)
    var = sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)
    return var ** 0.5


def _rows_for(data, catalog, today):
    rows = []
    for series in catalog:
        horizons = horizons_for(series.cycle)
        points = data.get(series.id) or []
        if not points:
            rows.append({"series": series, "empty": True, "changes": {},
                         "raw": {}, "stale_days": None, "sigma": None, "z": None})
            continue
        last_date, last_value = points[-1]
        row = {
            "series": series, "empty": False,
            "date": last_date, "value": last_value,
            "value_text": "{:,.{p}f}{s}".format(last_value, p=series.decimals, s=series.suffix),
            "changes": {}, "raw": {},
        }
        for name, step in horizons:
            if series.cycle == "D":
                if step == 1:
                    past = points[-2] if len(points) >= 2 else None
                else:
                    target = (datetime.fromisoformat(last_date).date()
                              - timedelta(days=step)).isoformat()
                    past = _value_on_or_before(points, target)
            else:
                past = _value_on_or_before(points, _months_before(last_date, step))
            if past is None or past[0] == last_date:
                row["changes"][name] = "-"
                row["raw"][name] = None
                continue
            num, text = _change(series, last_value, past[1])
            row["changes"][name] = text
            row["raw"][name] = num

        sigma = _sigma(points, series.diff_style)
        first_horizon = horizons[0][0]
        move = row["raw"].get(first_horizon)
        row["sigma"] = sigma
        row["z"] = (move / sigma) if (sigma and move is not None and sigma > 0) else None
        last = datetime.fromisoformat(last_date).date()
        row["stale_days"] = (today - last).days
        row["months_behind"] = (today.year - last.year) * 12 + (today.month - last.month)
        row["period_text"] = format_period(last_date, series.cycle)
        rows.append(row)
    return rows


def board(conn=None):
    """日次(市場)の盤面。"""
    own = conn is None
    conn = conn or store.connect()
    try:
        return _rows_for(store.load_all(conn), DAILY, date.today())
    finally:
        if own:
            conn.close()


def monthly_board(conn=None):
    """月次・四半期(公的統計)の盤面。"""
    own = conn is None
    conn = conn or store.connect()
    try:
        return _rows_for(store.load_all(conn), MONTHLY, date.today())
    finally:
        if own:
            conn.close()


def _pick(rows, series_id, horizon):
    for r in rows:
        if r["series"].id == series_id and not r.get("empty"):
            return r["raw"].get(horizon)
    return None


def _level(rows, series_id):
    for r in rows:
        if r["series"].id == series_id and not r.get("empty"):
            return r["value"], r["date"]
    return None, None


def price_context(mrows, today=None):
    """物価・賃金の背景。金利の動きがこれと整合するかを見るために使う。"""
    lines = []
    cpi, cpi_date = _level(mrows, "cpi_core")
    if cpi is not None:
        trend = _pick(mrows, "cpi_core", "3ヶ月前差")
        word = "横ばい"
        if trend is not None and trend >= CPI_MOVE:
            word = "加速"
        elif trend is not None and trend <= -CPI_MOVE:
            word = "減速"
        lines.append("コアCPI(生鮮食品を除く総合)は{} 前年同月比 {:+.1f}%、3ヶ月前から{}".format(
            format_period(cpi_date, CYCLE_MONTHLY), cpi, word))
    real, real_date = _level(mrows, "wage_real")
    if real is not None:
        lines.append("実質賃金は{} 前年同月比 {:+.1f}%（{}）".format(
            format_period(real_date, CYCLE_MONTHLY), real,
            "プラス" if real > 0 else "マイナス"))
    gdp, gdp_date = _level(mrows, "gdp_real")
    if gdp is not None:
        lines.append("実質GDPは{} 前期比年率 {:+.1f}%".format(
            format_period(gdp_date, CYCLE_QUARTERLY), gdp))
    return lines


def boj_context(today=None):
    """次回会合までの日数。金利の織り込みを読む時の土台になる。"""
    meetings = boj.load()
    if not meetings:
        return None
    nxt = boj.next_meeting(meetings, today)
    last = boj.last_meeting(meetings, today)
    parts = []
    if nxt:
        parts.append("次回の日銀金融政策決定会合は {} 〜 {}（あと{}日）".format(
            nxt["start"], nxt["end"], boj.days_until(nxt, today)))
    if last:
        parts.append("直近の会合は {} 〜 {}".format(last["start"], last["end"]))
    return " ／ ".join(parts) if parts else None


def diagnose(rows, horizon="1ヶ月", mrows=None, today=None):
    """金利・為替の組み合わせから、動きの主因の当たりを付ける。

    断定はしない。あくまで「どの説明と整合するか」を出す。
    """
    d2 = _pick(rows, "jgb2y", horizon)
    d10 = _pick(rows, "jgb10y", horizon)
    d30 = _pick(rows, "jgb30y", horizon)
    us10 = _pick(rows, "ust10y", horizon)
    fx = _pick(rows, "usdjpy", horizon)

    facts = []
    for label, value, fmt in (("日本10年", d10, "{:+.1f}bp"), ("2年", d2, "{:+.1f}bp"),
                              ("30年", d30, "{:+.1f}bp"), ("米10年", us10, "{:+.1f}bp"),
                              ("ドル円", fx, "{:+.2f}%")):
        if value is not None:
            facts.append(label + " " + fmt.format(value))

    context = list(price_context(mrows or [], today))
    meeting = boj_context(today)
    if meeting:
        context.append(meeting)

    def out(headline, reason, level):
        return {"headline": headline, "reason": reason, "facts": facts,
                "context": context, "horizon": horizon, "level": level}

    if d10 is None:
        return out("判定できない（10年国債のデータが無い）", "", "none")

    curve = (d30 - d2) if (d30 is not None and d2 is not None) else None
    if curve is None:
        curve_text = ""
    elif curve > BP_FLAT:
        curve_text = "カーブはスティープ化（超長期が短期より上）。"
    elif curve < -BP_FLAT:
        curve_text = "カーブはフラット化（短期が超長期より上）。"
    else:
        curve_text = "カーブの傾きはほぼ不変。"

    cpi_trend = _pick(mrows or [], "cpi_core", "3ヶ月前差")
    if cpi_trend is None:
        cpi_text = ""
    elif cpi_trend >= CPI_MOVE:
        cpi_text = "コアCPIも3ヶ月前から{:+.1f}pt と加速しており、物価側とも整合する。".format(cpi_trend)
    elif cpi_trend <= -CPI_MOVE:
        cpi_text = "一方でコアCPIは3ヶ月前から{:+.1f}pt と減速しており、物価側とは整合しない。".format(cpi_trend)
    else:
        cpi_text = "コアCPIはほぼ横ばいで、物価side からの後押しは弱い。"

    if abs(d10) < BP_MOVE:
        return out("長期金利は横ばい圏",
                   "日本10年の{}変化が±{:.0f}bp未満。{}".format(horizon, BP_MOVE, curve_text),
                   "flat")

    up = d10 > 0
    direction = "上昇" if up else "低下"

    # 1) 日米が同じだけ動いていれば、日本固有の材料では説明しきれない
    if us10 is not None and us10 * d10 > 0 and abs(d10 - us10) < GLOBAL_GAP:
        return out("世界的な金利{}に連動している可能性".format(direction),
                   ("日本10年 {:+.1f}bp に対し米10年 {:+.1f}bp と差が{:.0f}bp未満。"
                    "日本固有の材料(財政・日銀)だけでは説明しきれない。{}"
                    ).format(d10, us10, GLOBAL_GAP, curve_text),
                   "global")

    # 2) 短期金利も一緒に動き、円が逆方向 → 金融政策期待が主因の形
    if d2 is not None and d2 * d10 > 0 and abs(d2) >= BP_FLAT:
        if fx is not None and ((up and fx < -FX_MOVE) or (not up and fx > FX_MOVE)):
            move = "円高" if up else "円安"
            return out("日銀の利{}げ期待が主因の形".format("上" if up else "下"),
                       ("2年 {:+.1f}bp と10年 {:+.1f}bp が同方向に動き、同時に{}({:+.2f}%)。"
                        "政策金利の織り込みが変化した時の典型的な形。{}{}"
                        ).format(d2, d10, move, fx, curve_text, cpi_text),
                       "boj")
        return out("短期金利を伴う金利{}".format(direction),
                   ("2年 {:+.1f}bp も同方向。政策期待の変化が効いている可能性。"
                    "ただし為替がその形に整合していないため断定はできない。{}{}"
                    ).format(d2, curve_text, cpi_text),
                   "boj_weak")

    # 3) 短期は動かず超長期が主導 → 財政・需給・長期インフレ側
    if d2 is not None and abs(d2) < BP_FLAT:
        if d30 is not None and abs(d30) > abs(d10):
            return out("財政・国債需給・長期インフレ懸念が主因の形",
                       ("2年は {:+.1f}bp とほぼ動かない一方、30年が {:+.1f}bp と"
                        "10年({:+.1f}bp)より大きく動いている。日銀の政策期待では説明しにくく、"
                        "国債の需給や長期のインフレ観に効く材料(増発・減税・積極財政)を見る場面。{}"
                        ).format(d2, d30, d10, curve_text),
                       "fiscal")
        return out("短期金利を伴わない長期金利{}".format(direction),
                   ("2年が {:+.1f}bp とほぼ動かないまま10年が {:+.1f}bp。"
                    "日銀の政策期待以外の材料を疑う場面。{}"
                    ).format(d2, d10, curve_text),
                   "fiscal_weak")

    return out("長期金利が{}（主因は特定できない）".format(direction),
               "日本10年 {:+.1f}bp。他の系列と整合する型が無い。{}".format(d10, curve_text),
               "unknown")


def alerts(rows):
    """平常の変動幅から外れた動きだけを拾う。「今日は何が変だ？」の答え。"""
    out = []
    for r in rows:
        if r.get("empty") or r.get("z") is None:
            continue
        if abs(r["z"]) >= SIGMA_ALERT:
            first = horizons_for(r["series"].cycle)[0][0]
            out.append("{} {}（平常の{:.1f}倍）".format(
                r["series"].label, r["changes"][first], abs(r["z"])))
    return out


def stale_notes(rows):
    """鮮度の警告。黙って古い数字を見せるのが一番まずいので明示する。"""
    out = []
    for r in rows:
        series = r["series"]
        if r.get("empty"):
            out.append("{}: データ無し".format(series.label))
            continue
        if series.cycle == "D":
            if r.get("stale_days") is not None and r["stale_days"] >= STALE_DAYS:
                out.append("{}: {}日前({})が最新".format(
                    series.label, r["stale_days"], r["date"]))
            continue
        behind = r.get("months_behind")
        if behind is not None and behind >= STALE_MONTHS.get(series.cycle, 3):
            out.append("{}: {}ヶ月遅れ({}が最新)".format(
                series.label, behind, format_period(r["date"], series.cycle)))
    return out


def _table(rows, cycle_label, heads):
    lines = ["指標 | 最新値 | 時点 | " + " | ".join(heads),
             "--- | --- | --- | " + " | ".join("---" for _ in heads)]
    for r in rows:
        s = r["series"]
        if r.get("empty"):
            lines.append("{} | データ無し | - | ".format(s.label) + " | ".join("-" for _ in heads))
            continue
        lines.append("{} | {} | {} | ".format(
            s.label, r["value_text"], r.get("period_text") or r["date"])
                     + " | ".join(r["changes"].get(h, "-") for h in heads))
    return lines


def board_text(rows, diag=None, mrows=None):
    """AIに貼って質問するためのテキスト。人間もAIも読める形にする。"""
    lines = ["# 市況ボード {} 時点".format(date.today().isoformat()), "",
             "## 日次（市場）"]
    lines += _table(rows, "日次", [h for h, _ in HORIZONS])
    lines += ["", "※金利の差分はbp、為替・株・商品は%。"]

    if mrows:
        monthly = [r for r in mrows if r["series"].cycle == CYCLE_MONTHLY]
        quarterly = [r for r in mrows if r["series"].cycle == CYCLE_QUARTERLY]
        if monthly:
            lines += ["", "## 月次（公的統計）"]
            lines += _table(monthly, "月次", [h for h, _ in MONTHLY_HORIZONS])
        if quarterly:
            lines += ["", "## 四半期"]
            lines += _table(quarterly, "四半期", [h for h, _ in QUARTERLY_HORIZONS])
        lines += ["", "※差分はポイント。"]

    hits = alerts(rows) + alerts(mrows or [])
    if hits:
        lines += ["", "## 平常より大きい動き"] + ["- " + h for h in hits]
    notes = stale_notes(rows) + stale_notes(mrows or [])
    if notes:
        lines += ["", "## 鮮度の注意"] + ["- " + n for n in notes]
    if diag:
        lines += ["", "## 所見（{}の変化から）".format(diag["horizon"]),
                  "- " + diag["headline"], "- 根拠: " + diag["reason"]]
        for c in diag.get("context", []):
            lines.append("- " + c)
    return "\n".join(lines)


if __name__ == "__main__":
    _conn = store.connect()
    _rows = board(_conn)
    _mrows = monthly_board(_conn)
    _conn.close()
    print(board_text(_rows, diagnose(_rows, mrows=_mrows), _mrows))
