#!/usr/bin/env python3
"""POE2 国服市场日报（v5 · 按玩法组织的策略清单）。

设计标准：**每一栏都必须回答一个具体问题**，答不上来的删掉。

栏目与它们回答的问题
  📋 策略清单（按玩法）—— 哪类玩法资金量大（值得投入）？每类最值钱的产出是什么？
  💎 高价值资产        —— 哪些东西适合长期持有/储值（稳定高价、奢侈品）？
  📉 长线下跌          —— 哪些东西在贬值、不要囤？（只认多日跌幅，短周期波动不作数）
  🌍 国服与国际服       —— 两边价差（交叉参考）

工程约定
  - 产物统一进 reports/
  - 行情按日缓存进 cache/<game>/market-YYYY-MM-DD.json（含锚点），当天复用、--refresh 强拉
  - 在线失败自动回退最近可用缓存；多日列有几天算几天

用法
  python3 poe2-market-report.py [--refresh] [--days 7] [--game poe2] [--no-intl]
"""
import argparse
import importlib.util
import json
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

CACHE_DIRNAME = "cache"
OUT_DIRNAME = "reports"

# 奢侈品/高价资产的判定门槛（以 D 为单位，运行时乘 cpd）
LUX_MIN_DIVINE = 20.0
# 长线下跌判定门槛（多日跌幅，%）
DECLINE_PCT = -20.0


def load_tier_tool():
    spec = importlib.util.spec_from_file_location("tier_tool", HERE / "poe2filter-cn-tier.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = load_tier_tool()


# ============================== 取数 ==============================

def fetch_items(game, token=None):
    """在线取全部道具（带 category_label）。token 无效时自动回退公开接口。"""
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


# ------------------------------ 按日缓存 ------------------------------

def cache_file(cache_dir, game, day):
    return Path(cache_dir) / game / f"market-{day}.json"


def load_day(game, token, cache_dir, refresh=False):
    """取当天数据：优先当天缓存，未命中才在线拉取并落盘；失败回退最近缓存。"""
    today = datetime.now().strftime("%Y-%m-%d")
    p = cache_file(cache_dir, game, today)
    if p.exists() and not refresh:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            print(f"[cache] 命中当天缓存 {p.name}")
            return d.get("items") or {}, d.get("volumes") or {}, {"day": today, "cached": True}
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 当天缓存损坏（{e}），重新拉取")
    items = volumes = None
    try:
        items = fetch_items(game, token)
        volumes = T.fetch_cn_volumes(game, token)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 在线取数失败（{e}），尝试回退历史缓存")
    if items:
        anchors = T.compute_anchors(game, items, T.PRICE_FIELDS)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"),
                                 "game": game, "anchors": anchors,
                                 "items": items, "volumes": volumes or {}},
                                ensure_ascii=False), encoding="utf-8")
        print(f"[cache] 已写入 {p}")
        return items, volumes or {}, {"day": today, "cached": False}
    for f in sorted((Path(cache_dir) / game).glob("market-*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        day = f.stem.replace("market-", "")
        if day == today:
            continue
        print(f"[回退] 使用历史缓存 {f.name}")
        return d.get("items") or {}, d.get("volumes") or {}, {"day": day, "cached": True, "stale": True}
    raise RuntimeError("无可用数据（在线失败且无缓存）")


def load_history(game, cache_dir, days, today):
    """读最近 N 天缓存（不含 today），返回 {day: 缓存dict}，按日期升序。"""
    base = Path(cache_dir) / game
    if not base.exists():
        return {}
    out = {}
    for f in sorted(base.glob("market-*.json")):
        day = f.stem.replace("market-", "")
        if day == today:
            continue
        try:
            out[day] = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
    keys = sorted(out)[-days:] if days else sorted(out)
    return {k: out[k] for k in keys}


def hist_price(game, rec, key, anchors_fallback):
    it = (rec.get("items") or {}).get(key)
    if not it:
        return None
    a = rec.get("anchors") or anchors_fallback
    return T.cn_value_in_chaos(game, it, a, T.PRICE_FIELDS)


# ============================== 展示 ==============================

def make_fmt(cpd, cpe):
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


def bar(pct, kind="up"):
    w = max(3, min(100, pct * 100))
    return f'<div class="bar {kind}"><i style="width:{w:.0f}%"></i></div>'


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
.asks{color:var(--fg);background:#1c2130;border-left:3px solid var(--acc);
padding:7px 14px;border-radius:0 8px 8px 0;font-size:13px;margin:0 0 16px}
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
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.panel{background:var(--card2);border:1px solid var(--line);border-radius:12px;padding:13px 16px;
margin-bottom:14px}
.panel h3{margin:0 0 9px;font-size:14px;font-weight:600;display:flex;
justify-content:space-between;align-items:baseline;gap:8px}
.panel h3 em{font-style:normal;color:var(--dim);font-size:11.5px;font-weight:400}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:13px}
th{color:var(--dim);font-weight:500;font-size:11.5px;text-align:right;padding:4px 7px;
border-bottom:1px solid var(--line)}
td{padding:6px 7px;text-align:right;border-bottom:1px solid #1e2330}
th:first-child,td:first-child{text-align:left}
tr:last-child td{border-bottom:none}
.up{color:var(--up)} .down{color:var(--down)} .dim{color:var(--dim)}
.bar{height:5px;border-radius:3px;background:#2a3140;overflow:hidden;min-width:50px}
.bar i{display:block;height:100%;background:var(--up)}
.bar.down i{background:var(--down)}
.bar.acc i{background:var(--acc)}
.bullets{margin:0;padding-left:20px} .bullets li{margin-bottom:6px}
footer{color:var(--dim);font-size:12px;margin-top:10px;line-height:1.9}
code{background:#232936;padding:1px 6px;border-radius:5px;font-size:12px}
"""


def kpi(k, v, s=""):
    return (f'<div class="kpi"><div class="k">{esc(k)}</div>'
            f'<div class="v">{v}</div><div class="s">{esc(s)}</div></div>')


def panel(title, sub, body):
    e = f"<em>{esc(sub)}</em>" if sub else ""
    return f'<div class="panel"><h3><span>{esc(title)}</span>{e}</h3>{body}</div>'


def table(headers, rows):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


def section(icon, title, who, ask, body):
    return (f'<section><h2><span class="ico">{icon}</span>{esc(title)}'
            f'<span class="who">{esc(who)}</span></h2>'
            f'<p class="asks">这栏看什么：{esc(ask)}</p>{body}</section>')


# ============================== 主流程 ==============================

def build(args):
    game = args.game
    cache_dir = Path(args.cache_dir) if args.cache_dir else HERE / CACHE_DIRNAME
    token = args.token or os.environ.get("POECURRENCY_TOKEN") or T.load_api_token()
    items, volumes, meta = load_day(game, token, cache_dir, refresh=args.refresh)
    hist = load_history(game, cache_dir, args.days, meta.get("day"))
    hist_days = sorted(hist)
    hlabel = f"{len(hist_days)}日" if hist_days else None

    anchors = T.compute_anchors(game, items, T.PRICE_FIELDS)
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
            "key": key, "cn": it.get("item_name") or key, "en": it.get("engname") or key,
            "cat": it.get("_cat") or "其他", "it": it,
            "p": val(it), "s": val(it, "sell_avg"), "v": volc(it),
            "r24": it.get("buy_avg_ratio"), "rh": None,
        })

    # 多日变化（有几天算几天）
    if hist_days:
        for r in rows:
            if not r["p"]:
                continue
            for day in hist_days:  # 从最早一天起找，缺就换下一天
                hv = hist_price(game, hist[day], r["key"], anchors)
                if hv:
                    r["rh"] = (r["p"] / hv - 1) * 100
                    break

    def move_of(r):
        """优先长线，其次 24h。"""
        return r["rh"] if r["rh"] is not None else r["r24"]

    def spread(r):
        return (r["p"] / r["s"]) if (r["p"] and r["s"]) else None

    # ---------- 策略清单：按玩法类目 ----------
    cats = defaultdict(list)
    for r in rows:
        cats[r["cat"]].append(r)
    strat = []
    for c, rs0 in cats.items():
        rs = [r for r in rs0 if r["p"]]
        if not rs:
            continue
        strat.append({"cat": c, "n": len(rs),
                      "tv": sum((r["v"] or 0) for r in rs),
                      "top": sorted(rs, key=lambda r: -r["p"])[:5]})
    strat.sort(key=lambda x: -x["tv"])

    # ---------- 高价值资产（稳定高价 / 奢侈品） ----------
    lux_min = LUX_MIN_DIVINE * cpd
    lux = []
    for r in rows:
        if not (r["p"] and r["v"]) or r["p"] < lux_min:
            continue
        mv = move_of(r)
        steady = 1.0 / (1.0 + abs(mv) / 20.0) if mv is not None else 0.5
        lux.append({**r, "score": r["p"] * steady})
    lux.sort(key=lambda r: -r["score"])
    lux = lux[:10]

    # ---------- 长线下跌 ----------
    decline = sorted([r for r in rows if r["rh"] is not None and r["rh"] <= DECLINE_PCT and r["p"]],
                     key=lambda r: r["rh"])[:8]

    # ---------- 碎片合成（价值下限参考） ----------
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
        arb.append({"cn": ci.get("item_name") or child, "own": cv, "der": pv / rule["count"]})

    # ---------- 国际服 ----------
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
            intl_lo, intl_hi = (sorted(both, key=lambda r: r["iratio"])[:6],
                                sorted(both, key=lambda r: -r["iratio"])[:6])
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 国际服对比跳过：{e}")

    # ============ 组装 ============
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = next((it.get("latest_datetime") for it in items.values() if it.get("latest_datetime")), "-")
    src_note = f"行情时间 {esc(str(stamp))}"
    if meta.get("cached"):
        src_note += "（本地缓存）"
    if meta.get("stale"):
        src_note += " ⚠️ 在线失败，已回退缓存"
    net_note = (f"长线列为 {hlabel}" if hlabel else "长线列待积累（本地缓存仅今天，需每日运行）")

    def item_table(rs, n=6):
        heads = ["标的", "价", hlabel or "24h", "求购资金"]
        body = [[f'<b>{esc(r["cn"])}</b>', fnum(r["p"]), delta(move_of(r)),
                 fnum(r["v"]) if r["v"] else '<span class="down">薄</span>'] for r in rs[:n]]
        return table(heads, body)

    kpis = [
        kpi("1 神圣石", f"{cpd:,.2f} C", "计价基准"),
        kpi("1 崇高石", f"{anchors['base_in_chaos']:,.4f} C", "小额单位"),
        kpi("本地缓存", f"{len(hist_days) + 1} 天", net_note),
    ]

    # 摘要（只说最值得注意的）
    spot = []
    if strat:
        b = max((s["top"][0] for s in strat if s["top"]), key=lambda r: r["p"] or 0)
        spot.append(f'单件最贵：<b>{esc(b["cn"])}</b> {fnum(b["p"])}')
        spot.append(f'资金最厚：<b>{esc(strat[0]["cat"])}</b> 求购体量 {fnum(strat[0]["tv"])}'
                    f'，头部 {esc(strat[0]["top"][0]["cn"])} {fnum(strat[0]["top"][0]["p"])}')
    if lux:
        spot.append(f'高价值里最稳：<b>{esc(lux[0]["cn"])}</b> {fnum(lux[0]["p"])}')
    if decline:
        spot.append(f'长线跌幅最大：<b>{esc(decline[0]["cn"])}</b> {decline[0]["rh"]:.1f}%')
    else:
        spot.append('长线涨跌待积累：本地缓存仅 1 天，多日列需每日运行几天后才有意义')
    if arb:
        a = max(arb, key=lambda x: (x["der"] / x["own"]) if x["own"] else 99)
        spot.append(f'碎片合成参考：<b>{esc(a["cn"])}</b> 自身 {fnum(a["own"])} ／ 合成价 {fnum(a["der"])}')

    # 策略卡片
    cards = []
    for s in strat:
        cards.append(panel(f'{s["cat"]}', f'{s["n"]} 项 ｜ 体量 {fnum(s["tv"])}',
                           item_table(s["top"], 5)))
    strategy_html = f'<div class="grid3">{"".join(cards)}</div>'

    lux_html = item_table(lux, 10) if lux else '<p class="hint">本期没有满足条件的标的</p>'
    dec_html = (item_table(decline, 8) if decline else
                '<p class="hint">需要多日数据（当前本地缓存仅 1 天）。短周期波动不构成下跌结论，'
                '请先每日运行积累缓存。</p>')

    intl_html = ""
    if intl_lo or intl_hi:
        io = [[f'<b>{esc(r["cn"])}</b>', fnum(r["p"]), fnum(r["i"]), f'{r["iratio"]:.2f}×']
              for r in intl_lo]
        ih = [[f'<b>{esc(r["cn"])}</b>', fnum(r["p"]), fnum(r["i"]), f'{r["iratio"]:.2f}×']
              for r in intl_hi]
        intl_html = (f'<div class="grid">'
                     f'{panel("国服更便宜", "比值 < 1", table(["标的", "国服", "国际服", "比值"], io))}'
                     f'{panel("国服更贵", "比值 > 1", table(["标的", "国服", "国际服", "比值"], ih))}'
                     f'</div>')

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>POE2 国服市场日报 {now}</title><style>{CSS}</style></head><body>
<h1>POE2 国服市场日报</h1>
<div class="stamp">生成于 {now} ｜ {src_note} ｜ 数据源 poecurrency.top · poe.ninja（{esc(league or '国际服')}）</div>

<div class="kpis">{"".join(kpis)}</div>

<section><h2><span class="ico">📌</span>行情摘要</h2>
<ul class="bullets">{"".join(f"<li>{x}</li>" for x in spot)}</ul></section>

{section("📋", "策略清单（按玩法）", "Farm",
         "哪类玩法资金量大（值得投入）、每类最值钱的产出是什么。按市场体量排序",
         strategy_html)}

{section("💎", "高价值资产", "Wealth",
         f"价格 ≥ {LUX_MIN_DIVINE:.0f}D 且相对稳定（含镜子 / 发辫等奢侈品）。适合长期持有或储值",
         lux_html)}

{section("📉", "长线下跌", "Avoid",
         f"多日跌幅 ≥ {abs(DECLINE_PCT):.0f}% 的标的：不要囤、不要在下跌途中接货",
         dec_html)}

{section("🌍", "国服与国际服", "Reference",
         "两边价差（国服价 ÷ 国际服价）。仅供交叉参考，不参与过滤器档位判定",
         intl_html) if intl_html else ""}

<footer>
口径：<code>buy_avg</code> = 你要买入的价（游戏内「出售挂单」侧）；<code>sell_avg</code> = 你能卖到的价（「求购单」侧）；
<code>sell*_vol</code> = 求购侧挂单的货币总量。变化列优先用多日（长线），无多日数据时退回 24h。<br>
数据：<code>cache/{game}/market-YYYY-MM-DD.json</code> ｜ 产物：<code>reports/</code> ｜
生成：<code>python3 poe2-market-report.py</code>
</footer></body></html>"""
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description="POE2 国服市场日报（v5）")
    ap.add_argument("-o", "--output", default=None, help="指定输出 HTML 路径")
    ap.add_argument("--out-dir", default=None, help=f"输出目录（默认 {OUT_DIRNAME}/）")
    ap.add_argument("--cache-dir", default=None, help=f"缓存目录（默认 {CACHE_DIRNAME}/）")
    ap.add_argument("--refresh", action="store_true", help="忽略当天缓存，强制重新拉取")
    ap.add_argument("--days", type=int, default=7, help="长线回看窗口天数（默认 7）")
    ap.add_argument("--game", default="poe2", choices=["poe2", "poe1"])
    ap.add_argument("--no-intl", action="store_true", help="跳过国服/国际服对比")
    ap.add_argument("--token", default=None, help="poecurrency.top token（默认读 config / 环境变量）")
    args = ap.parse_args(argv)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = Path(args.out_dir) if args.out_dir else HERE / OUT_DIRNAME
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{args.game}-market-{datetime.now():%Y-%m-%d}.html"
    out.write_text(build(args), encoding="utf-8")
    print(f"[完成] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
