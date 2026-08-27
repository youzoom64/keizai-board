"""系列レジストリ。系列を足す時はここに1行足すだけで済ませる。

日次(市場)と月次・四半期(公的統計)を同じ形で持つ。周期だけ cycle で分ける。
"""
from __future__ import annotations

from dataclasses import dataclass

# 差分の出し方。単位が違うものを同じ土俵で語らないために分けている。
DIFF_BP = "bp"    # 金利。差を bp で出す
DIFF_PCT = "pct"  # 為替・株・商品。変化率を % で出す
DIFF_PT = "pt"    # 既に率や倍の水準。差をポイントで出す

CYCLE_DAILY = "D"
CYCLE_MONTHLY = "M"
CYCLE_QUARTERLY = "Q"

CYCLE_LABEL = {CYCLE_DAILY: "日次", CYCLE_MONTHLY: "月次", CYCLE_QUARTERLY: "四半期"}


@dataclass(frozen=True)
class Series:
    id: str
    label: str
    group: str            # 単位グループ。実数表示の軸割り当てに使う
    unit: str
    decimals: int
    primary: tuple        # (source, symbol) / dashboard は (source, code, cycle, seasonal)
    fallback: tuple | None = None
    diff_style: str = DIFF_PCT
    color: str = "#7f7f7f"
    cycle: str = CYCLE_DAILY
    suffix: str = ""      # 最新値の後ろに付ける単位
    note: str = ""        # 画面のツールチップに出す補足
    scale: float = 1.0    # 取得値に掛ける倍率。10億円→兆円などの単位換算に使う
    solid: bool = False   # 実線で描く。色が近くても線の形で見分けられるように
    family: str = ""      # チェックの折りたたみ見出し。一列に並べると混ざって読めない

    @property
    def is_rate(self) -> bool:
        return self.diff_style == DIFF_BP

    @property
    def short_label(self) -> str:
        """チェックボックスに出す名前。

        以前は「(前年同月比)」を落としていたが、指数を足したことで
        「国内企業物価」と「国内企業物価 指数」が並び、どちらが何か
        分からなくなった。正式名をそのまま出す。
        """
        return self.label

    @property
    def always_raw(self) -> bool:
        """指数化や変化幅にしても意味が薄い系列は、常に実数で描く。"""
        return self.cycle != CYCLE_DAILY

    @property
    def sources(self) -> list[tuple]:
        return [s for s in (self.primary, self.fallback) if s]


DAILY: list[Series] = [
    Series("jgb2y", "日本2年国債", "金利", "%", 3,
           ("mof", "2年"), None, DIFF_BP, "#4c9be8", CYCLE_DAILY, "%",
           "政策金利の織り込みが出やすい年限", family="金利"),
    Series("jgb10y", "日本10年国債", "金利", "%", 3,
           ("mof", "10年"), None, DIFF_BP, "#ef5350", CYCLE_DAILY, "%",
           "いわゆる長期金利", family="金利"),
    Series("jgb30y", "日本30年国債", "金利", "%", 3,
           ("mof", "30年"), None, DIFF_BP, "#b47cf0", CYCLE_DAILY, "%",
           "財政・需給の懸念が出やすい年限", family="金利"),
    Series("ust10y", "米10年国債", "金利", "%", 3,
           ("yahoo", "^TNX"), ("fred", "DGS10"), DIFF_BP, "#ffa726", CYCLE_DAILY, "%",
           "世界的な金利上昇かどうかの物差し", family="金利"),
    Series("usdjpy", "ドル円", "為替", "円", 2,
           ("yahoo", "USDJPY=X"), ("fred", "DEXJPUS"), DIFF_PCT, "#4caf50", CYCLE_DAILY, "",
           "上昇＝円安", family="為替・株・商品"),
    Series("nikkei225", "日経平均", "株式", "円", 0,
           ("yahoo", "^N225"), ("fred", "NIKKEI225"), DIFF_PCT, "#26c6da", CYCLE_DAILY, "", family="為替・株・商品"),
    Series("wti", "WTI原油", "商品", "ドル", 2,
           ("yahoo", "CL=F"), ("fred", "DCOILWTICO"), DIFF_PCT, "#a1887f", CYCLE_DAILY, "",
           "輸入インフレの手掛かり", family="為替・株・商品"),
]

# 統計ダッシュボードAPI(appId不要)。(source, 指標コード, 周期, 季調区分)
MONTHLY: list[Series] = [
    Series("cpi_core", "コアCPI(前年同月比)", "前年比", "%", 1,
           ("dashboard", "0703010601010030010", "1", "1"),
           ("dashboard", "0703010501010030010", "1", "1"),
           DIFF_PT, "#ff7043", CYCLE_MONTHLY, "%",
           "生鮮食品を除く総合。日銀が最も見る物価", family="物価（CPI）"),
    # 指数は対になる前年比のすぐ隣に置く。離れていると見比べられない。
    Series("cpi_core_index", "コアCPI 指数", "物価指数", "2020年=100", 1,
           ("dashboard", "0703010501010090010", "1", "1"), None,
           DIFF_PCT, "#3949ab", CYCLE_MONTHLY, "",
           "生鮮食品を除く総合・2020年=100。川下の物価", solid=True, family="物価（CPI）"),
    Series("cpi_all", "総合CPI(前年同月比)", "前年比", "%", 1,
           ("dashboard", "0703010601010030000", "1", "1"),
           ("dashboard", "0703010501010030000", "1", "1"),
           DIFF_PT, "#ffb74d", CYCLE_MONTHLY, "%", family="物価（CPI）"),
    Series("cpi_core_core", "コアコアCPI(前年同月比)", "前年比", "%", 1,
           ("dashboard", "0703010601010030040", "1", "1"),
           ("dashboard", "0703010501010030040", "1", "1"),
           DIFF_PT, "#d4a017", CYCLE_MONTHLY, "%",
           "生鮮食品及びエネルギーを除く総合。基調的な物価", family="物価（CPI）"),
    Series("cgpi", "国内企業物価(前年同月比)", "前年比", "%", 1,
           ("dashboard", "0703040400000030010", "1", "1"), None,
           DIFF_PT, "#ec407a", CYCLE_MONTHLY, "%",
           "日銀の国内企業物価指数(総平均)。企業間で取引される物の価格で、"
           "消費者物価より先に動きやすい", family="企業物価"),
    Series("cgpi_index", "国内企業物価 指数", "物価指数", "2020年=100", 1,
           ("dashboard", "0703040400000090010", "1", "1"), None,
           DIFF_PCT, "#00897b", CYCLE_MONTHLY, "",
           "2020年=100。企業間で取引される物の値段", solid=True, family="企業物価"),
    Series("import_price_jpy", "輸入物価・円建(前年同月比)", "前年比", "%", 1,
           ("dashboard", "0703060401000030010", "1", "1"), None,
           DIFF_PT, "#ab47bc", CYCLE_MONTHLY, "%",
           "円ベース。契約通貨ベースとの差が、輸入インフレのうち円安由来の分になる", family="輸入・輸出物価"),
    Series("import_price_index", "輸入物価・円建 指数", "物価指数", "2020年=100", 1,
           ("dashboard", "0703060401000090010", "1", "1"), None,
           DIFF_PCT, "#f9a825", CYCLE_MONTHLY, "",
           "円ベース・2020年=100。川上の物価", solid=True, family="輸入・輸出物価"),
    Series("import_price_ccy", "輸入物価・契約通貨(前年同月比)", "前年比", "%", 1,
           ("dashboard", "0703060402000030010", "1", "1"), None,
           DIFF_PT, "#ce93d8", CYCLE_MONTHLY, "%",
           "契約通貨ベース。為替を除いた、現地価格そのものの動き", family="輸入・輸出物価"),
    Series("export_price_jpy", "輸出物価・円建(前年同月比)", "前年比", "%", 1,
           ("dashboard", "0703050401000030010", "1", "1"), None,
           DIFF_PT, "#8bc34a", CYCLE_MONTHLY, "%", family="輸入・輸出物価"),
    Series("export_price_ccy", "輸出物価・契約通貨(前年同月比)", "前年比", "%", 1,
           ("dashboard", "0703050402000030010", "1", "1"), None,
           DIFF_PT, "#c5e1a5", CYCLE_MONTHLY, "%", family="輸入・輸出物価"),
    # 前年比だけだと「どこまで上がったか」が読めない。水準を並べると、
    # 川上（輸入）→川中（企業間）→川下（消費者）の伝わり方が形で見える。
    Series("wage_real", "実質賃金(前年同月比)", "前年比", "%", 1,
           ("dashboard", "0302030201010030010", "1", "1"), None,
           DIFF_PT, "#29b6f6", CYCLE_MONTHLY, "%",
           "毎月勤労統計。物価を差し引いた賃金", family="労働・所得"),
    Series("wage_nominal", "名目賃金(前年同月比)", "前年比", "%", 1,
           ("dashboard", "0302030202010030010", "1", "1"), None,
           DIFF_PT, "#81d4fa", CYCLE_MONTHLY, "%", family="労働・所得"),
    Series("unemployment", "完全失業率", "雇用", "%", 1,
           ("dashboard", "0301010000020020010", "1", "1"), None,
           DIFF_PT, "#8d6e63", CYCLE_MONTHLY, "%", family="雇用"),
    Series("jobs_ratio", "有効求人倍率", "雇用", "倍", 2,
           ("dashboard", "0301020001000010010", "1", "1"), None,
           DIFF_PT, "#9ccc65", CYCLE_MONTHLY, "倍", family="雇用"),
    Series("gdp_nominal", "名目GDP(実額)", "GDP", "兆円", 1,
           ("dashboard", "0705010501000010000", "2", "2"), None,
           DIFF_PCT, "#26a69a", CYCLE_QUARTERLY, "兆円",
           "支出側・2020年基準・季節調整済みの年換算値。"
           "債務残高との比や税収を語る時の分母はこちら", 0.001, family="GDP"),
    Series("gdp_real_amount", "実質GDP(実額)", "GDP", "兆円", 1,
           ("dashboard", "0705020501000010000", "2", "2"), None,
           DIFF_PCT, "#5c6bc0", CYCLE_QUARTERLY, "兆円",
           "支出側・2020年基準・季節調整済みの年換算値。"
           "名目との差が物価のぶん（GDPデフレーター）", 0.001, family="GDP"),
    Series("gdp_nominal_rate", "名目GDP(前期比年率)", "前年比", "%", 1,
           ("dashboard", "0705010501000060000", "2", "2"), None,
           DIFF_PT, "#80cbc4", CYCLE_QUARTERLY, "%",
           "実質との差がGDPデフレーター、つまり物価のぶん", family="GDP"),
    Series("gdp_real", "実質GDP(前期比年率)", "前年比", "%", 1,
           ("dashboard", "0705020501000060000", "2", "2"), None,
           DIFF_PT, "#7986cb", CYCLE_QUARTERLY, "%",
           "支出側・2020年基準・季節調整値", family="GDP"),
]

SERIES: list[Series] = DAILY + MONTHLY
BY_ID: dict[str, Series] = {s.id: s for s in SERIES}
DEFAULT_CHART_IDS = ["jgb10y", "jgb2y", "usdjpy"]
