#!/usr/bin/env python3
"""POE2 国服市场报表生成器（自包含 HTML）。

复用 poe2filter-cn-tier.py 的取价 / 锚点 / 挂单量函数，把当天行情整理成一份
单文件 HTML 报表（内联 CSS，不依赖外部 CDN / JS）。

用法:
    python3 poe2-market-report.py                      # 默认输出 poe2-market-<日期>.html
    python3 poe2-market-report.py -o report.html
    python3 poe2-market-report.py --no-intl            # 跳过国服 / 国际服对比（少一次请求）
    python3 poe2-market-report.py --game poe1          # 一代同样可用

口径备忘（与过滤器工具保持一致）:
  * 接口 buy_avg = 你要买入的价（游戏里「出售挂单」侧）；sell_avg = 你能卖到的价（「求购单」侧）
  * sell*_vol = 该挂单里有多少个通货（求购那行 = 多少个支付货币 ⇒ 货币总量/价值）
  * 求购金额 = Σ(sell*_vol)，按该道具 currency_unit 折算成混沌
"""
import argparse
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import poe_ninja  # noqa: E402  (同目录)


def load_tier_tool():
    """加载同目录的 poe2filter-cn-tier.py（文件名带横线，不能用 import）。"""
    spec = importlib.util.spec_from_file_location("tier_tool", HERE / "poe2filter-cn-tier.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = load_tier_tool()


# ============================== 取数与换算 ==============================

def collect(game, token):
    prices = T.fetch_prices(game, token)
    anchors = T.compute_anchors(game, prices, T.PRICE_FIELDS)
    fac = T.compute_unit_factors(prices, game, anchors, T.PRICE_FIELDS)
    volumes = T.fetch_cn_volumes(game, token)
    return prices, anchors, fac, volumes


def mk_val(prices, anchors, game):
    def val(item, field="buy_avg"):
        return T.cn_value_in_chaos(game, item, anchors, [field]) if item else None
    return val


def mk_volume(prices, volumes, anchors, game):
    """求购金额（折算混沌）、折合件数。"""
    def vol_of(item):
        if not item:
            return None, None
        rec = volumes.get(T.normalize_name(item.get("engname") or ""))
        if not rec:
            return None, None
        raw = rec.get("sell_value")
        unit = item.get("currency_unit")
        return T._value_in_chaos(raw, unit, anchors), raw
    return vol_of


# ============================== HTML 片段 ==============================

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def fmt(v, fac, unit=None):
    return T.fmt_price(v, fac) if v is not None else "-"


def pct(v):
    if v is None:
        return "-"
    cls = "up" if v > 0 else ("down" if v < 0 else "")
    sign = "+" if v > 0 else ""
    return f'<span class="{cls}">{sign}{v:.1f}%</span>'


def table(headers, rows, cls=""):
    out = [f'<table class="{cls}">', "<thead><tr>"]
    out += [f"<th>{esc(h)}</th>" for h in headers]
    out += ["</tr></thead><tbody>"]
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    out += ["</tbody></table>"]
    return "\n".join(out)


def section(title, body, note=""):
    n = f'<p class="note">{note}</p>' if note else ""
    return f'<section><h2>{esc(title)}</h2>{n}{body}</section>'


CSS = """
:root{--bg:#12141a;--card:#1b1e27;--fg:#e6e8ee;--dim:#9aa1b1;--acc:#c8a24a;
--up:#4caf7d;--down:#e05c5c;--line:#2a2f3a}
*{box-sizing:border-box}
body{margin:0;padding:32px;background:var(--bg);color:var(--fg);
font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:17px;margin:0 0 10px;color:var(--acc);border-left:3px solid var(--acc);padding-left:10px}
.sub{color:var(--dim);margin-bottom:24px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:18px 20px;margin-bottom:20px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-weight:500;font-size:12px;text-align:right}
th:first-child,td:first-child{text-align:left}
tbody tr:hover{background:#222732}
.up{color:var(--up)} .down{color:var(--down)}
.note{color:var(--dim);font-size:12px;margin:0 0 12px}
.cards{display:flex;gap:14px;flex-wrap:wrap}
.card{background:#222732;border-radius:8px;padding:12px 16px;min-width:150px}
.card .k{color:var(--dim);font-size:12px}
.card .v{font-size:19px;margin-top:4px}
.tag{display:inline-block;padding:1px 7px;border-radius:99px;font-size:11px;line-height:1.7}
.tag.ok{background:#1e3a2c;color:#7fd3a3}
.tag.warn{background:#3a2a1e;color:#e0b37a}
.tag.no{background:#331f1f;color:#e08a8a}
ul.hl{margin:0;padding-left:20px} ul.hl li{margin-bottom:4px}
footer{color:var(--dim);font-size:12px;margin-top:8px}
code{background:#222732;padding:1px 5px;border-radius:4px}
"""


# ============================== 报表主体 ==============================

def build(args):
    game = args.game
    token = args.token or __import__("os").environ.get("POECURRENCY_TOKEN") or T.load_api_token()
    prices, anchors, fac, volumes = collect(game, token)

    val = mk_val(prices, anchors, game)
    vol_of = mk_volume(prices, volumes, anchors, game)
    cpd = anchors["divine_in_chaos"]

    def row_of(name):
        return prices.get(T.normalize_name(name))

    # ---------- 基准通货 ----------
    bench = []
    for name in T.BENCHMARK_NAMES_COMMON + T.BENCHMARK_NAMES_EXTRA.get(game, []):
        it = row_of(name)
        if not it:
            continue
        v = val(it)
        sv, _ = vol_of(it)
        bench.append((it, v, sv))
    bench.sort(key=lambda r: -(r[1] or 0))

    # ---------- 全部道具（带常用派生量） ----------
    rows = []
    for key, it in prices.items():
        if not it:
            continue
        b, s = val(it, "buy_avg"), val(it, "sell_avg")
        sv, raw = vol_of(it)
        rows.append({
            "key": key, "it": it,
            "cn": it.get("item_name") or key, "en": it.get("engname") or key,
            "buy": b, "sell": s, "vol": sv,
            "ratio": (b / s) if (b and s) else None,
            "r24": it.get("buy_avg_ratio"), "s24": it.get("sell_avg_ratio"),
        })

    # ---------- 精华 ----------
    ess = [r for r in rows if "精华" in str(r["cn"]) or "essence" in r["key"]]
    ess.sort(key=lambda r: -(r["buy"] or 0))

    # ---------- 合成套利 ----------
    craft = []
    for child, rule in (T.CRAFT_RATIOS.get(game) or {}).items():
        if not isinstance(rule, dict) or not rule.get("parent") or not rule.get("count"):
            continue
        cit, pit = row_of(child), row_of(rule["parent"])
        if not cit or not pit:
            continue
        cv, pv = val(cit), val(pit, "sell_avg") or val(pit)
        if not pv:
            continue
        derived = pv / rule["count"]
        craft.append({
            "cn": cit.get("item_name") or child, "en": child,
            "own": cv, "pcn": pit.get("item_name") or rule["parent"],
            "pv": pv, "derived": derived, "count": rule["count"],
            "worth": "合成" if (not cv or derived > cv) else "自身",
        })

    # ---------- 流动性 / 价差 ----------
    liq = [r for r in rows if r["vol"]]
    liq.sort(key=lambda r: -(r["vol"] or 0))
    spread = [r for r in rows if r["ratio"] and r["ratio"] >= 3 and r["buy"] and r["buy"] > 0.5]
    spread.sort(key=lambda r: -r["ratio"])

    # ---------- 涨跌 ----------
    mov = [r for r in rows if r["r24"] is not None and r["buy"]]
    up = sorted(mov, key=lambda r: -r["r24"])[:10]
    down = sorted(mov, key=lambda r: r["r24"])[:10]

    # ---------- 国服 vs 国际服 ----------
    intl_rows, league = [], None
    if not args.no_intl:
        try:
            league = poe_ninja.get_current_league(game)
            names = {str(it.get("engname")) for it in prices.values() if it.get("engname")}
            intl = poe_ninja.fetch_international_prices(game, name_set=names)
            intl_lc = {str(k).lower(): v for k, v in intl.items()}
            for r in rows:
                iv = intl_lc.get(str(r["en"]).lower())
                if iv and r["buy"]:
                    intl_rows.append({**r, "intl": iv, "iratio": r["buy"] / iv})
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 国际服对比跳过：{e}")
    cheap = sorted([r for r in intl_rows if r["iratio"]], key=lambda r: r["iratio"])[:12]
    pricey = sorted([r for r in intl_rows if r["iratio"]], key=lambda r: -r["iratio"])[:12]

    # ---------- 组装 HTML ----------
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    latest = next((it.get("latest_datetime") for it in prices.values() if it.get("latest_datetime")), "-")

    def t_basic(r, extra=None):
        cells = [f'<b>{esc(r["cn"])}</b>', fmt(r["buy"], fac), pct(r["r24"]),
                 fmt(r["vol"], fac) if r["vol"] else "-"]
        if extra:
            cells += extra(r)
        return cells

    h_bench = table(["名称", "国服价", "24h(买)", "求购金额"],
                    [[f'<b>{esc(it.get("item_name"))}</b>', fmt(v, fac), pct(it.get("buy_avg_ratio")),
                      fmt(sv, fac) if sv else "-"] for it, v, sv in bench])

    h_ess = table(["精华", "国服价", "24h(买)", "求购金额", "求购价"],
                  [[f'<b>{esc(r["cn"])}</b>', fmt(r["buy"], fac), pct(r["r24"]),
                    fmt(r["vol"], fac) if r["vol"] else "-",
                    fmt(r["sell"], fac)] for r in ess[:25]])

    h_craft = table(["碎片", "自身价", "成品", "成品价", "合成价(÷份数)", "取值"],
                    [[f'<b>{esc(c["cn"])}</b>', fmt(c["own"], fac), esc(c["pcn"]),
                      fmt(c["pv"], fac), fmt(c["derived"], fac),
                      f'<span class="tag {"ok" if c["worth"]=="合成" else "warn"}">{c["worth"]}</span>'
                      f' <span class="note">÷{c["count"]}</span>'] for c in craft])

    h_liq = table(["道具", "求购金额", "求购价", "出售挂单价", "价差"],
                  [[f'<b>{esc(r["cn"])}</b>', fmt(r["vol"], fac), fmt(r["sell"], fac),
                    fmt(r["buy"], fac), f'{r["ratio"]:.1f}×' if r["ratio"] else "-"]
                   for r in liq[:20]])

    h_spread = table(["道具", "出售挂单价", "求购价", "价差", "求购金额"],
                     [[f'<b>{esc(r["cn"])}</b>', fmt(r["buy"], fac), fmt(r["sell"], fac),
                       f'{r["ratio"]:.1f}×', fmt(r["vol"], fac) if r["vol"] else "-"]
                      for r in spread[:20]])

    h_mov = table(["涨", "24h", "价"], [[f'<b>{esc(r["cn"])}</b>', pct(r["r24"]), fmt(r["buy"], fac)]
                                        for r in up]) + \
        table(["跌", "24h", "价"], [[f'<b>{esc(r["cn"])}</b>', pct(r["r24"]), fmt(r["buy"], fac)]
                                    for r in down])

    h_intl = ""
    if intl_rows:
        h_intl = table(["国服便宜", "国服", "国际服", "比值"],
                       [[f'<b>{esc(r["cn"])}</b>', fmt(r["buy"], fac), fmt(r["intl"], fac),
                         f'{r["iratio"]:.2f}×'] for r in cheap]) + \
            table(["国服偏贵", "国服", "国际服", "比值"],
                  [[f'<b>{esc(r["cn"])}</b>', fmt(r["buy"], fac), fmt(r["intl"], fac),
                    f'{r["iratio"]:.2f}×'] for r in pricey])

    # ---------- 要点 ----------
    hl = [f'汇率：1 神圣 = <b>{cpd:.2f} 混沌</b>，1 崇高 = <b>{anchors["base_in_chaos"]:.4f} 混沌</b>']
    if ess:
        hl.append(f'最贵精华：<b>{esc(ess[0]["cn"])}</b> {fmt(ess[0]["buy"], fac)}')
    if liq:
        hl.append(f'求购资金最厚：<b>{esc(liq[0]["cn"])}</b> {fmt(liq[0]["vol"], fac)}')
    arb = [c for c in craft if c["worth"] == "合成"]
    if arb:
        hl.append("合成套利：<b>" + "、".join(esc(c["cn"]) for c in arb) + "</b>（合成价高于自身价）")
    if spread:
        hl.append(f'买卖价差最大：<b>{esc(spread[0]["cn"])}</b> {spread[0]["ratio"]:.0f}×（挂高价大概率卖不掉）')

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>POE2 国服市场报表 {now}</title><style>{CSS}</style></head><body>
<h1>POE2 国服市场报表</h1>
<div class="sub">生成时间 {now} ｜ 接口最新数据 {esc(str(latest))} ｜ 数据源 poecurrency.top（国服）+ poe.ninja（国际服{esc(league or "")}）</div>

<section><h2>本期要点</h2><ul class="hl">{"".join(f"<li>{x}</li>" for x in hl)}</ul></section>

{section("基准通货", h_bench, "标志通货与常用兑换；24h 涨跌取买均价变化")}
{section("精华行情", h_ess, "国服精华按买入价排序")}
{section("碎片合成", h_craft, "碎片价值 = max(自身价, 成品价 ÷ 份数)；标「合成」表示按合成价更值")}
{section("流动性榜（求购资金最厚 = 最容易变现）", h_liq, "求购金额 = 求购侧挂单的货币总量（折算混沌）")}
{section("买卖价差异常（挂高价可能长期卖不掉）", h_spread, "价差 = 出售挂单价 ÷ 求购价；越大越说明卖盘虚高")}
{section("24 小时涨跌", h_mov, "按买均价 24h 变化率")}
{section(f"国服 vs 国际服", h_intl, "比值 = 国服价 ÷ 国际服价；仅供交叉参考，不参与档位判定") if h_intl else ""}

<footer>
口径：接口 <code>buy_avg</code> = 你要买入的价（游戏内「出售挂单」侧），
<code>sell_avg</code> = 你能卖到的价（「求购单」侧）；
<code>*_vol</code> = 该挂单里有多少个通货。
本报表由 <code>poe2-market-report.py</code> 生成，仅供个人参考。
</footer>
</body></html>"""
    return html_doc


def main(argv=None):
    ap = argparse.ArgumentParser(description="POE2 国服市场报表生成器（HTML）")
    ap.add_argument("-o", "--output", default=None, help="输出 HTML 路径")
    ap.add_argument("--game", default="poe2", choices=["poe2", "poe1"])
    ap.add_argument("--no-intl", action="store_true", help="跳过国服/国际服对比")
    ap.add_argument("--token", default=None,
                    help="poecurrency.top API Token（默认读同目录 config 或环境变量 POECURRENCY_TOKEN）")
    args = ap.parse_args(argv)

    doc = build(args)
    out = Path(args.output) if args.output else HERE / f"poe2-market-{datetime.now():%Y-%m-%d}.html"
    out.write_text(doc, encoding="utf-8")
    print(f"[完成] 报表已生成：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
