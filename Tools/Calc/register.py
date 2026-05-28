# Tools/Calc/register.py
"""
数学计算工具注册模块
仅提供工具包装函数，并通过 get_tools() 返回工具列表
工具文档由 main.py 根据 Usage.yaml 统一构建
"""

import json
import logging
from typing import Optional

from .Calc import handle_calc

log = logging.getLogger("mcp_server")


def _safe_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2)


async def calc(
    operation: str,
    expression: str = "",
    equation: str = "",
    variable: str = "x",
    point: str = "0",
    order: int = 1,
    lower_limit: str = "0",
    upper_limit: str = "1",
) -> str:
    """
    数学计算工具，支持化简、求解、求导、积分、求值等操作。
    文档字符串由 main.py 动态注入，此处为占位。
    """
    args = {
        "operation": operation,
        "expression": expression,
        "equation": equation,
        "variable": variable,
        "point": point,
        "order": order,
        "lower_limit": lower_limit,
        "upper_limit": upper_limit,
    }
    try:
        result = handle_calc(args)
        return result if isinstance(result, str) else _safe_json(result)
    except Exception as e:
        log.exception("计算工具异常")
        return _safe_json({"status": "error", "error": str(e)})


def get_tools():
    """
    返回该工具包提供的所有工具函数列表。
    每个元素为字典，包含 func 和可选的 name。
    """
    return [
        {"func": calc, "name": "calc"},
    ]