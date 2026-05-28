# Tools/Search/register.py
"""
搜索引擎工具注册模块
仅提供工具包装函数，并通过 get_tools() 返回工具列表
工具文档由 main.py 根据 Usage.yaml 统一构建
"""

import json
import logging
from typing import List, Optional

from .Search import (
    search_keyword as _search_impl,
    batch_search as _batch_impl,
    fetch_url_deep as _fetch_impl,
    batch_fetch_urls as _batch_fetch_impl,
)

log = logging.getLogger("mcp_server")


# ========== 工具包装函数 ==========
# 注意：这些函数将作为 MCP 工具暴露，文档字符串由 main.py 动态注入

async def search(
    keyword: str,
    engine: str = "",
    page: int = 1,
    max_results: int = 20,
) -> str:
    """执行单关键词搜索"""
    result = await _search_impl(
        keyword,
        engine if engine else None,
        page,
        max_results
    )
    return json.dumps(result, ensure_ascii=False)


async def batch_search(
    keywords: List[str],
    engine: str = "",
    page: int = 1,
    max_results: int = 20,
) -> str:
    """批量搜索多个关键词"""
    result = await _batch_impl(
        keywords,
        engine if engine else None,
        page,
        max_results
    )
    return json.dumps(result, ensure_ascii=False)


async def fetch_url(
    url: str,
    max_length: int = 5000,
    render_timeout: int = 2000,
    wait_for_selector: Optional[str] = None,
    isolated: bool = False,
) -> str:
    """获取单个 URL 的深度内容（支持 JavaScript 渲染）"""
    result = await _fetch_impl(
        url,
        max_length,
        render_timeout,
        wait_for_selector,
        isolated
    )
    return json.dumps(result, ensure_ascii=False)


async def batch_fetch_urls(
    urls: List[str],
    max_length: int = 5000,
    max_concurrent: int = 3,
    render_timeout: int = 2000,
    wait_for_selector: Optional[str] = None,
    isolated: bool = False,
) -> str:
    """批量获取多个 URL 的内容"""
    result = await _batch_fetch_impl(
        urls,
        max_length,
        max_concurrent,
        render_timeout,
        wait_for_selector,
        isolated
    )
    return json.dumps(result, ensure_ascii=False)


# ========== 工具列表导出 ==========
def get_tools():
    """
    返回该工具包提供的所有工具函数列表。
    每个元素为字典，包含 func 和可选的 name。
    """
    return [
        {"func": search, "name": "search"},
        {"func": batch_search, "name": "batch_search"},
        {"func": fetch_url, "name": "fetch_url"},
        {"func": batch_fetch_urls, "name": "batch_fetch_urls"},
    ]