# poe2filter-cn-tier-config.example.py
# 用法：复制本文件为 poe2filter-cn-tier-config.py，并填入你的 API Token。
#   cp poe2filter-cn-tier-config.example.py poe2filter-cn-tier-config.py
#
# poe2filter-cn-tier.py 会自动 import 同目录下的 poe2filter-cn-tier-config.py 读取配置。
# 该文件含密钥，不要提交到公开仓库。

API_TOKEN = "在这里填你的 token"

# 需要删除的道具集合（默认为空 = 不删除任何道具）。
# 用于列出「国服确实没有」的道具（国际服多出、国服未更新），
# 过滤器里出现会导致导入国服加载失败，需要删除。
# 国服查不到价格的道具（大概率只是没有成交量）不会被删，会保留原档。
#
# 示例：
# REMOVE_ITEMS = {
#     "Some New League Currency",
#     "Another Intl-Only Item",
# }
REMOVE_ITEMS = set()
