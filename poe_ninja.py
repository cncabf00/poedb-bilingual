#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
poe_ninja.py — 国际服物价抓取模块（支持 POE1 / POE2）

从 poe.ninja 抓取国际服物价，用于「相对判定」落档算法里计算
原过滤器每档的上下限（原过滤器按国际服物价分档）。

关键端点（game 区分前缀）：
    赛季列表：
        POE2: https://poe.ninja/poe2/api/data/index-state
        POE1: https://poe.ninja/poe1/api/data/index-state
        → economyLeagues[0] 即当前赛季（非 hardcore）
    物价数据：
        {prefix}/api/economy/exchange/current/overview?league={league}&type={type}

两代 primaryValue 单位不同：
    POE2: primaryValue = 值多少「神圣」，需乘 core.rates.chaos 折到混沌
    POE1: primaryValue = 值多少「混沌」（chaos=1, divine=352.4），直接就是混沌价

各分类对应的 type 值（注意区分大小写）：
    Currency 通货 / Delirium 液化情感 / Breach 催化剂 / Essences 精华
    Ritual 预兆 / Fragments 碎片 / Runes 符文 / SoulCores 灵核
"""

import json
import re
import urllib.parse
import urllib.request

# 各 game 的 API 前缀
INDEX_STATE_URLS = {
    "poe1": "https://poe.ninja/poe1/api/data/index-state",
    "poe2": "https://poe.ninja/poe2/api/data/index-state",
}
ECONOMY_URLS = {
    "poe1": "https://poe.ninja/poe1/api/economy/exchange/current/overview",
    "poe2": "https://poe.ninja/poe2/api/economy/exchange/current/overview",
}
STASH_URLS = {
    "poe1": "https://poe.ninja/poe1/api/economy/stash/current/item/overview",
    "poe2": "https://poe.ninja/poe2/api/economy/stash/current/item/overview",
}

# 分类 type 值（用于抓取国际服价）
TYPE_CURRENCY = "Currency"
TYPE_DELIRIUM = "Delirium"
TYPE_BREACH = "Breach"
TYPE_ESSENCES = "Essences"
TYPE_RITUAL = "Ritual"
TYPE_FRAGMENTS = "Fragments"
TYPE_RUNES = "Runes"
TYPE_SOUL_CORES = "SoulCores"

# ============================== POE2 slug → 英文名 ==============================

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

# ============================== POE1 slug → 英文名 ==============================

# POE1 缩写 slug（无法用规则自动转换的）
SLUG_TO_NAME_POE1_ABBREV = {
    "alch": "Orb of Alchemy",
    "alt": "Orb of Alteration",
    "annul": "Orb of Annulment",
    "aug": "Orb of Augmentation",
    "bauble": "Glassblower's Bauble",
    "blessed": "Blessed Orb",
    "chance": "Orb of Chance",
    "chaos": "Chaos Orb",
    "chrome": "Chromatic Orb",
    "divine": "Divine Orb",
    "exalted": "Exalted Orb",
    "fusing": "Orb of Fusing",
    "gcp": "Gemcutter's Prism",
    "jewellers": "Jeweller's Orb",
    "mirror": "Mirror of Kalandra",
    "portal": "Portal Scroll",
    "regal": "Regal Orb",
    "regret": "Orb of Regret",
    "scour": "Orb of Scouring",
    "scrap": "Armourer's Scrap",
    "transmute": "Orb of Transmutation",
    "vaal": "Vaal Orb",
    "whetstone": "Blacksmith's Whetstone",
    "wisdom": "Scroll of Wisdom",
}

# POE1 所有格词（slug 中间出现时转成 "X's"）
POSSESSIVE_POE1 = {
    "awakeners": "Awakener's",
    "armourers": "Armourer's",
    "blacksmiths": "Blacksmith's",
    "crusaders": "Crusader's",
    "elders": "Elder's",
    "hinekoras": "Hinekora's",
    "hunters": "Hunter's",
    "jewellers": "Jeweller's",
    "mans": "Man's",
    "mavens": "Maven's",
    "redeemers": "Redeemer's",
    "rogues": "Rogue's",
    "shapers": "Shaper's",
    "warlords": "Warlord's",
}

_POE1_LOWERCASE = {"of", "the", "and"}


def _poe1_slug_to_name(slug):
    """POE1 slug → 英文名（缩写表优先，其余用 title-case + 所有格规则）。"""
    if slug in SLUG_TO_NAME_POE1_ABBREV:
        return SLUG_TO_NAME_POE1_ABBREV[slug]
    parts = slug.split("-")
    out = []
    for p in parts:
        if p in _POE1_LOWERCASE:
            out.append(p)
        elif p in POSSESSIVE_POE1:
            out.append(POSSESSIVE_POE1[p])
        else:
            out.append(p.capitalize())
    return " ".join(out)


def slug_to_name(game, slug):
    """按 game 把 poe.ninja 的 id slug 转成过滤器里的英文 BaseType 名。"""
    if game == "poe1":
        return _poe1_slug_to_name(slug)
    return SLUG_TO_NAME.get(slug)


# ============================== 分类配置 ==============================

# exchange 端点（/api/economy/exchange/current/overview）的通货类分类 type（按 game）
# POE2 有 14 个通货类分类；POE1 只需 Currency（已含催化剂/生命之力等）
EXCHANGE_TYPES = {
    "poe2": [
        "Currency", "Fragments", "Abyss", "UncutGems", "LineageSupportGems",
        "Essences", "SoulCores", "Idols", "Runes", "Ritual", "Expedition",
        "Delirium", "Breach", "Verisium",
    ],
    "poe1": ["Currency"],
}

# stash 端点（/api/economy/stash/current/item/overview）的分类 type（按 game）
# 用 baseType 字段直接匹配英文名（无需 slug）
STASH_TYPES = {
    "poe2": ["PrecursorTablets"],
    "poe1": [],
}


def slugify(name):
    """英文名 → poe.ninja 的 id slug。

    去撇号、空格转横线、去括号/冒号/逗号、小写；
    "(Level N)" → "N"（如 Uncut Skill Gem (Level 1) → uncut-skill-gem-1）。
    """
    s = re.sub(r"\(level\s*(\d+)\)", r"\1", name, flags=re.IGNORECASE)
    return (
        s.replace("'", "")
        .replace(" ", "-")
        .replace("(", "")
        .replace(")", "")
        .replace(":", "")
        .replace(",", "")
        .lower()
    )


# ============================== 抓取 ==============================

def http_get_json(url, timeout=30):
    """GET 一个 JSON 接口，返回解析后的对象。"""
    req = urllib.request.Request(url, headers={"User-Agent": "poe2filter-cn-tier/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_index_state(game):
    """返回 index-state 的 JSON（含 economyLeagues 赛季列表）。"""
    return http_get_json(INDEX_STATE_URLS[game])


def get_current_league(game, index_state=None):
    """返回当前赛季名（economyLeagues 第一项，非 hardcore）。"""
    if index_state is None:
        index_state = fetch_index_state(game)
    leagues = index_state.get("economyLeagues", [])
    for league in leagues:
        if not league.get("hardcore", False):
            return league["name"]
    if leagues:
        return leagues[0]["name"]
    raise RuntimeError(f"无法确定 {game} 当前赛季")


def fetch_overview(game, league, type_value):
    """返回某 game 某赛季某分类的 exchange overview JSON。"""
    league_q = urllib.parse.quote_plus(league)
    url = f"{ECONOMY_URLS[game]}?league={league_q}&type={type_value}"
    return http_get_json(url)


def fetch_stash_overview(game, league, type_value):
    """返回某 game 某赛季某分类的 stash overview JSON（如 PrecursorTablets）。"""
    league_q = urllib.parse.quote_plus(league)
    url = f"{STASH_URLS[game]}?league={league_q}&type={type_value}"
    return http_get_json(url)


def fetch_prices_by_type(game, league, type_value):
    """抓取某分类的国际服价，返回 {英文名: 混沌价值}。

    POE2: primaryValue 是「值多少神圣」，乘 core.rates.chaos 折到混沌。
    POE1: primaryValue 直接就是混沌价。
    匹配不上的 id（slug 无法映射成英文名）会被跳过。
    """
    data = fetch_overview(game, league, type_value)
    prices = {}

    if game == "poe1":
        for line in data.get("lines", []):
            slug = line.get("id")
            name = slug_to_name(game, slug)
            if name and line.get("primaryValue") is not None:
                prices[name] = line["primaryValue"]
        return prices

    chaos_per_divine = data.get("core", {}).get("rates", {}).get("chaos")
    if not chaos_per_divine:
        return {}
    for line in data.get("lines", []):
        slug = line.get("id")
        name = slug_to_name(game, slug)
        if name and line.get("primaryValue") is not None:
            prices[name] = line["primaryValue"] * chaos_per_divine
    return prices


def _match_slug(game, slug, slug_map):
    """把 slug 匹配到英文名。

    先试 slug_map（过滤器英文名的 slugify，覆盖长 slug）；
    再回退内置映射（覆盖 Currency 的短 slug，如 chaos/divine/alch）。
    """
    if not slug:
        return None
    if slug_map is not None and slug in slug_map:
        return slug_map[slug]
    return slug_to_name(game, slug)


def fetch_international_prices(game, name_set=None):
    """抓取某 game 全部通货类国际服价，返回 {英文名: 混沌价值}。

    name_set: 过滤器里的英文名集合（用于 slug / baseType 匹配）。
              None 时用内置 SLUG_TO_NAME（仅 Currency）。
    """
    league = get_current_league(game)
    slug_map = {slugify(n): n for n in name_set} if name_set is not None else None
    result = {}

    # exchange 端点（用 slug 匹配）
    for type_value in EXCHANGE_TYPES.get(game, []):
        try:
            data = fetch_overview(game, league, type_value)
        except Exception:  # noqa: BLE001
            continue
        lines = data.get("lines", [])
        if game == "poe1":
            # POE1: primaryValue 直接是混沌价
            for line in lines:
                name = _match_slug(game, line.get("id"), slug_map)
                if name and line.get("primaryValue") is not None:
                    result[name] = line["primaryValue"]
            continue
        # POE2: primaryValue × rates.chaos 折到混沌
        cpd = data.get("core", {}).get("rates", {}).get("chaos")
        if not cpd:
            continue
        for line in lines:
            name = _match_slug(game, line.get("id"), slug_map)
            if name and line.get("primaryValue") is not None:
                result[name] = line["primaryValue"] * cpd

    # stash 端点（用 baseType 匹配）
    for type_value in STASH_TYPES.get(game, []):
        try:
            data = fetch_stash_overview(game, league, type_value)
        except Exception:  # noqa: BLE001
            continue
        cpd = data.get("core", {}).get("rates", {}).get("chaos")
        if not cpd:
            continue
        # 同一 baseType 有多个 variant（Normal/Magic/Rare），取最小 primaryValue
        seen = {}
        for line in data.get("lines", []):
            bt = line.get("baseType")
            val = line.get("primaryValue")
            if not bt or val is None:
                continue
            if name_set is not None and bt not in name_set:
                continue
            if bt not in seen or val < seen[bt]:
                seen[bt] = val
        for bt, val in seen.items():
            result[bt] = val * cpd

    return result


if __name__ == "__main__":
    for game in ("poe2", "poe1"):
        league = get_current_league(game)
        print(f"[{game}] 当前赛季: {league}")
        prices = fetch_international_prices(game)
        print(f"[{game}] 抓到 {len(prices)} 个通货价格（折到混沌）")
        for name, val in sorted(prices.items(), key=lambda kv: -kv[1])[:8]:
            print(f"    {name:<30} {val:.4f}")
        print()
