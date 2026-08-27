"""日銀 金融政策決定会合の開催日程。

金利が動いた日が会合の日だったのかどうかは、原因を絞る時に効くので、
日程を取ってグラフ上に印として出せるようにする。
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import date

from bs4 import BeautifulSoup

from .store import DATA_DIR

URL = "https://www.boj.or.jp/mopo/mpmsche_minu/index.htm"
PAST_URL = "https://www.boj.or.jp/mopo/mpmsche_minu/past.htm"  # 過去年分。同じ表構造
CACHE = DATA_DIR / "boj_meetings.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) market_watch/1.0"

_YEAR = re.compile(r"(\d{4})\s*年")
_DAY = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def _fetch_page(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", errors="replace")


def fetch() -> list[dict]:
    """開催日程の一覧を取ってくる。1行が1会合で、多くは2日開催。

    今年以降は index、過去年は past に載っているので両方を読んで重複を潰す。
    """
    found: dict[str, dict] = {}
    for url in (URL, PAST_URL):
        try:
            html = _fetch_page(url)
        except Exception:
            # 片方が落ちても、取れた分だけで続ける。
            continue
        for meeting in _parse(html):
            found[meeting["start"]] = meeting
    return sorted(found.values(), key=lambda m: m["start"])


def _parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    meetings: list[dict] = []
    for table in soup.find_all("table"):
        caption = table.find("caption")
        if not caption:
            continue
        year_match = _YEAR.search(caption.get_text(" ", strip=True))
        if not year_match:
            continue
        year = int(year_match.group(1))

        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            text = cells[0].get_text(" ", strip=True)
            days = _DAY.findall(text)
            if not days:
                continue
            # 「1月22日（木）・23日（金）」のように2日目は月が省かれる場合がある
            first_month, first_day = int(days[0][0]), int(days[0][1])
            if len(days) >= 2:
                last_month, last_day = int(days[-1][0]), int(days[-1][1])
            elif "・" in text:
                second = re.search(r"・\s*(\d{1,2})\s*日", text)
                last_month = first_month
                last_day = int(second.group(1)) if second else first_day
            else:
                last_month, last_day = first_month, first_day
            try:
                start = date(year, first_month, first_day).isoformat()
                end = date(year, last_month, last_day).isoformat()
            except ValueError:
                continue
            meetings.append({"start": start, "end": end, "label": text.split("[")[0].strip()})
    return meetings


def save(meetings: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(
        {"fetched_at": date.today().isoformat(), "meetings": meetings},
        ensure_ascii=False, indent=2), encoding="utf-8")


def load() -> list[dict]:
    """取得済みの日程を読む。取れていなければ空を返し、画面側は黙って続ける。"""
    if not CACHE.exists():
        return []
    try:
        return json.loads(CACHE.read_text(encoding="utf-8")).get("meetings", [])
    except (ValueError, OSError):
        return []


def refresh() -> list[dict]:
    meetings = fetch()
    if meetings:
        save(meetings)
    return meetings


def next_meeting(meetings: list[dict] | None = None, today: str | None = None) -> dict | None:
    meetings = meetings if meetings is not None else load()
    today = today or date.today().isoformat()
    for m in meetings:
        if m["end"] >= today:
            return m
    return None


def last_meeting(meetings: list[dict] | None = None, today: str | None = None) -> dict | None:
    meetings = meetings if meetings is not None else load()
    today = today or date.today().isoformat()
    past = [m for m in meetings if m["end"] < today]
    return past[-1] if past else None


def days_until(meeting: dict, today: str | None = None) -> int:
    today = date.fromisoformat(today) if today else date.today()
    return (date.fromisoformat(meeting["start"]) - today).days


if __name__ == "__main__":
    got = refresh()
    print("取得", len(got), "件 ->", CACHE)
    nxt = next_meeting(got)
    if nxt:
        print("次回:", nxt["start"], "〜", nxt["end"], "(あと", days_until(nxt), "日)")
