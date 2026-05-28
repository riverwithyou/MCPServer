"""
终端工具注册模块
"""
from .Terminal import (
    terminal_start,
    terminal_send,
    terminal_send_multiline,
    terminal_send_key,
    terminal_send_raw,
    terminal_read,
    terminal_expect,
    terminal_exec,
    terminal_exitstatus,
    terminal_set_winsize,
    terminal_close,
)

def get_tools():
    """
    返回该工具包提供的所有工具函数列表。
    每个元素为字典，包含 func 和可选的 name。
    """
    return [
        {"func": terminal_start, "name": "terminal_start"},
        {"func": terminal_send, "name": "terminal_send"},
        {"func": terminal_send_multiline, "name": "terminal_send_multiline"},
        {"func": terminal_send_key, "name": "terminal_send_key"},
        {"func": terminal_send_raw, "name": "terminal_send_raw"},
        {"func": terminal_read, "name": "terminal_read"},
        {"func": terminal_expect, "name": "terminal_expect"},
        {"func": terminal_exec, "name": "terminal_exec"},
        {"func": terminal_exitstatus, "name": "terminal_exitstatus"},
        {"func": terminal_set_winsize, "name": "terminal_set_winsize"},
        {"func": terminal_close, "name": "terminal_close"},
    ]