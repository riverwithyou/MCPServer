"""
向量存储模块 - 生产版 (Linux)
- 多用户隔离（PBKDF2-SHA256 600k 迭代），私有/共享集合
- 本地嵌入模型（Qwen3-Embedding-0.6B，CPU）
- 跨进程文件锁 + 读写锁，配额/限流/并发控制
- 共享集合角色控制（reader/writer）
- 仅支持单进程部署（ChromaDB PersistentClient 限制）
- 修复：删除用户彻底清理残留，创建用户冲突重试
"""

import asyncio
import fcntl
import gc
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
import logging
import secrets

import aiofiles
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

# 模型名称（注意需要与目录同名）
MODEL_NAME = "Qwen3-Embedding-0.6B"

# ========== 路径配置（支持环境变量覆盖） ==========
BASE_DIR = Path(__file__).parent
# 允许通过 VECTOR_STORE_DATA_ROOT 整体重定向数据根目录
DATA_ROOT = Path(os.getenv("VECTOR_STORE_DATA_ROOT", BASE_DIR / "data"))
DATA_DIR = DATA_ROOT
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MODEL_PATH = str(DATA_DIR / "models" / MODEL_NAME)
LOCAL_MODEL_PATH = os.getenv("VECTOR_MODEL_PATH", DEFAULT_MODEL_PATH)
MODEL_CACHE_DIR = str(DATA_DIR / "models")

VECTORDB_ROOT = DATA_DIR / "vectordb"
VECTORDB_ROOT.mkdir(parents=True, exist_ok=True)

SHARED_COLLECTIONS_ROOT = VECTORDB_ROOT / "_shared"
SHARED_COLLECTIONS_ROOT.mkdir(parents=True, exist_ok=True)
SHARED_CONFIG_FILE = VECTORDB_ROOT / "_shared_collections.json"

# ========== 校验规则 ==========
ALLOWED_USERNAME_REGEX = r'^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,31}$'
ALLOWED_COLLECTION_REGEX = r'^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$'

MAX_DOCS_PER_REQUEST = 1000
MAX_DOC_LENGTH = 10000
MAX_METADATA_SIZE_BYTES = 1024 * 10
MAX_TOP_K = 100
DEFAULT_TOP_K = 10

MAX_DOCS_PER_USER = 100_000
MAX_DOCS_PER_SHARED_COLLECTION = 500_000
QUOTA_CACHE_TTL = 5

RATE_LIMIT_CAPACITY = 60
RATE_LIMIT_REFILL_RATE = 1.0

MAX_GLOBAL_EMBEDDINGS = 4
MAX_USER_EMBEDDINGS = 2

LOCK_TIMEOUT = 30
TMP_FILE_MAX_AGE = 3600

# ========== 日志 ==========
logger = logging.getLogger("vector_store")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# ========== 工具函数 ==========
def validate_username(username: str) -> bool:
    return bool(re.fullmatch(ALLOWED_USERNAME_REGEX, username))

def validate_collection_name(name: str) -> bool:
    return bool(re.fullmatch(ALLOWED_COLLECTION_REGEX, name))

def validate_where_filter(where: Dict[str, Any], max_depth: int = 3) -> bool:
    key_count = 0
    def _validate(obj, depth):
        nonlocal key_count
        if depth > max_depth:
            return False
        if not isinstance(obj, dict):
            return False
        for key, value in obj.items():
            key_count += 1
            if key_count > 20:
                return False
            if key.startswith("$"):
                return False
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
                return False
            if isinstance(value, dict):
                for op, op_val in value.items():
                    if op not in {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin"}:
                        return False
                    if op in ("$in", "$nin"):
                        if not isinstance(op_val, list) or len(op_val) > 10:
                            return False
                    elif not isinstance(op_val, (str, int, float, bool)):
                        return False
                if not _validate(value, depth + 1):
                    return False
            else:
                if not isinstance(value, (str, int, float, bool)):
                    return False
        return True
    return _validate(where, 1)

def validate_documents(documents: List[str], metadatas: Optional[List[Dict]] = None) -> Tuple[bool, str]:
    if not isinstance(documents, list) or not documents:
        return False, "文档列表不能为空"
    if not all(isinstance(d, str) for d in documents):
        return False, "文档列表包含非字符串元素"
    if len(documents) > MAX_DOCS_PER_REQUEST:
        return False, f"单次最多 {MAX_DOCS_PER_REQUEST} 篇文档"
    if metadatas:
        if not isinstance(metadatas, list) or not all(isinstance(m, dict) for m in metadatas):
            return False, "元数据列表格式无效"
        if len(metadatas) != len(documents):
            return False, "metadatas 长度与 documents 不一致"
    for i, doc in enumerate(documents):
        if len(doc) > MAX_DOC_LENGTH:
            return False, f"第 {i} 篇文档超过最大长度 {MAX_DOC_LENGTH}"
    if metadatas:
        for i, md in enumerate(metadatas):
            try:
                if len(json.dumps(md).encode()) > MAX_METADATA_SIZE_BYTES:
                    return False, f"第 {i} 篇元数据过大"
            except Exception:
                return False, f"第 {i} 篇元数据序列化失败"
    return True, ""

# ========== 密码工具 ==========
PW_ITERATIONS = 600_000

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), PW_ITERATIONS)
    return f"{salt}:{dk.hex()}"

def verify_password(stored: str, provided: str) -> bool:
    try:
        salt, key = stored.split(':')
        dk = hashlib.pbkdf2_hmac('sha256', provided.encode(), salt.encode(), PW_ITERATIONS)
        return secrets.compare_digest(dk.hex(), key)
    except Exception:
        return False

# ========== 限流器（带自动清理） ==========
class TokenBucket:
    def __init__(self, capacity: float, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill = None
        self.lock = asyncio.Lock()
        self.last_access = time.monotonic()

    async def consume(self, tokens: float = 1.0) -> bool:
        async with self.lock:
            now = time.monotonic()
            self.last_access = now
            if self.last_refill is None:
                self.last_refill = now
                self.tokens = self.capacity
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

class RateLimiter:
    def __init__(self):
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task = None

    async def start_cleanup(self, ttl: float = 600.0):
        if self._cleanup_task is not None:
            return
        async def _run():
            while True:
                await asyncio.sleep(300)
                now = time.monotonic()
                async with self._lock:
                    expired = [u for u, b in self._buckets.items() if now - b.last_access > ttl]
                    for u in expired:
                        del self._buckets[u]
        self._cleanup_task = asyncio.create_task(_run())

    async def stop_cleanup(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def check(self, username: str) -> bool:
        async with self._lock:
            if username not in self._buckets:
                self._buckets[username] = TokenBucket(RATE_LIMIT_CAPACITY, RATE_LIMIT_REFILL_RATE)
            return await self._buckets[username].consume()

rate_limiter = RateLimiter()

# ========== 异步读写锁（带超时） ==========
class AsyncRWLock:
    def __init__(self):
        self._cond = asyncio.Condition()
        self._readers = 0
        self._writers_waiting = 0
        self._writer_active = False

    async def acquire_read(self, timeout: Optional[float] = LOCK_TIMEOUT):
        deadline = time.monotonic() + timeout if timeout else None
        async with self._cond:
            while self._writers_waiting > 0 or self._writer_active:
                if deadline and time.monotonic() >= deadline:
                    raise asyncio.TimeoutError("读取锁获取超时")
                await asyncio.wait_for(self._cond.wait(), timeout=deadline - time.monotonic() if deadline else None)
            self._readers += 1

    async def release_read(self):
        async with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    async def acquire_write(self, timeout: Optional[float] = LOCK_TIMEOUT):
        deadline = time.monotonic() + timeout if timeout else None
        async with self._cond:
            self._writers_waiting += 1
            try:
                while self._readers > 0 or self._writer_active:
                    if deadline and time.monotonic() >= deadline:
                        raise asyncio.TimeoutError("写入锁获取超时")
                    await asyncio.wait_for(self._cond.wait(), timeout=deadline - time.monotonic() if deadline else None)
                self._writer_active = True
            finally:
                self._writers_waiting -= 1

    async def release_write(self):
        async with self._cond:
            self._writer_active = False
            self._cond.notify_all()

@asynccontextmanager
async def read_lock(rwlock: AsyncRWLock):
    await rwlock.acquire_read()
    try:
        yield
    finally:
        await rwlock.release_read()

@asynccontextmanager
async def write_lock(rwlock: AsyncRWLock):
    await rwlock.acquire_write()
    try:
        yield
    finally:
        await rwlock.release_write()

# ========== 嵌入模型（无校验，直接加载） ==========
_embedding_model = None
_model_lock = asyncio.Lock()

_global_embed_sem = asyncio.Semaphore(MAX_GLOBAL_EMBEDDINGS)
_user_embed_sems: Dict[str, asyncio.Semaphore] = {}
_user_embed_sems_lock = asyncio.Lock()

async def get_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model
    async with _model_lock:
        if _embedding_model is not None:
            return _embedding_model
        _embedding_model = await asyncio.to_thread(
            SentenceTransformer,
            LOCAL_MODEL_PATH,
            cache_folder=MODEL_CACHE_DIR,
            local_files_only=True,
            device="cpu"
        )
        logger.info("本地嵌入模型已加载: %s", LOCAL_MODEL_PATH)
        return _embedding_model

async def compute_embeddings(texts: List[str], username: str = "default") -> List[List[float]]:
    async with _user_embed_sems_lock:
        if username not in _user_embed_sems:
            _user_embed_sems[username] = asyncio.Semaphore(MAX_USER_EMBEDDINGS)
        user_sem = _user_embed_sems[username]
    async with user_sem:
        async with _global_embed_sem:
            model = await get_model()
            return await asyncio.to_thread(model.encode, texts, show_progress_bar=False)

# ========== 共享集合管理器（角色 + 原子写入） ==========
class SharedCollectionManager:
    def __init__(self, config_file: Path):
        self.config_file = config_file
        self._lock = asyncio.Lock()
        self._loaded = False
        self._load_lock = asyncio.Lock()
        self._collections: Dict[str, Dict] = {}
        self._cleanup_task = None
        for tmp in config_file.parent.glob("*.tmp"):
            try:
                tmp.unlink()
            except Exception:
                pass

    async def _ensure_loaded(self):
        if self._loaded:
            return
        async with self._load_lock:
            if self._loaded:
                return
            await self._load()
            self._loaded = True

    async def _load(self):
        if self.config_file.exists():
            async with aiofiles.open(self.config_file, "r") as f:
                self._collections = json.loads(await f.read())
        else:
            self._collections = {}

    async def _save(self):
        data = self._collections.copy()
        await asyncio.to_thread(self._sync_save, data)

    def _sync_save(self, data: dict):
        lock_fd = None
        tmp_path = None
        try:
            lock_fd = open(self.config_file.with_suffix(".lock"), "w")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            with tempfile.NamedTemporaryFile(mode='w', dir=self.config_file.parent,
                                             suffix='.tmp', delete=False) as tmp:
                tmp_path = Path(tmp.name)
                json.dump(data, tmp, indent=2)
            tmp_path.replace(self.config_file)
        except Exception:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass
            raise
        finally:
            if lock_fd:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()

    async def start_cleanup(self):
        async def _cleanup_loop():
            while True:
                await asyncio.sleep(600)
                for tmp in self.config_file.parent.glob("*.tmp"):
                    try:
                        if time.time() - tmp.stat().st_mtime > TMP_FILE_MAX_AGE:
                            tmp.unlink()
                    except Exception:
                        pass
        self._cleanup_task = asyncio.create_task(_cleanup_loop())

    async def close(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    # ---------- 角色控制 ----------
    def _has_write(self, collection_name: str, username: str) -> bool:
        coll = self._collections.get(collection_name)
        if not coll:
            return False
        members = coll.get("members", {})
        if username in members:
            return members[username].get("writer", False)
        return False

    async def check_write_access(self, collection_name: str, username: str) -> bool:
        await self._ensure_loaded()
        async with self._lock:
            return self._has_write(collection_name, username)

    async def check_read_access(self, collection_name: str, username: str) -> bool:
        await self._ensure_loaded()
        async with self._lock:
            coll = self._collections.get(collection_name)
            return coll is not None and username in coll.get("members", {})

    async def collection_exists(self, collection_name: str) -> bool:
        await self._ensure_loaded()
        async with self._lock:
            return collection_name in self._collections

    async def create_shared(self, collection_name: str, owner: str):
        await self._ensure_loaded()
        async with self._lock:
            if collection_name in self._collections:
                raise ValueError("集合已存在")
            self._collections[collection_name] = {
                "owner": owner,
                "members": {owner: {"writer": True}}
            }
            await self._save()

    async def delete_shared(self, collection_name: str, requester: str) -> bool:
        await self._ensure_loaded()
        async with self._lock:
            if self._collections.get(collection_name, {}).get("owner") != requester:
                return False
            del self._collections[collection_name]
            await self._save()
            return True

    async def transfer_ownership(self, collection_name: str, current_owner: str, new_owner: str) -> bool:
        await self._ensure_loaded()
        async with self._lock:
            coll = self._collections.get(collection_name)
            if not coll or coll["owner"] != current_owner:
                return False
            coll["owner"] = new_owner
            if new_owner not in coll["members"]:
                coll["members"][new_owner] = {"writer": True}
            else:
                coll["members"][new_owner]["writer"] = True
            await self._save()
            return True

    async def grant_access(self, collection_name: str, requester: str, target: str, writer: bool = True) -> bool:
        await self._ensure_loaded()
        async with self._lock:
            coll = self._collections.get(collection_name)
            if not coll or coll["owner"] != requester:
                return False
            if target not in coll["members"]:
                coll["members"][target] = {"writer": writer}
            else:
                coll["members"][target]["writer"] = writer
            await self._save()
            return True

    async def revoke_access(self, collection_name: str, requester: str, target: str) -> bool:
        await self._ensure_loaded()
        async with self._lock:
            coll = self._collections.get(collection_name)
            if not coll or coll["owner"] != requester:
                return False
            if target in coll["members"]:
                del coll["members"][target]
                await self._save()
            return True

    async def is_owner(self, collection_name: str, username: str) -> bool:
        await self._ensure_loaded()
        async with self._lock:
            coll = self._collections.get(collection_name)
            return coll is not None and coll["owner"] == username

    async def list_shared_collections(self, username: str) -> List[str]:
        await self._ensure_loaded()
        async with self._lock:
            return [name for name, coll in self._collections.items() if username in coll.get("members", {})]

    async def get_collections_owned_by(self, username: str) -> List[str]:
        await self._ensure_loaded()
        async with self._lock:
            return [name for name, coll in self._collections.items() if coll["owner"] == username]

    async def remove_user_from_all(self, username: str):
        await self._ensure_loaded()
        async with self._lock:
            modified = False
            for coll in self._collections.values():
                if username in coll.get("members", {}):
                    del coll["members"][username]
                    modified = True
            if modified:
                await self._save()

    async def atomic_delete_collection(self, collection_name: str, owner: str, delete_db_func):
        await self._ensure_loaded()
        async with self._lock:
            coll = self._collections.get(collection_name)
            if not coll or coll["owner"] != owner:
                raise ValueError("非所有者或集合不存在")
            backup = json.loads(json.dumps(self._collections))
            del self._collections[collection_name]
            try:
                await self._save()
                await delete_db_func()
            except Exception:
                self._collections = json.loads(json.dumps(backup))
                await self._save()
                raise

# ========== 主向量存储管理器 ==========
class VectorStoreManager:
    def __init__(self):
        self._chroma_clients: Dict[str, chromadb.PersistentClient] = {}
        self._client_locks: Dict[str, asyncio.Lock] = {}
        self._client_locks_lock = asyncio.Lock()
        self._private_collection_locks: Dict[Tuple[str, str], asyncio.Lock] = {}
        self._private_locks_lock = asyncio.Lock()
        self._shared_client = None
        self._shared_client_lock = asyncio.Lock()
        self._shared_collection_rwlocks: Dict[str, AsyncRWLock] = {}
        self._shared_locks_lock = asyncio.Lock()
        self._shared_manager = SharedCollectionManager(SHARED_CONFIG_FILE)
        self._quota_cache: Dict[str, Tuple[int, float]] = {}
        self._quota_cache_lock = asyncio.Lock()
        asyncio.create_task(self._init_background_tasks())

    async def _init_background_tasks(self):
        await rate_limiter.start_cleanup()
        await self._shared_manager.start_cleanup()

    # ---------- 认证 ----------
    def _user_auth_file(self, username: str) -> Path:
        return VECTORDB_ROOT / username / "auth.json"

    async def _store_password(self, username: str, password: str):
        auth_file = self._user_auth_file(username)
        auth_file.parent.mkdir(parents=True, exist_ok=True)
        hashed = hash_password(password)
        async with aiofiles.open(auth_file, "w") as f:
            await f.write(json.dumps({"password": hashed}))
        os.chmod(auth_file, 0o600)

    async def _verify_password(self, username: str, password: str) -> bool:
        auth_file = self._user_auth_file(username)
        if not auth_file.exists():
            return False
        try:
            async with aiofiles.open(auth_file, "r") as f:
                data = json.loads(await f.read())
            return verify_password(data.get("password", ""), password)
        except Exception:
            return False

    async def _check_rate_limit(self, username: str) -> Tuple[bool, str]:
        if not await rate_limiter.check(username):
            return False, "请求过于频繁，请稍后重试"
        return True, ""

    async def _get_user_doc_count(self, username: str) -> int:
        async with self._quota_cache_lock:
            now = time.monotonic()
            if username in self._quota_cache:
                count, ts = self._quota_cache[username]
                if now - ts < QUOTA_CACHE_TTL:
                    return count
        client = await self._get_client(username)
        def count_docs():
            total = 0
            for col in client.list_collections():
                total += col.count()
            return total
        count = await asyncio.to_thread(count_docs)
        async with self._quota_cache_lock:
            self._quota_cache[username] = (count, time.monotonic())
        return count

    async def _check_quota(self, username: str, additional: int) -> Tuple[bool, str]:
        current = await self._get_user_doc_count(username)
        if current + additional > MAX_DOCS_PER_USER:
            return False, f"用户文档数量已达上限（{MAX_DOCS_PER_USER}）"
        return True, ""

    async def _get_shared_collection_doc_count(self, collection_name: str) -> int:
        client = await self._get_shared_client()
        try:
            coll = client.get_collection(collection_name)
            return await asyncio.to_thread(coll.count)
        except ValueError:
            return 0

    async def _invalidate_quota_cache(self, username: str):
        async with self._quota_cache_lock:
            self._quota_cache.pop(username, None)

    # ---------- 客户端管理 ----------
    async def _get_client(self, username: str) -> chromadb.PersistentClient:
        if not validate_username(username):
            raise ValueError("无效的用户名")
        async with self._client_locks_lock:
            if username not in self._chroma_clients:
                self._client_locks[username] = asyncio.Lock()
                user_path = VECTORDB_ROOT / username
                user_path.mkdir(parents=True, exist_ok=True)
                self._chroma_clients[username] = chromadb.PersistentClient(
                    path=str(user_path),
                    settings=Settings(anonymized_telemetry=False)
                )
            return self._chroma_clients[username]

    async def _remove_client(self, username: str):
        async with self._client_locks_lock:
            self._chroma_clients.pop(username, None)
            self._client_locks.pop(username, None)

    async def _get_private_collection_lock(self, username: str, collection_name: str) -> asyncio.Lock:
        key = (username, collection_name)
        async with self._private_locks_lock:
            if key not in self._private_collection_locks:
                self._private_collection_locks[key] = asyncio.Lock()
            return self._private_collection_locks[key]

    async def _cleanup_private_lock(self, username: str, collection_name: str):
        key = (username, collection_name)
        async with self._private_locks_lock:
            self._private_collection_locks.pop(key, None)

    async def _get_shared_client(self) -> chromadb.PersistentClient:
        async with self._shared_client_lock:
            if self._shared_client is None:
                self._shared_client = chromadb.PersistentClient(
                    path=str(SHARED_COLLECTIONS_ROOT),
                    settings=Settings(anonymized_telemetry=False)
                )
            return self._shared_client

    async def _get_shared_collection_rwlock(self, collection_name: str) -> AsyncRWLock:
        async with self._shared_locks_lock:
            if collection_name not in self._shared_collection_rwlocks:
                self._shared_collection_rwlocks[collection_name] = AsyncRWLock()
            return self._shared_collection_rwlocks[collection_name]

    # ---------- 用户管理（修复删除残留和创建冲突） ----------
    async def create_user(self, username: str, password: str) -> Tuple[bool, str]:
        if not validate_username(username):
            return False, "用户名格式无效（字母数字开头，1-32位）"
        if len(password) < 6:
            return False, "密码长度至少6位"
        user_dir = VECTORDB_ROOT / username
        try:
            user_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return False, "用户已存在"
        try:
            await self._get_client(username)
            await self._store_password(username, password)
            client = await self._get_client(username)
            await asyncio.to_thread(client.create_collection, f"default_{username}")
            return True, "用户创建成功"
        except chromadb.errors.InternalError as e:
            logger.error("创建用户时集合已存在，可能有残留数据: %s", e)
            await self._remove_client(username)
            shutil.rmtree(user_dir, ignore_errors=True)
            return False, "用户数据残留，请稍后重试或联系管理员清理"
        except Exception:
            logger.exception("创建用户失败，回滚")
            await self._remove_client(username)
            shutil.rmtree(user_dir, ignore_errors=True)
            return False, "用户创建失败"

    async def delete_user(self, username: str, password: str,
                         transfer_to: Optional[str] = None) -> Tuple[bool, str]:
        if not await self._verify_password(username, password):
            return False, "密码错误"
        owned = await self._shared_manager.get_collections_owned_by(username)
        if owned:
            if not transfer_to:
                return False, f"请指定 transfer_to 转移共享集合：{', '.join(owned)}"
            if not (VECTORDB_ROOT / transfer_to).exists():
                return False, f"目标用户 {transfer_to} 不存在"
            for coll in owned:
                if not await self._shared_manager.transfer_ownership(coll, username, transfer_to):
                    return False, f"转移集合 {coll} 失败"

        user_dir = VECTORDB_ROOT / username
        if not user_dir.exists():
            return False, "用户不存在"

        await self._shared_manager.remove_user_from_all(username)

        # 移除客户端引用并强制清理
        await self._remove_client(username)
        gc.collect()
        await asyncio.sleep(0.1)

        # 重试删除目录，最多 3 次
        deleted = False
        for attempt in range(3):
            try:
                shutil.rmtree(user_dir)
                deleted = True
                break
            except OSError as e:
                logger.warning("删除用户目录失败 (尝试 %d/3): %s", attempt + 1, e)
                gc.collect()
                await asyncio.sleep(0.2 * (attempt + 1))

        if not deleted:
            logger.error("删除用户目录最终失败: %s", user_dir)
            return False, "删除用户目录失败，请手动清理"

        async with _user_embed_sems_lock:
            _user_embed_sems.pop(username, None)
        await self._invalidate_quota_cache(username)

        logger.info("用户 %s 已删除", username)
        return True, "用户已删除"

    async def change_password(self, username: str, old_password: str, new_password: str) -> Tuple[bool, str]:
        if not await self._verify_password(username, old_password):
            return False, "旧密码错误"
        if len(new_password) < 6:
            return False, "新密码长度至少6位"
        rate_ok, msg = await self._check_rate_limit(username)
        if not rate_ok:
            return False, msg
        await self._store_password(username, new_password)
        return True, "密码修改成功"

    # ---------- 私有集合操作 ----------
    async def list_private_collections(self, username: str, password: str) -> Dict:
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        try:
            client = await self._get_client(username)
            names = await asyncio.to_thread(lambda: [c.name for c in client.list_collections()])
            return {"status": "success", "collections": names}
        except Exception:
            logger.exception("列出私有集合失败")
            return {"status": "error", "error": "获取集合列表失败"}

    async def add_documents_private(self, username, password, collection_name, documents, metadatas=None, ids=None):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        if not validate_collection_name(collection_name):
            return {"status": "error", "error": "集合名无效"}
        valid, msg = validate_documents(documents, metadatas)
        if not valid:
            return {"status": "error", "error": msg}
        rate_ok, msg = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": msg}
        quota_ok, msg = await self._check_quota(username, len(documents))
        if not quota_ok:
            return {"status": "error", "error": msg}
        try:
            client = await self._get_client(username)
            coll = await asyncio.to_thread(client.get_or_create_collection, collection_name)
            if ids is None:
                ids = [str(uuid.uuid4()) for _ in documents]
            if metadatas is None:
                metadatas = [{} for _ in documents]
            embeddings = await compute_embeddings(documents, username)
            lock = await self._get_private_collection_lock(username, collection_name)
            async with lock:
                await asyncio.to_thread(coll.add, embeddings=embeddings,
                                        documents=documents, metadatas=metadatas, ids=ids)
            await self._invalidate_quota_cache(username)
            return {"status": "success", "ids": ids}
        except Exception:
            logger.exception("添加私有文档失败")
            return {"status": "error", "error": "添加文档失败"}

    async def search_private(self, username, password, collection_name, query_text, top_k=DEFAULT_TOP_K, where=None):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        if not validate_collection_name(collection_name):
            return {"status": "error", "error": "集合名无效"}
        if not query_text:
            return {"status": "error", "error": "查询不能为空"}
        if top_k > MAX_TOP_K:
            top_k = MAX_TOP_K
        if where is not None and not validate_where_filter(where):
            return {"status": "error", "error": "无效的过滤条件"}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        try:
            client = await self._get_client(username)
            try:
                coll = await asyncio.to_thread(client.get_collection, collection_name)
            except ValueError:
                return {"status": "success", "documents": [], "metadatas": [], "distances": [], "ids": []}
            query_vec = await compute_embeddings([query_text], username)
            lock = await self._get_private_collection_lock(username, collection_name)
            async with lock:
                results = await asyncio.to_thread(coll.query, query_embeddings=query_vec,
                                                  n_results=top_k, where=where)
            return {
                "status": "success",
                "documents": results["documents"][0] if results["documents"] else [],
                "metadatas": results["metadatas"][0] if results["metadatas"] else [],
                "distances": results["distances"][0] if results["distances"] else [],
                "ids": results["ids"][0] if results["ids"] else []
            }
        except Exception:
            logger.exception("搜索私有集合失败")
            return {"status": "error", "error": "搜索失败"}

    async def delete_private_collection(self, username, password, collection_name):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        if not validate_collection_name(collection_name):
            return {"status": "error", "error": "集合名无效"}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        try:
            client = await self._get_client(username)
            lock = await self._get_private_collection_lock(username, collection_name)
            async with lock:
                await asyncio.to_thread(client.delete_collection, collection_name)
            await self._cleanup_private_lock(username, collection_name)
            await self._invalidate_quota_cache(username)
            return {"status": "success"}
        except ValueError:
            return {"status": "error", "error": "集合不存在"}
        except Exception:
            logger.exception("删除私有集合失败")
            return {"status": "error", "error": "删除集合失败"}

    async def delete_documents_private(self, username, password, collection_name, ids):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        if not validate_collection_name(collection_name):
            return {"status": "error", "error": "集合名无效"}
        if not ids:
            return {"status": "error", "error": "ids 不能为空"}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        try:
            client = await self._get_client(username)
            coll = await asyncio.to_thread(client.get_collection, collection_name)
            lock = await self._get_private_collection_lock(username, collection_name)
            async with lock:
                await asyncio.to_thread(coll.delete, ids=ids)
            await self._invalidate_quota_cache(username)
            return {"status": "success"}
        except ValueError:
            return {"status": "error", "error": "集合不存在"}
        except Exception:
            logger.exception("删除私有文档失败")
            return {"status": "error", "error": "删除文档失败"}

    async def update_documents_private(self, username, password, collection_name, ids, documents=None, metadatas=None):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        if not validate_collection_name(collection_name):
            return {"status": "error", "error": "集合名无效"}
        if documents is None and metadatas is None:
            return {"status": "error", "error": "至少提供 documents 或 metadatas"}
        if documents is not None:
            if not documents:
                return {"status": "error", "error": "documents 不能为空列表"}
            valid, msg = validate_documents(documents, metadatas)
            if not valid:
                return {"status": "error", "error": msg}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        try:
            client = await self._get_client(username)
            coll = await asyncio.to_thread(client.get_collection, collection_name)
            kwargs = {"ids": ids}
            if documents is not None:
                kwargs["embeddings"] = await compute_embeddings(documents, username)
                kwargs["documents"] = documents
            if metadatas is not None:
                kwargs["metadatas"] = metadatas
            lock = await self._get_private_collection_lock(username, collection_name)
            async with lock:
                await asyncio.to_thread(coll.update, **kwargs)
            return {"status": "success"}
        except ValueError:
            return {"status": "error", "error": "集合或文档不存在"}
        except Exception:
            logger.exception("更新私有文档失败")
            return {"status": "error", "error": "更新文档失败"}

    async def get_document_private(self, username, password, collection_name, doc_id):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        try:
            client = await self._get_client(username)
            def _get():
                try:
                    coll = client.get_collection(collection_name)
                    return coll.get(ids=[doc_id], include=["documents", "metadatas"])
                except ValueError:
                    return None
            res = await asyncio.to_thread(_get)
            if res is None or not res["ids"]:
                return {"status": "error", "error": "文档不存在"}
            return {
                "status": "success",
                "id": res["ids"][0],
                "document": res["documents"][0] if res["documents"] else "",
                "metadata": res["metadatas"][0] if res["metadatas"] else {}
            }
        except Exception:
            logger.exception("获取私有文档失败")
            return {"status": "error", "error": "获取文档失败"}

    async def collection_stats_private(self, username, password, collection_name):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        try:
            client = await self._get_client(username)
            def _count():
                try:
                    return client.get_collection(collection_name).count()
                except ValueError:
                    return 0
            count = await asyncio.to_thread(_count)
            return {"status": "success", "name": collection_name, "count": count}
        except Exception:
            logger.exception("获取私有集合统计失败")
            return {"status": "error", "error": "获取统计失败"}

    # ---------- 共享集合操作 ----------
    async def create_shared_collection(self, owner, password, collection_name):
        if not await self._verify_password(owner, password):
            return {"status": "error", "error": "认证失败"}
        if not validate_collection_name(collection_name):
            return {"status": "error", "error": "集合名无效"}
        rate_ok, _ = await self._check_rate_limit(owner)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        if await self._shared_manager.collection_exists(collection_name):
            return {"status": "error", "error": "集合已存在"}
        try:
            await self._shared_manager.create_shared(collection_name, owner)
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        client = await self._get_shared_client()
        try:
            await asyncio.to_thread(client.get_or_create_collection, collection_name)
        except Exception:
            await self._shared_manager.delete_shared(collection_name, owner)
            logger.exception("创建共享集合时 ChromaDB 失败，已回滚权限")
            return {"status": "error", "error": "创建共享集合失败"}
        return {"status": "success", "collection": collection_name}

    async def delete_shared_collection(self, owner, password, collection_name):
        if not await self._verify_password(owner, password):
            return {"status": "error", "error": "认证失败"}
        if not await self._shared_manager.is_owner(collection_name, owner):
            return {"status": "error", "error": "只有所有者可以删除"}
        rate_ok, _ = await self._check_rate_limit(owner)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        client = await self._get_shared_client()
        rwlock = await self._get_shared_collection_rwlock(collection_name)
        async with write_lock(rwlock):
            async def delete_db():
                await asyncio.to_thread(client.delete_collection, collection_name)
            try:
                await self._shared_manager.atomic_delete_collection(collection_name, owner, delete_db)
            except ValueError as e:
                return {"status": "error", "error": str(e)}
            except Exception:
                logger.exception("删除共享集合失败，已回滚权限")
                return {"status": "error", "error": "删除共享集合失败"}
        return {"status": "success"}

    async def add_documents_shared(self, username, password, collection_name, documents, metadatas=None, ids=None):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        if not await self._shared_manager.check_write_access(collection_name, username):
            return {"status": "error", "error": "无权写入该共享集合"}
        valid, msg = validate_documents(documents, metadatas)
        if not valid:
            return {"status": "error", "error": msg}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        client = await self._get_shared_client()
        rwlock = await self._get_shared_collection_rwlock(collection_name)
        async with write_lock(rwlock):
            cur = await self._get_shared_collection_doc_count(collection_name)
            if cur + len(documents) > MAX_DOCS_PER_SHARED_COLLECTION:
                return {"status": "error", "error": "共享集合文档数已达上限"}
            coll = await asyncio.to_thread(client.get_collection, collection_name)
            if ids is None:
                ids = [str(uuid.uuid4()) for _ in documents]
            if metadatas is None:
                metadatas = [{} for _ in documents]
            embeddings = await compute_embeddings(documents, username)
            await asyncio.to_thread(coll.add, embeddings=embeddings,
                                    documents=documents, metadatas=metadatas, ids=ids)
        return {"status": "success", "ids": ids}

    async def search_shared(self, username, password, collection_name, query_text, top_k=DEFAULT_TOP_K, where=None):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        if not await self._shared_manager.check_read_access(collection_name, username):
            return {"status": "error", "error": "无权访问该共享集合"}
        if not query_text:
            return {"status": "error", "error": "查询不能为空"}
        if top_k > MAX_TOP_K:
            top_k = MAX_TOP_K
        if where is not None and not validate_where_filter(where):
            return {"status": "error", "error": "无效的过滤条件"}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        client = await self._get_shared_client()
        rwlock = await self._get_shared_collection_rwlock(collection_name)
        async with read_lock(rwlock):
            try:
                coll = await asyncio.to_thread(client.get_collection, collection_name)
            except ValueError:
                return {"status": "success", "documents": [], "metadatas": [], "distances": [], "ids": []}
            query_vec = await compute_embeddings([query_text], username)
            results = await asyncio.to_thread(coll.query, query_embeddings=query_vec,
                                              n_results=top_k, where=where)
        return {
            "status": "success",
            "documents": results["documents"][0] if results["documents"] else [],
            "metadatas": results["metadatas"][0] if results["metadatas"] else [],
            "distances": results["distances"][0] if results["distances"] else [],
            "ids": results["ids"][0] if results["ids"] else []
        }

    async def delete_documents_shared(self, username, password, collection_name, ids):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        if not await self._shared_manager.check_write_access(collection_name, username):
            return {"status": "error", "error": "无权写入该共享集合"}
        if not ids:
            return {"status": "error", "error": "ids 不能为空"}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        client = await self._get_shared_client()
        rwlock = await self._get_shared_collection_rwlock(collection_name)
        async with write_lock(rwlock):
            try:
                coll = await asyncio.to_thread(client.get_collection, collection_name)
                await asyncio.to_thread(coll.delete, ids=ids)
            except ValueError:
                return {"status": "error", "error": "集合不存在"}
        return {"status": "success"}

    async def update_documents_shared(self, username, password, collection_name, ids, documents=None, metadatas=None):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        if not await self._shared_manager.check_write_access(collection_name, username):
            return {"status": "error", "error": "无权写入该共享集合"}
        if documents is None and metadatas is None:
            return {"status": "error", "error": "至少提供 documents 或 metadatas"}
        if documents is not None:
            if not documents:
                return {"status": "error", "error": "documents 不能为空列表"}
            valid, msg = validate_documents(documents, metadatas)
            if not valid:
                return {"status": "error", "error": msg}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        client = await self._get_shared_client()
        rwlock = await self._get_shared_collection_rwlock(collection_name)
        async with write_lock(rwlock):
            try:
                coll = await asyncio.to_thread(client.get_collection, collection_name)
            except ValueError:
                return {"status": "error", "error": "集合不存在"}
            kwargs = {"ids": ids}
            if documents is not None:
                kwargs["embeddings"] = await compute_embeddings(documents, username)
                kwargs["documents"] = documents
            if metadatas is not None:
                kwargs["metadatas"] = metadatas
            await asyncio.to_thread(coll.update, **kwargs)
        return {"status": "success"}

    async def get_document_shared(self, username, password, collection_name, doc_id):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        if not await self._shared_manager.check_read_access(collection_name, username):
            return {"status": "error", "error": "无权访问该共享集合"}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        client = await self._get_shared_client()
        rwlock = await self._get_shared_collection_rwlock(collection_name)
        async with read_lock(rwlock):
            def _get():
                try:
                    coll = client.get_collection(collection_name)
                    return coll.get(ids=[doc_id], include=["documents", "metadatas"])
                except ValueError:
                    return None
            res = await asyncio.to_thread(_get)
        if res is None or not res["ids"]:
            return {"status": "error", "error": "文档不存在"}
        return {
            "status": "success",
            "id": res["ids"][0],
            "document": res["documents"][0] if res["documents"] else "",
            "metadata": res["metadatas"][0] if res["metadatas"] else {}
        }

    async def collection_stats_shared(self, username, password, collection_name):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        if not await self._shared_manager.check_read_access(collection_name, username):
            return {"status": "error", "error": "无权访问该共享集合"}
        rate_ok, _ = await self._check_rate_limit(username)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        client = await self._get_shared_client()
        rwlock = await self._get_shared_collection_rwlock(collection_name)
        async with read_lock(rwlock):
            def _count():
                try:
                    return client.get_collection(collection_name).count()
                except ValueError:
                    return 0
            count = await asyncio.to_thread(_count)
        return {"status": "success", "name": collection_name, "count": count}

    async def grant_shared_access(self, owner, password, collection_name, target, writer: bool = True):
        if not await self._verify_password(owner, password):
            return {"status": "error", "error": "认证失败"}
        rate_ok, _ = await self._check_rate_limit(owner)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        success = await self._shared_manager.grant_access(collection_name, owner, target, writer)
        if not success:
            return {"status": "error", "error": "授权失败"}
        return {"status": "success"}

    async def revoke_shared_access(self, owner, password, collection_name, target):
        if not await self._verify_password(owner, password):
            return {"status": "error", "error": "认证失败"}
        rate_ok, _ = await self._check_rate_limit(owner)
        if not rate_ok:
            return {"status": "error", "error": "请求过于频繁"}
        success = await self._shared_manager.revoke_access(collection_name, owner, target)
        if not success:
            return {"status": "error", "error": "撤销失败"}
        return {"status": "success"}

    async def transfer_shared_collection(self, owner, password, collection_name, new_owner):
        if not await self._verify_password(owner, password):
            return {"status": "error", "error": "认证失败"}
        if not validate_username(new_owner):
            return {"status": "error", "error": "无效的新所有者用户名"}
        success = await self._shared_manager.transfer_ownership(collection_name, owner, new_owner)
        if not success:
            return {"status": "error", "error": "转让失败"}
        return {"status": "success"}

    async def list_shared_collections(self, username, password):
        if not await self._verify_password(username, password):
            return {"status": "error", "error": "认证失败"}
        colls = await self._shared_manager.list_shared_collections(username)
        return {"status": "success", "collections": colls}

    # ---------- 健康检查 ----------
    async def health_check(self) -> Dict:
        result = {
            "status": "ok",
            "model_loaded": _embedding_model is not None,
            "shared_manager_loaded": self._shared_manager._loaded,
            "active_users": len(self._chroma_clients),
        }
        try:
            client = await self._get_shared_client()
            await asyncio.to_thread(client.list_collections)
        except Exception:
            result["status"] = "degraded"
            result["error"] = "shared_db_unreachable"
        return result

    # ---------- 关闭 ----------
    async def close(self):
        await rate_limiter.stop_cleanup()
        await self._shared_manager.close()

# ========== 单例 ==========
_vector_store_instance = None
_instance_lock = asyncio.Lock()

async def get_vector_store() -> VectorStoreManager:
    global _vector_store_instance
    if _vector_store_instance is not None:
        return _vector_store_instance
    async with _instance_lock:
        if _vector_store_instance is None:
            _vector_store_instance = VectorStoreManager()
        return _vector_store_instance