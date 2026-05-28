import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Type
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, Page, Playwright, BrowserContext

log = logging.getLogger("mcp_server")

# ---------- 配置加载 ----------
CONFIG_PATH = Path(__file__).parent / "data" / "config.json"
_CONFIG: Dict[str, Any] = {}
_SEARCH_ENGINES: Dict[str, Any] = {}
_DEFAULT_ENGINE: str = "bing"
_PARSER_CLASS_WHITELIST = {"BingParser", "DuckDuckGoParser"}

def _calc_offset(engine_name: str, page: int) -> int:
    if engine_name == "bing":
        return (page - 1) * 10 + 1
    elif engine_name == "duckduckgo":
        return (page - 1) * 30
    return (page - 1) * 10 + 1

def _validate_config(config: Dict[str, Any]) -> Dict[str, Any]:
    default_config = {
        "search_engines": {
            "bing": {
                "url_template": "https://www.bing.com/search?q={query}&first={offset}",
                "parser_class": "BingParser",
                "enabled": True,
                "page_limit": 50,
                "delay_between_requests": 1.0
            },
            "duckduckgo": {
                "url_template": "https://html.duckduckgo.com/html/?q={query}&s={offset}",
                "parser_class": "DuckDuckGoParser",
                "enabled": True,
                "page_limit": 20,
                "delay_between_requests": 1.0
            }
        },
        "default_engine": "bing",
        "global": {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "request_timeout": 10,
            "max_retries": 3,
            "retry_backoff_base": 1.0,
            "playwright_timeout": 30000,
            "max_concurrent_pages": 5,
            "rate_limit_interval": 1.0
        }
    }
    for key, val in default_config.items():
        if key not in config:
            config[key] = val
    for name in default_config["search_engines"]:
        if name not in config.get("search_engines", {}):
            config["search_engines"][name] = default_config["search_engines"][name].copy()
        else:
            eng = config["search_engines"][name]
            for opt, default_val in default_config["search_engines"][name].items():
                if opt not in eng:
                    eng[opt] = default_val
            # 白名单校验 parser_class
            if eng.get("parser_class") not in _PARSER_CLASS_WHITELIST:
                log.warning("非法 parser_class: %s，重置为默认值", eng.get("parser_class"))
                eng["parser_class"] = default_config["search_engines"][name]["parser_class"]
            # 校验 url_template
            url = eng.get("url_template", "")
            if not (url.startswith("https://") and "://" in url and len(url) > 8):
                log.warning("url_template 无效: %s，重置为默认值", url)
                eng["url_template"] = default_config["search_engines"][name]["url_template"]
            # 校验数值字段
            page_limit = eng.get("page_limit", 1)
            if not isinstance(page_limit, int) or page_limit < 1:
                eng["page_limit"] = default_config["search_engines"][name]["page_limit"]
            delay = eng.get("delay_between_requests", 0)
            if not isinstance(delay, (int, float)) or delay < 0:
                eng["delay_between_requests"] = default_config["search_engines"][name]["delay_between_requests"]
    if config.get("default_engine") not in config["search_engines"]:
        config["default_engine"] = default_config["default_engine"]
    return config

def _get_default_config() -> Dict[str, Any]:
    default = {
        "search_engines": {
            "bing": {
                "url_template": "https://www.bing.com/search?q={query}&first={offset}",
                "parser_class": "BingParser",
                "enabled": True,
                "page_limit": 50,
                "delay_between_requests": 1.0
            },
            "duckduckgo": {
                "url_template": "https://html.duckduckgo.com/html/?q={query}&s={offset}",
                "parser_class": "DuckDuckGoParser",
                "enabled": True,
                "page_limit": 20,
                "delay_between_requests": 1.0
            }
        },
        "default_engine": "bing",
        "global": {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "request_timeout": 10,
            "max_retries": 3,
            "retry_backoff_base": 1.0,
            "playwright_timeout": 30000,
            "max_concurrent_pages": 5,
            "rate_limit_interval": 1.0
        }
    }
    return _validate_config(default)

def _load_config(reload: bool = False) -> None:
    global _CONFIG, _SEARCH_ENGINES, _DEFAULT_ENGINE
    if not reload and _CONFIG:
        return
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            _CONFIG = _validate_config(cfg)
        else:
            log.warning("配置文件不存在: %s，使用后备配置", CONFIG_PATH)
            _CONFIG = _get_default_config()
        _SEARCH_ENGINES = _CONFIG.get("search_engines", {})
        _DEFAULT_ENGINE = _CONFIG.get("default_engine", "bing")
        # 如果浏览器已启动，热重载可能无法应用新 UA/并发数，记录警告
        if reload and (_browser is not None):
            log.warning("配置已重载，但浏览器实例已存在，新的 UA/并发数等参数不会生效，请重启服务器或调用 shutdown_browser")
    except Exception as e:
        log.error("加载配置文件失败: %s，使用后备配置", e)
        _CONFIG = _get_default_config()
        _SEARCH_ENGINES = _CONFIG.get("search_engines", {})
        _DEFAULT_ENGINE = _CONFIG.get("default_engine", "bing")

_load_config()

def reload_config() -> None:
    _load_config(reload=True)
    log.info("配置已重载")

# ---------- 搜索引擎解析器 ----------
class SearchEngineParser:
    @staticmethod
    def parse(html: str) -> List[Dict[str, str]]:
        raise NotImplementedError

class BingParser(SearchEngineParser):
    @staticmethod
    def parse(html: str) -> List[Dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        results = []
        for li in soup.select("li.b_algo"):
            title_tag = li.find("h2")
            if not title_tag:
                continue
            a_tag = title_tag.find("a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            link = a_tag.get("href")
            snippet_tag = li.find("p")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
            if title and link:
                results.append({"title": title, "link": link, "snippet": snippet})
        if not results and html.strip():
            log.warning("Bing 解析结果为空，HTML 片段: %s", html[:500])
        return results

class DuckDuckGoParser(SearchEngineParser):
    @staticmethod
    def parse(html: str) -> List[Dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        results = []
        for article in soup.select("article"):
            title_tag = article.find("a", class_="result__a")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            link = title_tag.get("href")
            snippet_tag = article.find("a", class_="result__snippet")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
            if title and link:
                results.append({"title": title, "link": link, "snippet": snippet})
        if not results and html.strip():
            log.warning("DuckDuckGo 解析结果为空，HTML 片段: %s", html[:500])
        return results

_PARSER_MAP = {
    "BingParser": BingParser,
    "DuckDuckGoParser": DuckDuckGoParser,
}

def get_parser(parser_class_name: str) -> Type[SearchEngineParser]:
    return _PARSER_MAP[parser_class_name]

# ---------- 全局浏览器管理器（支持共享上下文或隔离上下文）---------
_browser: Optional[Browser] = None
_playwright: Optional[Playwright] = None
_shared_context: Optional[BrowserContext] = None
_page_semaphore: asyncio.Semaphore = None
_browser_lock = asyncio.Lock()
_browser_ua: str = ""

async def _init_browser() -> Browser:
    global _browser, _playwright, _shared_context, _page_semaphore, _browser_ua
    async with _browser_lock:
        if _browser is None:
            _browser_ua = _CONFIG.get("global", {}).get("user_agent", "Mozilla/5.0")
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(headless=True)
            _shared_context = await _browser.new_context(user_agent=_browser_ua)
            max_pages = _CONFIG.get("global", {}).get("max_concurrent_pages", 5)
            _page_semaphore = asyncio.Semaphore(max_pages)
            log.info("全局浏览器已启动，共享上下文，最大并发页面数: %d", max_pages)
        return _browser

async def shutdown_browser() -> None:
    global _browser, _playwright, _shared_context, _page_semaphore
    async with _browser_lock:
        if _shared_context:
            await _shared_context.close()
            _shared_context = None
        if _browser:
            await _browser.close()
            _browser = None
        if _playwright:
            await _playwright.stop()
            _playwright = None
        _page_semaphore = None
        log.info("全局浏览器已关闭")

async def _get_page(isolated: bool = False) -> tuple[Page, Optional[BrowserContext]]:
    """返回 (page, context)，如果 isolated=True，则 context 为新建的独立上下文，需要在释放时关闭"""
    await _init_browser()
    await _page_semaphore.acquire()
    try:
        if isolated:
            context = await _browser.new_context(user_agent=_browser_ua)
            page = await context.new_page()
            timeout = _CONFIG.get("global", {}).get("playwright_timeout", 30000)
            page.set_default_timeout(timeout)
            return page, context
        else:
            page = await _shared_context.new_page()
            timeout = _CONFIG.get("global", {}).get("playwright_timeout", 30000)
            page.set_default_timeout(timeout)
            return page, None
    except Exception:
        _page_semaphore.release()
        raise

async def _release_page(page: Page, context: Optional[BrowserContext] = None) -> None:
    try:
        await page.close()
        if context:
            await context.close()
    except Exception as e:
        log.warning("关闭页面/上下文时出错: %s", e)
    finally:
        _page_semaphore.release()

# ---------- 全局速率限制器（锁外 sleep）---------
_last_request_time = 0.0
_request_rate_lock = asyncio.Lock()

async def _rate_limit():
    interval = _CONFIG.get("global", {}).get("rate_limit_interval", 1.0)
    if interval <= 0:
        return
    global _last_request_time
    async with _request_rate_lock:
        now = time.monotonic()
        next_allowed = _last_request_time + interval
        if now >= next_allowed:
            _last_request_time = now
            return
        _last_request_time = next_allowed
    # 锁外 sleep
    await asyncio.sleep(next_allowed - now)

# ---------- HTTP 客户端复用 ----------
_http_client: Optional[httpx.AsyncClient] = None
_http_client_lock = asyncio.Lock()

async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    async with _http_client_lock:
        if _http_client is None or _http_client.is_closed:
            timeout = httpx.Timeout(_CONFIG.get("global", {}).get("request_timeout", 10))
            _http_client = httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                proxy=os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY"),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
            )
        return _http_client

async def _fetch_html(url: str) -> Optional[str]:
    await _rate_limit()
    cfg = _CONFIG.get("global", {})
    max_retries = cfg.get("max_retries", 3)
    backoff_base = cfg.get("retry_backoff_base", 1.0)
    headers = {"User-Agent": cfg.get("user_agent", "Mozilla/5.0")}
    for attempt in range(max_retries):
        try:
            client = await _get_http_client()
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text
        except httpx.TimeoutException:
            log.warning("请求超时 (尝试 %d/%d): %s", attempt+1, max_retries, url[:100])
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(backoff_base * (2 ** attempt))
        except Exception as e:
            safe_url = url[:100] + "..." if len(url) > 100 else url
            log.error("请求失败 (尝试 %d/%d): %s - %s", attempt+1, max_retries, safe_url, e)
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(backoff_base * (2 ** attempt))
    return None

# ---------- 搜索引擎核心搜索 ----------
async def _search_engine(
    engine_name: str,
    keyword: str,
    page: int = 1,
    max_results: int = 20
) -> Dict[str, Any]:
    engine_cfg = _SEARCH_ENGINES.get(engine_name)
    if not engine_cfg or not engine_cfg.get("enabled", False):
        return {
            "status": "error",
            "error_type": "engine_disabled",
            "error": f"引擎 '{engine_name}' 不可用或已禁用",
            "engine": engine_name
        }

    page_limit = engine_cfg.get("page_limit", 50)
    if page < 1:
        page = 1
    if page > page_limit:
        log.warning("请求页码 %d 超过限制 %d，已截断", page, page_limit)
        page = page_limit

    url_template = engine_cfg.get("url_template")
    parser_class_name = engine_cfg.get("parser_class")
    if not url_template or not parser_class_name:
        return {"status": "error", "error_type": "config_error", "error": f"引擎 {engine_name} 配置无效"}

    offset = _calc_offset(engine_name, page)
    url = url_template.format(query=quote_plus(keyword), offset=offset)

    # 引擎特定额外延迟（可选）
    delay = engine_cfg.get("delay_between_requests", 0)
    if delay > 0:
        jitter = random.uniform(0, delay * 0.5)
        await asyncio.sleep(delay + jitter)

    html = await _fetch_html(url)
    if not html:
        return {"status": "error", "error_type": "network_error", "error": "获取搜索结果失败"}

    try:
        Parser = get_parser(parser_class_name)
        results = Parser.parse(html)
    except Exception as e:
        log.exception("解析 HTML 异常: %s", e)
        return {"status": "error", "error_type": "parse_error", "error": f"解析结果失败: {str(e)}"}

    results = results[:max_results]
    return {
        "status": "success",
        "engine": engine_name,
        "keyword": keyword,
        "page": page,
        "results": results,
        "count": len(results)
    }

# ---------- 公开接口 ----------
async def search_keyword(
    keyword: str,
    engine: str = None,
    page: int = 1,
    max_results: int = 20
) -> Dict[str, Any]:
    if not keyword:
        return {"status": "error", "error_type": "invalid_param", "error": "keyword is required"}

    requested_engine = engine or _DEFAULT_ENGINE
    actual_engine = requested_engine
    fallback = False
    fallback_message = None

    engine_cfg = _SEARCH_ENGINES.get(requested_engine)
    if not engine_cfg or not engine_cfg.get("enabled", False):
        fallback = True
        actual_engine = _DEFAULT_ENGINE
        fallback_message = f"引擎 '{requested_engine}' 不可用，已降级至默认引擎 '{actual_engine}'"
        log.warning(fallback_message)

    result = await _search_engine(actual_engine, keyword, page, max_results)
    if fallback:
        result["fallback"] = True
        result["fallback_message"] = fallback_message
    return result

async def batch_search(
    keywords: List[str],
    engine: str = None,
    page: int = 1,
    max_results: int = 20
) -> Dict[str, Any]:
    if not keywords:
        return {"status": "error", "error_type": "invalid_param", "error": "keywords list is empty"}

    tasks = [search_keyword(kw, engine, page, max_results) for kw in keywords]
    results = await asyncio.gather(*tasks)
    combined = {kw: res for kw, res in zip(keywords, results)}
    return {"status": "success", "results": combined}

async def fetch_url_deep(
    url: str,
    max_length: int = 5000,
    render_timeout: int = 2000,
    wait_for_selector: str = None,
    isolated: bool = False
) -> Dict[str, Any]:
    """
    深度爬取单个网址。
    :param url: 目标网址
    :param max_length: 返回文本的最大字符数
    :param render_timeout: 若指定 wait_for_selector，则为等待该选择器的超时时间（毫秒）；否则为页面加载后额外等待时间（毫秒）
    :param wait_for_selector: 可选，等待某个 CSS 选择器出现后再提取内容
    :param isolated: 是否使用独立的浏览器上下文（隔离 Cookie/存储），默认 False（共享上下文）
    """
    if not url:
        return {"status": "error", "error_type": "invalid_param", "error": "url is required"}

    result = {"status": "success", "url": url, "content": "", "error": None, "original_length": 0}
    page = None
    context = None
    try:
        page, context = await _get_page(isolated=isolated)
        goto_timeout = _CONFIG.get("global", {}).get("playwright_timeout", 30000)
        await page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout)
        if wait_for_selector:
            try:
                await page.wait_for_selector(wait_for_selector, timeout=render_timeout)
            except Exception as e:
                log.warning("等待选择器 %s 超时: %s", wait_for_selector, e)
        elif render_timeout > 0:
            await page.wait_for_timeout(render_timeout)

        try:
            text = await page.evaluate("""
                () => {
                    const body = document.body;
                    if (!body) return '';
                    return body.innerText || body.textContent || '';
                }
            """)
        except Exception as e:
            log.debug("page.evaluate 失败: %s，降级使用 HTML 解析", e)
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator="\n", strip=True)

        text = text.strip()
        original_len = len(text)
        result["original_length"] = original_len
        if not text:
            result["error"] = "No text content extracted"
            result["error_type"] = "no_content"
        else:
            if len(text) > max_length:
                text = text[:max_length] + f"\n... [截断]"
            result["content"] = text
    except Exception as e:
        safe_url = url[:100] + "..." if len(url) > 100 else url
        log.exception("深度爬取失败: %s", safe_url)
        result["status"] = "error"
        result["error_type"] = "crawl_error"
        result["error"] = str(e)
    finally:
        if page:
            await _release_page(page, context)
    return result

async def batch_fetch_urls(
    urls: List[str],
    max_length: int = 5000,
    max_concurrent: int = 3,
    render_timeout: int = 2000,
    wait_for_selector: str = None,
    isolated: bool = False
) -> Dict[str, Any]:
    if not urls:
        return {"status": "error", "error_type": "invalid_param", "error": "urls list is empty"}

    semaphore = asyncio.Semaphore(max_concurrent)
    async def fetch_one(url: str):
        async with semaphore:
            return url, await fetch_url_deep(url, max_length, render_timeout, wait_for_selector, isolated)
    tasks = [fetch_one(url) for url in urls]
    results_list = await asyncio.gather(*tasks)
    results_dict = {url: result for url, result in results_list}
    return {"status": "success", "results": results_dict, "total": len(results_dict)}