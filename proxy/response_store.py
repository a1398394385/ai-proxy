from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional


@dataclass
class ResponseRecord:
    response_id: str
    model: str
    output: list           # Responses API output items（返回给客户端）
    conversation: list     # Chat Messages 格式（previous_response_id 重建历史用）
    usage: dict
    status: str
    created_at: float
    expires_at: float      # TTL 过期时间戳


class ResponseStore:
    """内存 response store，LRU + TTL 双重淘汰。

    使用 OrderedDict 实现 LRU：
    - move_to_end(key) O(1) 将访问项移到尾部（最新）
    - popitem(last=False) O(1) 淘汰头部（最旧）
    """

    def __init__(self, max_entries: int = 1000, ttl_seconds: int = 3600):
        self._store: OrderedDict = OrderedDict()
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()     # ThreadedHTTPServer 多线程并发保护

    def put(self, response_id: str, record: ResponseRecord):
        """存储 response；先淘汰过期条目，再按 LRU 淘汰超量条目。"""
        with self._lock:
            self._evict_expired()
            if response_id in self._store:
                del self._store[response_id]
            elif len(self._store) >= self._max_entries:
                self._store.popitem(last=False)    # 淘汰最旧（LRU head）
            self._store[response_id] = record

    def get(self, response_id: str) -> Optional[ResponseRecord]:
        """获取 response；不存在或已过期返回 None；命中则更新 LRU 顺序。"""
        with self._lock:
            if response_id not in self._store:
                return None
            record = self._store[response_id]
            if time.time() > record.expires_at:
                del self._store[response_id]
                return None
            self._store.move_to_end(response_id)   # 移到尾部（最近使用）
            return record

    @property
    def ttl_seconds(self) -> int:
        """公共只读属性，供 store 外调用方获取 TTL 配置。"""
        return self._ttl_seconds

    def get_conversation(self, response_id: str) -> list:
        """获取对应的 Chat Messages 历史（直接从 record.conversation 读取）。"""
        record = self.get(response_id)
        return record.conversation if record else []

    def _evict_expired(self):
        now = time.time()
        expired = [k for k, v in self._store.items() if now > v.expires_at]
        for k in expired:
            del self._store[k]
