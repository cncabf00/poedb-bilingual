#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
poe2filter-cn-tier.py — POE 过滤器国服通货重分级工具（支持 POE1 / POE2）

把国际服过滤器（poe2filter.com 或 filterblade/NeverSink）里的通货类物品，
按国服物价重新分档，并删掉国服查不到的道具（防过滤器导入国服后加载失败）。

核心思路（相对判定）：
  1. 用国际服价（poe.ninja）算出原过滤器每档的上下限（档内最贵/最便宜道具）
  2. 相邻两档分界点 = (上一档下界 + 下一档上界) / 2
  3. 用国服价（poecurrency.top）相对分界点重新落档
  4. C/D/E 三个标志通货（混沌/神圣/崇高石）钉在原档位不动
  5. 国服查不到的道具删除（国际服比国服多出的物品）；白名单（KEEP_WHITELIST）里的基础通货保留原档

用法示例：
    # 默认：同时处理 POE1 + POE2 默认目录（自动扫 .filter，排除 [CN]，自动识别格式）
    python3 poe2filter-cn-tier.py

    # 只处理某一代
    python3 poe2filter-cn-tier.py --game poe1

    # 指定目录（测试用）
    python3 poe2filter-cn-tier.py --poe1-dir "D:/p1" --poe2-dir "D:/p2"

    # 单个文件（自动识别格式）
    python3 poe2filter-cn-tier.py --filter "D:/xxx.filter"

    # 携带 API Token（可选；无 token 用免费 summary 接口）
    python3 poe2filter-cn-tier.py --token ***

    # 更省事：同目录放 poe2filter-cn-tier-config.py，里面写 API_TOKEN = "***"

参数（均可选）：
    --game STR      处理哪一代：both（默认）/ poe1 / poe2
    --poe1-dir PATH POE1 过滤器目录（默认 Documents/My Games/Path of Exile）
    --poe2-dir PATH POE2 过滤器目录（默认 Documents/My Games/Path of Exile 2）
    --filter PATH   单个过滤器文件（可选，指定后只处理这一个）
    --output PATH   单文件模式的输出路径（默认加 [CN] 前缀）
    --format STR    过滤器格式：auto（默认自动识别）/ poe2filter / filterblade
    --token ***     poecurrency.top 的 API Token（可选，优先于 config 文件）
    --price-field   取值字段，默认 buy_avg
    --verbose       打印每件物品的详细对照表

    说明：POE1 只支持 filterblade（NeverSink）；poe2filter.com 无一代版本。
    API Token 优先级：--token *** > 同目录 config 的 API_TOKEN > 无（免费接口）
"""

import argparse
import importlib.util
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import poe_ninja

# ============================== 配置 ==============================

API_BASE = "https://poecurrency.top"

# 分级方式改为「相对判定」：不再用固定阈值（原来的 LEVELS 四套绝对值已废弃）。
# 改为根据原过滤器里每档实际道具的「国际服价」算每档上下限，
# 相邻两档平均值做分界点，最后用「国服价」相对分界点落档。
# 详见 compute_boundaries / assign_currency。

TIER_ORDER = ["S", "A", "B", "C", "D", "E", "F"]  # 从高到低
TIER_RANK = {t: i for i, t in enumerate(TIER_ORDER)}

# 堆叠阈值（从大到小）。poe2filter.com 用这 4 档判断“大额堆叠 → 升档”。
# 仅对 Class == "Stackable Currency" 的段生效（符文/预兆等不可堆叠）。
STACK_CHECKPOINTS = [3, 5, 10, 20]

# 取值字段的兜底顺序（买1均价 → 卖均价 → 最新买1 → 最新卖1）。
PRICE_FIELDS = ["buy_avg", "sell_avg", "latest_buy1", "latest_sell1"]

# 默认过滤器目录（Windows 文档目录）。POE1 与 POE2 各自一个。
DEFAULT_DIRS = {
    "poe1": Path.home() / "Documents" / "My Games" / "Path of Exile",
    "poe2": Path.home() / "Documents" / "My Games" / "Path of Exile 2",
}

# 本地配置文件（与脚本同目录，可选）：里面定义 API_TOKEN = "***"，脚本会自动 import 读取。
# 该文件含密钥，不要提交到公开仓库。
CONFIG_FILE = "poe2filter-cn-tier-config.py"

# 锚点通货（用于把 e/d 计价统一折算）。
CHAOS_NAME = "Chaos Orb"    # C = 混沌石
DIVINE_NAME = "Divine Orb"  # D = 神圣石
EXALTED_NAME = "Exalted Orb"  # e 计价基准（崇高石）

# 标志通货（锚定，钉在原档位，且用于推算各档价格下限）
PERFECT_EXALTED_NAME = "Perfect Exalted Orb"
ANCHOR_NAMES = {CHAOS_NAME, DIVINE_NAME, EXALTED_NAME, PERFECT_EXALTED_NAME}
ANCHOR_DISCOUNT = 0.7   # 档位下限 = 标志通货价 × 0.7（下浮 30%）
TIER_STEP = 3.0          # 无标志通货的档位：相邻档 ×3 / ÷3

# 末尾附「标志通货兑换比例」的名称（C/D/E 全变种 + 发辫 + 镜子）
BENCHMARK_NAMES = [
    "Chaos Orb", "Greater Chaos Orb", "Perfect Chaos Orb",
    "Divine Orb",
    "Exalted Orb", "Greater Exalted Orb", "Perfect Exalted Orb",
    "Hinekora's Lock", "Mirror of Kalandra",
]

# 要处理的区域：从 "Tiered Currency Rules" 之后，到 "Bottom Free-text Rules" 之前。
# 之前的 Uniques/Gear/Jewellery 等装备段、以及 "Currency Rules"（Gold 规则）都不处理。
AREA_START = "Tiered Currency Rules"
AREA_END = ("Bottom Free-text Rules", "Filter Configuration")


# ============================== 数据获取 ==============================

def _load_config_module():
    """尝试 import 同目录的 config 文件，返回模块对象；失败返回 None。"""
    script_dir = Path(__file__).resolve().parent
    config_path = script_dir / CONFIG_FILE
    if not config_path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "_poe2filter_cn_tier_config", str(config_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 读取 {CONFIG_FILE} 失败：{e}")
        return None


def load_api_token():
    """从 config 文件读取 API_TOKEN。有效则返回 token，否则 None。"""
    mod = _load_config_module()
    if mod is None:
        return None
    token = getattr(mod, "API_TOKEN", None)
    if isinstance(token, str) and token.strip() and not token.strip().lower().startswith("xxxxx"):
        return token.strip()
    return None


def load_remove_items():
    """从 config 文件读取 REMOVE_ITEMS（确需删除的道具集合，默认空）。

    这些是「国服确实没有」的道具（国际服多出、国服未更新），
    过滤器里出现会导致导入国服加载失败，需要删除。默认空 = 不删除任何道具。
    """
    mod = _load_config_module()
    if mod is None:
        return set()
    items = getattr(mod, "REMOVE_ITEMS", None)
    if isinstance(items, (set, list, tuple)):
        return {str(x) for x in items if x}
    return set()


def http_get_json(url, timeout=30):
    """GET 一个 JSON 接口，返回解析后的对象。"""
    req = urllib.request.Request(url, headers={"User-Agent": "poe2filter-cn-tier/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def fetch_prices(game, token=None):
    """拉取国服 POE 通货价格。返回 {engname: item, ...}。

    game: "poe1"（version=1）或 "poe2"（version=2）。
    token 为空时用 /api/summary（免费、1 小时缓存、无需鉴权）；
    有 token 时优先用 /api/summary_validate（带异常点剔除），401 则回退 summary。
    """
    version = "1" if game == "poe1" else "2"
    summary_url = f"{API_BASE}/api/summary?version={version}"

    if token:
        validate_url = f"{API_BASE}/api/summary_validate?version={version}&token={token}"
        try:
            data = http_get_json(validate_url)
            print("[API] 使用 summary_validate（带异常剔除），token 有效")
            return _index_items(data)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("[WARN] token 无效或已过期，回退到免费 summary 接口")
            else:
                print(f"[WARN] summary_validate 请求失败({e.code})，回退到 summary")
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] summary_validate 异常({e})，回退到 summary")

    data = http_get_json(summary_url)
    print("[API] 使用 summary（免费接口）")
    return _index_items(data)


def normalize_name(name):
    """归一化英文名用于匹配（去撇号、统一小写）。

    过滤器/poe.ninja 的英文名带撇号（如 Awakener's Orb），而国服 poecurrency.top
    的 engname 部分不带撇号（如 Awakeners Orb）。归一化后两者可对齐。
    """
    if not name:
        return ""
    return name.replace("'", "").replace("\u2019", "").lower()


def _dispw(s):
    """字符串显示宽度（CJK 算 2 列）。"""
    return sum(2 if ord(c) > 0x2E7F else 1 for c in s)


def _wpad(s, width):
    """按显示宽度左对齐补空格（中文对齐用）。"""
    s = str(s)
    return s + " " * max(0, width - _dispw(s))


def _fmt_num(x):
    """数字格式化（去多余小数，大数千分位）。"""
    if x is None:
        return "-"
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.0f}"
    if ax >= 100:
        return f"{x:.1f}"
    if ax >= 1:
        return f"{x:.2f}"
    if ax >= 0.01:
        return f"{x:.3f}"
    return f"{x:.5f}".rstrip("0").rstrip(".") or "0"


def compute_unit_factors(cn_prices, game, anchors, price_fields):
    """由 C/D/E 锚点算单位换算：1C=?D、1C=?E。"""
    d = cn_value_in_chaos(game, cn_prices.get(normalize_name(DIVINE_NAME)), anchors, price_fields)
    e = cn_value_in_chaos(game, cn_prices.get(normalize_name(EXALTED_NAME)), anchors, price_fields)
    return {"divine_in_chaos": d or 1.0, "exalted_in_chaos": e or 1.0}


def fmt_price(v_c, fac):
    """国服混沌价 → 带单位字符串（C 主 + 最接近的 D/E）。"""
    if v_c is None:
        return "-"
    parts = [f"{_fmt_num(v_c)}C"]
    cand = []
    d = v_c / fac["divine_in_chaos"]
    e = v_c / fac["exalted_in_chaos"]
    if d > 0:
        cand.append((abs(math.log10(d)), f"{_fmt_num(d)}D"))
    if e > 0:
        cand.append((abs(math.log10(e)), f"{_fmt_num(e)}E"))
    if cand:
        cand.sort(key=lambda x: x[0])
        parts.append(f"({cand[0][1]})")
    return "".join(parts)


def _index_items(data):
    """把 summary 的嵌套结构拍平成 {归一化英文名: item}。"""
    index = {}
    for group in data:
        for item in group.get("items", []):
            eng = item.get("engname")
            if eng:
                index[normalize_name(eng)] = item
    return index


# ============================== 国际服物价备份表 ==============================

def intl_backup_path(directory, fmt):
    """国际服物价备份表路径（放在过滤器目录，按格式区分：filterblade / poe2filter）。"""
    return Path(directory) / f"intl-{fmt}.json"


def load_intl_backup(path):
    """读国际服物价备份表。返回 (prices_dict, mtime_float)；不存在/损坏返回 (None, None)。"""
    p = Path(path)
    if not p.exists():
        return None, None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        prices = data.get("prices", {})
        if not isinstance(prices, dict):
            return None, None
        return prices, p.stat().st_mtime
    except Exception:  # noqa: BLE001
        return None, None


def save_intl_backup(path, league, prices):
    """写国际服物价备份表（记录当时的国际服价快照）。"""
    p = Path(path)
    data = {
        "league": league,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "prices": prices,
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================== 价值折算 ==============================

def first_nonzero(item, fields):
    for f in fields:
        v = item.get(f)
        if isinstance(v, (int, float)) and v > 0:
            return v
    return None


def compute_anchors(game, prices, price_fields):
    """计算 game 相关的折算锚点，返回 dict（把国服价折到「混沌」用）。

    POE2 计价单位 e(崇高)/d(神圣)：
        {"divine_in_chaos": divine_e/chaos_e, "base_unit": "e", "base_in_chaos": 1/chaos_e}
    POE1 计价单位 c(混沌)/d(神圣)：
        {"divine_in_chaos": divine_c, "base_unit": "c", "base_in_chaos": 1.0}
    """
    divine_item = prices.get(normalize_name(DIVINE_NAME))
    if not divine_item:
        raise RuntimeError(
            f"API 里找不到锚点通货（{DIVINE_NAME}），无法归一化。"
        )

    if game == "poe1":
        divine_c = first_nonzero(divine_item, price_fields)  # 1 神圣 = ? 混沌
        if not divine_c:
            raise RuntimeError(f"锚点通货价格为 0（Divine={divine_c}），无法归一化。")
        return {"divine_in_chaos": divine_c, "base_unit": "c", "base_in_chaos": 1.0}

    # POE2
    chaos_item = prices.get(normalize_name(CHAOS_NAME))
    if not chaos_item:
        raise RuntimeError(
            f"API 里找不到锚点通货（{CHAOS_NAME}），无法归一化。"
        )
    chaos_e = first_nonzero(chaos_item, price_fields)
    divine_e = first_nonzero(divine_item, price_fields)
    if not chaos_e or not divine_e:
        raise RuntimeError(
            f"锚点通货价格为 0（Chaos={chaos_e}, Divine={divine_e}），无法归一化。"
        )
    return {
        "divine_in_chaos": divine_e / chaos_e,
        "base_unit": "e",
        "base_in_chaos": 1.0 / chaos_e,
    }


def cn_value_in_chaos(game, item, anchors, price_fields):
    """把国服某通货折到「混沌」计价。返回 float 或 None（无法判定）。"""
    if item is None:
        return None
    price = first_nonzero(item, price_fields)
    unit = item.get("currency_unit")

    if price is not None:
        if unit == "d":
            return price * anchors["divine_in_chaos"]
        if unit == anchors["base_unit"]:
            return price * anchors["base_in_chaos"]
        return None  # 未知计价单位

    # 价格为 0 的特殊基准通货（作为 1 个基准单位处理）
    if game == "poe2" and item.get("engname") == EXALTED_NAME:
        return anchors["base_in_chaos"]
    if game == "poe1" and item.get("engname") == CHAOS_NAME:
        return 1.0
    return None


# ============================== 分级（锚定标志通货 + 估算） ==============================

def compute_floors(cn_prices, anchor_tiers, game, anchors, price_fields):
    """按标志通货推算各档价格下限（全局）。返回 {tier: 下限(国服混沌价)}。

    anchor_tiers: {标志通货名: 原档位}（从过滤器里取）
    规则：
      1) 有标志通货的档位：下限 = 标志通货国服价 × ANCHOR_DISCOUNT（下浮 30%）
      2) 无标志通货的档位：在相邻两个有锚档位之间做几何插值（等价于 ×3/÷3 阶梯）
    """
    tier_price = {}
    for name, tier in anchor_tiers.items():
        item = cn_prices.get(normalize_name(name))
        if item is None or tier is None:
            continue
        p = cn_value_in_chaos(game, item, anchors, price_fields)
        if p:
            # 同一档多个标志通货时取最低价（作为该档下限基准）
            tier_price[tier] = min(tier_price.get(tier, p), p)

    floors = {t: p * ANCHOR_DISCOUNT for t, p in tier_price.items()}
    anchored = sorted(floors, key=lambda t: TIER_RANK[t])  # 高→低

    # 相邻两个有锚档位之间几何插值填充
    for i in range(len(anchored) - 1):
        hi, lo = anchored[i], anchored[i + 1]
        gap = TIER_RANK[lo] - TIER_RANK[hi] - 1
        if gap <= 0:
            continue
        ratio = (floors[lo] / floors[hi]) ** (1.0 / (gap + 1))
        for j in range(1, gap + 1):
            t = TIER_ORDER[TIER_RANK[hi] + j]
            floors[t] = floors[hi] * (ratio ** j)

    # 向两端延伸（÷3 更低档 / ×3 更高档）
    if anchored:
        lo_tier = anchored[-1]
        f = floors[lo_tier]
        for t in TIER_ORDER[TIER_RANK[lo_tier] + 1:]:
            f = f / TIER_STEP
            floors[t] = f
        hi_tier = anchored[0]
        f = floors[hi_tier]
        for t in reversed(TIER_ORDER[:TIER_RANK[hi_tier]]):
            f = f * TIER_STEP
            floors[t] = f

    return floors


def tier_of_value(value, floors):
    """按国服价落档：取「下限 <= value」的最高档。"""
    for t in TIER_ORDER:  # 从高到低
        f = floors.get(t)
        if f is not None and value >= f:
            return t
    for t in reversed(TIER_ORDER):  # 低于所有下限 → 最低的有锚档
        if t in floors:
            return t
    return None


def assign_currency(name, cn_value, original_tier, floors, stackable):
    """判定单个通货的新档位 + 堆叠升档。

    规则4（不动 > 上调 > 下调）：按国服价落到对应档；国服价仍在原档区间就保留原档，
    高于原档区间则上调，低于则下调（自然实现）。标志通货钉在原档；查不到价的保留原档。
    """
    if name in ANCHOR_NAMES or cn_value is None or not floors:
        base = original_tier
    else:
        t = tier_of_value(cn_value, floors)
        base = t if t is not None else original_tier

    promotions = []
    if stackable and cn_value is not None and floors:
        prev = base
        for n in sorted(STACK_CHECKPOINTS):  # 3,5,10,20 升序
            t = tier_of_value(n * cn_value, floors)
            if t is None:
                continue
            if TIER_RANK[t] < TIER_RANK[prev]:  # t 比 prev 更高档
                promotions.append((n, t))
                prev = t
    return base, promotions


# ============================== 过滤器解析 ==============================

def locate_sections(lines):
    """定位所有需要处理的段（Tiered Currency Rules 之后、Bottom Free-text Rules 之前）。

    返回 [(section_title, start_idx, end_idx), ...]，end 为下一段标题的起始行。
    """
    # 收集所有 "### " 段标题
    headers = []  # (title, index)
    for i, line in enumerate(lines):
        if line.startswith("### ") and "####" not in line:
            headers.append((line.strip()[4:], i))
    headers.append(("__END__", len(lines)))

    result = []
    in_area = False
    for k in range(len(headers) - 1):
        title, start = headers[k]
        end = headers[k + 1][1]
        if title == AREA_START:
            in_area = True
            continue
        if title in AREA_END:
            break
        if not in_area:
            continue
        result.append((title, start, end))
    return result


def parse_blocks(section_lines):
    """把段内容解析成块列表。每个块: {action, title, body:[...]}。"""
    blocks = []
    i = 0
    n = len(section_lines)
    while i < n:
        line = section_lines[i]
        m = re.match(r"^(Show|Hide) # (.+?) \(currency\)$", line)
        if m:
            action, title = m.group(1), m.group(2)
            body = []
            i += 1
            while i < n and section_lines[i].strip() != "":
                body.append(section_lines[i])
                i += 1
            while i < n and section_lines[i].strip() == "":
                i += 1
            blocks.append({"action": action, "title": title, "body": body})
        else:
            i += 1
    return blocks


def title_info(title):
    """从块标题解析出 (stack_n, tier, block_name)。

    例: "S-Tier Currency" -> (None, 'S', 'Currency')
        "Stacks of 20+ → S-Tier Currency" -> (20, 'S', 'Currency')
    """
    m = re.match(r"Stacks of (\d+)\+ → ([SABCDEF])-Tier (.+)$", title)
    if m:
        return int(m.group(1)), m.group(2), m.group(3)
    m = re.match(r"([SABCDEF])-Tier (.+)$", title)
    if m:
        return None, m.group(1), m.group(2)
    return None, None, None


def parse_section(section_lines):
    """解析一个段，返回 (groups, tier_display_partial)。

    groups: [{name, class, currencies: {name: {base, promotions}}}]
    同一段可能有多个 (name, class) 组（如 Breach 段含 Breach + Breachstones）。
    """
    blocks = parse_blocks(section_lines)
    groups = {}  # (name, class) -> {name, class, currencies}
    tier_display = {}  # tier -> {action, display}

    for b in blocks:
        stack_n, tier, name = title_info(b["title"])
        if tier is None or name is None:
            continue

        class_val = None
        base_types = []
        display = []
        for ln in b["body"]:
            s = ln.strip()
            if s.startswith("Class =="):
                m = re.search(r'"([^"]+)"', ln)
                class_val = m.group(1) if m else None
            elif s.startswith("StackSize"):
                pass
            elif s.startswith("BaseType =="):
                base_types = re.findall(r'"([^"]+)"', ln)
            else:
                display.append(ln)

        key = (name, class_val)
        grp = groups.setdefault(
            key, {"name": name, "class": class_val, "currencies": {}}
        )

        # base 块才有权威展示样式（堆叠块同 tier 复用）
        if stack_n is None:
            tier_display.setdefault(tier, {"action": b["action"], "display": display})

        for bt in base_types:
            rec = grp["currencies"].setdefault(bt, {"base": None, "promotions": []})
            if stack_n is None:
                rec["base"] = tier
            else:
                rec["promotions"].append((stack_n, tier))

    # 排序每个通货的 promotions（按 N 升序）并去重
    for grp in groups.values():
        for rec in grp["currencies"].values():
            rec["promotions"] = sorted(set(rec["promotions"]))

    return list(groups.values()), tier_display


# ============================== 重新生成 ==============================

def render_base_block(tier, block_name, names, class_line, tier_display):
    """渲染 base tier 块。"""
    meta = tier_display.get(tier, {"action": "Show", "display": []})
    action = meta["action"]
    display = meta["display"]
    head = f"{action} # {tier}-Tier {block_name} (currency)"
    out = [head, class_line, f'  BaseType == {" ".join(chr(34) + n + chr(34) for n in names)}']
    out.extend(display)
    return out


def render_stack_block(n, target, block_name, names, class_line, tier_display):
    """渲染 'Stacks of N+ → target' 块。"""
    meta = tier_display.get(target, {"action": "Show", "display": []})
    display = meta["display"]
    head = f"Show # Stacks of {n}+ → {target}-Tier {block_name} (currency)"
    out = [
        head,
        f"  StackSize >= {n}",
        class_line,
        f'  BaseType == {" ".join(chr(34) + n + chr(34) for n in names)}',
    ]
    out.extend(display)
    return out


def regenerate_section(groups, tier_display, new_assignment):
    """按新的分级结果，重排并生成整段内容。

    groups: [{name, class, currencies}]
    new_assignment: {currency_name: {"base": tier, "promotions": [(N, target), ...]}}
    """
    out = []
    for base_tier in TIER_ORDER:
        # 该 tier 下各组的通货（按新的分级结果 new_assignment 分组）
        tier_groups = []  # (grp, {currency: rec})
        for grp in groups:
            sub = {}
            for bt, orig_rec in grp["currencies"].items():
                na = new_assignment.get(bt)
                if na is None:  # 兜底：查不到时保留原分级
                    na = {"base": orig_rec["base"], "promotions": list(orig_rec["promotions"])}
                if na["base"] == base_tier:
                    sub[bt] = na
            if sub:
                tier_groups.append((grp, sub))
        if not tier_groups:
            continue

        # 1) 堆叠升档（仅 Stackable Currency 组）
        proms = []  # (target, n, block_name, [names])
        for grp, sub in tier_groups:
            if grp["class"] != "Stackable Currency":
                continue
            pm = {}
            for bt, rec in sub.items():
                for n, target in rec["promotions"]:
                    pm.setdefault((target, n), []).append(bt)
            for (target, n), names in pm.items():
                proms.append((target, n, grp["name"], names))
        proms.sort(key=lambda x: (TIER_RANK[x[0]], -x[1], x[2]))
        for target, n, name, names in proms:
            class_line = f'  Class == "Stackable Currency"'
            out.extend(
                render_stack_block(n, target, name, sorted(names), class_line, tier_display)
            )
            out.append("")

        # 2) base 块（按组顺序）
        for grp, sub in tier_groups:
            class_line = f'  Class == "{grp["class"]}"'
            names = sorted(sub.keys())
            out.extend(
                render_base_block(base_tier, grp["name"], names, class_line, tier_display)
            )
            out.append("")

    return out


# ============================== filterblade (NeverSink) 解析/生成 ==============================

FILTERBLADE_TIER_MAP = {"s": "S", "a": "A", "b": "B", "c": "C", "d": "D", "e": "E"}
FILTERBLADE_TIER_REVERSE = {v: k for k, v in FILTERBLADE_TIER_MAP.items()}

# 块头：Show # %H8 $type->currency $tier->a !currency_a
FILTERBLADE_HEADER_RE = re.compile(
    r'^(Show|Hide) # (?:%\w+\s+)?\$type->([\w>-]+) \$tier->(\w+)\s+!(\w+)$'
)


def parse_filterblade(lines):
    """解析 filterblade 的通货块，返回 (groups, tier_meta, blocks)。

    groups: [{name: type_marker, class, currencies: {name: {base, promotions}}}]
    tier_meta: {tier: {action, style, identifier, class_line, display}}
    blocks: [{type, tier, basetype_idx, start_idx, end_idx}]（用于原位替换）
    """
    groups = {}  # (type, class) -> {name, class, currencies}
    tier_meta = {}
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip("\r\n")
        m = FILTERBLADE_HEADER_RE.match(line)
        if m:
            action, type_marker, tier_raw, identifier = m.groups()
            tier = FILTERBLADE_TIER_MAP.get(tier_raw)
            start_idx = i
            j = i + 1
            body = []
            while j < n and lines[j].strip() != "":
                body.append(lines[j].rstrip("\r\n"))
                j += 1
            end_idx = j
            class_line = None
            class_val = None
            basetype_idx = None
            base_types = []
            display = []
            for k, ln in enumerate(body):
                s = ln.lstrip("\t")
                if s.startswith("Class =="):
                    class_line = ln
                    cm = re.search(r'"([^"]+)"', s)
                    class_val = cm.group(1) if cm else None
                elif s.startswith("BaseType =="):
                    basetype_idx = start_idx + 1 + k
                    base_types = re.findall(r'"([^"]+)"', s)
                else:
                    display.append(ln)
            style_m = re.search(r'%\w+', line)
            style = style_m.group(0) if style_m else ""
            if tier is not None:
                key = (type_marker, class_val)
                grp = groups.setdefault(
                    key, {"name": type_marker, "class": class_val, "currencies": {}}
                )
                tier_meta.setdefault(tier, {
                    "action": action, "style": style, "identifier": identifier,
                    "class_line": class_line, "display": display,
                })
                blocks.append({
                    "type": type_marker, "tier": tier,
                    "basetype_idx": basetype_idx, "start_idx": start_idx, "end_idx": end_idx,
                })
                for bt in base_types:
                    grp["currencies"].setdefault(bt, {"base": tier, "promotions": []})
            i = j
            while i < n and lines[i].strip() == "":
                i += 1
        else:
            i += 1
    return list(groups.values()), tier_meta, blocks


def regenerate_filterblade(lines, groups, tier_meta, blocks, new_assignments):
    """原位替换 filterblade 通货块的 BaseType（空档则删除整块）。"""
    type_items = {}
    for grp in groups:
        for base_tier in TIER_ORDER:
            names = [bt for bt, rec in grp["currencies"].items()
                     if new_assignments.get(bt, rec)["base"] == base_tier]
            if names:
                type_items[(grp["name"], base_tier)] = sorted(names)

    result = list(lines)
    for b in sorted(blocks, key=lambda x: -x["start_idx"]):  # 从后往前，避免索引偏移
        key = (b["type"], b["tier"])
        names = type_items.get(key)
        if names:
            result[b["basetype_idx"]] = "\tBaseType == " + " ".join(f'"{n}"' for n in names)
        else:
            del result[b["start_idx"]:b["end_idx"]]
    return result


# ============================== 主流程 ==============================

def make_output_path(filter_path, explicit_output):
    """生成输出路径。默认加 [CN] 前缀。"""
    if explicit_output:
        return Path(explicit_output).expanduser()
    p = Path(filter_path).expanduser()
    return p.with_name("[CN]" + p.name)


def re_tier(parsed, cn_prices, intl_prices, game, anchors, price_fields, remove_items, verbose):
    """重新分级（锚定标志通货法），返回 (new_assignments, total_items, total_changed)。

    只删除「黑名单」（配置里 REMOVE_ITEMS）列出的道具（国服确实没有的，防加载失败）；
    其余（含国服查不到价的道具）都保留原档。
    """
    new_assignments = {}
    total_items = 0
    total_changed = 0
    total_removed = 0
    removed_rows = []
    section_summary = []
    detail_rows = []
    remove_norm = {normalize_name(n) for n in remove_items}

    # 标志通货的档位（取 base 块），据此算全局各档价格下限
    anchor_tiers = {}
    for _t, _s, _e, _groups in parsed:
        for grp in _groups:
            for bt, rec in grp["currencies"].items():
                if bt in ANCHOR_NAMES and rec["base"] and bt not in anchor_tiers:
                    anchor_tiers[bt] = rec["base"]
    floors = compute_floors(cn_prices, anchor_tiers, game, anchors, price_fields)
    fac = compute_unit_factors(cn_prices, game, anchors, price_fields)
    if verbose and floors:
        print("[档位下限] " + ", ".join(f"{t}={fmt_price(floors[t], fac)}" for t in TIER_ORDER if t in floors))

    for title, start, end, groups in parsed:
        n_items = 0
        n_changed = 0
        for grp in groups:
            # 1) 删除黑名单（REMOVE_ITEMS）里的道具（国服确实没有的）
            for bt in list(grp["currencies"]):
                if normalize_name(bt) in remove_norm:
                    old_tier = grp["currencies"][bt]["base"]
                    del grp["currencies"][bt]
                    total_removed += 1
                    removed_rows.append((title, bt, old_tier))
            if not grp["currencies"]:
                continue  # 整组删光，跳过

            # 2) 按标志通货下限重新分级
            stackable = grp["class"] == "Stackable Currency"
            for bt, old_rec in grp["currencies"].items():
                n_items += 1
                item = cn_prices.get(normalize_name(bt))
                cn_value = cn_value_in_chaos(game, item, anchors, price_fields)
                base, promotions = assign_currency(
                    bt, cn_value, old_rec["base"], floors, stackable
                )
                new_assignments[bt] = {"base": base, "promotions": promotions}

                if base != old_rec["base"]:
                    n_changed += 1
                proms_str = ", ".join(f"{n}+→{t}" for n, t in promotions) or "-"
                vc = fmt_price(cn_value, fac)
                detail_rows.append((title, bt, old_rec["base"], base, vc, proms_str))

        total_items += n_items
        total_changed += n_changed
        section_summary.append((title, n_items, n_changed))

    # 中文名查询（从国服数据取 item_name）
    def cn_of(en_name):
        it = cn_prices.get(normalize_name(en_name))
        cn = (it.get("item_name") or "") if it else ""
        return cn or "-"

    # 打印删除信息
    if removed_rows:
        print(f"\n[删除] 移除 {total_removed} 个黑名单道具（防加载失败）:")
        for title, bt, old_tier in removed_rows:
            print(f"  - {cn_of(bt)} {bt}（原 {old_tier} 档，段「{title}」）")

    # 变更明细（默认输出：只列 tier 发生变化的道具）
    changed_rows = [r for r in detail_rows if r[2] != r[3]]
    if changed_rows:
        print(f"\n=== 变更明细（{len(changed_rows)} 个 tier 变化） ===")
        print(_wpad("段", 18) + _wpad("中文", 18) + _wpad("英文", 32) + _wpad("旧 → 新", 10) + "价格")
        for title, name, old_tier, new_tier, vc, proms in changed_rows:
            print(_wpad(title, 18) + _wpad(cn_of(name), 18) + _wpad(name, 32)
                  + _wpad(f"{old_tier or '-'} → {new_tier}", 10) + vc)
    else:
        print("\n[变更明细] 无 tier 变化")

    # 打印汇总
    print("\n=== 各段重新分级汇总 ===")
    print(f"{'段':<24}{'物品数':>6}{'变动数':>8}")
    for title, n_items, n_changed in section_summary:
        print(f"{title:<24}{n_items:>6}{n_changed:>8}")

    if verbose:
        print("\n=== 详细对照表 ===")
        print(_wpad("段", 18) + _wpad("中文", 18) + _wpad("英文", 30)
              + _wpad("旧", 4) + _wpad("新", 4) + _wpad("价格", 18) + "堆叠升档")
        for title, name, old_tier, new_tier, vc, proms in detail_rows:
            mark = "" if old_tier == new_tier else " *"
            print(_wpad(title, 18) + _wpad(cn_of(name), 18) + _wpad(name, 30)
                  + _wpad(old_tier or "-", 4) + _wpad(new_tier, 4) + _wpad(vc, 18)
                  + proms + mark)

    print(
        f"[统计] 共 {total_items} 个通货类物品，其中 {total_changed} 个 tier 发生变化，"
        f"{total_items - total_changed} 个不变，删除 {total_removed} 个黑名单道具"
    )

    return new_assignments, total_items, total_changed


def regenerate_poe2filter(lines, parsed, tier_display, new_assignments):
    """重新生成 poe2filter 格式（替换各段）。"""
    new_lines = []
    cursor = 0
    for title, start, end, groups in parsed:
        new_lines.extend(lines[cursor:start])
        new_lines.append(f"### {title}")
        new_lines.append("#######################################################")
        new_lines.append("")
        new_section = regenerate_section(groups, tier_display, new_assignments)
        new_lines.extend(new_section)
        cursor = end - 1  # 下一段的开头分隔线留给下一轮
    new_lines.extend(lines[cursor:])
    return new_lines


def detect_format(lines):
    """自动识别过滤器格式。返回 "poe2filter" / "filterblade" / None（识别失败）。"""
    for line in lines[:300]:
        if "NeverSink" in line or "$type->" in line or "filterblade" in line.lower():
            return "filterblade"
        if "poe2filter.com" in line or "### Currency" in line:
            return "poe2filter"
    return None


def detect_format_file(path, fmt_override):
    """识别单个过滤器文件的格式（只读前 300 行）。"""
    if fmt_override != "auto":
        return fmt_override
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = [f.readline() for _ in range(300)]
    except Exception:  # noqa: BLE001
        return None
    return detect_format(head)


def scan_filters(directory):
    """扫描目录下所有 .filter 文件（排除 [CN] 前缀的，即自己生成的）。"""
    if not directory.is_dir():
        return []
    files = []
    for p in sorted(directory.glob("*.filter")):
        if p.name.startswith("[CN]"):
            continue
        files.append(p)
    return files


def collect_base_names(files):
    """从过滤器文件里提取所有 BaseType 英文名（用于国际服价 slug/baseType 匹配）。"""
    names = set()
    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for line in text.splitlines():
            if "BaseType ==" in line:
                names.update(re.findall(r'"([^"]+)"', line))
    return names


def process_one(filter_path, game, args, cn_prices, intl_prices, anchors, price_fields, remove_items):
    """处理单个过滤器文件（识别格式 → 解析 → 重分级 → 生成 → 写回）。"""
    print(f"\n--- 处理: {filter_path.name} ---")

    # 读文件 + 检测行尾
    raw_bytes = filter_path.read_bytes()
    crlf = b"\r\n" in raw_bytes
    raw = raw_bytes.decode("utf-8")
    lines = [l.rstrip("\r") for l in raw.split("\n")]
    newline = "\r\n" if crlf else "\n"

    # 识别格式
    fmt = args.format
    if fmt == "auto":
        fmt = detect_format(lines)
    if fmt is None:
        print(f"  [跳过] 无法识别格式，不转换")
        return
    if fmt == "poe2filter" and game == "poe1":
        print(f"  [跳过] POE1 不支持 poe2filter 格式")
        return

    # 按格式解析
    if fmt == "poe2filter":
        sections = locate_sections(lines)
        if not sections:
            print(f"  [跳过] 未找到 '{AREA_START}' 区域，结构可能不匹配")
            return
        parsed = []  # (title, start, end, groups)
        tier_display = {}
        for title, start, end in sections:
            groups, td = parse_section(lines[start + 1 : end])
            if not groups:
                continue
            for tier, meta in td.items():
                tier_display.setdefault(tier, meta)
            parsed.append((title, start, end, groups))
    else:
        groups, tier_display, blocks = parse_filterblade(lines)
        parsed = [("filterblade", 0, 0, groups)]

    # 相对判定重新分级（只删除黑名单道具）
    new_assignments, _, _ = re_tier(
        parsed, cn_prices, intl_prices, game, anchors, price_fields, remove_items, args.verbose,
    )

    # 按格式重新生成并写回
    if fmt == "poe2filter":
        new_lines = regenerate_poe2filter(lines, parsed, tier_display, new_assignments)
    else:
        new_lines = regenerate_filterblade(lines, groups, tier_display, blocks, new_assignments)
    output_path = make_output_path(filter_path, args.output if args.filter else None)
    output_path.write_text(newline.join(new_lines), encoding="utf-8")
    print(f"  [完成] 输出：{output_path}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="POE 过滤器国服通货重分级工具（支持 POE1 / POE2）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--game", default="both", choices=["both", "poe1", "poe2"],
                        help="处理哪一代（默认 both 两代都处理）")
    parser.add_argument("--poe1-dir", default=None,
                        help="POE1 过滤器目录（可选，覆盖默认目录）")
    parser.add_argument("--poe2-dir", default=None,
                        help="POE2 过滤器目录（可选，覆盖默认目录）")
    parser.add_argument("--filter", default=None,
                        help="单个过滤器文件（可选，指定后只处理这一个）")
    parser.add_argument("--output", default=None,
                        help="单文件模式的输出路径（可选，默认同名 + -cn 后缀）")
    parser.add_argument("--format", default="auto", choices=["auto", "poe2filter", "filterblade"],
                        help="过滤器格式（默认 auto 自动识别）")
    parser.add_argument("--token", default=None,
                        help="poecurrency.top API Token（可选，优先于 config 文件）")
    parser.add_argument(
        "--price-field",
        default="buy_avg",
        choices=PRICE_FIELDS,
        help="取值字段（默认 buy_avg）",
    )
    parser.add_argument("--verbose", action="store_true", help="打印每件物品的详细对照表")
    args = parser.parse_args(argv)

    # 取值字段顺序：用户选的字段排最前，其余兜底
    price_fields = [args.price_field] + [f for f in PRICE_FIELDS if f != args.price_field]
    token = args.token if args.token else load_api_token()
    remove_items = load_remove_items()

    games = ["poe1", "poe2"] if args.game == "both" else [args.game]
    dir_overrides = {"poe1": args.poe1_dir, "poe2": args.poe2_dir}

    for game in games:
        print(f"\n{'=' * 62}")
        print(f"===== 处理 {game.upper()} =====")
        print('=' * 62)

        # 1. 确定目录 + 文件列表
        if args.filter:
            directory = Path(args.filter).expanduser().parent
            filter_files = [Path(args.filter).expanduser()]
        else:
            d = dir_overrides[game]
            directory = Path(d).expanduser() if d else DEFAULT_DIRS[game]
            filter_files = scan_filters(directory)
        if not filter_files:
            print(f"[提示] {game} 没有找到需要处理的 .filter 文件（目录：{directory if not args.filter else '单文件'}）")
            continue
        print(f"[扫描] 找到 {len(filter_files)} 个待处理文件:")
        for fp in filter_files:
            print(f"  - {fp.name}")

        # 2. 拉国服价（总是最新）
        try:
            cn_prices = fetch_prices(game, token)
            anchors = compute_anchors(game, cn_prices, price_fields)
        except Exception as e:  # noqa: BLE001
            print(f"[错误] {game} 抓取国服价失败：{e}，跳过")
            continue
        print(f"[国服] 1 神圣 ≈ {anchors['divine_in_chaos']:.2f} 混沌")

        # 3. 识别格式 + 按格式分组
        groups = {}  # fmt -> [files]
        for fp in filter_files:
            fmt = detect_format_file(fp, args.format)
            if fmt is None:
                print(f"[跳过] {fp.name} 无法识别格式，不转换")
                continue
            if fmt == "poe2filter" and game == "poe1":
                print(f"[跳过] {fp.name} POE1 不支持 poe2filter 格式")
                continue
            groups.setdefault(fmt, []).append(fp)
        if not groups:
            print(f"[提示] {game} 没有可处理的过滤器")
            continue

        # 4. 判断哪些格式组需要拉最新国际服价（多对一，任意一个新就算）
        need_fetch = set()
        for fmt, files in groups.items():
            backup_path = intl_backup_path(directory, fmt)
            backup_prices, backup_mtime = load_intl_backup(backup_path)
            newest = max(f.stat().st_mtime for f in files)
            if backup_prices is None or newest > backup_mtime:
                need_fetch.add(fmt)

        # 5. 按需拉一次最新国际服价（同一游戏各格式共用同一个价源）
        intl_latest = None
        league = None
        if need_fetch:
            try:
                league = poe_ninja.get_current_league(game)
                all_names = collect_base_names([f for fs in groups.values() for f in fs])
                intl_latest = poe_ninja.fetch_international_prices(game, name_set=all_names)
                print(f"[国际服] 拉到最新 {len(intl_latest)} 个通货价格")
            except Exception as e:  # noqa: BLE001
                print(f"[WARN] 拉取国际服价失败（{e}），需更新的格式组将回退用快照")

        # 6. 确定每组国际服价 + 更新快照
        intl_by_fmt = {}
        for fmt, files in groups.items():
            backup_path = intl_backup_path(directory, fmt)
            backup_prices, _ = load_intl_backup(backup_path)
            if fmt in need_fetch and intl_latest is not None:
                intl_by_fmt[fmt] = intl_latest
                save_intl_backup(backup_path, league, intl_latest)
                print(f"[国际服] {fmt} 快照已更新（{len(intl_latest)} 个价格）")
            elif backup_prices is not None:
                intl_by_fmt[fmt] = backup_prices
                print(f"[国际服] {fmt} 使用快照 {len(backup_prices)} 个价格（过滤器未更新）")
            else:
                print(f"[错误] {fmt} 无国际服价（快照缺失且拉取失败），跳过该组")

        # 7. 逐个文件处理
        for fmt, files in groups.items():
            if fmt not in intl_by_fmt:
                continue
            intl_prices = intl_by_fmt[fmt]
            for fp in files:
                try:
                    process_one(fp, game, args, cn_prices, intl_prices, anchors, price_fields, remove_items)
                except Exception as e:  # noqa: BLE001
                    print(f"  [错误] 处理 {fp.name} 失败：{e}")

        # 8. 标志通货兑换比例（C/D/E 全变种 + 发辫 + 镜子）
        fac = compute_unit_factors(cn_prices, game, anchors, price_fields)
        print(f"\n=== 标志通货兑换比例 [{game.upper()}] ===")
        for name in BENCHMARK_NAMES:
            it = cn_prices.get(normalize_name(name))
            v = cn_value_in_chaos(game, it, anchors, price_fields) if it else None
            cn = (it.get("item_name") or "-") if it else "-"
            print("  " + _wpad(cn, 14) + _wpad(name, 26) + fmt_price(v, fac))


if __name__ == "__main__":
    main()
