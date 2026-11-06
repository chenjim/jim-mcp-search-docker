"""按查询类型选择搜索源。

`shuffle` / `random` 轮转的目的是把各家的免费额度用满，但它不区分意图：
问 PostgreSQL 版本和问中文政策，被分到同一个源的概率一样。
多源实测（13 条 query × 5 源 × 2 轮）显示不同意图的最优源并不重合，
所以这里先用语言 + 意图词做轻量分类，命中不了返回默认顺序。

规则是硬的、可解释的；不做模型分类，避免多一次调用和不可预期。
"""
import re
from typing import List

_CJK = re.compile(r"[\u4e00-\u9fff]")

_EN_DOCS = re.compile(
    r"\brelease notes?\b|\bdocs?\b|\bdocumentation\b|\bapi\b|\bspecs?\b|"
    r"\bchangelog\b|\breference\b|\bsdk\b|\bguide\b"
)
_EN_REVIEW = re.compile(
    r"\breview\b|\bbest\b|\btop\b|\bprice\b|\bpricing\b|\bvs\b|"
    r"\bcomparison\b|\bbuy\b|\bdeal\b"
)

_ZH_POLICY = ("政策", "标准", "监管", "新规", "法规", "条例", "规划", "通知", "意见", "办法")
_ZH_DATA = ("统计", "数据", "同比", "环比", "gdp", "cpi", "pmi", "人口", "收入", "财报")
_ZH_COMMUNITY = ("教程", "怎么", "如何", "踩坑", "经验", "知乎", "博客", "指南", "用法")

# 每个类型的有序候选。前两位才是实测里的主力，后面是降级位。
ROUTES = {
    "zh_policy": ("deepseek", "baidu", "brave", "tavily"),
    "zh_data": ("parallel", "tavily", "deepseek", "baidu"),
    "zh_community": ("baidu", "brave", "tavily", "parallel"),
    "zh_default": ("baidu", "deepseek", "brave", "tavily"),
    "en_docs": ("parallel", "brave", "tavily", "deepseek"),
    "en_review": ("brave", "tavily", "parallel", "deepseek"),
    "en_default": ("brave", "tavily", "parallel", "deepseek"),
}


def classify(query: str) -> str:
    """返回路由名；纯规则，不抛异常。"""
    q = (query or "").strip().lower()
    if not q:
        return "en_default"
    if _CJK.search(q):
        if any(word in q for word in _ZH_POLICY):
            return "zh_policy"
        if any(word in q for word in _ZH_DATA):
            return "zh_data"
        if any(word in q for word in _ZH_COMMUNITY):
            return "zh_community"
        return "zh_default"
    if _EN_DOCS.search(q):
        return "en_docs"
    if _EN_REVIEW.search(q):
        return "en_review"
    return "en_default"


def providers_for(query: str) -> List[str]:
    return list(ROUTES[classify(query)])
