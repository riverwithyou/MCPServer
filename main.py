# main.py
"""
MCP 服务器启动器
"""
import asyncio
import importlib
import inspect
import logging
import sys
from pathlib import Path
from typing import Dict, Any, List

import yaml
from fastmcp import FastMCP
from rich.logging import RichHandler
import rich.traceback

# ---------- 日志配置 ----------
LOG_DIR = Path(__file__).parent / "Logs"
LOG_DIR.mkdir(exist_ok=True)

file_handler = logging.FileHandler(LOG_DIR / "MainLogs.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

rich.traceback.install(show_locals=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, markup=True), file_handler],
)
log = logging.getLogger("mcp_server")

# ---------- 全局 MCP 实例 ----------
mcp = FastMCP("MCPServer")

# ---------- 配置路径 ----------
CONFIG_PATH = Path(__file__).parent / "Config" / "tools.yaml"


def load_config() -> List[Dict[str, Any]]:
    """加载 tools.yaml 配置文件，返回 tools 列表"""
    if not CONFIG_PATH.exists():
        log.error("配置文件不存在: %s", CONFIG_PATH)
        sys.exit(1)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        tools = config.get("tools", [])
        if not isinstance(tools, list):
            log.error("配置文件中的 'tools' 字段必须是列表")
            sys.exit(1)
        return tools
    except Exception as e:
        log.error("加载配置文件失败: %s", e)
        sys.exit(1)


def _load_usage_yaml(usage_path: Path) -> Dict[str, Any]:
    """加载 Usage.yaml 文件，必须存在且格式正确"""
    try:
        with open(usage_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if "tools" not in data:
            log.error("Usage.yaml 缺少 'tools' 字段: %s", usage_path)
            sys.exit(1)
        return data
    except Exception as e:
        log.error("解析 Usage.yaml 失败: %s - %s", usage_path, e)
        sys.exit(1)


def _build_tool_doc(tool_name: str, usage_data: Dict[str, Any], toolset_name: str) -> str:
    """
    构建单个工具的文档字符串。
    格式：
        【工具集：<toolset_name>】
        【<工具摘要>】

        参数：
          - <name> (必需/可选，默认<value>): <description>
        ...
        返回值：<returns>
        示例：
          <example>
    """
    tool_info = usage_data.get("tools", {}).get(tool_name)
    if not tool_info:
        log.error("工具 %s 在 Usage.yaml 中未定义", tool_name)
        sys.exit(1)

    lines = []
    lines.append(f"【工具集：{toolset_name}】")
    lines.append(f"【{tool_info.get('summary', tool_name)}】")

    # 参数部分
    params = tool_info.get("parameters")
    if params:
        lines.append("\n参数：")
        for p in params:
            req = "必需" if p.get("required") else "可选"
            default = f"，默认 {p['default']}" if "default" in p else ""
            lines.append(f"  - `{p['name']}` ({req}{default}): {p.get('description', '')}")

    # 返回值
    returns = tool_info.get("returns")
    if returns:
        lines.append(f"\n返回值：{returns}")

    # 示例
    example = tool_info.get("example")
    if example:
        lines.append(f"\n示例：\n{example}")

    return "\n".join(lines)


def register_tools():
    """加载所有启用的工具包，注册工具到 mcp"""
    tools_config = load_config()
    total = len(tools_config)
    enabled_count = 0
    disabled_count = 0

    for tool_cfg in tools_config:
        name = tool_cfg.get("name")
        module_path = tool_cfg.get("module")
        enabled = tool_cfg.get("enabled", True)
        description = tool_cfg.get("description", "")

        if not enabled:
            log.info("禁用工具包：%s %s", name, description)
            disabled_count += 1
            continue

        if not module_path:
            log.error("工具包 %s 缺少 module 字段，无法启用", name)
            sys.exit(1)

        try:
            # 动态导入模块
            mod = importlib.import_module(module_path)

            # 检查 get_tools 函数
            if not hasattr(mod, "get_tools"):
                log.error("模块 %s 中没有 get_tools 函数，无法启用工具包 %s", module_path, name)
                sys.exit(1)

            tools = mod.get_tools()
            if not isinstance(tools, list):
                log.error("模块 %s 的 get_tools 返回值必须是列表", module_path)
                sys.exit(1)

            if not tools:
                log.warning("工具包 %s 未返回任何工具", name)

            # 获取模块所在目录
            module_dir = Path(inspect.getfile(mod)).parent

            # 强制要求 Usage.yaml 存在
            usage_path = module_dir / "Usage.yaml"
            if not usage_path.exists():
                log.error("工具包 %s 缺少 Usage.yaml 文件", name)
                sys.exit(1)
            usage_data = _load_usage_yaml(usage_path)

            # 注册每个工具
            for tool_def in tools:
                if not isinstance(tool_def, dict):
                    log.error("工具定义必须是字典，得到 %s", type(tool_def))
                    sys.exit(1)

                func = tool_def.get("func")
                tool_func_name = tool_def.get("name")
                if not tool_func_name:
                    tool_func_name = func.__name__

                if not callable(func):
                    log.error("工具定义缺少可调用对象 func")
                    sys.exit(1)

                # 构建文档并挂载
                doc = _build_tool_doc(tool_func_name, usage_data, name)
                func.__doc__ = doc

                # 注册到 MCP
                mcp.tool(name=tool_func_name)(func)
                log.debug("注册工具: %s (%s)", tool_func_name, name)

            log.info("启用工具包：%s (%s)，注册了 %d 个工具", name, description, len(tools))
            enabled_count += 1

        except Exception as e:
            log.exception("导入模块失败 %s: %s", module_path, e)
            sys.exit(1)

    log.info("共 %d 个工具包，启用 %d 个，禁用 %d 个", total, enabled_count, disabled_count)


def main():
    log.info("MCP 服务器启动")
    register_tools()
    try:
        mcp.run(transport="streamable-http", host="0.0.0.0", port=8082)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("服务器被用户终止")
    except Exception as e:
        log.exception("服务器运行异常")
        sys.exit(1)


if __name__ == "__main__":
    main()