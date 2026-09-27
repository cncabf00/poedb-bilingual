#!/usr/bin/env python3
"""POE2 国服市场日报（v2 · 按玩家视角组织，PPT 式一屏一类）。

设计原则（主人反馈后重做）：
  1. 先定「我是谁、要干什么」，再决定看什么 —— 三个视角：刷子 / 做装 / 保值
  2. 一屏一类，每类只给 Top 3~8，不铺全量
  3. 数字说人话：智能单位（D/C/E）+ 两位有效数字 + 涨跌箭头
  4. 视觉上卡片化 + 条形图，像 PPT 而不是数据库导出

用法:
    python3 poe2-market-report.py                 # → poe2-market-<日期>.html
    python3 poe2-market-report.py --game poe1
    python3 poe2-market-report.py --no-intl
"""
import argparse
import importlib.util
import math
import os
import sys
import urllib.parse
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import poe_ninja  # noqa: E402


def load_tier_tool():
    spec = importlib.util.spec_from_file_location("tier_tool", HERE / "poe2filter-cn-tier.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = load_tier_tool()

# 做装耗材类目（按接口 category_label 匹配，命中即入"做装"视角）
CRAFT_CATS = ["精华", "催化剂", "合金通货", "溶剂", "秘术溶剂", "预兆",
              "符文", "灵核", "星辉矿石", "液化情感"]


# ============================== 取数 ==============================

def fetch_items(game, token=None):
    """取全部道具（带 category_label）。token 无效时自动回退公开接口。"""
    version = "1" if game == "poe1" else "2"
    urls = []
    if token:
        urls.append(f"{T.API_BASE}/api/summary_validate?version={version}"
                    f"&token={urllib.parse.quote(token)}")
    urls.append(f"{T.API_BASE}/api/summary?version={version}")
    data = None
    for u in urls:
        try:
            data = T.http_get_json(u, timeout=60)
            break
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] {u.split('?')[0]} 取数失败（{e}），尝试下一个源")
    if not data:
        raise RuntimeError("无法获取行情数据")
    out = {}
    for grp in data:
        cat = grp.get("category_label") or "其他"
        for it in grp.get("items", []):
            key = T.normalize_name(it.get("engname") or "")
            if not key:
                continue
            it["_cat"] = cat
            out[key] = it
    return out


# ============================== 数字格式化 ==============================

def make_fmt(cpd, cpe):
    """智能单位：>=1D 用 D，>=1C 用 C，其余用 E。"""
    def f(v):
        if v is None:
            return "-"
        if v >= cpd:
            return f"{v / cpd:,.2f}D"
        if v >= 1:
            return f"{v:,.2f}C"
        return f"{v * cpe:,.2f}E"
    return f


def delta(r):
    if r is None:
        return '<span class="dim">–</span>'
    if r >= 0.5:
        return f'<span class="up">▲{r:.1f}%</span>'
    if r <= -0.5:
        return f'<span class="down">▼{abs(r):.1f}%</span>'
    return '<span class="dim">±0.0%</span>'


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bar(pct_of_max, kind="up"):
    w = max(3, min(100, pct_of_max * 100))
    return f'<div class="bar {kind}"><i style="width:{w:.0f}%"></i></div>'


# ============================== HTML 骨架 ==============================

CSS = """
:root{--bg:#0f1116;--card:#181b23;--card2:#1f2430;--fg:#e8eaf0;--dim:#8f97a8;
--acc:#d4af5f;--up:#57c98a;--down:#ef6b6b;--line:#262b36}
*{box-sizing:border-box}
body{margin:0;padding:36px 40px;background:var(--bg);color:var(--fg);
font:15px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif}
h1{font-size:26px;margin:0 0 6px;letter-spacing:.5px}
.stamp{color:var(--dim);font-size:13px;margin-bottom:26px}
h2{font-size:19px;margin:0 0 4px;display:flex;align-items:center;gap:9px}
h2 .ico{font-size:20px}
h2 .who{color:var(--acc);font-size:12px;border:1px solid var(--acc);border-radius:99px;
padding:1px 9px;font-weight:400}
.hint{color:var(--dim);font-size:12.5px;margin:0 0 16px}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:22px 26px;margin-bottom:22px}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:22px}
.kpi{background:linear-gradient(160deg,#20263a,#181b23);border:1px solid var(--line);
border-radius:14px;padding:16px 20px}
.kpi .k{color:var(--dim);font-size:12.5px}
.kpi .v{font-size:28px;font-weight:600;margin-top:2px;letter-spacing:.5px}
.kpi .s{color:var(--dim);font-size:12px;margin-top:2px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.panel{background:var(--card2);border:1px solid var(--line);border-radius:12px;padding:14px 18px}
.panel h3{margin:0 0 10px;font-size:14.5px;color:var(--fg);font-weight:600}
.panel h3 small{color:var(--dim);font-weight:400;margin-left:6px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:13.5px}
th{color:var(--dim);font-weight:500;font-size:12px;text-align:right;padding:5px 8px;
border-bottom:1px solid var(--line)}
td{padding:7px 8px;text-align:right;border-bottom:1px solid #1e2330}
th:first-child,td:first-child{text-align:left}
tr:last-child td{border-bottom:none}
.up{color:var(--up)} .down{color:var(--down)} .dim{color:var(--dim)}
.bar{height:5px;border-radius:3px;background:#2a3140;overflow:hidden;min-width:60px}
.bar i{display:block;height:100%;background:var(--up)}
.bar.down i{background:var(--down)}
.bar.acc i{background:var(--acc)}
.bullets{margin:0;padding-left:20px}
.bullets li{margin-bottom:7px}
.tag{font-size:11px;padding:1px 7px;border-radius:99px;background:#2a3140;color:var(--dim)}
footer{color:var(--dim);font-size:12px;margin-top:10px;line-height:1.9}
code{background:#232936;padding:1px 6px;border-radius:5px;font-size:12px}
"""


def kpi(k, v, s=""):
    return f'<div class="kpi"><div class="k">{esc(k)}</div><div class="v">{v}</div><div class="s">{esc(s)}</div></div>'


def panel(title, sub, body):
    s = f"<small>{esc(sub)}</small>" if sub else ""
    return f'<div class="panel"><h3>{esc(title)}{s}</h3>{body}</div>'


def table(headers, rows):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


def section(icon, title, who, hint, body):
    return (f'<section><h2><span class="ico">{icon}</span>{esc(title)}'
            f'<span class="who">{esc(who)}</span></h2>'
            f'<p class="hint">{hint}</p>{body}</section>')


# ============================== 报表主体 ==============================

def build(args):
    game = args.game
    token = args.token or os.environ.get("POECURRENCY_TOKEN") or T.load_api_token()
    items = fetch_items(game, token)
    anchors = T.compute_anchors(game, items, T.PRICE_FIELDS)
    volumes = T.fetch_cn_volumes(game, token)
    cpd = anchors["divine_in_chaos"]
    cpe = 1.0 / anchors["base_in_chaos"]
    fnum = make_fmt(cpd, cpe)

    def val(it, fld="buy_avg"):
        return T.cn_value_in_chaos(game, it, anchors, [fld]) if it else None

    def volc(it):
        rec = volumes.get(T.normalize_name(it.get("engname") or ""))
        if not rec:
            return None
        return T._value_in_chaos(rec.get("sell_value"), it.get("currency_unit"), anchors)

    rows = []
    for key, it in items.items():
        rows.append({
            "cn": it.get("item_name") or key, "en": it.get("engname") or key,
            "cat": it.get("_cat") or "其他", "it": it,
            "p": val(it), "s": val(it, "sell_avg"), "v": volc(it),
            "r24": it.get("buy_avg_ratio"),
        })

    def spread(r):
        return (r["p"] / r["s"]) if (r["p"] and r["s"]) else None

    # ---------- 类目冷热 ----------
    cats = defaultdict(list)
    for r in rows:
        cats[r["cat"]].append(r)
    cat_stat = []
    for c, rs in cats.items():
        rr = [r["r24"] for r in rs if r["r24"] is not None and r["p"]]
        best = max((r["p"] or 0) for r in rs)
        cat_stat.append({"cat": c, "n": len(rs), "avg": (sum(rr) / len(rr)) if rr else None,
                         "best": best, "top": max(rs, key=lambda r: r["p"] or 0)})

    # ---------- 刷子：正在涨 / 正在跌 ----------
    liquid = [r for r in rows if r["v"] and r["p"]]
    up = sorted([r for r in liquid if r["r24"] and r["r24"] > 0],
                key=lambda r: -r["r24"])[:6]
    down = sorted([r for r in liquid if r["r24"] and r["r24"] < 0 and r["p"] >= 5],
                  key=lambda r: r["r24"])[:6]

    # ---------- 做装：按类目 Top3 ----------
    craft = []
    for c in CRAFT_CATS:
        rs = [r for r in cats.get(c, []) if r["p"]]
        if not rs:
            continue
        craft.append((c, sorted(rs, key=lambda r: -r["p"])[:3]))

    # ---------- 保值：抗跌 × 变现 × 量级 ----------
    def keep_score(r):
        if not (r["p"] and r["v"]) or r["r24"] is None:
            return None
        steady = 1.0 / (1.0 + abs(r["r24"]) / 8.0)          # 24h 越稳越高
        money = min(1.0, math.log10(r["v"] + 1) / 3.5)       # 求购资金越厚越高
        scale = min(1.0, math.log10(r["p"] + 1) / 4.0)       # 价格量级越大越像"资产"
        return steady * money * scale
    keep = []
    for r in rows:
        sc = keep_score(r)
        if sc:
            keep.append({**r, "score": sc})
    keep.sort(key=lambda r: -r["score"])
    keep = keep[:10]

    # ---------- 避坑：买卖价差 ----------
    bad = [r for r in rows if spread(r) and spread(r) >= 3 and r["p"] and r["p"] >= 0.5]
    bad.sort(key=lambda r: -spread(r))

    # ---------- 合成套利 ----------
    arb = []
    for child, rule in (T.CRAFT_RATIOS.get(game) or {}).items():
        if not isinstance(rule, dict) or not rule.get("parent") or not rule.get("count"):
            continue
        ci = items.get(T.normalize_name(child))
        pi = items.get(T.normalize_name(rule["parent"]))
        if not ci or not pi:
            continue
        cv, pv = val(ci), val(pi, "sell_avg") or val(pi)
        if not pv:
            continue
        der = pv / rule["count"]
        arb.append({"cn": ci.get("item_name") or child, "own": cv, "der": der,
                    "pc": pi.get("item_name") or rule["parent"], "n": rule["count"]})

    # ---------- 国服 vs 国际服 ----------
    intl_hi, intl_lo, league = [], [], None
    if not args.no_intl:
        try:
            league = poe_ninja.get_current_league(game)
            names = {r["en"] for r in rows}
            intl = poe_ninja.fetch_international_prices(game, name_set=names)
            lc = {str(k).lower(): v for k, v in intl.items()}
            both = [{**r, "i": lc[str(r["en"]).lower()]} for r in rows if str(r["en"]).lower() in lc]
            both = [r for r in both if r["p"] and r["i"]]
            for r in both:
                r["iratio"] = r["p"] / r["i"]
            intl_lo = sorted(both, key=lambda r: r["iratio"])[:5]
            intl_hi = sorted(both, key=lambda r: -r["iratio"])[:5]
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 国际服对比跳过：{e}")

    # ============ 组装 ============
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = next((it.get("latest_datetime") for it in items.values() if it.get("latest_datetime")), "-")

    # KPI
    kpis = [
        kpi("1 神圣石", f"{cpd:,.2f} C", "计价基准"),
        kpi("1 崇高石", f"{anchors['base_in_chaos']:,.4f} C", "小额通货单位"),
        kpi("行情时间", str(stamp)[5:], "数据源 poecurrency.top"),
    ]

    # 速览
    spot = []
    if up:
        spot.append(f'气氛最热：<b>{esc(up[0]["cn"])}</b> '
                    f'<span class="up">▲{up[0]["r24"]:.1f}%</span> → {fnum(up[0]["p"])}')
    if down:
        spot.append(f'跌得最狠：<b>{esc(down[0]["cn"])}</b> '
                    f'<span class="down">▼{abs(down[0]["r24"]):.1f}%</span> → {fnum(down[0]["p"])}')
    if keep:
        spot.append(f'最稳的资产：<b>{esc(keep[0]["cn"])}</b> {fnum(keep[0]["p"])}'
                    f'（24h {keep[0]["r24"]:+.1f}%，求购 {fnum(keep[0]["v"])}）')
    if bad:
        spot.append(f'最坑的挂单：<b>{esc(bad[0]["cn"])}</b> 价差 '
                    f'{spread(bad[0]):.0f}×（挂高价基本卖不掉）')
    if arb:
        a = max(arb, key=lambda x: (x["der"] / x["own"]) if x["own"] else 99)
        spot.append(f'合成划算：<b>{esc(a["cn"])}</b> 自身 {fnum(a["own"])} ＜ 合成价 {fnum(a["der"])}')

    # 刷子视角
    def r_rows(rs):
        vmax = max((r["p"] or 0) for r in rs) or 1
        return [[f'<b>{esc(r["cn"])}</b><br><span class="tag">{esc(r["cat"])}</span>',
                 fnum(r["p"]), delta(r["r24"]),
                 f'{fnum(r["v"])}{bar((r["v"] or 0) / (max((x["v"] or 0) for x in rs) or 1), "acc")}']
                for r in rs]

    cat_bars = []
    cmax = max((abs(c["avg"]) for c in cat_stat if c["avg"]), default=1)
    for c in sorted([x for x in cat_stat if x["avg"] is not None], key=lambda x: -x["avg"])[:10]:
        w = abs(c["avg"]) / cmax * 100
        kind = "up" if c["avg"] >= 0 else "down"
        cat_bars.append(
            f'<tr><td>{esc(c["cat"])} <span class="dim">({c["n"]})</span></td>'
            f'<td class="{"up" if c["avg"]>=0 else "down"}">{c["avg"]:+.1f}%</td>'
            f'<td style="width:45%">{bar(w/100, kind)}</td></tr>')

    farm = (f'<div class="grid">'
            f'{panel("🔥 值得关注（在涨 + 卖得掉）", "按 24h 涨幅", table(["道具", "价", "24h", "求购资金"], r_rows(up))) }'
            f'{panel("⚠️ 建议避开（曾经值钱、在跌）", "价格 ≥ 5C 且 24h 跌幅大", table(["道具", "价", "24h", "求购资金"], r_rows(down))) }'
            f'</div>'
            f'<div style="margin-top:18px">'
            f'{panel("各玩法冷热", "类目内 24h 平均涨跌", table(["类目", "均值", ""], cat_bars))}'
            f'</div>')

    # 做装视角
    ccards = []
    for c, rs in craft:
        body = "".join(
            f'<tr><td>{esc(r["cn"])}</td><td>{fnum(r["p"])}</td><td>{delta(r["r24"])}</td>'
            f'<td class="dim">{fnum(r["v"]) if r["v"] else "薄"}</td></tr>'
            for r in rs)
        ccards.append(panel(c, "Top 3", "<table>" + body + "</table>"))
    craft_html = f'<div class="grid3">{"".join(ccards)}</div>'
    if arb:
        craft_html += ('<div style="margin-top:18px">' +
                       panel("碎片合成（自身价 vs 成品价 ÷ 份数）", "标 ▲ 表示按合成价更值",
                             table(["碎片", "自身价", "成品价", "合成价", ""],
                                   [[f'<b>{esc(a["cn"])}</b>', fnum(a["own"]), 
                                     f'{esc(a["pc"])} {fnum(a["der"] * a["n"])}', fnum(a["der"]),
                                     '<span class="up">▲ 取合成价</span>'
                                     if (not a["own"] or a["der"] > a["own"]) else
                                     '<span class="dim">取自身价</span>'] for a in arb])) +
                       '</div>')

    # 保值视角
    k_rows = [[f'<b>{esc(r["cn"])}</b>', fnum(r["p"]), delta(r["r24"]),
               fnum(r["v"]), f'{r["score"]*100:.0f}'] for r in keep]
    wealth = panel("抗跌 + 好变现 + 有量级", "保值分 = 稳定性 × 求购资金 × 价格量级",
                   table(["道具", "价", "24h", "求购资金", "分"], k_rows))

    # 避坑
    b_rows = [[f'<b>{esc(r["cn"])}</b>', fnum(r["p"]), fnum(r["s"]),
               f'{spread(r):.1f}×', fnum(r["v"]) if r["v"] else '<span class="down">薄</span>']
              for r in bad[:8]]
    avoid = panel("挂高价可能长期卖不掉", "价差 = 出售挂单价 ÷ 求购价",
                  table(["道具", "出售挂单价", "求购价", "价差", "求购资金"], b_rows))

    # 国际服
    intl_html = ""
    if intl_lo or intl_hi:
        io = [[f'<b>{esc(r["cn"])}</b>', fnum(r["p"]), fnum(r["i"]), f'{r["iratio"]:.2f}×']
              for r in intl_lo]
        ih = [[f'<b>{esc(r["cn"])}</b>', fnum(r["p"]), fnum(r["i"]), f'{r["iratio"]:.2f}×']
              for r in intl_hi]
        intl_html = (f'<div class="grid">'
                     f'{panel("国服更便宜", "比值 < 1", table(["道具", "国服", "国际服", "比值"], io))}'
                     f'{panel("国服更贵", "比值 > 1", table(["道具", "国服", "国际服", "比值"], ih))}'
                     f'</div>')

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>POE2 国服市场日报 {now}</title><style>{CSS}</style></head><body>
<h1>POE2 国服市场日报</h1>
<div class="stamp">生成于 {now} ｜ 行情时间 {esc(str(stamp))} ｜ 数据源 poecurrency.top（国服）· poe.ninja（{esc(league or '国际服')}）</div>

<div class="kpis">{"".join(kpis)}</div>

<section><h2><span class="ico">📌</span>今日要点</h2>
<ul class="bullets">{"".join(f"<li>{x}</li>" for x in spot)}</ul></section>

{section("⛏️", "刷子视角", "我在刷图", "看两件事：哪类玩法在涨（值得刷）、哪些东西曾经值钱但现在在跌（避开）", farm)}

{section("🔨", "做装视角", "我在做装备", "做装耗材按玩法分类，每类只看最贵的三个", craft_html)}

{section("🏦", "保值视角", "我在存钱", "没有基准货币，所以用三件事衡量：抗跌（24h 稳）、好变现（求购资金厚）、有量级（价格够高）", wealth)}

{section("🚫", "避坑清单", "别踩", "买卖价差过大的道具：挂高价可能长期无人接", avoid)}

{section("🌍", "国服 vs 国际服", "交叉参考", "仅供参照，不参与过滤器档位判定", intl_html) if intl_html else ""}

<footer>
口径：<code>buy_avg</code> = 你要买入的价（游戏内「出售挂单」侧）；<code>sell_avg</code> = 你能卖到的价（「求购单」侧）；
<code>sell*_vol</code> = 求购侧挂单的货币总量。保值分为自定口径，仅供参考。<br>
生成：<code>python3 poe2-market-report.py</code>
</footer></body></html>"""
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description="POE2 国服市场日报（v2）")
    ap.add_argument("-o", "--output", default=None, help="输出 HTML 路径")
    ap.add_argument("--game", default="poe2", choices=["poe2", "poe1"])
    ap.add_argument("--no-intl", action="store_true", help="跳过国服/国际服对比")
    ap.add_argument("--token", default=None, help="poecurrency.top token（默认读 config / 环境变量）")
    args = ap.parse_args(argv)
    out = Path(args.output) if args.output else HERE / f"poe2-market-{datetime.now():%Y-%m-%d}.html"
    out.write_text(build(args), encoding="utf-8")
    print(f"[完成] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
