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

    def query_token_stats(
        self,
        period: str,
        model: str | None = None,
        request_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple:
        """查询请求详情列表（分页）。

        Args:
            period: 时间周期
            model: 可选，按 target_model 过滤
            request_type: 可选，按 request_type 过滤
            limit: 每页数量
            offset: 偏移量

        Returns:
            (rows, total_count) 元组
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
            # 获取总数
            total_row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM token_stats WHERE {where_clause}",
                params,
            ).fetchone()
            total_count = total_row["cnt"]

            # 获取分页数据
            rows = conn.execute(
                f"""
                SELECT id, request_id, request_type, model, target_model,
                       request_ts, duration_ms, input_tokens, output_tokens,
                       cached_read_tokens, cached_write_tokens, status
                FROM token_stats
                WHERE {where_clause}
                ORDER BY request_ts DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()

            return (rows, total_count)
        finally:
            conn.close()

    def aggregate_by_model(self, period: str) -> list:
        """按 target_model 分组聚合统计数据。

        Args:
            period: 时间周期

        Returns:
            按模型分组的聚合数据列表
        """
        time_condition = self._period_to_condition(period)

        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT target_model,
                       COUNT(*) as request_count,
                       SUM(input_tokens) as total_input,
                       SUM(output_tokens) as total_output,
                       SUM(cached_read_tokens) as total_cache_read,
                       SUM(cached_write_tokens) as total_cache_write,
                       AVG(duration_ms) as avg_duration
                FROM token_stats
                WHERE {time_condition}
                GROUP BY target_model
                ORDER BY total_output DESC
                """,
            ).fetchall()

            return [
                {
                    "model": row["target_model"],
                    "request_count": row["request_count"],
                    "input_tokens": row["total_input"],
                    "output_tokens": row["total_output"],
                    "cached_read_tokens": row["total_cache_read"],
                    "cached_write_tokens": row["total_cache_write"],
                    "total_tokens": (
                        row["total_input"]
                        + row["total_output"]
                        + row["total_cache_read"]
                        + row["total_cache_write"]
                    ),
                    "avg_duration_ms": round(row["avg_duration"], 2) if row["avg_duration"] else 0,
                }
                for row in rows
            ]
        finally:
            conn.close()

    def aggregate_by_upstream(self, period: str) -> list:
        """按 upstream_id 维度聚合统计数据（直接 GROUP BY + LEFT JOIN）。

        Args:
            period: 时间周期

        Returns:
            [{upstream_id, upstream_name, request_count, input_tokens,
              output_tokens, cache_read_tokens, cache_write_tokens, total_tokens}]
        """
        time_condition = self._period_to_condition(period)

        conn = self._get_conn()
        try:
            try:
                rows = conn.execute(
                    f"""
                    SELECT ts.upstream_id,
                           COALESCE(u.name, '__unknown__') as upstream_name,
                           u.base_url,
                           COUNT(*) as request_count,
                           COALESCE(SUM(ts.input_tokens), 0) as total_input,
                           COALESCE(SUM(ts.output_tokens), 0) as total_output,
                           COALESCE(SUM(ts.cached_read_tokens), 0) as total_cache_read,
                           COALESCE(SUM(ts.cached_write_tokens), 0) as total_cache_write
                    FROM token_stats ts
                    LEFT JOIN upstreams u ON ts.upstream_id = u.id
                    WHERE {time_condition}
                    GROUP BY ts.upstream_id
                    ORDER BY total_output DESC
                    """,
                ).fetchall()
            except sqlite3.OperationalError:
                # upstreams 表不存在时返回空列表
                return []

            return [
                {
                    "upstream_id": row["upstream_id"],
                    "upstream_name": row["upstream_name"],
                    "base_url": row["base_url"],
                    "request_count": row["request_count"],
                    "input_tokens": row["total_input"],
                    "output_tokens": row["total_output"],
                    "cache_read_tokens": row["total_cache_read"],
                    "cache_write_tokens": row["total_cache_write"],
                    "total_tokens": (
                        row["total_input"]
                        + row["total_output"]
                        + row["total_cache_read"]
                        + row["total_cache_write"]
                    ),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def aggregate_trend(self, period: str) -> list:
        """获取时间趋势数据。

        Args:
            period: 时间周期

        Returns:
            按时间分组的趋势数据点列表
        """
        time_condition = self._period_to_condition(period)

        # 根据周期决定时间粒度
        if period in ("day", "24h"):
            # 日内按小时聚合
            group_expr = "strftime('%Y-%m-%d %H:00', request_ts)"
        elif period in ("week", "7d"):
            # 周内按天聚合
            group_expr = "date(request_ts)"
        else:
            # 月内按天聚合
            group_expr = "date(request_ts)"

        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT {group_expr} as time_bucket,
                       COUNT(*) as request_count,
                       SUM(input_tokens) as total_input,
                       SUM(output_tokens) as total_output,
                       SUM(cached_read_tokens) as total_cache_read,
                       SUM(cached_write_tokens) as total_cache_write
                FROM token_stats
                WHERE {time_condition}
                GROUP BY time_bucket
                ORDER BY time_bucket ASC
                """,
            ).fetchall()

            return [
                {
                    "time": row["time_bucket"],
                    "request_count": row["request_count"],
                    "input_tokens": row["total_input"],
                    "output_tokens": row["total_output"],
                    "cached_read_tokens": row["total_cache_read"],
                    "cached_write_tokens": row["total_cache_write"],
                    "total_tokens": (
                        row["total_input"]
                        + row["total_output"]
                        + row["total_cache_read"]
                        + row["total_cache_write"]
                    ),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def aggregate_summary(self, period: str) -> dict:
        """获取汇总统计数据。

        Args:
            period: 时间周期

        Returns:
            汇总统计 dict
        """
        time_condition = self._period_to_condition(period)

        conn = self._get_conn()
        try:
            row = conn.execute(
                f"""
                SELECT COUNT(*) as request_count,
                       COALESCE(SUM(input_tokens), 0) as total_input,
                       COALESCE(SUM(output_tokens), 0) as total_output,
                       COALESCE(SUM(cached_read_tokens), 0) as total_cache_read,
                       COALESCE(SUM(cached_write_tokens), 0) as total_cache_write,
                       AVG(duration_ms) as avg_duration
                FROM token_stats
                WHERE {time_condition}
                """,
            ).fetchone()

            total_input = row["total_input"]
            total_output = row["total_output"]
            total_cache_read = row["total_cache_read"]
            total_cache_write = row["total_cache_write"]

            return {
                "period": period,
                "request_count": row["request_count"] or 0,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cached_read_tokens": total_cache_read,
                "cached_write_tokens": total_cache_write,
                "total_tokens": total_input + total_output + total_cache_read + total_cache_write,
                "avg_duration_ms": round(row["avg_duration"], 2) if row["avg_duration"] else 0,
            }
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



class _SessionDao:
    """Sessions 表数据访问对象 — 从 state.db 读取 AI Coding Session 数据。

    将 sessions 包装为与 token_stats 统一的记录格式，支持按模型聚合。
    state.db 不存在时返回空列表，不抛异常。

    Args:
        db_path: state.db 路径
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    # ─── 工具方法 ───

    @staticmethod
    def _period_to_condition(period: str) -> str:
        """将 period 转换为 SQLite 时间条件（Unix 时间戳比较）。"""
        mapping = {
            "day": "strftime('%s', 'now', '-1 day')",
            "24h": "strftime('%s', 'now', '-1 day')",
            "week": "strftime('%s', 'now', '-7 days')",
            "7d": "strftime('%s', 'now', '-7 days')",
            "month": "strftime('%s', 'now', '-30 days')",
            "30d": "strftime('%s', 'now', '-30 days')",
        }
        threshold = mapping.get(period, "strftime('%s', 'now', '-7 days')")
        return f"started_at >= {threshold}"

    @staticmethod
    def _normalize_model_name(name: str) -> str:
        """去掉模型名中的 [xxx] 上下文后缀。"""
        if not name:
            return name
        bracket_pos = name.find("[")
        if bracket_pos >= 0:
            return name[:bracket_pos].rstrip()
        return name

    def _get_conn(self) -> sqlite3.Connection | None:
        """创建数据库连接，state.db 不存在时返回 None。"""
        if not self.db_path.exists():
            return None
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> dict:
        """将 sessions 行包装为统一格式记录。"""
        model = row["model"] or ""
        normalized = _SessionDao._normalize_model_name(model)
        ts = row["started_at"]
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "request_id": f"sess-{row['id']}",
            "request_type": "session",
            "model": model,
            "target_model": normalized,
            "request_ts": ts,
            "duration_ms": None,
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cached_read_tokens": row["cache_read_tokens"],
            "cached_write_tokens": row["cache_write_tokens"],
            "status": "completed",
            "_source": "session",
        }

    # ─── 查询方法 ───

    def query_sessions(self, period: str, model: str | None = None) -> list:
        """查询 sessions 记录。

        Args:
            period: 时间周期
            model: 可选，按模型过滤（使用 normalized 名）

        Returns:
            统一格式的记录列表
        """
        conn = self._get_conn()
        if conn is None:
            return []
        try:
            time_condition = self._period_to_condition(period)
            conditions = [time_condition, "input_tokens IS NOT NULL"]
            params: list = []

            if model:
                # 精确匹配或 [ctx] 后缀前缀匹配
                conditions.append("(model = ? OR model LIKE ?)")
                params.extend([model, f"{model}[%"])

            where_clause = " AND ".join(conditions)

            rows = conn.execute(
                f"""
                SELECT id, model, started_at, input_tokens, output_tokens,
                       cache_read_tokens, cache_write_tokens
                FROM sessions
                WHERE {where_clause}
                ORDER BY started_at DESC
                """,
                params,
            ).fetchall()

            return [self._row_to_record(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    def query_sessions_paged(self, period: str, model: str | None = None,
                             request_type: str | None = None,
                             limit: int = 50, offset: int = 0) -> tuple:
        """查询 sessions 记录（支持分页）。

        Args:
            period: 时间周期
            model: 可选，按模型过滤（使用 normalized 名）
            request_type: 可选，按请求类型过滤（sessions 固定为 'session'）
            limit: 每页数量
            offset: 偏移量

        Returns:
            (rows, total_count) 元组，rows 为统一格式的记录列表
        """
        conn = self._get_conn()
        if conn is None:
            return ([], 0)
        try:
            time_condition = self._period_to_condition(period)
            conditions = [time_condition, "input_tokens IS NOT NULL"]
            params_count: list = []
            params_data: list = []

            if model:
                conditions.append("(model = ? OR model LIKE ?)")
                params_count.extend([model, f"{model} [%"])
                params_data.extend([model, f"{model} [%"])

            if request_type:
                if request_type == "session":
                    pass
                else:
                    return ([], 0)

            where_clause = " AND ".join(conditions)

            total_row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM sessions WHERE {where_clause}",
                params_count,
            ).fetchone()
            total_count = total_row["cnt"]

            rows = conn.execute(
                f"SELECT id, model, started_at, input_tokens, output_tokens, "
                f"cache_read_tokens, cache_write_tokens "
                f"FROM sessions WHERE {where_clause} "
                f"ORDER BY started_at DESC LIMIT ? OFFSET ?",
                params_data + [limit, offset],
            ).fetchall()

            return ([self._row_to_record(r) for r in rows], total_count)
        except Exception:
            return ([], 0)
        finally:
            conn.close()

    def aggregate_by_model(self, period: str) -> list:
        """按模型分组聚合 sessions 数据。

        Args:
            period: 时间周期

        Returns:
            按模型聚合的统计列表
        """
        conn = self._get_conn()
        if conn is None:
            return []
        try:
            time_condition = self._period_to_condition(period)

            rows = conn.execute(
                f"""
                SELECT model,
                       COUNT(*) as session_count,
                       SUM(input_tokens) as total_input,
                       SUM(output_tokens) as total_output,
                       SUM(cache_read_tokens) as total_cache_read,
                       SUM(cache_write_tokens) as total_cache_write
                FROM sessions
                WHERE {time_condition} AND input_tokens IS NOT NULL AND model IS NOT NULL
                GROUP BY model
                """,
            ).fetchall()

            # 按 normalized model 名合并
            model_map: dict = {}
            for row in rows:
                base = self._normalize_model_name(row["model"])
                if base not in model_map:
                    model_map[base] = {
                        "model": base,
                        "request_count": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cached_read_tokens": 0,
                        "cached_write_tokens": 0,
                    }
                m = model_map[base]
                m["request_count"] += row["session_count"] or 0
                m["input_tokens"] += row["total_input"] or 0
                m["output_tokens"] += row["total_output"] or 0
                m["cached_read_tokens"] += row["total_cache_read"] or 0
                m["cached_write_tokens"] += row["total_cache_write"] or 0

            result = []
            for m in model_map.values():
                m["total_tokens"] = (
                    m["input_tokens"] + m["output_tokens"]
                    + m["cached_read_tokens"] + m["cached_write_tokens"]
                )
                result.append(m)

            result.sort(key=lambda x: x["total_tokens"], reverse=True)
            return result
        except Exception:
            return []
        finally:
            conn.close()

    def aggregate_summary(self, period: str) -> dict:
        """汇总 sessions 数据，返回与 _TokenStatsDao.aggregate_summary 相同结构的 dict。"""
        conn = self._get_conn()
        if conn is None:
            return {"period": period, "request_count": 0, "input_tokens": 0,
                    "output_tokens": 0, "cached_read_tokens": 0,
                    "cached_write_tokens": 0, "total_tokens": 0, "avg_duration_ms": 0}
        try:
            time_condition = self._period_to_condition(period)
            row = conn.execute(
                f"""SELECT COUNT(*) as session_count,
                           COALESCE(SUM(input_tokens), 0) as total_input,
                           COALESCE(SUM(output_tokens), 0) as total_output,
                           COALESCE(SUM(cache_read_tokens), 0) as total_cache_read,
                           COALESCE(SUM(cache_write_tokens), 0) as total_cache_write
                    FROM sessions
                    WHERE {time_condition} AND input_tokens IS NOT NULL""",
            ).fetchone()
            total_input = row["total_input"]
            total_output = row["total_output"]
            total_cache_read = row["total_cache_read"]
            total_cache_write = row["total_cache_write"]
            return {
                "period": period,
                "request_count": row["session_count"] or 0,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cached_read_tokens": total_cache_read,
                "cached_write_tokens": total_cache_write,
                "total_tokens": total_input + total_output + total_cache_read + total_cache_write,
                "avg_duration_ms": 0,
            }
        except Exception:
            return {"period": period, "request_count": 0, "input_tokens": 0,
                    "output_tokens": 0, "cached_read_tokens": 0,
                    "cached_write_tokens": 0, "total_tokens": 0, "avg_duration_ms": 0}
        finally:
            conn.close()

    def aggregate_trend(self, period: str) -> list:
        """按时间粒度聚合 sessions 数据，返回与 _TokenStatsDao.aggregate_trend 相同结构的 list。
        started_at 是 Unix 时间戳，分组时需用 datetime(started_at, 'unixepoch', 'localtime')。"""
        conn = self._get_conn()
        if conn is None:
            return []
        try:
            time_condition = self._period_to_condition(period)
            if period in ("day", "24h"):
                group_expr = "strftime('%Y-%m-%d %H:00', datetime(started_at, 'unixepoch', 'localtime'))"
            else:
                group_expr = "date(datetime(started_at, 'unixepoch', 'localtime'))"

            rows = conn.execute(
                f"""SELECT {group_expr} as time_bucket,
                           COUNT(*) as session_count,
                           SUM(input_tokens) as total_input,
                           SUM(output_tokens) as total_output,
                           SUM(cache_read_tokens) as total_cache_read,
                           SUM(cache_write_tokens) as total_cache_write
                    FROM sessions
                    WHERE {time_condition} AND input_tokens IS NOT NULL
                    GROUP BY time_bucket
                    ORDER BY time_bucket ASC""",
            ).fetchall()

            return [
                {
                    "time": row["time_bucket"],
                    "request_count": row["session_count"],
                    "input_tokens": row["total_input"],
                    "output_tokens": row["total_output"],
                    "cached_read_tokens": row["total_cache_read"],
                    "cached_write_tokens": row["total_cache_write"],
                    "total_tokens": (row["total_input"] + row["total_output"]
                                     + row["total_cache_read"] + row["total_cache_write"]),
                }
                for row in rows
            ]
        except Exception:
            return []
        finally:
            conn.close()


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

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> dict:
        """将 message 行包装为统一格式记录。"""
        return {
            "request_id": f"oc-msg-{row['message_id']}",
            "request_type": "session",
            "model": row["model_id"] or "",
            "target_model": row["model_id"] or "",
            "request_ts": row["request_ts"],
            "duration_ms": row["duration_ms"],
            "input_tokens": row["input_tokens"] or 0,
            "output_tokens": row["output_tokens"] or 0,
            "cached_read_tokens": row["cache_read_tokens"] or 0,
            "cached_write_tokens": row["cache_write_tokens"] or 0,
            "status": "completed",
            "_source": "opencode",
        }

    def _get_conn(self) -> sqlite3.Connection | None:
        """创建数据库连接，opencode.db 不存在时返回 None。"""
        if not self.db_path.exists():
            return None
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ─── 查询方法 ───

    def aggregate_by_model(self, period: str) -> list:
        """按 modelID 分组聚合 token 统计数据。"""
        conn = self._get_conn()
        if conn is None:
            return []
        try:
            time_condition = self._period_to_condition(period)
            rows = conn.execute(f"""
                SELECT
                    json_extract(m.data, '$.modelID') as model,
                    COUNT(*) as request_count,
                    SUM(CAST(json_extract(m.data, '$.tokens.input') AS INTEGER)) as total_input,
                    SUM(CAST(json_extract(m.data, '$.tokens.output') AS INTEGER)
                        + CAST(json_extract(m.data, '$.tokens.reasoning') AS INTEGER)) as total_output,
                    SUM(CAST(json_extract(m.data, '$.tokens.cache.read') AS INTEGER)) as total_cache_read,
                    SUM(CAST(json_extract(m.data, '$.tokens.cache.write') AS INTEGER)) as total_cache_write
                FROM message m
                WHERE {time_condition}
                  AND json_extract(m.data, '$.tokens.input') IS NOT NULL
                GROUP BY json_extract(m.data, '$.modelID')
                ORDER BY total_output DESC
            """).fetchall()

            return [
                {
                    "model": row["model"] or "",
                    "request_count": row["request_count"],
                    "input_tokens": row["total_input"] or 0,
                    "output_tokens": row["total_output"] or 0,
                    "cached_read_tokens": row["total_cache_read"] or 0,
                    "cached_write_tokens": row["total_cache_write"] or 0,
                    "total_tokens": (row["total_input"] or 0)
                    + (row["total_output"] or 0)
                    + (row["total_cache_read"] or 0)
                    + (row["total_cache_write"] or 0),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def aggregate_summary(self, period: str) -> dict:
        """汇总 opencode 数据，返回与 _TokenStatsDao.aggregate_summary 相同结构的 dict。"""
        conn = self._get_conn()
        if conn is None:
            return {"period": period, "request_count": 0, "input_tokens": 0,
                    "output_tokens": 0, "cached_read_tokens": 0,
                    "cached_write_tokens": 0, "total_tokens": 0, "avg_duration_ms": 0}
        try:
            time_condition = self._period_to_condition(period)
            row = conn.execute(f"""
                SELECT COUNT(*) as request_count,
                       COALESCE(SUM(CAST(json_extract(m.data, '$.tokens.input') AS INTEGER)), 0) as total_input,
                       COALESCE(SUM(CAST(json_extract(m.data, '$.tokens.output') AS INTEGER)
                           + CAST(json_extract(m.data, '$.tokens.reasoning') AS INTEGER)), 0) as total_output,
                       COALESCE(SUM(CAST(json_extract(m.data, '$.tokens.cache.read') AS INTEGER)), 0) as total_cache_read,
                       COALESCE(SUM(CAST(json_extract(m.data, '$.tokens.cache.write') AS INTEGER)), 0) as total_cache_write,
                       AVG(json_extract(m.data, '$.time.completed')
                           - json_extract(m.data, '$.time.created')) as avg_duration
                FROM message m
                WHERE {time_condition}
                  AND json_extract(m.data, '$.tokens.input') IS NOT NULL
            """).fetchone()
            total_input = row["total_input"]
            total_output = row["total_output"]
            total_cache_read = row["total_cache_read"]
            total_cache_write = row["total_cache_write"]
            return {
                "period": period,
                "request_count": row["request_count"] or 0,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cached_read_tokens": total_cache_read,
                "cached_write_tokens": total_cache_write,
                "total_tokens": total_input + total_output + total_cache_read + total_cache_write,
                "avg_duration_ms": round(row["avg_duration"], 2) if row["avg_duration"] else 0,
            }
        finally:
            conn.close()

    def aggregate_trend(self, period: str) -> list:
        """按时间粒度聚合 opencode 数据，返回 time key。"""
        conn = self._get_conn()
        if conn is None:
            return []
        try:
            time_condition = self._period_to_condition(period)
            if period in ("day", "24h"):
                group_expr = "strftime('%Y-%m-%d %H:00', datetime(m.time_created / 1000, 'unixepoch', 'localtime'))"
            else:
                group_expr = "date(datetime(m.time_created / 1000, 'unixepoch', 'localtime'))"

            rows = conn.execute(f"""
                SELECT {group_expr} as time_bucket,
                       COUNT(*) as request_count,
                       SUM(CAST(json_extract(m.data, '$.tokens.input') AS INTEGER)) as total_input,
                       SUM(CAST(json_extract(m.data, '$.tokens.output') AS INTEGER)
                           + CAST(json_extract(m.data, '$.tokens.reasoning') AS INTEGER)) as total_output,
                       SUM(CAST(json_extract(m.data, '$.tokens.cache.read') AS INTEGER)) as total_cache_read,
                       SUM(CAST(json_extract(m.data, '$.tokens.cache.write') AS INTEGER)) as total_cache_write
                FROM message m
                WHERE {time_condition}
                  AND json_extract(m.data, '$.tokens.input') IS NOT NULL
                GROUP BY time_bucket
                ORDER BY time_bucket ASC
            """).fetchall()

            return [
                {
                    "time": row["time_bucket"],
                    "request_count": row["request_count"],
                    "input_tokens": row["total_input"] or 0,
                    "output_tokens": row["total_output"] or 0,
                    "cached_read_tokens": row["total_cache_read"] or 0,
                    "cached_write_tokens": row["total_cache_write"] or 0,
                    "total_tokens": (row["total_input"] or 0)
                    + (row["total_output"] or 0)
                    + (row["total_cache_read"] or 0)
                    + (row["total_cache_write"] or 0),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def query_messages_paged(self, period: str, model: str | None = None,
                             request_type: str | None = None,
                             limit: int = 50, offset: int = 0) -> tuple:
        """分页查询 opencode messages（请求日志）。

        Args:
            period: 时间周期
            model: 可选，按 modelID 过滤
            request_type: 可选，仅 "session" 时返回数据，其余返回空
            limit: 每页数量
            offset: 偏移量

        Returns:
            (records_list, total_count) 元组
        """
        if request_type and request_type != "session":
            return ([], 0)

        conn = self._get_conn()
        if conn is None:
            return ([], 0)
        try:
            time_condition = self._period_to_condition(period)
            conditions = [time_condition, "json_extract(m.data, '$.tokens.input') IS NOT NULL"]
            params_count: list = []
            params_data: list = []

            if model:
                conditions.append("json_extract(m.data, '$.modelID') = ?")
                params_count.append(model)
                params_data.append(model)

            where_clause = " AND ".join(conditions)

            total_row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM message m "
                f"WHERE {where_clause}", params_count).fetchone()
            total_count = total_row["cnt"]

            rows = conn.execute(f"""
                SELECT m.id as message_id,
                       json_extract(m.data, '$.modelID') as model_id,
                       datetime(m.time_created / 1000, 'unixepoch') as request_ts,
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
                LIMIT ? OFFSET ?
            """, params_data + [limit, offset]).fetchall()

            return ([self._row_to_record(r) for r in rows], total_count)
        except Exception:
            return ([], 0)
        finally:
            conn.close()


class _Merger:
    """N 数据源合并：按规范化模型名求和，字段名统一为 cache_*，趋势 key 统一为 date"""

    _RENAME_MAP = {
        "cached_read_tokens": "cache_read_tokens",
        "cached_write_tokens": "cache_write_tokens",
        "time": "date",
    }

    @classmethod
    def _rename(cls, d: dict) -> dict:
        """将 cached_* 字段重命名为 cache_*，time 重命名为 date"""
        result = {}
        for k, v in d.items():
            result[cls._RENAME_MAP.get(k, k)] = v
        return result

    @staticmethod
    def merge_summary(*summaries: dict) -> dict:
        """合并 N 个汇总 dict，各数值字段求和，字段重命名为 cache*。
        avg_duration_ms 优先级：proxy > opencode > session（取第一个非零值）。"""
        result = {}
        avg_duration = 0
        for s in summaries:
            s = _Merger._rename(s)
            if not result:
                result = {
                    "period": s.get("period", "week"),
                    "request_count": s.get("request_count", 0),
                    "input_tokens": s.get("input_tokens", 0),
                    "output_tokens": s.get("output_tokens", 0),
                    "cache_read_tokens": s.get("cache_read_tokens", 0),
                    "cache_write_tokens": s.get("cache_write_tokens", 0),
                    "total_tokens": s.get("total_tokens", 0),
                    "avg_duration_ms": s.get("avg_duration_ms", 0),
                }
                avg_duration = s.get("avg_duration_ms", 0)
            else:
                result["request_count"] += s.get("request_count", 0)
                result["input_tokens"] += s.get("input_tokens", 0)
                result["output_tokens"] += s.get("output_tokens", 0)
                result["cache_read_tokens"] += s.get("cache_read_tokens", 0)
                result["cache_write_tokens"] += s.get("cache_write_tokens", 0)
                result["total_tokens"] = (result["input_tokens"] + result["output_tokens"]
                                          + result["cache_read_tokens"] + result["cache_write_tokens"])
                if avg_duration == 0 and s.get("avg_duration_ms", 0) > 0:
                    result["avg_duration_ms"] = s["avg_duration_ms"]
                    avg_duration = s["avg_duration_ms"]
        return result

    @staticmethod
    def merge_model_lists(*lists: list) -> list:
        """合并 N 个 by_model 列表，同名模型 token 求和，字段重命名为 cache*"""
        merged: dict = {}
        for items in lists:
            for item in items:
                r = _Merger._rename(item)
                model = _SessionDao._normalize_model_name(r["model"])
                if model not in merged:
                    merged[model] = {"model": model, "request_count": 0,
                                     "input_tokens": 0, "output_tokens": 0,
                                     "cache_read_tokens": 0, "cache_write_tokens": 0,
                                     "avg_duration_ms": 0}
                m = merged[model]
                m["request_count"] += r.get("request_count", 0)
                m["input_tokens"] += r.get("input_tokens", 0)
                m["output_tokens"] += r.get("output_tokens", 0)
                m["cache_read_tokens"] += r.get("cache_read_tokens", 0)
                m["cache_write_tokens"] += r.get("cache_write_tokens", 0)
                avg = r.get("avg_duration_ms", 0)
                if avg:
                    m["avg_duration_ms"] = avg

        for m in merged.values():
            m["total_tokens"] = (m["input_tokens"] + m["output_tokens"]
                                 + m["cache_read_tokens"] + m["cache_write_tokens"])
        return list(merged.values())

    @staticmethod
    def merge_trend_lists(*lists: list) -> list:
        """合并 N 个趋势列表，同时间点各指标求和，字段重命名为 cache*，key 统一为 date"""
        merged: dict = {}
        for items in lists:
            for item in items:
                r = _Merger._rename(item)
                key = r.get("date", r.get("time", ""))
                if key not in merged:
                    merged[key] = {"date": key, "request_count": 0,
                                   "input_tokens": 0, "output_tokens": 0,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0}
                m = merged[key]
                m["request_count"] += r.get("request_count", 0)
                m["input_tokens"] += r.get("input_tokens", 0)
                m["output_tokens"] += r.get("output_tokens", 0)
                m["cache_read_tokens"] += r.get("cache_read_tokens", 0)
                m["cache_write_tokens"] += r.get("cache_write_tokens", 0)

        for m in merged.values():
            m["total_tokens"] = (m["input_tokens"] + m["output_tokens"]
                                 + m["cache_read_tokens"] + m["cache_write_tokens"])
        return list(merged.values())


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
        state_db_path: str,
        opencode_db_path: str | None = None,
    ) -> None:
        self.data_db_path = Path(data_db_path) if data_db_path else DATA_DB
        self.state_db_path = Path(state_db_path)

        self.opencode_db_path = Path(opencode_db_path) if opencode_db_path else self._OPENCODE_DB_DEFAULT
        self._opencode_dao = None  # 懒加载

        # 初始化上游解析器
        self._upstream_resolver = _UpstreamResolver(self.data_db_path)

    # ─── TokenStatsDao 实例 ───

    def _get_dao(self) -> _TokenStatsDao:
        """获取 TokenStatsDao 实例。"""
        return _TokenStatsDao(self.data_db_path)

    def _get_session_dao(self) -> _SessionDao:
        """获取 SessionDao 实例。"""
        return _SessionDao(self.state_db_path)

    def _get_opencode_dao(self):
        """获取 OpenCodeDao 实例，数据库不存在时返回 None。"""
        if self._opencode_dao is None:
            dao = _OpenCodeDao(self.opencode_db_path)
            self._opencode_dao = dao if dao.db_path.exists() else None
        return self._opencode_dao

    # ─── Provider 接口 ───

    def fetch_by_model(self, period: str) -> list:
        """按模型维度获取统计数据，合并 proxy + sessions + opencode 三源。"""
        dao = self._get_dao()
        session_dao = self._get_session_dao()
        opencode_dao = self._get_opencode_dao()
        proxy_models = dao.aggregate_by_model(period)
        session_models = session_dao.aggregate_by_model(period)
        opencode_models = opencode_dao.aggregate_by_model(period) if opencode_dao else []
        merged = _Merger.merge_model_lists(proxy_models, session_models, opencode_models)
        calculator = self._get_calculator()
        for m in merged:
            m["estimated_cost_cny"] = round(calculator.calculate(
                model=m["model"], input_tokens=m["input_tokens"],
                output_tokens=m["output_tokens"],
                cache_read_tokens=m["cache_read_tokens"],
                cache_write_tokens=m["cache_write_tokens"],
            ), 6)
            m["display_name"] = calculator.get_display_name(m["model"])
        merged.sort(key=lambda x: x.get("total_tokens", 0), reverse=True)
        return merged

    def fetch_requests(
        self,
        period: str,
        model: str | None = None,
        request_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """获取请求详情列表（分页），合并 token_stats 和 sessions 两个数据源。

        Args:
            period: 时间周期
            model: 可选，按模型过滤
            request_type: 可选，按请求类型过滤
            limit: 每页数量
            offset: 偏移量

        Returns:
            {requests: [...], total: int, limit: int, offset: int}
        """
        fetch_limit = limit + offset

        token_dao = self._get_dao()
        token_rows, token_total = token_dao.query_token_stats(
            period=period,
            model=model,
            request_type=request_type,
            limit=fetch_limit,
            offset=0,
        )

        session_dao = self._get_session_dao()
        session_rows, session_total = session_dao.query_sessions_paged(
            period=period,
            model=model,
            request_type=request_type,
            limit=fetch_limit,
            offset=0,
        )

        opencode_dao = self._get_opencode_dao()
        if opencode_dao:
            oc_rows, oc_total = opencode_dao.query_messages_paged(
                period=period,
                model=model,
                request_type=request_type,
                limit=fetch_limit,
                offset=0,
            )
        else:
            oc_rows, oc_total = [], 0

        calculator = self._get_calculator()
        unified_requests = []

        for row in token_rows:
            row_dict = dict(row) if hasattr(row, 'keys') else dict(row)
            row_dict = _Merger._rename(row_dict)
            row_dict["_source"] = "proxy"
            row_dict["estimated_cost_cny"] = calculator.calculate(
                model=row_dict.get("target_model", row_dict.get("model", "")),
                input_tokens=row_dict.get("input_tokens", 0),
                output_tokens=row_dict.get("output_tokens", 0),
                cache_read_tokens=row_dict.get("cache_read_tokens", 0),
                cache_write_tokens=row_dict.get("cache_write_tokens", 0),
            )
            unified_requests.append(row_dict)

        for rec in session_rows:
            rec = _Merger._rename(rec)
            rec["estimated_cost_cny"] = calculator.calculate(
                model=rec.get("target_model", rec.get("model", "")),
                input_tokens=rec.get("input_tokens", 0),
                output_tokens=rec.get("output_tokens", 0),
                cache_read_tokens=rec.get("cache_read_tokens", 0),
                cache_write_tokens=rec.get("cache_write_tokens", 0),
            )
            unified_requests.append(rec)

        for rec in oc_rows:
            rec = _Merger._rename(rec)
            rec["estimated_cost_cny"] = calculator.calculate(
                model=rec.get("target_model", rec.get("model", "")),
                input_tokens=rec.get("input_tokens", 0),
                output_tokens=rec.get("output_tokens", 0),
                cache_read_tokens=rec.get("cache_read_tokens", 0),
                cache_write_tokens=rec.get("cache_write_tokens", 0),
            )
            unified_requests.append(rec)

        unified_requests.sort(key=lambda x: x.get("request_ts", ""), reverse=True)

        total = token_total + session_total + oc_total
        paginated = unified_requests[offset:offset + limit]

        return {
            "requests": paginated,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def fetch_by_upstream(self, period: str) -> dict:
        """按上游维度获取统计数据，三源独立归桶。

        Proxy 按 aggregate_by_upstream() 分组，Hermes sessions 归入 [Hermes]，
        OpenCode 归入 [OpenCode]，每项计算成本后按 estimated_cost_cny 降序排列。

        Args:
            period: 时间周期

        Returns:
            {upstreams: [{upstream_id, base_url, upstream_name, request_count,
              input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
              total_tokens, estimated_cost_cny}]}
        """
        token_dao = self._get_dao()
        session_dao = self._get_session_dao()
        opencode_dao = self._get_opencode_dao()
        calculator = self._get_calculator()

        # 1. Proxy 数据 — 直接 GROUP BY upstream_id
        proxy_data = token_dao.aggregate_by_upstream(period)

        # 2. Hermes sessions → [Hermes] 桶（汇总 aggregate_by_model）
        session_models = session_dao.aggregate_by_model(period)
        hermes_bucket = {
            "upstream_id": "[Hermes]",
            "upstream_name": "[Hermes]",
            "base_url": None,
            "request_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
        }
        for m in session_models:
            hermes_bucket["request_count"] += m.get("request_count", 0)
            hermes_bucket["input_tokens"] += m.get("input_tokens", 0)
            hermes_bucket["output_tokens"] += m.get("output_tokens", 0)
            hermes_bucket["cache_read_tokens"] += m.get("cache_read_tokens", 0)
            hermes_bucket["cache_write_tokens"] += m.get("cache_write_tokens", 0)
        hermes_bucket["total_tokens"] = (
            hermes_bucket["input_tokens"] + hermes_bucket["output_tokens"]
            + hermes_bucket["cache_read_tokens"] + hermes_bucket["cache_write_tokens"]
        )

        # 3. OpenCode → [OpenCode] 桶
        oc_bucket = {
            "upstream_id": "[OpenCode]",
            "upstream_name": "[OpenCode]",
            "base_url": None,
            "request_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
        }
        if opencode_dao:
            oc_models = opencode_dao.aggregate_by_model(period)
            for m in oc_models:
                oc_bucket["request_count"] += m.get("request_count", 0)
                oc_bucket["input_tokens"] += m.get("input_tokens", 0)
                oc_bucket["output_tokens"] += m.get("output_tokens", 0)
                oc_bucket["cache_read_tokens"] += m.get("cache_read_tokens", 0)
                oc_bucket["cache_write_tokens"] += m.get("cache_write_tokens", 0)
        oc_bucket["total_tokens"] = (
            oc_bucket["input_tokens"] + oc_bucket["output_tokens"]
            + oc_bucket["cache_read_tokens"] + oc_bucket["cache_write_tokens"]
        )

        # 4. 合并三源（proxy 已按 upstream_id 分组）
        merged: dict = {}
        for item in proxy_data:
            merged[item["upstream_id"]] = item

        if hermes_bucket["request_count"] > 0:
            merged[hermes_bucket["upstream_id"]] = hermes_bucket
        if oc_bucket["request_count"] > 0:
            merged[oc_bucket["upstream_id"]] = oc_bucket

        # 5. 计算成本并格式化
        result = []
        for uid, agg in merged.items():
            cost = calculator.calculate(
                model=agg.get("upstream_name", ""),
                input_tokens=agg["input_tokens"],
                output_tokens=agg["output_tokens"],
                cache_read_tokens=agg["cache_read_tokens"],
                cache_write_tokens=agg["cache_write_tokens"],
            )
            result.append({
                "upstream_id": uid,
                "base_url": agg.get("base_url"),
                "upstream_name": agg["upstream_name"],
                "request_count": agg["request_count"],
                "input_tokens": agg["input_tokens"],
                "output_tokens": agg["output_tokens"],
                "cache_read_tokens": agg["cache_read_tokens"],
                "cache_write_tokens": agg["cache_write_tokens"],
                "total_tokens": agg["total_tokens"],
                "estimated_cost_cny": round(cost, 6),
            })

        # 6. 按 estimated_cost_cny 降序排列
        result.sort(key=lambda x: x["estimated_cost_cny"], reverse=True)
        return {"upstreams": result}

    def fetch_trend(self, period: str) -> list:
        """获取时间趋势数据，合并 proxy + sessions + opencode 三源。逐点计算成本。"""
        dao = self._get_dao()
        session_dao = self._get_session_dao()
        opencode_dao = self._get_opencode_dao()
        proxy_trend = dao.aggregate_trend(period)
        session_trend = session_dao.aggregate_trend(period)
        opencode_trend = opencode_dao.aggregate_trend(period) if opencode_dao else []
        merged = _Merger.merge_trend_lists(proxy_trend, session_trend, opencode_trend)
        merged.sort(key=lambda x: x.get("date", ""))

        # 加权均摊：从 per-model 汇总数据计算总成本，按 token 比例均摊到每个时间桶
        by_model = self.fetch_by_model(period)
        total_cost = sum(m.get("estimated_cost_cny", 0) for m in by_model)
        total_tokens = sum(m.get("total_tokens", 0) or 0 for m in by_model)

        for point in merged:
            point_tokens = point.get("total_tokens", 0) or (
                point.get("input_tokens", 0) + point.get("output_tokens", 0)
                + point.get("cache_read_tokens", 0) + point.get("cache_write_tokens", 0)
            )
            if total_tokens > 0:
                point["estimated_cost_cny"] = total_cost * point_tokens / total_tokens
            else:
                point["estimated_cost_cny"] = 0.0

        return merged

    def fetch_summary(self, period: str) -> dict:
        """获取汇总统计数据，合并 proxy + sessions + opencode 三源。成本按模型逐个计算后求和。"""
        dao = self._get_dao()
        session_dao = self._get_session_dao()
        opencode_dao = self._get_opencode_dao()
        proxy = dao.aggregate_summary(period)
        session = session_dao.aggregate_summary(period)
        opencode = opencode_dao.aggregate_summary(period) if opencode_dao else {}
        result = _Merger.merge_summary(proxy, session, opencode)
        # 成本按模型逐个计算再求和
        proxy_models = dao.aggregate_by_model(period)
        session_models = session_dao.aggregate_by_model(period)
        opencode_models = opencode_dao.aggregate_by_model(period) if opencode_dao else []
        merged_models = _Merger.merge_model_lists(proxy_models, session_models, opencode_models)
        calculator = self._get_calculator()
        total_cost = 0
        for m in merged_models:
            total_cost += calculator.calculate(
                model=m["model"], input_tokens=m["input_tokens"],
                output_tokens=m["output_tokens"],
                cache_read_tokens=m["cache_read_tokens"],
                cache_write_tokens=m["cache_write_tokens"],
            )
        result["estimated_cost_cny"] = round(total_cost, 6)
        return result

    def fetch_all_summaries(self) -> dict:
        """获取 day/week/month 三个周期的汇总数据。"""
        result = {}
        for period in ("day", "week", "month"):
            result[period] = self.fetch_summary(period)
        return result

    def fetch_by_model_requests(
        self,
        model: str,
        period: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """获取指定模型的请求详情列表(分页),合并 token_stats 和 sessions 数据源。

        Args:
            model: 模型名称
            period: 时间周期
            limit: 每页数量
            offset: 偏移量

        Returns:
            {model: str, requests: [...], total: int, limit: int, offset: int}
        """
        fetch_limit = limit + offset

        # 1. 从 token_stats 取 limit+offset 条
        token_dao = self._get_dao()
        token_rows, token_total = token_dao.query_token_stats(
            period=period,
            model=model,
            limit=fetch_limit,
            offset=0,
        )

        # 2. 从 sessions 取 limit+offset 条(按 model 过滤,会用 _normalize_model_name 匹配)
        session_dao = self._get_session_dao()
        session_rows, session_total = session_dao.query_sessions_paged(
            period=period,
            model=model,
            limit=fetch_limit,
            offset=0,
        )

        opencode_dao = self._get_opencode_dao()
        if opencode_dao:
            oc_rows, oc_total = opencode_dao.query_messages_paged(
                period=period,
                model=model,
                request_type=None,
                limit=fetch_limit,
                offset=0,
            )
        else:
            oc_rows, oc_total = [], 0

        # 3. 合并两个列表,统一添加 estimated_cost_cny 字段
        calculator = self._get_calculator()
        unified_requests = []

        for row in token_rows:
            row_dict = dict(row) if hasattr(row, 'keys') else dict(row)
            row_dict = _Merger._rename(row_dict)
            row_dict["_source"] = "proxy"
            row_dict["estimated_cost_cny"] = calculator.calculate(
                model=row_dict.get("target_model", row_dict.get("model", "")),
                input_tokens=row_dict.get("input_tokens", 0),
                output_tokens=row_dict.get("output_tokens", 0),
                cache_read_tokens=row_dict.get("cache_read_tokens", 0),
                cache_write_tokens=row_dict.get("cache_write_tokens", 0),
            )
            unified_requests.append(row_dict)

        for rec in session_rows:
            rec = _Merger._rename(rec)
            rec["estimated_cost_cny"] = calculator.calculate(
                model=rec.get("target_model", rec.get("model", "")),
                input_tokens=rec.get("input_tokens", 0),
                output_tokens=rec.get("output_tokens", 0),
                cache_read_tokens=rec.get("cache_read_tokens", 0),
                cache_write_tokens=rec.get("cache_write_tokens", 0),
            )
            unified_requests.append(rec)

        for rec in oc_rows:
            rec = _Merger._rename(rec)
            rec["estimated_cost_cny"] = calculator.calculate(
                model=rec.get("target_model", rec.get("model", "")),
                input_tokens=rec.get("input_tokens", 0),
                output_tokens=rec.get("output_tokens", 0),
                cache_read_tokens=rec.get("cache_read_tokens", 0),
                cache_write_tokens=rec.get("cache_write_tokens", 0),
            )
            unified_requests.append(rec)

        # 4. 按 request_ts DESC 排序
        unified_requests.sort(key=lambda x: x.get("request_ts", ""), reverse=True)

        # 5. 切片返回
        total = token_total + session_total + oc_total
        paginated = unified_requests[offset:offset + limit]

        # 6. 返回格式
        return {
            "model": model,
            "requests": paginated,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    # ─── 辅助方法 ───

    def _load_upstream_map(self) -> dict:
        """从 data db 读取 target_model -> upstream_name 映射。"""
        model_map = self._upstream_resolver._model_map
        return {model: info["upstream_name"] for model, info in model_map.items()}

    def _resolve_upstream(self, target_model: str) -> dict:
        """解析 target_model 对应的 upstream 信息。"""""
        return self._upstream_resolver.resolve(target_model)

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
