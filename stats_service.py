#!/usr/bin/env python3
"""StatsService — Token 统计数据查询服务层。

从 data db 的 token_stats 表读取数据，提供按模型、上游、趋势、
汇总等维度的统计查询接口。

职责：
- 封装 token_stats 表的 SQL 查询逻辑
- 通过 Provider 接口定义标准化的数据访问方法
- 每次查询新建 SQLite 连接，用完立即关闭

用法：
    from stats_service import StatsService

    service = StatsService(
        data_db_path=None,
        data_db_path=None,
        state_db_path="~/.hermes/state.db",
    )
    summary = service.fetch_summary(period="24h")
"""

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from proxy.paths import DATA_DB
from proxy.pricing_manager import PricingDB

class _TokenStatsDao:
    """Token 统计数据访问对象。

    封装 token_stats 表的 SQL 查询逻辑，每次查询新建 SQLite 连接，用完立即关闭。

    Args:
        db_path: data db 路径
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    # ─── 工具方法 ───

    @staticmethod
    def _period_to_condition(period: str) -> str:
        """将 period 字符串转换为 SQLite 时间条件。

        Args:
            period: "day"/"24h", "week"/"7d", "month"/"30d"

        Returns:
            SQLite datetime 条件字符串
        """
        mapping = {
            "day": "datetime('now', '-1 day')",
            "24h": "datetime('now', '-1 day')",
            "week": "datetime('now', '-7 days')",
            "7d": "datetime('now', '-7 days')",
            "month": "datetime('now', '-30 days')",
            "30d": "datetime('now', '-30 days')",
        }
        threshold = mapping.get(period, "datetime('now', '-7 days')")
        return f"request_ts >= {threshold}"

    def _get_conn(self) -> sqlite3.Connection:
        """创建新的数据库连接。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ─── 查询方法 ───

    def query_raw(
        self,
        period: str,
        model: str | None = None,
        request_type: str | None = None,
    ) -> list[dict]:
        """查询原始 token_stats 记录，返回统一格式 dict 列表。

        Args:
            period: 时间周期
            model: 可选，按 target_model 精确匹配（已规范化的模型名）
            request_type: 可选，按 request_type 过滤

        Returns:
            统一格式记录列表
        """
        time_condition = self._period_to_condition(period)
        conditions = [time_condition]
        params: list = []

        if model:
            conditions.append("target_model = ?")
            params.append(model)

        if request_type:
            conditions.append("request_type = ?")
            params.append(request_type)

        where_clause = " AND ".join(conditions)

        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT request_id, request_type, target_model,
                       request_ts, duration_ms, input_tokens, output_tokens,
                       cached_read_tokens, cached_write_tokens,
                       COALESCE(upstream_id, '__unknown__') as upstream_id,
                       status
                FROM token_stats
                WHERE {where_clause}
                ORDER BY request_ts DESC
                """,
                params,
            ).fetchall()

            return [
                {
                    "request_id": row["request_id"],
                    "model": row["target_model"] or "",
                    "request_type": row["request_type"],
                    "request_ts": row["request_ts"],
                    "duration_ms": row["duration_ms"],
                    "status": row["status"] or "completed",
                    "input_tokens": row["input_tokens"] or 0,
                    "output_tokens": row["output_tokens"] or 0,
                    "cache_read_tokens": row["cached_read_tokens"] or 0,   # DB 列名带 d
                    "cache_write_tokens": row["cached_write_tokens"] or 0, # 输出时去 d
                    "upstream_id": row["upstream_id"],
                }
                for row in rows
            ]
        finally:
            conn.close()


class _UpstreamResolver:
    """上游解析器 — 从 data db 加载 model → upstream 映射，内置 TTL 缓存。

    构造时加载全量映射到内存，每次 resolve() 检查缓存是否过期（60s），
    过期自动刷新。data db 不存在时返回空映射，不抛异常。

    Args:
        data_db_path: data db 路径
    """

    def __init__(self, data_db_path: Path) -> None:
        self.data_db_path = data_db_path
        self._cache_ttl = 60  # 缓存 60 秒
        self._loaded_at = 0.0  # 初始化为 0，强制首次加载
        self._model_map: dict = {}  # {model_name: {upstream_id, upstream_name, base_url}}
        self._id_map: dict = {}  # {upstream_id: {upstream_name, base_url}}
        self._refresh()

    def _refresh(self) -> None:
        """从 data db 重新加载映射到内存。"""
        self._loaded_at = time.time()
        self._model_map = {}
        self._id_map = {}

        if not self.data_db_path.exists():
            return

        try:
            conn = sqlite3.connect(str(self.data_db_path))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT tm.name, u.name as upstream_name, u.base_url, u.id as upstream_id
                    FROM target_models tm
                    JOIN upstreams u ON tm.upstream_id = u.id
                    WHERE tm.name IS NOT NULL AND u.name IS NOT NULL AND u.name != ''
                    """
                ).fetchall()

                for row in rows:
                    model_name = row["name"]
                    upstream_id = row["upstream_id"]
                    up_name = row["upstream_name"]
                    base_url = row["base_url"]
                    self._model_map[model_name] = {
                        "upstream_id": upstream_id,
                        "upstream_name": up_name,
                        "base_url": base_url,
                    }
                    if upstream_id not in self._id_map:
                        self._id_map[upstream_id] = {
                            "upstream_name": up_name,
                            "base_url": base_url,
                        }
            finally:
                conn.close()
        except Exception:
            # data db 损坏或无法读取时，保持空映射
            pass

    def resolve(self, target_model: str) -> dict:
        """解析 target_model 对应的 upstream 信息。

        Args:
            target_model: 目标模型名称

        Returns:
            {upstream_id, upstream_name, base_url} 或
            {upstream_id: '__unknown__', upstream_name: '__unknown__', base_url: None}
        """
        # 检查缓存是否过期
        if time.time() - self._loaded_at > self._cache_ttl:
            self._refresh()

        entry = self._model_map.get(target_model)
        if entry:
            return {
                "upstream_id": entry["upstream_id"],
                "upstream_name": entry["upstream_name"],
                "base_url": entry["base_url"],
            }

        return {
            "upstream_id": "__unknown__",
            "upstream_name": "__unknown__",
            "base_url": None,
        }

    def resolve_by_id(self, upstream_id: str) -> dict:
        """按 upstream_id 解析 upstream 信息。

        Args:
            upstream_id: upstream ID

        Returns:
            {upstream_id, upstream_name, base_url} 或
            {upstream_id, upstream_name: '__unknown__', base_url: None}
        """
        if time.time() - self._loaded_at > self._cache_ttl:
            self._refresh()

        entry = self._id_map.get(upstream_id)
        if entry:
            return {
                "upstream_id": upstream_id,
                "upstream_name": entry["upstream_name"],
                "base_url": entry["base_url"],
            }

        return {
            "upstream_id": upstream_id,
            "upstream_name": "__unknown__",
            "base_url": None,
        }

    def get_all_upstreams(self) -> list:
        """返回所有 upstream 列表。

        Returns:
            [{upstream_id, upstream_name, base_url}]
        """
        # 检查缓存是否过期
        if time.time() - self._loaded_at > self._cache_ttl:
            self._refresh()

        return [
            {
                "upstream_id": uid,
                "upstream_name": info["upstream_name"],
                "base_url": info["base_url"],
            }
            for uid, info in self._id_map.items()
        ]


class _CostCalculator:
    """成本计算器 — 从 data db 加载 model_pricing 表（通过 PricingDB），无过期缓存。

    USD 价格自动 × 7 转为人民币，RMB 价格原样使用。
    calculate() 统一返回人民币金额。
    缓存仅在显式调用 invalidate_cache() 后失效。

    Args:
        data_db_path: data db 路径
    """

    EXCHANGE_RATE = 7  # USD → RMB

    def __init__(self, data_db_path: str | Path) -> None:
        self._pricing_db = PricingDB(Path(data_db_path))
        self._pricing_cache: dict = {}
        self._display_name_cache: dict = {}  # lowercase model_id → display_name

    # ─── 定价加载 ───

    def get_pricing(self) -> dict:
        """从 data db 加载 model_pricing 表（带缓存），自动换算为人民币。

        model_id 统一小写存入缓存，实现大小写不敏感匹配。

        Returns:
            {model_id_lower: {input_cost, output_cost, cache_read_cost, cache_creation_cost}}
            价格单位：RMB / 1M tokens
        """
        if self._pricing_cache:
            return self._pricing_cache

        try:
            rows = self._pricing_db.list_pricings()
            pricing = {}
            display_names = {}
            for r in rows:
                rate = 1 if r["currency"] == "RMB" else self.EXCHANGE_RATE
                multiplier = float(r.get("multiplier", "1.0"))
                key = r["model_id"].lower()
                pricing[key] = {
                    "input_cost": round(float(r["input_cost_per_million"]) * rate * multiplier, 6),
                    "output_cost": round(float(r["output_cost_per_million"]) * rate * multiplier, 6),
                    "cache_read_cost": round(float(r["cache_read_cost_per_million"]) * rate * multiplier, 6),
                    "cache_creation_cost": round(float(r["cache_creation_cost_per_million"]) * rate * multiplier, 6),
                    "input_includes_cache_read": r.get("input_includes_cache_read", 0),
                }
                display_names[key] = r["display_name"]
            self._pricing_cache = pricing
            self._display_name_cache = display_names
            return pricing
        except Exception as e:
            print(f"Error reading model pricing: {e}")
            return {}

    def invalidate_cache(self):
        """主动失效缓存，供定价修改后调用。"""
        self._pricing_cache = {}
        self._display_name_cache = {}

    def get_display_name(self, model: str) -> str:
        """获取模型显示名，不存在则返回模型原名。"""
        if not model:
            return model
        self.get_pricing()  # 确保缓存已加载
        return self._display_name_cache.get(model.lower(), model)

    # ─── 成本计算 ───

    def calculate(
        self,
        model: str,
        input_tokens: int | float | None,
        output_tokens: int | float | None,
        cache_read_tokens: int | float | None,
        cache_write_tokens: int | float | None,
    ) -> float:
        """根据模型计费规则计算成本（人民币）。

        Returns:
            总成本（人民币，float）。模型无定价时返回 0。
        """
        pricing = self.get_pricing()

        if not pricing:
            return 0

        key = model.lower() if model else ""
        if key not in pricing:
            return 0

        p = pricing[key]

        input_cost = (input_tokens or 0) / 1_000_000 * p["input_cost"]
        output_cost = (output_tokens or 0) / 1_000_000 * p["output_cost"]
        cache_read_cost = (cache_read_tokens or 0) / 1_000_000 * p["cache_read_cost"]
        cache_write_cost = (cache_write_tokens or 0) / 1_000_000 * p["cache_creation_cost"]

        return input_cost + output_cost + cache_read_cost + cache_write_cost

    def calculate_breakdown(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> dict:
        """返回 4 项独立成本（人民币），不求和。

        Returns:
            {"input_cost_cny": float, "output_cost_cny": float,
             "cache_read_cost_cny": float, "cache_write_cost_cny": float}
        """
        pricing = self.get_pricing()
        if not pricing:
            return {"input_cost_cny": 0.0, "output_cost_cny": 0.0,
                    "cache_read_cost_cny": 0.0, "cache_write_cost_cny": 0.0}

        key = model.lower() if model else ""
        if key not in pricing:
            return {"input_cost_cny": 0.0, "output_cost_cny": 0.0,
                    "cache_read_cost_cny": 0.0, "cache_write_cost_cny": 0.0}

        p = pricing[key]
        input_cost = (input_tokens or 0) / 1_000_000 * p["input_cost"]
        output_cost = (output_tokens or 0) / 1_000_000 * p["output_cost"]
        cache_read_cost = (cache_read_tokens or 0) / 1_000_000 * p["cache_read_cost"]
        cache_write_cost = (cache_write_tokens or 0) / 1_000_000 * p["cache_creation_cost"]

        return {
            "input_cost_cny": round(input_cost, 6),
            "output_cost_cny": round(output_cost, 6),
            "cache_read_cost_cny": round(cache_read_cost, 6),
            "cache_write_cost_cny": round(cache_write_cost, 6),
        }


class _OpenCodeDao:
    """OpenCode 数据访问对象 — 从 opencode.db 读取 session/message token 数据。

    按 message 级别聚合（每条 assistant message 的 tokens 计入其 modelID），
    reasoning tokens 合并入 output_tokens。数据库不存在时返回空结果，不抛异常。

    Args:
        db_path: opencode.db 路径
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    # ─── 工具方法 ───

    @staticmethod
    def _period_to_condition(period: str) -> str:
        """将 period 转换为 Unix 毫秒时间戳条件。"""
        mapping = {
            "day": "86400000",
            "24h": "86400000",
            "week": "604800000",
            "7d": "604800000",
            "month": "2592000000",
            "30d": "2592000000",
        }
        delta_ms = mapping.get(period, "604800000")
        return f"m.time_created >= (strftime('%s', 'now') * 1000 - {delta_ms})"

    def _get_conn(self) -> sqlite3.Connection | None:
        """创建数据库连接，opencode.db 不存在时返回 None。"""
        if not self.db_path.exists():
            return None
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def query_raw(
        self,
        period: str,
        model: str | None = None,
    ) -> list[dict]:
        """查询原始 opencode message 记录，返回统一格式 dict 列表。

        Args:
            period: 时间周期
            model: 可选，按 modelID 过滤

        Returns:
            统一格式记录列表。opencode.db 不存在时返回空列表。
        """
        conn = self._get_conn()
        if conn is None:
            return []
        try:
            time_condition = self._period_to_condition(period)
            conditions = [time_condition, "json_extract(m.data, '$.tokens.input') IS NOT NULL"]
            params: list = []

            if model:
                conditions.append("json_extract(m.data, '$.modelID') = ?")
                params.append(model)

            where_clause = " AND ".join(conditions)

            rows = conn.execute(
                f"""
                SELECT m.id as message_id,
                       json_extract(m.data, '$.modelID') as model_id,
                       datetime(m.time_created / 1000, 'unixepoch', 'localtime') as request_ts,
                       CAST(json_extract(m.data, '$.tokens.input') AS INTEGER) as input_tokens,
                       CAST(json_extract(m.data, '$.tokens.output') AS INTEGER)
                           + CAST(json_extract(m.data, '$.tokens.reasoning') AS INTEGER) as output_tokens,
                       CAST(json_extract(m.data, '$.tokens.cache.read') AS INTEGER) as cache_read_tokens,
                       CAST(json_extract(m.data, '$.tokens.cache.write') AS INTEGER) as cache_write_tokens,
                       CAST(json_extract(m.data, '$.time.completed') AS INTEGER)
                           - CAST(json_extract(m.data, '$.time.created') AS INTEGER) as duration_ms
                FROM message m
                WHERE {where_clause}
                ORDER BY m.time_created DESC
                """,
                params,
            ).fetchall()

            return [
                {
                    "request_id": f"oc-msg-{row['message_id']}",
                    "model": row["model_id"] or "",
                    "request_type": "session",
                    "request_ts": row["request_ts"],
                    "duration_ms": row["duration_ms"],
                    "status": "completed",
                    "input_tokens": row["input_tokens"] or 0,
                    "output_tokens": row["output_tokens"] or 0,
                    "cache_read_tokens": row["cache_read_tokens"] or 0,
                    "cache_write_tokens": row["cache_write_tokens"] or 0,
                    "upstream_id": "opencode",
                }
                for row in rows
            ]
        except Exception:
            return []
        finally:
            conn.close()

class StatsService:
    """Token 统计数据查询服务。

    Args:
        data_db_path: data db 路径
        state_db_path: state.db 路径（运行时状态）
        opencode_db_path: opencode.db 路径（默认 ~/.local/share/opencode/opencode.db）
    """

    # ─── 默认 opencode 路径 ───
    _OPENCODE_DB_DEFAULT = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

    def __init__(
        self,
        data_db_path: str,
        opencode_db_path: str | None = None,
    ) -> None:
        self.data_db_path = Path(data_db_path) if data_db_path else DATA_DB

        self.opencode_db_path = Path(opencode_db_path) if opencode_db_path else self._OPENCODE_DB_DEFAULT
        self._opencode_dao = None  # 懒加载

        # 初始化上游解析器
        self._upstream_resolver = _UpstreamResolver(self.data_db_path)

    # ─── 统一数据查询 ───

    def _fetch_unified_records(
        self,
        period: str,
        model: str | None = None,
        request_type: str | None = None,
        source: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list | tuple[list, int]:
        """统一原始数据查询 — 所有统计视图的唯一数据源。

        依次从三源拉取原始行，转换为统一格式，逐条计算 4 项成本，
        合并后按 request_ts DESC 排序。

        Args:
            period: "day"/"week"/"month"
            model: 可选，内部完成规范化后对各源分别匹配
            request_type: 可选，过滤请求类型
            source: 可选，过滤数据来源 (hermes/opencode/proxy)
            limit: 指定时启用分页，返回 (records, total)
            offset: 分页偏移

        Returns:
            无分页: [record, ...]
            有分页: ([record, ...], total_count)
        """
        # 1. 模型名规范化
        normalized_model = model

        # 2. 查询两源
        records = []
        try:
            records.extend(self._get_dao().query_raw(period, normalized_model, request_type))
        except Exception:
            pass
        opencode_dao = self._get_opencode_dao()
        if opencode_dao:
            try:
                records.extend(opencode_dao.query_raw(period, normalized_model))
            except Exception:
                pass

        # 3. 逐条计算 4 项成本
        calculator = self._get_calculator()

        # 对 proxy 来源的记录，按 model_pricing 配置扣除重复 cache_read
        #    某些模型（如 kimi-k2.6）虽走 Anthropic 协议但 input_tokens 已包含 cache_read，
        #    在查询时从 input_tokens 扣除，避免成本重复计算。token_stats 原始值不变。
        pricing = calculator.get_pricing()
        for r in records:
            if r["upstream_id"] != "opencode":
                key = r["model"].lower() if r["model"] else ""
                p = pricing.get(key)
                if p and p.get("input_includes_cache_read", 0):
                    cache = r.get("cache_read_tokens", 0) or 0
                    if cache > 0:
                        r["input_tokens"] = max(0, r["input_tokens"] - cache)

        for r in records:
            breakdown = calculator.calculate_breakdown(
                model=r["model"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                cache_read_tokens=r["cache_read_tokens"],
                cache_write_tokens=r["cache_write_tokens"],
            )
            r["input_cost_cny"] = breakdown["input_cost_cny"]
            r["output_cost_cny"] = breakdown["output_cost_cny"]
            r["cache_read_cost_cny"] = breakdown["cache_read_cost_cny"]
            r["cache_write_cost_cny"] = breakdown["cache_write_cost_cny"]

        # 4. 按 source 过滤数据来源
        if source == "opencode":
            records = [r for r in records if r["upstream_id"] == source]
        elif source == "proxy":
            records = [r for r in records if r["upstream_id"] != "opencode"]

        # 5. 按 request_ts DESC 排序
        records.sort(key=lambda r: r["request_ts"], reverse=True)

        # 6. 分页或全量返回
        if limit is not None:
            total = len(records)
            return (records[offset:offset + limit], total)
        return records

    # ─── TokenStatsDao 实例 ───

    def _get_dao(self) -> _TokenStatsDao:
        """获取 TokenStatsDao 实例。"""
        return _TokenStatsDao(self.data_db_path)

    @staticmethod
    def _normalize_model_name(name: str) -> str:
        """去掉模型名中的 [xxx] 上下文后缀。"""
        if not name:
            return name
        bracket_pos = name.find("[")
        if bracket_pos >= 0:
            return name[:bracket_pos].rstrip()
        return name

    def _get_opencode_dao(self):
        """获取 OpenCodeDao 实例，数据库不存在时返回 None。"""
        if self._opencode_dao is None:
            dao = _OpenCodeDao(self.opencode_db_path)
            self._opencode_dao = dao if dao.db_path.exists() else None
        return self._opencode_dao

    # ─── Provider 接口 ───

    def fetch_summary(self, period: str) -> dict:
        """获取汇总统计数据，合并三源。"""
        records = self._fetch_unified_records(period)
        if not records:
            return {
                "period": period, "request_count": 0, "input_tokens": 0,
                "output_tokens": 0, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "total_tokens": 0,
                "estimated_cost_cny": 0.0, "avg_duration_ms": 0,
            }
        total_input = sum(r["input_tokens"] for r in records)
        total_output = sum(r["output_tokens"] for r in records)
        total_cache_read = sum(r["cache_read_tokens"] for r in records)
        total_cache_write = sum(r["cache_write_tokens"] for r in records)
        total_cost = sum(
            r["input_cost_cny"] + r["output_cost_cny"]
            + r["cache_read_cost_cny"] + r["cache_write_cost_cny"]
            for r in records
        )
        durations = [r["duration_ms"] for r in records if r["duration_ms"]]
        avg_duration = round(sum(durations) / len(durations), 2) if durations else 0
        return {
            "period": period,
            "request_count": len(records),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_tokens": total_cache_read,
            "cache_write_tokens": total_cache_write,
            "total_tokens": total_input + total_output + total_cache_read + total_cache_write,
            "estimated_cost_cny": round(total_cost, 6),
            "avg_duration_ms": avg_duration,
        }

    def fetch_by_model(self, period: str) -> list:
        """按模型维度获取统计数据。"""
        records = self._fetch_unified_records(period)
        calculator = self._get_calculator()
        grouped: dict = {}
        for r in records:
            model = r["model"]
            if model not in grouped:
                grouped[model] = {
                    "model": model, "request_count": 0,
                    "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "total_tokens": 0, "estimated_cost_cny": 0.0,
                    "avg_duration_ms": 0,
                }
                grouped[model]["display_name"] = calculator.get_display_name(model)
            m = grouped[model]
            m["request_count"] += 1
            m["input_tokens"] += r["input_tokens"]
            m["output_tokens"] += r["output_tokens"]
            m["cache_read_tokens"] += r["cache_read_tokens"]
            m["cache_write_tokens"] += r["cache_write_tokens"]
            m["total_tokens"] += (r["input_tokens"] + r["output_tokens"]
                                 + r["cache_read_tokens"] + r["cache_write_tokens"])
            m["estimated_cost_cny"] += (r["input_cost_cny"] + r["output_cost_cny"]
                                       + r["cache_read_cost_cny"] + r["cache_write_cost_cny"])

        result = []
        for m in grouped.values():
            m["estimated_cost_cny"] = round(m["estimated_cost_cny"], 6)
            result.append(m)
        result.sort(key=lambda x: x["total_tokens"], reverse=True)
        return result

    def fetch_by_upstream(self, period: str) -> dict:
        """按上游维度获取统计数据。"""
        records = self._fetch_unified_records(period)
        resolver = self._upstream_resolver
        grouped: dict = {}

        for r in records:
            uid = r["upstream_id"]
            if uid not in grouped:
                grouped[uid] = {
                    "upstream_id": uid,
                    "request_count": 0, "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "total_tokens": 0, "total_cost": 0.0,
                }
            g = grouped[uid]
            g["request_count"] += 1
            g["input_tokens"] += r["input_tokens"]
            g["output_tokens"] += r["output_tokens"]
            g["cache_read_tokens"] += r["cache_read_tokens"]
            g["cache_write_tokens"] += r["cache_write_tokens"]
            g["total_tokens"] += (r["input_tokens"] + r["output_tokens"]
                                 + r["cache_read_tokens"] + r["cache_write_tokens"])
            g["total_cost"] += (r["input_cost_cny"] + r["output_cost_cny"]
                               + r["cache_read_cost_cny"] + r["cache_write_cost_cny"])

        result = []
        for uid, agg in grouped.items():
            if uid == "opencode":
                upstream_name = "[OpenCode]"
                base_url = None
            else:
                info = resolver.resolve_by_id(uid)
                upstream_name = info["upstream_name"]
                base_url = info.get("base_url")
            result.append({
                "upstream_id": uid,
                "upstream_name": upstream_name,
                "base_url": base_url,
                "request_count": agg["request_count"],
                "input_tokens": agg["input_tokens"],
                "output_tokens": agg["output_tokens"],
                "cache_read_tokens": agg["cache_read_tokens"],
                "cache_write_tokens": agg["cache_write_tokens"],
                "total_tokens": agg["total_tokens"],
                "estimated_cost_cny": round(agg["total_cost"], 6),
            })

        result.sort(key=lambda x: x["estimated_cost_cny"], reverse=True)
        return {"upstreams": result}

    def fetch_trend(self, period: str) -> list:
        """获取时间趋势数据，逐桶聚合。"""
        records = self._fetch_unified_records(period)

        if period in ("day", "24h"):
            def bucket_key(ts):
                return ts[:13] + ":00"
        else:
            def bucket_key(ts):
                return ts[:10]

        buckets: dict = {}
        for r in records:
            key = bucket_key(r["request_ts"])
            if key not in buckets:
                buckets[key] = {"date": key, "request_count": 0,
                                "input_tokens": 0, "output_tokens": 0,
                                "cache_read_tokens": 0, "cache_write_tokens": 0,
                                "estimated_cost_cny": 0.0}
            b = buckets[key]
            b["request_count"] += 1
            b["input_tokens"] += r["input_tokens"]
            b["output_tokens"] += r["output_tokens"]
            b["cache_read_tokens"] += r["cache_read_tokens"]
            b["cache_write_tokens"] += r["cache_write_tokens"]
            b["estimated_cost_cny"] += (r["input_cost_cny"] + r["output_cost_cny"]
                                        + r["cache_read_cost_cny"] + r["cache_write_cost_cny"])

        result = []
        for b in buckets.values():
            b["total_tokens"] = (b["input_tokens"] + b["output_tokens"]
                                 + b["cache_read_tokens"] + b["cache_write_tokens"])
            b["estimated_cost_cny"] = round(b["estimated_cost_cny"], 6)
            result.append(b)
        result.sort(key=lambda x: x["date"])
        return result

    def fetch_requests(
        self,
        period: str,
        model: str | None = None,
        request_type: str | None = None,
        source: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """获取请求详情列表（分页）。"""
        records, total = self._fetch_unified_records(
            period=period, model=model, request_type=request_type,
            source=source, limit=limit, offset=offset,
        )
        return {
            "requests": records,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def fetch_all_summaries(self) -> dict:
        """获取 day/week/month 三个周期的汇总数据。"""
        result = {}
        for period in ("day", "week", "month"):
            result[period] = self.fetch_summary(period)
        return result
    # ─── 辅助方法 ───

    def _get_calculator(self) -> _CostCalculator:
        """懒加载获取 _CostCalculator 单例。"""
        if not hasattr(self, "_cost_calculator"):
            self._cost_calculator = _CostCalculator(self.data_db_path)
        return self._cost_calculator

    def invalidate_pricing_cache(self):
        """失效定价缓存，供 API 层定价修改后调用。"""
        if hasattr(self, "_cost_calculator"):
            self._cost_calculator.invalidate_cache()

    def get_pricing(self) -> dict:
        """获取模型计费规则（委托 _CostCalculator）。

        Returns:
            {model_id: {input_cost, output_cost, cache_read_cost, cache_creation_cost}}
            cc-switch.db 不存在时返回空 dict。
        """
        return self._get_calculator().get_pricing()

    def calculate_cost(
        self,
        model: str,
        input_tokens: int | float | None,
        output_tokens: int | float | None,
        cache_read_tokens: int | float | None,
        cache_write_tokens: int | float | None,
    ) -> float:
        """根据模型计费规则计算成本（委托 _CostCalculator）。

        Args:
            model: 模型名称
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数
            cache_read_tokens: 缓存读取 token 数
            cache_write_tokens: 缓存写入 token 数

        Returns:
            总成本（float）。模型无定价时返回 0。
        """
        return self._get_calculator().calculate(
            model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
        )
