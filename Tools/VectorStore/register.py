"""
向量存储工具注册模块
仅提供工具包装函数，并通过 get_tools() 返回工具列表
工具文档由 main.py 根据 Usage.yaml 统一构建
"""

import json
import logging
from typing import List, Optional, Dict, Any

from . import VectorStore

log = logging.getLogger("mcp_server")


# ========== 工具包装函数 ==========
# 注意：这些函数将作为 MCP 工具暴露，文档字符串由 main.py 动态注入

# ---------- 用户管理 ----------
async def create_user(username: str, password: str) -> str:
    """创建一个新的向量数据库用户"""
    vs = await VectorStore.get_vector_store()
    success, msg = await vs.create_user(username, password)
    return json.dumps({"status": success, "message": msg}, ensure_ascii=False)


async def delete_user(username: str, password: str, transfer_to: Optional[str] = None) -> str:
    """删除用户，可选择将共享集合转移给目标用户"""
    vs = await VectorStore.get_vector_store()
    success, msg = await vs.delete_user(username, password, transfer_to)
    return json.dumps({"status": success, "message": msg}, ensure_ascii=False)


async def change_password(username: str, old_password: str, new_password: str) -> str:
    """修改用户密码"""
    vs = await VectorStore.get_vector_store()
    success, msg = await vs.change_password(username, old_password, new_password)
    return json.dumps({"status": success, "message": msg}, ensure_ascii=False)


# ---------- 私有集合操作 ----------
async def list_private_collections(username: str, password: str) -> str:
    """列出当前用户的所有私有集合"""
    vs = await VectorStore.get_vector_store()
    result = await vs.list_private_collections(username, password)
    return json.dumps(result, ensure_ascii=False)


async def add_documents_private(
    username: str,
    password: str,
    collection_name: str,
    documents: List[str],
    metadatas: Optional[List[Dict[str, Any]]] = None,
    ids: Optional[List[str]] = None,
) -> str:
    """向私有集合中添加文档"""
    vs = await VectorStore.get_vector_store()
    result = await vs.add_documents_private(
        username, password, collection_name, documents, metadatas, ids
    )
    return json.dumps(result, ensure_ascii=False)


async def search_private(
    username: str,
    password: str,
    collection_name: str,
    query_text: str,
    top_k: int = 10,
    where: Optional[Dict[str, Any]] = None,
) -> str:
    """在私有集合中搜索相似文档"""
    vs = await VectorStore.get_vector_store()
    result = await vs.search_private(
        username, password, collection_name, query_text, top_k, where
    )
    return json.dumps(result, ensure_ascii=False)


async def delete_private_collection(username: str, password: str, collection_name: str) -> str:
    """删除一个私有集合"""
    vs = await VectorStore.get_vector_store()
    result = await vs.delete_private_collection(username, password, collection_name)
    return json.dumps(result, ensure_ascii=False)


async def delete_documents_private(
    username: str,
    password: str,
    collection_name: str,
    ids: List[str],
) -> str:
    """删除私有集合中的文档"""
    vs = await VectorStore.get_vector_store()
    result = await vs.delete_documents_private(username, password, collection_name, ids)
    return json.dumps(result, ensure_ascii=False)


async def update_documents_private(
    username: str,
    password: str,
    collection_name: str,
    ids: List[str],
    documents: Optional[List[str]] = None,
    metadatas: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """更新私有集合中的文档"""
    vs = await VectorStore.get_vector_store()
    result = await vs.update_documents_private(
        username, password, collection_name, ids, documents, metadatas
    )
    return json.dumps(result, ensure_ascii=False)


async def get_document_private(
    username: str, password: str, collection_name: str, doc_id: str
) -> str:
    """获取私有集合中的单个文档"""
    vs = await VectorStore.get_vector_store()
    result = await vs.get_document_private(username, password, collection_name, doc_id)
    return json.dumps(result, ensure_ascii=False)


async def collection_stats_private(
    username: str, password: str, collection_name: str
) -> str:
    """获取私有集合的统计信息"""
    vs = await VectorStore.get_vector_store()
    result = await vs.collection_stats_private(username, password, collection_name)
    return json.dumps(result, ensure_ascii=False)


# ---------- 共享集合管理 ----------
async def create_shared_collection(owner: str, password: str, collection_name: str) -> str:
    """创建一个共享集合（所有者为 owner）"""
    vs = await VectorStore.get_vector_store()
    result = await vs.create_shared_collection(owner, password, collection_name)
    return json.dumps(result, ensure_ascii=False)


async def delete_shared_collection(owner: str, password: str, collection_name: str) -> str:
    """删除共享集合（仅所有者）"""
    vs = await VectorStore.get_vector_store()
    result = await vs.delete_shared_collection(owner, password, collection_name)
    return json.dumps(result, ensure_ascii=False)


async def add_documents_shared(
    username: str,
    password: str,
    collection_name: str,
    documents: List[str],
    metadatas: Optional[List[Dict[str, Any]]] = None,
    ids: Optional[List[str]] = None,
) -> str:
    """向共享集合添加文档（需要写权限）"""
    vs = await VectorStore.get_vector_store()
    result = await vs.add_documents_shared(
        username, password, collection_name, documents, metadatas, ids
    )
    return json.dumps(result, ensure_ascii=False)


async def search_shared(
    username: str,
    password: str,
    collection_name: str,
    query_text: str,
    top_k: int = 10,
    where: Optional[Dict[str, Any]] = None,
) -> str:
    """搜索共享集合（需要读权限）"""
    vs = await VectorStore.get_vector_store()
    result = await vs.search_shared(
        username, password, collection_name, query_text, top_k, where
    )
    return json.dumps(result, ensure_ascii=False)


async def delete_documents_shared(
    username: str, password: str, collection_name: str, ids: List[str]
) -> str:
    """删除共享集合中的文档（需要写权限）"""
    vs = await VectorStore.get_vector_store()
    result = await vs.delete_documents_shared(username, password, collection_name, ids)
    return json.dumps(result, ensure_ascii=False)


async def update_documents_shared(
    username: str,
    password: str,
    collection_name: str,
    ids: List[str],
    documents: Optional[List[str]] = None,
    metadatas: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """更新共享集合中的文档（需要写权限）"""
    vs = await VectorStore.get_vector_store()
    result = await vs.update_documents_shared(
        username, password, collection_name, ids, documents, metadatas
    )
    return json.dumps(result, ensure_ascii=False)


async def get_document_shared(
    username: str, password: str, collection_name: str, doc_id: str
) -> str:
    """获取共享集合中的单个文档"""
    vs = await VectorStore.get_vector_store()
    result = await vs.get_document_shared(username, password, collection_name, doc_id)
    return json.dumps(result, ensure_ascii=False)


async def collection_stats_shared(
    username: str, password: str, collection_name: str
) -> str:
    """获取共享集合的统计信息"""
    vs = await VectorStore.get_vector_store()
    result = await vs.collection_stats_shared(username, password, collection_name)
    return json.dumps(result, ensure_ascii=False)


# ---------- 共享集合权限管理 ----------
async def grant_shared_access(
    owner: str, password: str, collection_name: str, target_user: str, writer: bool = True
) -> str:
    """授予用户对共享集合的读/写权限"""
    vs = await VectorStore.get_vector_store()
    result = await vs.grant_shared_access(owner, password, collection_name, target_user, writer)
    return json.dumps(result, ensure_ascii=False)


async def revoke_shared_access(
    owner: str, password: str, collection_name: str, target_user: str
) -> str:
    """撤销用户对共享集合的访问权限"""
    vs = await VectorStore.get_vector_store()
    result = await vs.revoke_shared_access(owner, password, collection_name, target_user)
    return json.dumps(result, ensure_ascii=False)


async def transfer_shared_collection(
    owner: str, password: str, collection_name: str, new_owner: str
) -> str:
    """将共享集合所有权转让给另一用户"""
    vs = await VectorStore.get_vector_store()
    result = await vs.transfer_shared_collection(owner, password, collection_name, new_owner)
    return json.dumps(result, ensure_ascii=False)


async def list_shared_collections(username: str, password: str) -> str:
    """列出所有有权访问的共享集合"""
    vs = await VectorStore.get_vector_store()
    result = await vs.list_shared_collections(username, password)
    return json.dumps(result, ensure_ascii=False)


# ---------- 系统健康检查 ----------
async def vector_health_check() -> str:
    """获取向量数据库系统健康状态"""
    vs = await VectorStore.get_vector_store()
    result = await vs.health_check()
    return json.dumps(result, ensure_ascii=False)


# ========== 工具列表导出 ==========
def get_tools():
    """
    返回该工具包提供的所有工具函数列表。
    每个元素为字典，包含 func 和可选的 name。
    """
    return [
        {"func": create_user, "name": "create_user"},
        {"func": delete_user, "name": "delete_user"},
        {"func": change_password, "name": "change_password"},
        {"func": list_private_collections, "name": "list_private_collections"},
        {"func": add_documents_private, "name": "add_documents_private"},
        {"func": search_private, "name": "search_private"},
        {"func": delete_private_collection, "name": "delete_private_collection"},
        {"func": delete_documents_private, "name": "delete_documents_private"},
        {"func": update_documents_private, "name": "update_documents_private"},
        {"func": get_document_private, "name": "get_document_private"},
        {"func": collection_stats_private, "name": "collection_stats_private"},
        {"func": create_shared_collection, "name": "create_shared_collection"},
        {"func": delete_shared_collection, "name": "delete_shared_collection"},
        {"func": add_documents_shared, "name": "add_documents_shared"},
        {"func": search_shared, "name": "search_shared"},
        {"func": delete_documents_shared, "name": "delete_documents_shared"},
        {"func": update_documents_shared, "name": "update_documents_shared"},
        {"func": get_document_shared, "name": "get_document_shared"},
        {"func": collection_stats_shared, "name": "collection_stats_shared"},
        {"func": grant_shared_access, "name": "grant_shared_access"},
        {"func": revoke_shared_access, "name": "revoke_shared_access"},
        {"func": transfer_shared_collection, "name": "transfer_shared_collection"},
        {"func": list_shared_collections, "name": "list_shared_collections"},
        {"func": vector_health_check, "name": "vector_health_check"},
    ]