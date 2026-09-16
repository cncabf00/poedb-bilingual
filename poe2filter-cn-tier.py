#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
poe2filter-cn-tier.py — POE2 过滤器国服通货重分级工具

用国服（poecurrency.top）的通货价格，把 poe2filter.com 导出的国际服过滤器里
所有「通货类」物品（通货/催化剂/合金/符文/精华/预兆/灵核/矿石…）的 tier
（S/A/B/C/D/E/F）按国服物价重新分级。

用法示例：
    # 全部用默认值（路径/输出/档位都默认，自动读同目录 config）
    python3 poe2filter-cn-tier.py

    # 指定输入、输出、档位
    python3 poe2filter-cn-tier.py --filter "D:/My Games/Path of Exile 2/poe2filter.filter" \
                                   --output "D:/poe2filter-cn.filter" \
                                   --level "very strict"

    # 携带 API Token（可选；无 token 时用免费 summary 接口）
    python3 poe2filter-cn-tier.py --token ***

    # 更省事：在脚本同目录放一个 poe2filter-cn-tier-config.py，里面写
    #     API_TOKEN = "***"
    # 脚本会自动 import 读取，就不用每次敲 --token ***

参数（均可选）：
    --filter PATH   过滤器路径。默认：<用户目录>/Documents/My Games/Path of Exile 2/poe2filter.filter
    --output PATH   输出路径。默认：同名文件 + "-cn" 后缀（如 poe2filter-cn.filter）
    --level STR     分级档位：very strict / strict / normal / early-game（默认 very strict）。
    --token ***     poecurrency.top 的 API Token（可选，优先于 config 文件）。
    --price-field   取值字段，默认 buy_avg（可选 buy_avg / sell_avg / latest_buy1 / latest_sell1）。
    --verbose       打印每件物品的详细对照表（默认只打印每段汇总）。

    API Token 读取优先级：命令行 --token *** 同目录 poe2filter-cn-tier-config.py 里的 API_TOKEN > 无（免费接口）。

分级规则（单件价值；单位：C=混沌石 Chaos Orb，D=神圣石 Divine Orb，E=崇高石 Exalted Orb）：
    very strict : S>=10D  A>=3D  B>=1D   C>=2C   D~1C    E<0.5C   F<0.1C
    strict      : S>=3D   A>=1D  B>=2C   C~1C   D<0.5C  E<0.1C   F<0.05C
    normal      : S>=100E A>=15E B>=3E   C~1E   D<0.5E  E<0.1E   F<0.01E
    early-game  : S>=20E  A>=2.5E B~1E   C<0.75E D<0.2E  E<0.1E   F<0.001E
    （"~" 表示中间档，落在上下两档之间；各档具体下界见脚本内 LEVELS 字典）
"""

import argparse
import importlib.util
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ============================== 配置 ==============================

API_BASE = "https://poecurrency.top"

# 分级档位。每个 tier 的价值下界（自上而下匹配），单位三种：
#   "divine" = 神圣石(D)、"chaos" = 混沌石(C)、"exalted" = 崇高石(E)。
# 每个档的阈值保持「原生单位」，不把 D 档换算成 C/E 再去比。
# 注："~1C" 这类中间档，其下界取下一档的上界（如 [0.5C, 2C)），即无缝隙分区。
LEVELS = {
    "very strict": {
        "S": ("divine", 10),
        "A": ("divine", 3),
        "B": ("divine", 1),
        "C": ("chaos", 2),
        "D": ("chaos", 0.5),
        "E": ("chaos", 0.1),
        "F": ("chaos", 0.0),
    },
    "strict": {
        "S": ("divine", 3),
        "A": ("divine", 1),
        "B": ("chaos", 2),
        "C": ("chaos", 0.5),
        "D": ("chaos", 0.1),
        "E": ("chaos", 0.05),
        "F": ("chaos", 0.0),
    },
    "normal": {
        "S": ("exalted", 100),
        "A": ("exalted", 15),
        "B": ("exalted", 3),
        "C": ("exalted", 0.5),
        "D": ("exalted", 0.1),
        "E": ("exalted", 0.01),
        "F": ("exalted", 0.0),
    },
    "early-game": {
        "S": ("exalted", 20),
        "A": ("exalted", 2.5),
        "B": ("exalted", 0.75),
        "C": ("exalted", 0.2),
        "D": ("exalted", 0.1),
        "E": ("exalted", 0.001),
        "F": ("exalted", 0.0),
    },
}

TIER_ORDER = ["S", "A", "B", "C", "D", "E", "F"]  # 从高到低
TIER_RANK = {t: i for i, t in enumerate(TIER_ORDER)}

# 堆叠阈值（从大到小）。poe2filter.com 用这 4 档判断“大额堆叠 → 升档”。
# 仅对 Class == "Stackable Currency" 的段生效（符文/预兆等不可堆叠）。
STACK_CHECKPOINTS = [3, 5, 10, 20]

# 取值字段的兜底顺序（买1均价 → 卖均价 → 最新买1 → 最新卖1）。
PRICE_FIELDS = ["buy_avg", "sell_avg", "latest_buy1", "latest_sell1"]

# 默认过滤器路径（Windows 文档目录）。
DEFAULT_FILTER = (
    Path.home() / "Documents" / "My Games" / "Path of Exile 2" / "poe2filter.filter"
)

# 本地配置文件（与脚本同目录，可选）：里面定义 API_TOKEN = "***"，脚本会自动 import 读取。
# 该文件含密钥，不要提交到公开仓库。
CONFIG_FILE = "poe2filter-cn-tier-config.py"

# 锚点通货（用于把 e/d 计价统一折算）。
CHAOS_NAME = "Chaos Orb"    # C = 混沌石
DIVINE_NAME = "Divine Orb"  # D = 神圣石
EXALTED_NAME = "Exalted Orb"  # e 计价基准（崇高石）

# 要处理的区域：从 "Tiered Currency Rules" 之后，到 "Bottom Free-text Rules" 之前。
# 之前的 Uniques/Gear/Jewellery 等装备段、以及 "Currency Rules"（Gold 规则）都不处理。
AREA_START = "Tiered Currency Rules"
AREA_END = ("Bottom Free-text Rules", "Filter Configuration")


# ============================== 数据获取 ==============================

def load_api_token():
    """默认尝试从脚本同目录 import poe2filter-cn-tier-config.py，读取其中的 API_TOKEN。

    存在且是有效 token 就返回；否则返回 None（走免费 summary 接口）。
    整个 import 用 try 包住，文件缺失 / 语法错 / 无 API_TOKEN 都不报错。
    """
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
        token = getattr(mod, "API_TOKEN", None)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 读取 {CONFIG_FILE} 失败：{e}")
        return None
    if isinstance(token, str) and token.strip() and not token.strip().lower().startswith("xxxxx"):
        return token.strip()
    return None


def http_get_json(url, timeout=30):
    """GET 一个 JSON 接口，返回解析后的对象。"""
    req = urllib.request.Request(url, headers={"User-Agent": "poe2filter-cn-tier/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def fetch_prices(token=None):
    """拉取国服 POE2 通货价格。返回 {engname: item, ...}。

    token 为空时用 /api/summary（免费、1 小时缓存、无需鉴权）；
    有 token 时优先用 /api/summary_validate（带异常点剔除），401 则回退 summary。
    """
    summary_url = f"{API_BASE}/api/summary?version=2"

    if token:
        validate_url = f"{API_BASE}/api/summary_validate?version=2&token={token}"
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


def _index_items(data):
    """把 summary 的嵌套结构拍平成 {engname: item}。"""
    index = {}
    for group in data:
        for item in group.get("items", []):
            eng = item.get("engname")
            if eng:
                index[eng] = item
    return index


# ============================== 价值折算 ==============================

def first_nonzero(item, fields):
    for f in fields:
        v = item.get(f)
        if isinstance(v, (int, float)) and v > 0:
            return v
    return None


def compute_anchors(prices, price_fields):
    """计算归一化锚点：1 混沌 = ? e，1 神圣 = ? e。返回 (chaos_in_e, divine_in_e)。"""
    chaos_item = prices.get(CHAOS_NAME)
    divine_item = prices.get(DIVINE_NAME)
    if not chaos_item or not divine_item:
        raise RuntimeError(
            f"API 里找不到锚点通货（{CHAOS_NAME} / {DIVINE_NAME}），无法归一化。"
        )

    chaos_e = first_nonzero(chaos_item, price_fields)
    divine_e = first_nonzero(divine_item, price_fields)
    if not chaos_e or not divine_e:
        raise RuntimeError(
            f"锚点通货价格为 0（Chaos={chaos_e}, Divine={divine_e}），无法归一化。"
        )
    return chaos_e, divine_e


def value_in_e(item, divine_in_e, price_fields):
    """计算某通货的单件价值（统一折到 e=崇高石 计价）。返回 float 或 None（无法判定）。

    这一步只是「算价值」，把 d/e 计价统一折到一个中间基准 e；
    分级标准本身不在这里换算，见 tier_of()。
    """
    price = first_nonzero(item, price_fields)
    unit = item.get("currency_unit")

    if price is not None:
        if unit == "d":
            return price * divine_in_e
        if unit == "e":
            return price
        return None  # 未知计价单位

    # 价格为 0：特殊处理“崇高石”这个 e 计价基准（其价值 = 1e）
    if item.get("engname") == EXALTED_NAME:
        return 1.0
    return None


# ============================== 分级 ==============================

def tier_of(values, level):
    """按价值分级。values: {"divine": float, "chaos": float, "exalted": float}

    分级标准保持「原生单位」：各档用各自的计价单位（divine/chaos/exalted）直接比较，
    不把标准换算成别的单位再去比——因为单位间的汇率是浮动的，而标准应该钉死在原单位上。
    """
    thresholds = LEVELS[level]
    for tier in TIER_ORDER:
        unit, thresh = thresholds[tier]
        if values[unit] >= thresh:
            return tier
    return "F"  # 理论上到不了这里


def compute_assignment(values, level, stackable=True):
    """给定单件价值（含 divine/chaos/exalted 三个量纲），返回 (base_tier, [(N, target), ...])。

    堆叠规则：poe2filter.com 会把“大额堆叠”视为总价值更高，从而升档。
    这里按 N∈{3,5,10,20} 检查 N×单件价值 是否跨入更高 tier。
    仅 stackable（可堆叠通货）才计算堆叠升档。
    """
    base = tier_of(values, level)
    promotions = []
    if stackable:
        prev = base
        for n in sorted(STACK_CHECKPOINTS):  # 3,5,10,20 升序
            n_values = {u: v * n for u, v in values.items()}
            t = tier_of(n_values, level)
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


# ============================== 主流程 ==============================

def resolve_paths(args):
    filter_path = Path(args.filter).expanduser()
    if args.output:
        output_path = Path(args.output).expanduser()
    else:
        output_path = filter_path.with_name(filter_path.stem + "-cn" + filter_path.suffix)
    return filter_path, output_path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="POE2 过滤器国服通货重分级工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--filter", default=str(DEFAULT_FILTER), help="过滤器路径")
    parser.add_argument("--output", default=None, help="输出路径（默认同名 + -cn 后缀）")
    parser.add_argument("--level", default="very strict", help="分级档位（very strict / strict / normal / early-game）")
    parser.add_argument("--token", default=None, help="poecurrency.top API Token（可选，优先于 config 文件）")
    parser.add_argument(
        "--price-field",
        default="buy_avg",
        choices=PRICE_FIELDS,
        help="取值字段（默认 buy_avg）",
    )
    parser.add_argument("--verbose", action="store_true", help="打印每件物品的详细对照表")
    args = parser.parse_args(argv)

    if args.level not in LEVELS:
        sys.exit(f"[错误] 档位 '{args.level}' 尚未支持。当前支持: {list(LEVELS.keys())}")

    # 取值字段顺序：用户选的字段排最前，其余兜底
    price_fields = [args.price_field] + [f for f in PRICE_FIELDS if f != args.price_field]

    # 1. 读过滤器
    filter_path, output_path = resolve_paths(args)
    if not filter_path.exists():
        sys.exit(f"[错误] 找不到过滤器文件：{filter_path}")
    lines = filter_path.read_text(encoding="utf-8").split("\n")

    # 2. 定位要处理的段
    sections = locate_sections(lines)
    if not sections:
        sys.exit(f"[错误] 未找到 '{AREA_START}' 区域，过滤器结构可能不匹配")

    # 3. 拉取价格并计算锚点
    token = args.token if args.token else load_api_token()
    prices = fetch_prices(token)
    chaos_in_e, divine_in_e = compute_anchors(prices, price_fields)
    print(
        f"[锚点] 1 混沌 ≈ {chaos_in_e} e, 1 神圣 ≈ {divine_in_e} e "
        f"(1 神圣 ≈ {divine_in_e / chaos_in_e:.2f} 混沌)"
    )

    # 4. 解析所有段（收集全局 tier 展示样式 + 各段分组）
    parsed = []  # (title, start, end, groups)
    global_tier_display = {}
    for title, start, end in sections:
        groups, tier_display = parse_section(lines[start + 1 : end])
        if not groups:
            continue
        # 跳过完全查不到的段（0% 覆盖，如 Uncut Gems/Tablets/Vault Keys 等），保持原样
        has_match = any(bt in prices for grp in groups for bt in grp["currencies"])
        if not has_match:
            continue
        for tier, meta in tier_display.items():
            global_tier_display.setdefault(tier, meta)
        parsed.append((title, start, end, groups))

    # 5. 对每个通货重新分级（全局价格查询）
    new_assignments = {}  # {currency_name: {"base", "promotions"}}
    total_items = 0
    total_changed = 0
    section_summary = []  # (title, n_items, n_changed)
    detail_rows = []  # (section, name, old, new, value_chaos, proms)

    for title, start, end, groups in parsed:
        n_items = 0
        n_changed = 0
        for grp in groups:
            stackable = grp["class"] == "Stackable Currency"
            for bt, old_rec in grp["currencies"].items():
                n_items += 1
                item = prices.get(bt)
                if item is None:
                    # API 查不到：保留原分级
                    new_assignments[bt] = {
                        "base": old_rec["base"],
                        "promotions": list(old_rec["promotions"]),
                    }
                    detail_rows.append((title, bt, old_rec["base"], old_rec["base"], None, "查不到，保留"))
                    continue

                ve = value_in_e(item, divine_in_e, price_fields)
                if ve is None:
                    new_assignments[bt] = {
                        "base": old_rec["base"],
                        "promotions": list(old_rec["promotions"]),
                    }
                    detail_rows.append((title, bt, old_rec["base"], old_rec["base"], None, "无价格，保留"))
                    continue

                values = {
                    "divine": ve / divine_in_e,
                    "chaos": ve / chaos_in_e,
                    "exalted": ve,  # 1e = 1 崇高石
                }
                base, promotions = compute_assignment(
                    values, args.level, stackable=stackable
                )
                new_assignments[bt] = {"base": base, "promotions": promotions}

                if base != old_rec["base"]:
                    n_changed += 1
                proms_str = ", ".join(f"{n}+→{t}" for n, t in promotions) or "-"
                detail_rows.append(
                    (title, bt, old_rec["base"], base, round(values["chaos"], 3), proms_str)
                )

        total_items += n_items
        total_changed += n_changed
        section_summary.append((title, n_items, n_changed))

    # 6. 打印汇总
    print("\n=== 各段重新分级汇总（旧 tier → 新 tier）===")
    print(f"{'段':<24}{'物品数':>6}{'变动数':>8}")
    for title, n_items, n_changed in section_summary:
        print(f"{title:<24}{n_items:>6}{n_changed:>8}")

    if args.verbose:
        print("\n=== 详细对照表 ===")
        print(f"{'段':<22}{'通货':<34}{'旧':<5}{'新':<5}{'价值(混沌)':<12}堆叠升档")
        for title, name, old_tier, new_tier, vc, proms in detail_rows:
            mark = "" if old_tier == new_tier else " *"
            vc_s = str(vc) if vc is not None else "-"
            print(f"{title:<22}{name:<34}{old_tier or '-':<5}{new_tier:<5}{vc_s:<12}{proms}{mark}")

    print(f"\n[统计] 共 {total_items} 个通货类物品，其中 {total_changed} 个 tier 发生变化，"
          f"{total_items - total_changed} 个不变")

    # 7. 重新生成并写回
    new_lines = []
    cursor = 0
    for title, start, end, groups in parsed:
        # 保留到段标题之前（含标题上方的分隔线）
        new_lines.extend(lines[cursor:start])
        new_lines.append(f"### {title}")
        new_lines.append("#######################################################")
        new_lines.append("")
        new_section = regenerate_section(groups, global_tier_display, new_assignments)
        new_lines.extend(new_section)
        cursor = end - 1  # 下一段的开头分隔线留给下一轮
    new_lines.extend(lines[cursor:])

    output_path.write_text("\n".join(new_lines), encoding="utf-8")
    print(f"\n[完成] 输出：{output_path}")


if __name__ == "__main__":
    main()
