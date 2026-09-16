#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
poe2filter-cn-tier.py — POE2 过滤器国服通货重分级工具

用国服（poecurrency.top）的通货价格，把 poe2filter.com 导出的国际服过滤器里
的通货 tier（S/A/B/C/D/E/F）按国服物价重新分级。

用法示例：
    # 全部用默认值（路径/输出/档位都默认）
    python3 poe2filter-cn-tier.py

    # 指定输入、输出、档位
    python3 poe2filter-cn-tier.py --filter "D:/My Games/Path of Exile 2/poe2filter.filter" \
                                   --output "D:/poe2filter-cn.filter" \
                                   --level "very strict"

    # 携带 API Token（可选；无 token 时用免费 summary 接口）
    python3 poe2filter-cn-tier.py --token <你的token>

参数（均可选）：
    --filter PATH   过滤器路径。默认：<用户目录>/Documents/My Games/Path of Exile 2/poe2filter.filter
    --output PATH   输出路径。默认：同名文件 + "-cn" 后缀（如 poe2filter-cn.filter）
    --level STR     分级档位。当前仅支持 "very strict"（默认）。
    --token STR     poecurrency.top 的 API Token（可选）。
    --price-field   取值字段，默认 buy_avg（可选 buy_avg / sell_avg / latest_buy1 / latest_sell1）。

分级规则（very strict，单件价值）：
    S: >= 10 神圣石 (Divine Orb)
    A: >= 3  神圣石
    B: >= 1  神圣石
    C: >= 2  混沌石 (Chaos Orb)
    D: ~ 1   混沌石（落在 [0.5, 2) 混沌区间，即 C 与 E 之间）
    E: >= 0.1 混沌石
    F: <  0.1 混沌石
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ============================== 配置 ==============================

API_BASE = "https://poecurrency.top"

# 分级档位。每个 tier 的价值下界（自上而下匹配），单位 "divine"(神圣石) 或 "chaos"(混沌石)。
# 注意 "D: ~1C" 这里解释为 [0.5C, 2C) 区间（介于 C 的 2C 与 E 的 0.5C 之间），
# 若你想把 D 的下界改成 1C，把下面的 0.5 改成 1.0 即可。
LEVELS = {
    "very strict": {
        "S": ("divine", 10),
        "A": ("divine", 3),
        "B": ("divine", 1),
        "C": ("chaos", 2),
        "D": ("chaos", 0.5),
        "E": ("chaos", 0.1),
        "F": ("chaos", 0.0),  # 兜底：低于 E 下界
    },
}

TIER_ORDER = ["S", "A", "B", "C", "D", "E", "F"]  # 从高到低
TIER_RANK = {t: i for i, t in enumerate(TIER_ORDER)}

# 堆叠阈值（从大到小）。poe2filter.com 用这 4 档判断“大额堆叠 → 升档”。
STACK_CHECKPOINTS = [3, 5, 10, 20]

# 取值字段的兜底顺序（买1均价 → 卖均价 → 最新买1 → 最新卖1）。
PRICE_FIELDS = ["buy_avg", "sell_avg", "latest_buy1", "latest_sell1"]

# 默认过滤器路径（Windows 文档目录）。
DEFAULT_FILTER = (
    Path.home() / "Documents" / "My Games" / "Path of Exile 2" / "poe2filter.filter"
)

# 锚点通货（用于把 e/d 计价统一折算成“混沌”）。
CHAOS_NAME = "Chaos Orb"    # C = 混沌石
DIVINE_NAME = "Divine Orb"  # D = 神圣石
EXALTED_NAME = "Exalted Orb"  # e 计价基准（崇高石）


# ============================== 数据获取 ==============================

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
            print(f"[API] 使用 summary_validate（带异常剔除），token 有效")
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

def tier_of(value_divine, value_chaos, level):
    """按价值分级。

    分级标准保持「原生单位」：S/A/B 档直接用神圣石(D)衡量，C/D/E/F 档直接用混沌石(C)衡量。
    不把「B = 1 神圣」这个标准换算成混沌再去比——因为 1 神圣值多少混沌是随汇率浮动的，
    而标准应该钉死在「1 神圣」上不动。
    """
    thresholds = LEVELS[level]
    for tier in TIER_ORDER:
        unit, thresh = thresholds[tier]
        if unit == "divine":
            if value_divine >= thresh:
                return tier
        else:
            if value_chaos >= thresh:
                return tier
    return "F"  # 理论上到不了这里


def compute_assignment(value_divine, value_chaos, level):
    """给定单件价值（同时有神圣/混沌两个量纲），返回 (base_tier, [(N, target_tier), ...])。

    堆叠规则：poe2filter.com 会把“大额堆叠”视为总价值更高，从而升档。
    这里按 N∈{3,5,10,20} 检查 N×单件价值 是否跨入更高 tier。
    """
    base = tier_of(value_divine, value_chaos, level)
    promotions = []
    prev = base
    for n in sorted(STACK_CHECKPOINTS):  # 3,5,10,20 升序
        t = tier_of(n * value_divine, n * value_chaos, level)
        if TIER_RANK[t] < TIER_RANK[prev]:  # t 比 prev 更高档
            promotions.append((n, t))
            prev = t
    return base, promotions


# ============================== 过滤器解析 ==============================

SECTION_NAME = "### Currency"


def locate_section(lines):
    """定位 '### Currency' 段，返回 (start, end) 区间（end 不含下一段的标题）。"""
    idx = None
    for i, line in enumerate(lines):
        if line.strip() == SECTION_NAME:
            idx = i
            break
    if idx is None:
        raise RuntimeError(f"过滤器里找不到 '{SECTION_NAME}' 段")

    # 找下一个 "### " 段标题
    end = idx + 1
    while end < len(lines) and not lines[end].startswith("### "):
        end += 1
    # end-1 是下一段标题上方的 "####...####" 分隔线，属于下一段，不含进来
    return idx, end - 1


def parse_blocks(section_lines):
    """把段内容解析成块列表。每个块: {action, title, lines:[...]}。"""
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
            # 跳过空行
            while i < n and section_lines[i].strip() == "":
                i += 1
            blocks.append({"action": action, "title": title, "body": body})
        else:
            i += 1
    return blocks


def title_info(title):
    """从块标题解析出 (stack_n, tier)。base 块返回 (None, tier)。"""
    m = re.match(r"Stacks of (\d+)\+ → ([SABCDEF])-Tier Currency", title)
    if m:
        return int(m.group(1)), m.group(2)
    m = re.match(r"([SABCDEF])-Tier Currency", title)
    if m:
        return None, m.group(1)
    return None, None


def parse_currency_section(section_lines):
    """解析段，返回 (class_line, tier_display, original_assignment)。

    - class_line: "  Class == ..." 行
    - tier_display: {tier: {"action": Show/Hide, "display": [cosmetic 行...]}}
    - original_assignment: {name: {"base": tier, "promotions": [(N, target), ...]}}
    """
    blocks = parse_blocks(section_lines)
    class_line = None
    tier_display = {}
    assignment = {}

    for b in blocks:
        stack_n, tier = title_info(b["title"])
        if tier is None:
            continue

        base_types = []
        display = []
        for ln in b["body"]:
            if ln.strip().startswith("Class =="):
                class_line = ln
            elif ln.strip().startswith("StackSize"):
                pass  # 堆叠条件单独记录
            elif ln.strip().startswith("BaseType =="):
                base_types = re.findall(r'"([^"]+)"', ln)
            else:
                display.append(ln)

        # 记录该 tier 的展示样式（base 块才有权威展示，堆叠块同 tier 复用）
        if stack_n is None:
            tier_display[tier] = {"action": b["action"], "display": display}

        for name in base_types:
            rec = assignment.setdefault(name, {"base": None, "promotions": []})
            if stack_n is None:
                rec["base"] = tier
            else:
                rec["promotions"].append((stack_n, tier))

    # 排序每个名字的 promotions（按 N 升序），并去重
    for rec in assignment.values():
        rec["promotions"] = sorted(set(rec["promotions"]))

    return class_line, tier_display, assignment


# ============================== 重新生成 ==============================

def render_base_block(tier, names, class_line, tier_display):
    """渲染 base tier 块。"""
    meta = tier_display.get(tier, {"action": "Show", "display": []})
    action = meta["action"]
    display = meta["display"]
    head = f"{action} # {tier}-Tier Currency (currency)"
    out = [head, class_line, f'  BaseType == {" ".join(chr(34) + n + chr(34) for n in names)}']
    out.extend(display)
    return out


def render_stack_block(n, target, names, class_line, tier_display):
    """渲染 'Stacks of N+ → target' 块。"""
    meta = tier_display.get(target, {"action": "Show", "display": []})
    display = meta["display"]
    head = f"Show # Stacks of {n}+ → {target}-Tier Currency (currency)"
    out = [
        head,
        f"  StackSize >= {n}",
        class_line,
        f'  BaseType == {" ".join(chr(34) + n + chr(34) for n in names)}',
    ]
    out.extend(display)
    return out


def regenerate_section(class_line, tier_display, new_assignment):
    """按新的分级结果，重排并生成整段内容。"""
    # new_assignment: {name: {"base": tier, "promotions": [(N, target), ...]}}
    out = []
    for base_tier in TIER_ORDER:
        group = {name: rec for name, rec in new_assignment.items() if rec["base"] == base_tier}
        if not group:
            continue

        # 收集该 base 组里所有“升档”堆叠规则
        prom_map = {}  # (target, N) -> [names]
        for name, rec in group.items():
            for n, target in rec["promotions"]:
                prom_map.setdefault((target, n), []).append(name)

        # 排序：target 从高到低，N 从大到小
        ordered = sorted(prom_map.items(), key=lambda kv: (TIER_RANK[kv[0][0]], -kv[0][1]))
        for (target, n), names in ordered:
            out.extend(render_stack_block(n, target, sorted(names), class_line, tier_display))
            out.append("")

        # base 块
        names = sorted(group.keys())
        out.extend(render_base_block(base_tier, names, class_line, tier_display))
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
    parser.add_argument("--level", default="very strict", help="分级档位（当前仅 very strict）")
    parser.add_argument("--token", default=None, help="poecurrency.top API Token（可选）")
    parser.add_argument(
        "--price-field",
        default="buy_avg",
        choices=PRICE_FIELDS,
        help="取值字段（默认 buy_avg）",
    )
    args = parser.parse_args(argv)

    if args.level not in LEVELS:
        sys.exit(
            f"[错误] 档位 '{args.level}' 尚未支持。当前支持: {list(LEVELS.keys())}"
        )

    # 取值字段顺序：用户选的字段排最前，其余兜底
    price_fields = [args.price_field] + [f for f in PRICE_FIELDS if f != args.price_field]

    # 1. 读过滤器
    filter_path, output_path = resolve_paths(args)
    if not filter_path.exists():
        sys.exit(f"[错误] 找不到过滤器文件：{filter_path}")
    text = filter_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    # 2. 定位并解析 Currency 段
    start, end = locate_section(lines)
    section_lines = lines[start + 1 : end]  # 去掉 "### Currency" 标题行本身
    class_line, tier_display, original = parse_currency_section(section_lines)
    if not class_line:
        sys.exit("[错误] 未能解析出 Class 行，段结构可能异常")

    # 3. 拉取价格并计算锚点
    prices = fetch_prices(args.token)
    chaos_in_e, divine_in_e = compute_anchors(prices, price_fields)
    print(
        f"[锚点] 1 混沌 ≈ {chaos_in_e} e, 1 神圣 ≈ {divine_in_e} e "
        f"(1 神圣 ≈ {divine_in_e / chaos_in_e:.2f} 混沌)"
    )

    # 4. 对每个通货重新分级
    new_assignment = {}
    table = []
    for name, old_rec in original.items():
        item = prices.get(name)
        if item is None:
            # API 查不到：保留原分级
            new_assignment[name] = {
                "base": old_rec["base"],
                "promotions": list(old_rec["promotions"]),
            }
            table.append((name, old_rec["base"], old_rec["base"], None, "查不到，保留"))
            continue

        # 价值先统一折到 e，再拆成「神圣」「混沌」两个量纲：
        # 分级标准（阈值）保持原生单位，不把 D 档换算成 C 再比。
        ve = value_in_e(item, divine_in_e, price_fields)
        if ve is None:
            new_assignment[name] = {
                "base": old_rec["base"],
                "promotions": list(old_rec["promotions"]),
            }
            table.append((name, old_rec["base"], old_rec["base"], None, "无价格，保留"))
            continue

        value_divine = ve / divine_in_e
        value_chaos = ve / chaos_in_e
        base, promotions = compute_assignment(value_divine, value_chaos, args.level)
        new_assignment[name] = {"base": base, "promotions": promotions}

        old_tier = old_rec["base"]
        proms_str = ", ".join(f"{n}+→{t}" for n, t in promotions) or "-"
        table.append((name, old_tier, base, round(value_chaos, 3), proms_str))

    # 5. 打印变更对照表
    print("\n=== 重新分级结果（旧 tier → 新 tier）===")
    print(f"{'通货':<32}{'旧':<5}{'新':<5}{'价值(混沌)':<12}堆叠升档")
    for name, old_tier, new_tier, vc, proms in table:
        mark = "" if old_tier == new_tier else " *"
        vc_s = str(vc) if vc is not None else "-"
        print(f"{name:<32}{old_tier or '-':<5}{new_tier:<5}{vc_s:<12}{proms}{mark}")

    # 6. 重新生成段并写回
    new_section = regenerate_section(class_line, tier_display, new_assignment)
    new_lines = (
        lines[: start + 1]            # 含 "### Currency" 标题行
        + new_section
        + lines[end:]                 # 后续段（含下一段的分隔线）
    )
    output_path.write_text("\n".join(new_lines), encoding="utf-8")

    changed = sum(1 for _, o, n, *_ in table if o != n)
    print(f"\n[完成] 输出：{output_path}")
    print(f"[统计] 共 {len(table)} 个通货，其中 {changed} 个 tier 发生变化，"
          f"{len(table) - changed} 个不变")


if __name__ == "__main__":
    main()
