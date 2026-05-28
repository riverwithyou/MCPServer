"""
智能交互终端工具（纯函数实现，无装饰器）
"""

import asyncio
import json
import time
import uuid
import re
import logging
from typing import Dict, Optional, List, Union, Tuple

import pexpect

# ---------- 配置 ----------
SESSION_TIMEOUT = 300
PATTERN_MAX_LENGTH = 500
CLEANUP_INTERVAL = 60
OUTPUT_CHUNK = 4096
MAX_OUTPUT_SIZE = 50000

DEFAULT_PAGER_PATTERNS = {
    "--More--": " ",
    "Press any key to continue": " ",
    "Press Enter to continue": "\r",
    "(END)": "q",
}

KEY_MAP = {
    "ctrl_c": ("control", "c"),
    "ctrl_d": ("control", "d"),
    "ctrl_z": ("control", "z"),
    "ctrl_a": ("control", "a"),
    "ctrl_e": ("control", "e"),
    "ctrl_l": ("control", "l"),
    "ctrl_u": ("control", "u"),
    "ctrl_k": ("control", "k"),
    "ctrl_w": ("control", "w"),
    "up": ("raw", "\x1b[A"),
    "down": ("raw", "\x1b[B"),
    "right": ("raw", "\x1b[C"),
    "left": ("raw", "\x1b[D"),
    "home": ("raw", "\x1b[H"),
    "end": ("raw", "\x1b[F"),
    "pageup": ("raw", "\x1b[5~"),
    "pagedown": ("raw", "\x1b[6~"),
    "insert": ("raw", "\x1b[2~"),
    "delete": ("raw", "\x1b[3~"),
    "f1": ("raw", "\x1bOP"),
    "f2": ("raw", "\x1bOQ"),
    "f3": ("raw", "\x1bOR"),
    "f4": ("raw", "\x1bOS"),
    "f5": ("raw", "\x1b[15~"),
    "f6": ("raw", "\x1b[17~"),
    "f7": ("raw", "\x1b[18~"),
    "f8": ("raw", "\x1b[19~"),
    "f9": ("raw", "\x1b[20~"),
    "f10": ("raw", "\x1b[21~"),
    "f11": ("raw", "\x1b[23~"),
    "f12": ("raw", "\x1b[24~"),
    "escape": ("raw", "\x1b"),
    "tab": ("raw", "\t"),
    "enter": ("raw", "\r"),
    "backspace": ("raw", "\x7f"),
}

_sessions: Dict[str, dict] = {}
_sessions_lock = asyncio.Lock()
_cleanup_task_handle: Optional[asyncio.Task] = None
_cleanup_start_lock = asyncio.Lock()
logger = logging.getLogger(__name__)

# ---------- 内部工具函数 ----------

async def _shutdown_session(sid: str):
    """关闭会话并释放资源"""
    async with _sessions_lock:
        session = _sessions.get(sid)
        if not session:
            return
        lock = session["lock"]
    async with lock:
        async with _sessions_lock:
            if sid in _sessions:
                session["closed"] = True
                del _sessions[sid]
                try:
                    session["child"].close(force=True)
                except Exception:
                    pass
                finally:
                    log_fd = session.get("log_fd")
                    if log_fd:
                        try:
                            log_fd.close()
                        except Exception:
                            pass

def _update_activity(session: dict):
    session["last_activity"] = time.time()

def _truncate_output(text: str) -> str:
    if len(text) > MAX_OUTPUT_SIZE:
        return text[:MAX_OUTPUT_SIZE] + f"\n\n[输出已截断，仅显示前{MAX_OUTPUT_SIZE}字符]"
    return text

def _read_blocking(child, deadline: float, chunk_size: int, max_size: int) -> Tuple[bytes, bool]:
    """
    同步阻塞式读取，在指定时间前不断尝试读取数据。
    该函数应在线程池中执行，避免阻塞事件循环。
    """
    data = b""
    while time.time() < deadline:
        try:
            chunk = child.read_nonblocking(size=chunk_size, timeout=0.2)
            if chunk:
                if len(data) < max_size:
                    data += chunk
            # 没有数据时继续等待，不提前退出
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF:
            break
    is_eof = not child.isalive()
    return data, is_eof

async def _read_available(child, max_wait: float = 2.0) -> Tuple[bytes, bool]:
    """
    从子进程读取所有可获取的输出，并彻底清空内核缓冲区。
    max_wait == 0: 完全非阻塞，读空缓冲区即返回（快速路径，不切换线程）。
    max_wait > 0:  在 max_wait 秒内反复尝试读取，直到无数据或 EOF。
                   使用 asyncio.to_thread 避免阻塞事件循环。
    """
    if max_wait == 0:
        # 非阻塞读取：全部读取后立即返回
        data = b""
        while True:
            try:
                chunk = child.read_nonblocking(size=OUTPUT_CHUNK, timeout=0)
                if chunk:
                    if len(data) < MAX_OUTPUT_SIZE:
                        data += chunk
                else:
                    break
            except pexpect.TIMEOUT:
                break
            except pexpect.EOF:
                return data, True
        return data, not child.isalive()
    else:
        deadline = time.time() + max_wait
        return await asyncio.to_thread(
            _read_blocking, child, deadline, OUTPUT_CHUNK, MAX_OUTPUT_SIZE
        )

async def _cleanup_task():
    """后台清理超时会话，包含异常保护"""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)
            now = time.time()
            async with _sessions_lock:
                expired = [
                    sid for sid, s in _sessions.items()
                    if now - s.get("last_activity", 0) > SESSION_TIMEOUT
                ]
            for sid in expired:
                await _shutdown_session(sid)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("清理任务异常: %s", e, exc_info=True)

async def _ensure_cleanup_started():
    global _cleanup_task_handle
    if _cleanup_task_handle is None:
        async with _cleanup_start_lock:
            if _cleanup_task_handle is None:
                _cleanup_task_handle = asyncio.create_task(_cleanup_task())

async def _get_valid_session(sid: str):
    async with _sessions_lock:
        session = _sessions.get(sid)
        if not session:
            return None, None, json.dumps({"ok": False, "error": "会话不存在"})
        lock = session["lock"]
    return session, lock, None

def _validate_and_get_patterns(patterns: Union[str, List[str]]) -> List[str]:
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise ValueError("patterns 必须是字符串或字符串列表")
    if len(patterns) == 0:
        raise ValueError("patterns 列表不能为空")
    for p in patterns:
        if len(p) > PATTERN_MAX_LENGTH:
            raise ValueError(f"Pattern '{p}' 超出最大长度 {PATTERN_MAX_LENGTH}")
    return patterns

# ========== 公共工具函数 ==========

async def terminal_start(command: str = "/bin/bash", env: Optional[Dict[str, str]] = None, log_file: Optional[str] = None) -> str:
    """启动一个新终端会话"""
    await _ensure_cleanup_started()
    sid = uuid.uuid4().hex[:8]
    child = None
    log_fd = None
    try:
        spawn_kwargs = {"encoding": None, "timeout": 30}
        if env is not None:
            spawn_kwargs["env"] = env
        # 使用线程池启动进程，避免阻塞事件循环
        child = await asyncio.to_thread(pexpect.spawn, command, **spawn_kwargs)
        if log_file:
            log_fd = open(log_file, "ab")
            child.logfile_read = log_fd
        initial, eof = await _read_available(child, max_wait=2.0)
        session = {
            "child": child,
            "lock": asyncio.Lock(),
            "last_activity": time.time(),
            "closed": False,
            "log_fd": log_fd,
        }
        async with _sessions_lock:
            _sessions[sid] = session
        output = initial.decode("utf-8", errors="replace")
        return json.dumps({
            "ok": True, "session": sid, "output": output,
            "eof": eof or not child.isalive()
        }, ensure_ascii=False)
    except Exception as e:
        if log_fd:
            try: log_fd.close()
            except: pass
        if child:
            try: child.close(force=True)
            except: pass
        return json.dumps({"ok": False, "error": str(e)})

async def terminal_send(sid: str, text: str) -> str:
    """发送一行文本。允许只含空白的字符串（会发送一个换行）。"""
    # 去除首尾空白后判断是否为空
    stripped = text.strip()
    if not stripped:
        return json.dumps({"ok": False, "error": "发送文本不能为空"})
    if "\n" in text or "\r" in text:
        return json.dumps({"ok": False, "error": "文本中不能包含换行符，请使用 terminal_send_multiline 或 terminal_send_raw"})

    session, lock, err = await _get_valid_session(sid)
    if err:
        return err

    await lock.acquire()
    need_close = False
    send_error = None
    result_json = None
    try:
        if session.get("closed"):
            result_json = json.dumps({"ok": False, "error": "会话已关闭"})
            return result_json
        _update_activity(session)
        child = session["child"]
        try:
            await asyncio.to_thread(child.sendline, stripped)
        except Exception as e:
            send_error = str(e)
            need_close = True
        if not need_close and not child.isalive():
            need_close = True
        if send_error is None:
            result_json = json.dumps({"ok": True})
        else:
            result_json = json.dumps({"ok": False, "error": send_error})
    finally:
        lock.release()
    if need_close:
        await _shutdown_session(sid)
    return result_json

async def terminal_send_multiline(sid: str, text: str) -> str:
    """发送多行文本（如脚本），自动在每行末尾追加回车。"""
    if not text.strip():
        return json.dumps({"ok": False, "error": "文本不能为空"})

    session, lock, err = await _get_valid_session(sid)
    if err:
        return err

    await lock.acquire()
    need_close = False
    send_error = None
    result_json = None
    try:
        if session.get("closed"):
            result_json = json.dumps({"ok": False, "error": "会话已关闭"})
            return result_json

        _update_activity(session)
        child = session["child"]
        lines = text.splitlines()
        for i, line in enumerate(lines):
            try:
                await asyncio.to_thread(child.sendline, line)
            except Exception as e:
                send_error = f"发送第 {i+1} 行时失败: {e}（前 {i} 行已发送）"
                need_close = True
                logger.error("会话 %s 多行发送失败于第 %d 行: %s", sid, i+1, e)
                break

        if not need_close and not child.isalive():
            need_close = True

        if send_error is None:
            result_json = json.dumps({"ok": True})
        else:
            result_json = json.dumps({"ok": False, "error": send_error})
    finally:
        lock.release()

    if need_close:
        await _shutdown_session(sid)
    return result_json


async def terminal_send_key(sid: str, key: str) -> str:
    """
    发送特殊按键。
    支持的按键: ctrl_c, ctrl_d, ctrl_z, ctrl_a, ctrl_e, ctrl_l,
               ctrl_u, ctrl_k, ctrl_w, up, down, left, right,
               home, end, pageup, pagedown, insert, delete,
               f1-f12, escape, tab, enter, backspace
    """
    key = key.lower().strip()
    if key not in KEY_MAP:
        return json.dumps({"ok": False, "error": f"不支持的按键: {key}，支持: {list(KEY_MAP.keys())}"})

    session, lock, err = await _get_valid_session(sid)
    if err:
        return err

    await lock.acquire()
    need_close = False
    send_error = None
    result_json = None
    try:
        if session.get("closed"):
            result_json = json.dumps({"ok": False, "error": "会话已关闭"})
            return result_json

        _update_activity(session)
        child = session["child"]
        action, value = KEY_MAP[key]
        try:
            if action == "control":
                await asyncio.to_thread(child.sendcontrol, value)
            else:
                await asyncio.to_thread(child.send, value)
        except Exception as e:
            send_error = str(e)
            need_close = True
            logger.error("会话 %s 发送按键 %s 失败: %s", sid, key, e)

        if not need_close and not child.isalive():
            need_close = True

        if send_error is None:
            result_json = json.dumps({"ok": True})
        else:
            result_json = json.dumps({"ok": False, "error": send_error})
    finally:
        lock.release()

    if need_close:
        await _shutdown_session(sid)
    return result_json


async def terminal_send_raw(sid: str, hex_bytes: str) -> str:
    """
    发送原始字节（十六进制字符串），例如 "1b5b41" 发送 ESC [ A。
    """
    if not hex_bytes:
        return json.dumps({"ok": False, "error": "十六进制字符串不能为空"})
    try:
        raw = bytes.fromhex(hex_bytes)
    except ValueError:
        return json.dumps({"ok": False, "error": "无效的十六进制字符串"})

    session, lock, err = await _get_valid_session(sid)
    if err:
        return err

    await lock.acquire()
    need_close = False
    send_error = None
    result_json = None
    try:
        if session.get("closed"):
            result_json = json.dumps({"ok": False, "error": "会话已关闭"})
            return result_json

        _update_activity(session)
        child = session["child"]
        try:
            await asyncio.to_thread(child.send, raw)
        except Exception as e:
            send_error = str(e)
            need_close = True
            logger.error("会话 %s 发送原始字节失败: %s", sid, e)

        if not need_close and not child.isalive():
            need_close = True

        if send_error is None:
            result_json = json.dumps({"ok": True})
        else:
            result_json = json.dumps({"ok": False, "error": send_error})
    finally:
        lock.release()

    if need_close:
        await _shutdown_session(sid)
    return result_json


async def terminal_read(sid: str) -> str:
    """
    读取会话中当前可用的所有输出（非阻塞，一次性完整读取）。
    若进程已结束，eof 将为 true，但会话不会自动关闭。
    调用者应在确认不再需要会话后手动调用 terminal_close 释放资源。
    返回 JSON: {ok, output, eof}
    """
    session, lock, err = await _get_valid_session(sid)
    if err:
        return err

    async with lock:
        if session.get("closed"):
            return json.dumps({"ok": False, "error": "会话已关闭"})
        _update_activity(session)
        child = session["child"]
        data, eof = await _read_available(child, max_wait=0.0)
        output = data.decode("utf-8", errors="replace")
        return json.dumps({
            "ok": True,
            "output": _truncate_output(output),
            "eof": eof
        }, ensure_ascii=False)


async def terminal_expect(
    sid: str,
    patterns: Union[str, List[str]],
    timeout: int = 10
) -> str:
    """
    等待输出中出现指定正则模式（字符串或字符串列表，至少一个模式）。
    返回 JSON: {ok, matched, matched_index? (仅匹配时), output, eof}
    """
    try:
        pat_list = _validate_and_get_patterns(patterns)
    except ValueError as e:
        return json.dumps({"ok": False, "error": str(e)})

    session, lock, err = await _get_valid_session(sid)
    if err:
        return err

    await lock.acquire()
    need_close = False
    result_json = None
    try:
        if session.get("closed"):
            result_json = json.dumps({"ok": False, "error": "会话已关闭"})
            return result_json

        _update_activity(session)
        child = session["child"]
        full_patterns = pat_list + [pexpect.TIMEOUT, pexpect.EOF]

        def _expect():
            try:
                idx = child.expect(full_patterns, timeout=timeout)
                before = child.before or b""
                after = child.after or b""
                return idx, before, after
            except pexpect.ExceptionPexpect as e:
                return -1, b"", str(e).encode()
            except Exception as e:
                return -1, b"", str(e).encode()

        idx, before, after = await asyncio.to_thread(_expect)

        if 0 <= idx < len(pat_list):
            output = (before + after).decode("utf-8", errors="replace")
            result_json = json.dumps({
                "ok": True,
                "matched": True,
                "matched_index": idx,
                "output": _truncate_output(output),
                "eof": False
            }, ensure_ascii=False)
        elif idx == len(pat_list):        # TIMEOUT
            output = before.decode("utf-8", errors="replace")
            result_json = json.dumps({
                "ok": True,
                "matched": False,
                "output": _truncate_output(output),
                "eof": False
            }, ensure_ascii=False)
        elif idx == len(pat_list) + 1:    # EOF
            output = before.decode("utf-8", errors="replace")
            need_close = True
            result_json = json.dumps({
                "ok": True,
                "matched": False,
                "output": _truncate_output(output),
                "eof": True
            }, ensure_ascii=False)
        else:                             # 异常
            error_msg = after.decode("utf-8", errors="replace") if after else "未知错误"
            result_json = json.dumps({"ok": False, "error": f"expect 执行失败: {error_msg}"})
    finally:
        lock.release()

    if need_close:
        await _shutdown_session(sid)
    return result_json


async def terminal_exec(
    sid: str,
    command: str,
    expect_map: Optional[Union[str, Dict[str, str]]] = None,
    enable_defaults: bool = True,
    timeout: int = 30
) -> str:
    """
    发送命令并自动应答提示。
    expect_map: JSON 字符串或字典，如 {"[Y/n]": "y", "password:": "mypass"}
    enable_defaults: 是否启用内置分页器处理（--More-- 等），默认为 True。
                    内置分页器发送时不附加换行，用户自定义提示仍然附加换行。
    timeout: 总等待超时（秒）。
    返回全部累积输出。
    """
    # 解析 expect_map
    if isinstance(expect_map, str):
        try:
            user_patterns = json.loads(expect_map)
        except json.JSONDecodeError:
            return json.dumps({"ok": False, "error": "expect_map JSON 格式错误"})
    elif isinstance(expect_map, dict):
        user_patterns = expect_map
    elif expect_map is None:
        user_patterns = {}
    else:
        return json.dumps({"ok": False, "error": "expect_map 必须是 JSON 字符串或字典"})

    if not isinstance(user_patterns, dict):
        return json.dumps({"ok": False, "error": "expect_map 必须是对象"})

    for k in user_patterns:
        if not isinstance(k, str):
            return json.dumps({"ok": False, "error": "expect_map 的键必须为字符串"})

    # 合并默认分页器模式（如果启用）
    patterns_dict = {}
    is_default = {}  # 记录哪些模式是来自默认的（未被用户覆盖）
    if enable_defaults:
        patterns_dict.update(DEFAULT_PAGER_PATTERNS)
        for k in DEFAULT_PAGER_PATTERNS:
            is_default[k] = True
    # 用户模式会覆盖默认模式
    patterns_dict.update(user_patterns)
    for k in user_patterns:
        is_default[k] = False

    compiled_patterns = [re.escape(k) for k in patterns_dict]
    responses = [patterns_dict[k] for k in patterns_dict]
    is_default_list = [is_default[k] for k in patterns_dict]  # 与模式同序

    session, lock, err = await _get_valid_session(sid)
    if err:
        return err

    await lock.acquire()
    need_close = False
    send_error = None
    result_json = None
    try:
        if session.get("closed"):
            result_json = json.dumps({"ok": False, "error": "会话已关闭"})
            return result_json

        _update_activity(session)
        child = session["child"]

        # 发送命令
        try:
            await asyncio.to_thread(child.sendline, command)
        except Exception as e:
            send_error = str(e)
            need_close = True
            logger.error("会话 %s 发送命令失败: %s", sid, e)

        if not send_error:
            # 命令发送成功，进入交互循环
            all_output = b""
            start_time = time.time()
            while time.time() - start_time < timeout:
                idx = await asyncio.to_thread(
                    child.expect,
                    compiled_patterns + [pexpect.TIMEOUT, pexpect.EOF],
                    timeout=5
                )
                if idx < len(compiled_patterns):
                    match_output = child.before + child.after
                    all_output += match_output
                    response_text = responses[idx]
                    try:
                        if is_default_list[idx]:
                            # 内置分页器：发送无换行符的按键
                            await asyncio.to_thread(child.send, response_text)
                        else:
                            # 用户自定义：使用 sendline
                            await asyncio.to_thread(child.sendline, response_text)
                    except Exception as e:
                        all_output += f"\n[自动回复失败: {e}]".encode()
                        logger.error("会话 %s 自动回复失败: %s", sid, e)
                        break
                    _update_activity(session)
                elif idx == len(compiled_patterns):   # TIMEOUT
                    all_output += child.before or b""
                    break
                else:                                 # EOF
                    all_output += child.before or b""
                    break

            # 捕获残留输出
            rest_data, _ = await _read_available(child, max_wait=0.5)
            all_output += rest_data

            eof = not child.isalive()
            if eof:
                need_close = True
            output = all_output.decode("utf-8", errors="replace")
            result_json = json.dumps({
                "ok": True,
                "output": _truncate_output(output),
                "eof": eof
            }, ensure_ascii=False)
        else:
            # 命令发送失败，收集可能已产生的输出
            rest_data, _ = await _read_available(child, max_wait=0.5)
            output = rest_data.decode("utf-8", errors="replace")
            result_json = json.dumps({
                "ok": False,
                "error": f"发送命令失败: {send_error}",
                "output": _truncate_output(output),
                "eof": True
            }, ensure_ascii=False)
    finally:
        lock.release()

    if need_close:
        await _shutdown_session(sid)
    return result_json


async def terminal_exitstatus(sid: str) -> str:
    """
    获取子进程退出状态码（仅当进程已结束时有效）。
    返回 JSON: {ok, exited, exitstatus, signalstatus}
    """
    session, lock, err = await _get_valid_session(sid)
    if err:
        return err

    async with lock:
        if session.get("closed"):
            return json.dumps({"ok": False, "error": "会话已关闭"})
        child = session["child"]
        if child.isalive():
            return json.dumps({"ok": False, "error": "进程尚未结束"})
        return json.dumps({
            "ok": True,
            "exited": child.exitstatus is not None,
            "exitstatus": child.exitstatus,
            "signalstatus": child.signalstatus
        })


async def terminal_set_winsize(sid: str, rows: int, cols: int) -> str:
    """
    设置终端窗口大小（行数、列数）。
    """
    if rows <= 0 or cols <= 0:
        return json.dumps({"ok": False, "error": "行数和列数必须为正整数"})

    session, lock, err = await _get_valid_session(sid)
    if err:
        return err

    async with lock:
        if session.get("closed"):
            return json.dumps({"ok": False, "error": "会话已关闭"})
        _update_activity(session)
        child = session["child"]
        try:
            await asyncio.to_thread(child.setwinsize, rows, cols)
            return json.dumps({"ok": True})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})


async def terminal_close(sid: str) -> str:
    """主动关闭会话并释放资源"""
    try:
        await _shutdown_session(sid)
        return json.dumps({"ok": True})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})