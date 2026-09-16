#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
poe_ninja.py — POE2 国际服物价抓取模块

从 poe.ninja 抓取国际服物价，用于「相对判定」落档算法里计算
原过滤器每档的上下限（原过滤器按国际服物价分档）。

关键端点：
    赛季列表：https://poe.ninja/poe2/api/data/index-state
        → economyLeagues[0] 即当前赛季（非 hardcore）
    物价数据：https://poe.ninja/poe2/api/economy/exchange/current/overview
        ?league={league}&type={type}
        → core.rates（1神圣=X混沌）、lines[].primaryValue（值多少神圣）

各分类对应的 type 值（poe.ninja POE2，注意区分大小写）：
    Currency 通货 / Delirium 液化情感 / Breach 催化剂 / Essences 精华
    Ritual 预兆 / Fragments 碎片 / Runes 符文 / SoulCores 灵核
"""

import json
import urllib.parse
import urllib.request

INDEX_STATE_URL = "https://poe.ninja/poe2/api/data/index-state"
ECONOMY_URL = "https://poe.ninja/poe2/api/economy/exchange/current/overview"

# 分类 type 值（用于抓取国际服价）
TYPE_CURRENCY = "Currency"
TYPE_DELIRIUM = "Delirium"
TYPE_BREACH = "Breach"
TYPE_ESSENCES = "Essences"
TYPE_RITUAL = "Ritual"
TYPE_FRAGMENTS = "Fragments"
TYPE_RUNES = "Runes"
TYPE_SOUL_CORES = "SoulCores"

# poe.ninja 的 id slug → 过滤器里的英文 BaseType 名（Currency 类型）
SLUG_TO_NAME = {
    # 基准通货
    "divine": "Divine Orb",
    "exalted": "Exalted Orb",
    "chaos": "Chaos Orb",
    "mirror": "Mirror of Kalandra",
    # 常规通货（缩写/短名）
    "alch": "Orb of Alchemy",
    "annul": "Orb of Annulment",
    "aug": "Orb of Augmentation",
    "chance": "Orb of Chance",
    "transmute": "Orb of Transmutation",
    "vaal": "Vaal Orb",
    "whetstone": "Blacksmith's Whetstone",
    "scrap": "Armourer's Scrap",
    "bauble": "Glassblower's Bauble",
    "gcp": "Gemcutter's Prism",
    "etcher": "Arcanist's Etcher",
    "artificers": "Artificer's Orb",
    "regal": "Regal Orb",
    "wisdom": "Scroll of Wisdom",
    # 完整 slug（title case，部分含撇号）
    "ancient-infuser": "Ancient Infuser",
    "architects-orb": "Architect's Orb",
    "chance-shard": "Chance Shard",
    "core-destabiliser": "Core Destabiliser",
    "crystallised-corruption": "Crystallised Corruption",
    "fracturing-orb": "Fracturing Orb",
    "greater-regal-orb": "Greater Regal Orb",
    "kopecs-orb-of-sacrifice": "Kopec's Orb of Sacrifice",
    "lesser-jewellers-orb": "Lesser Jeweller's Orb",
    "perfect-chaos-orb": "Perfect Chaos Orb",
    "perfect-exalted-orb": "Perfect Exalted Orb",
    "perfect-jewellers-orb": "Perfect Jeweller's Orb",
    "perfect-regal-orb": "Perfect Regal Orb",
    "vaal-arcanists-infuser": "Vaal Arcanist's Infuser",
    "vaal-armourers-infuser": "Vaal Armourer's Infuser",
    "vaal-blacksmiths-infuser": "Vaal Blacksmith's Infuser",
    "vaal-cultivation-orb": "Vaal Cultivation Orb",
    "vaal-siphoner": "Vaal Siphoner",
    "cryptic-key": "Cryptic Key",
    "hinekoras-lock": "Hinekora's Lock",
    "artificers-shard": "Artificer's Shard",
    "greater-chaos-orb": "Greater Chaos Orb",
    "greater-exalted-orb": "Greater Exalted Orb",
    "greater-jewellers-orb": "Greater Jeweller's Orb",
    "greater-orb-of-augmentation": "Greater Orb of Augmentation",
    "greater-orb-of-transmutation": "Greater Orb of Transmutation",
    "kamasas-orb-of-sacrifice": "Kamasa's Orb of Sacrifice",
    "orb-of-extraction": "Orb of Extraction",
    "perfect-orb-of-augmentation": "Perfect Orb of Augmentation",
    "perfect-orb-of-transmutation": "Perfect Orb of Transmutation",
    "regal-shard": "Regal Shard",
    "transmutation-shard": "Transmutation Shard",
    "vaal-catalysing-infuser": "Vaal Catalysing Infuser",
    "yaomacs-orb-of-sacrifice": "Yaomac's Orb of Sacrifice",
    "yuguls-orb-of-sacrifice": "Yugul's Orb of Sacrifice",
}


def http_get_json(url, timeout=30):
    """GET 一个 JSON 接口，返回解析后的对象。"""
    req = urllib.request.Request(url, headers={"User-Agent": "poe2filter-cn-tier/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_index_state():
    """返回 index-state 的 JSON（含 economyLeagues 赛季列表）。"""
    return http_get_json(INDEX_STATE_URL)


def get_current_league(index_state=None):
    """返回当前赛季名（economyLeagues 第一项，非 hardcore）。"""
    if index_state is None:
        index_state = fetch_index_state()
    leagues = index_state.get("economyLeagues", [])
    for league in leagues:
        if not league.get("hardcore", False):
            return league["name"]
    if leagues:
        return leagues[0]["name"]
    raise RuntimeError("无法确定当前赛季")


def fetch_overview(league, type_value):
    """返回某赛季某分类的 overview JSON。"""
    league_q = urllib.parse.quote_plus(league)
    url = f"{ECONOMY_URL}?league={league_q}&type={type_value}"
    return http_get_json(url)


def fetch_prices_by_type(league, type_value, slug_map):
    """抓取某分类的国际服价，返回 {英文名: 混沌价值}。

    primaryValue 是「值多少神圣」，乘 core.rates.chaos 折到混沌。
    匹配不上的 id（不在 slug_map）会被跳过。
    """
    data = fetch_overview(league, type_value)
    chaos_per_divine = data.get("core", {}).get("rates", {}).get("chaos")
    if not chaos_per_divine:
        return {}
    prices = {}
    for line in data.get("lines", []):
        slug = line.get("id")
        name = slug_map.get(slug)
        if name and line.get("primaryValue") is not None:
            prices[name] = line["primaryValue"] * chaos_per_divine
    return prices


def fetch_international_prices(league=None):
    """抓取全部通货类国际服价（Currency 类型为主），返回 {英文名: 混沌价值}。

    目前只抓 Currency（主通货）。其它分类（Delirium/Breach/Essences/Ritual 等）
    后续按需扩展 slug 映射后追加。
    """
    if league is None:
        league = get_current_league()
    return fetch_prices_by_type(league, TYPE_CURRENCY, SLUG_TO_NAME)


if __name__ == "__main__":
    league = get_current_league()
    print(f"当前赛季: {league}")
    prices = fetch_international_prices(league)
    print(f"抓到 {len(prices)} 个通货价格（折到混沌）:")
    for name, val in sorted(prices.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<28} {val:.4f}")
