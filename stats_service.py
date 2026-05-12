#!/usr/bin/env python3
"""StatsService — Token 统计数据查询服务层。

从 access_log.db 的 token_stats 表读取数据，提供按模型、上游、趋势、
汇总等维度的统计查询接口。

职责：
- 封装 token_stats 表的 SQL 查询逻辑
- 通过 Provider 接口定义标准化的数据访问方法
- 每次查询新建 SQLite 连接，用完立即关闭

用法：
    from stats_service import StatsService

    service = StatsService(
        access_log_db_path="data/access_log.db",
        config_db_path="~/.hermes/config.db",
        state_db_path="~/.hermes/state.db",
        cc_switch_db_path="~/.cc-switch/cc-switch.db",
    )
    summary = service.fetch_summary(period="24h")
"""

import sqlite3
import time
from pathlib import Path

class _TokenStatsDao:
    """Token 统计数据访问对象。

    封装 token_stats 表的 SQL 查询逻辑，每次查询新建 SQLite 连接，用完立即关闭。

    Args:
        db_path: access_log.db 路径
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

    def aggregate_by_upstream(self, period: str, upstream_map: dict) -> list:
        """按 upstream 维度聚合统计数据。

        Args:
            period: 时间周期
            upstream_map: {target_model: upstream_name} 映射表

        Returns:
            按上游分组的聚合数据列表
        """
        if not upstream_map:
            return []

        time_condition = self._period_to_condition(period)

        # 构建 CASE WHEN 表达式用于按 upstream 分组
        case_parts = []
        for _target_model, _upstream_name in upstream_map.items():
            case_parts.append("WHEN target_model = ? THEN ?")

        case_sql = "CASE " + " ".join(case_parts) + " ELSE 'Other' END"
        case_params: list = []
        for target_model, upstream_name in upstream_map.items():
            case_params.extend([target_model, upstream_name])

        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT {case_sql} as upstream,
                       COUNT(*) as request_count,
                       SUM(input_tokens) as total_input,
                       SUM(output_tokens) as total_output,
                       SUM(cached_read_tokens) as total_cache_read,
                       SUM(cached_write_tokens) as total_cache_write
                FROM token_stats
                WHERE {time_condition}
                GROUP BY upstream
                ORDER BY total_output DESC
                """,
                case_params,
            ).fetchall()

            return [
                {
                    "upstream": row["upstream"],
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
    """上游解析器 — 从 config.db 加载 model → upstream 映射，内置 TTL 缓存。

    构造时加载全量映射到内存，每次 resolve() 检查缓存是否过期（60s），
    过期自动刷新。config.db 不存在时返回空映射，不抛异常。

    Args:
        config_db_path: config.db 路径
    """

    def __init__(self, config_db_path: Path) -> None:
        self.config_db_path = config_db_path
        self._cache_ttl = 60  # 缓存 60 秒
        self._loaded_at = 0.0  # 初始化为 0，强制首次加载
        self._model_map: dict = {}  # {model_name: {upstream_name, upstream_url}}
        self._upstream_list: list = []  # [{upstream_name, upstream_url}]
        self._refresh()

    def _refresh(self) -> None:
        """从 config.db 重新加载映射到内存。"""
        self._loaded_at = time.time()
        self._model_map = {}
        self._upstream_list = []

        if not self.config_db_path.exists():
            return

        try:
            conn = sqlite3.connect(str(self.config_db_path))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT tm.name, u.base_url as upstream_url, u.id as upstream_id
                    FROM target_models tm
                    JOIN upstreams u ON tm.upstream_id = u.id
                    WHERE tm.name IS NOT NULL AND u.base_url IS NOT NULL
                    """
                ).fetchall()

                seen_upstreams = set()
                for row in rows:
                    model_name = row["name"]
                    upstream_url = row["upstream_url"]
                    self._model_map[model_name] = {
                        "upstream_name": upstream_url,
                        "upstream_url": upstream_url,
                    }
                    if upstream_url not in seen_upstreams:
                        seen_upstreams.add(upstream_url)
                        self._upstream_list.append({
                            "upstream_name": upstream_url,
                            "upstream_url": upstream_url,
                        })
            finally:
                conn.close()
        except Exception:
            # config.db 损坏或无法读取时，保持空映射
            pass

    def resolve(self, target_model: str) -> dict:
        """解析 target_model 对应的 upstream 信息。

        Args:
            target_model: 目标模型名称

        Returns:
            {upstream_name: str, upstream_url: str} 或
            {upstream_name: '__unknown__', upstream_url: None}
        """
        # 检查缓存是否过期
        if time.time() - self._loaded_at > self._cache_ttl:
            self._refresh()

        entry = self._model_map.get(target_model)
        if entry:
            return {
                "upstream_name": entry["upstream_name"],
                "upstream_url": entry["upstream_url"],
            }

        return {
            "upstream_name": "__unknown__",
            "upstream_url": None,
        }

    def get_all_upstreams(self) -> list:
        """返回所有 upstream 列表。

        Returns:
            [{upstream_name, upstream_url}]
        """
        # 检查缓存是否过期
        if time.time() - self._loaded_at > self._cache_ttl:
            self._refresh()

        return list(self._upstream_list)


class _CostCalculator:
    """成本计算器 — 从 cc-switch.db 加载 model_pricing 表，内置 TTL 缓存。

    构造时不立即加载，首次 get_pricing() 时懒加载。每次 get_pricing() 检查缓存
    是否过期（300s），过期自动刷新。cc-switch.db 不存在时返回空定价，不抛异常。

    Args:
        cc_switch_db_path: cc-switch.db 路径
    """

    def __init__(self, cc_switch_db_path: str | Path) -> None:
        self.cc_switch_db_path = Path(cc_switch_db_path)
        self._cache_ttl = 300  # 缓存 5 分钟
        self._pricing_cache: dict = {}
        self._pricing_cache_time: float = 0.0

    # ─── 定价加载 ───

    def get_pricing(self) -> dict:
        """从 cc-switch.db 加载 model_pricing 表（带缓存）。

        Returns:
            {model_id: {input_cost, output_cost, cache_read_cost, cache_creation_cost}}
            cc-switch.db 不存在或表不存在时返回空 dict。
        """
        # 缓存检查
        if time.time() - self._pricing_cache_time < self._cache_ttl and self._pricing_cache:
            return self._pricing_cache

        if not self.cc_switch_db_path.exists():
            return {}

        try:
            conn = sqlite3.connect(str(self.cc_switch_db_path))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT * FROM model_pricing").fetchall()
                pricing = {}
                for r in rows:
                    pricing[r["model_id"]] = {
                        "input_cost": float(r["input_cost_per_million"]),
                        "output_cost": float(r["output_cost_per_million"]),
                        "cache_read_cost": float(r["cache_read_cost_per_million"]),
                        "cache_creation_cost": float(r["cache_creation_cost_per_million"]),
                    }
                self._pricing_cache = pricing
                self._pricing_cache_time = time.time()
                return pricing
            finally:
                conn.close()
        except Exception as e:
            print(f"Error reading model pricing: {e}")
            return {}

    # ─── 成本计算 ───

    def calculate(
        self,
        model: str,
        input_tokens: int | float | None,
        output_tokens: int | float | None,
        cache_read_tokens: int | float | None,
        cache_write_tokens: int | float | None,
    ) -> float:
        """根据模型计费规则计算成本。

        Args:
            model: 模型名称
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数
            cache_read_tokens: 缓存读取 token 数
            cache_write_tokens: 缓存写入 token 数

        Returns:
            总成本（float）。模型无定价时返回 0。
        """
        pricing = self.get_pricing()

        if not pricing or model not in pricing:
            return 0

        p = pricing[model]

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
        return {
            "request_id": f"sess-{row['id']}",
            "request_type": "session",
            "model": model,
            "target_model": normalized,
            "request_ts": row["started_at"],
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


class StatsService:
    """Token 统计数据查询服务。

    Args:
        access_log_db_path: access_log.db 路径（token_stats 表所在）
        config_db_path: config.db 路径（模型路由配置）
        state_db_path: state.db 路径（运行时状态）
        cc_switch_db_path: cc-switch.db 路径（功能开关）
    """

    def __init__(
        self,
        access_log_db_path: str,
        config_db_path: str,
        state_db_path: str,
        cc_switch_db_path: str,
    ) -> None:
        self.access_log_db_path = Path(access_log_db_path)
        self.config_db_path = Path(config_db_path)
        self.state_db_path = Path(state_db_path)
        self.cc_switch_db_path = Path(cc_switch_db_path)

        # 初始化上游解析器
        self._upstream_resolver = _UpstreamResolver(self.config_db_path)

    # ─── TokenStatsDao 实例 ───

    def _get_dao(self) -> _TokenStatsDao:
        """获取 TokenStatsDao 实例。"""
        return _TokenStatsDao(self.access_log_db_path)

    def _get_session_dao(self) -> _SessionDao:
        """获取 SessionDao 实例。"""
        return _SessionDao(self.state_db_path)

    # ─── Provider 接口 ───

    def fetch_by_model(self, period: str) -> list:
        """按模型维度获取统计数据。

        Args:
            period: 时间周期，如 "24h", "7d", "30d"

        Returns:
            模型统计列表
        """
        dao = self._get_dao()
        return dao.aggregate_by_model(period)

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

        calculator = self._get_calculator()
        unified_requests = []

        for row in token_rows:
            row_dict = dict(row) if hasattr(row, 'keys') else dict(row)
            row_dict["_source"] = "proxy"
            row_dict["estimated_cost_usd"] = calculator.calculate(
                model=row_dict.get("target_model", row_dict.get("model", "")),
                input_tokens=row_dict.get("input_tokens", 0),
                output_tokens=row_dict.get("output_tokens", 0),
                cache_read_tokens=row_dict.get("cached_read_tokens", 0),
                cache_write_tokens=row_dict.get("cached_write_tokens", 0),
            )
            unified_requests.append(row_dict)

        for rec in session_rows:
            rec["estimated_cost_usd"] = calculator.calculate(
                model=rec.get("target_model", rec.get("model", "")),
                input_tokens=rec.get("input_tokens", 0),
                output_tokens=rec.get("output_tokens", 0),
                cache_read_tokens=rec.get("cached_read_tokens", 0),
                cache_write_tokens=rec.get("cached_write_tokens", 0),
            )
            unified_requests.append(rec)

        unified_requests.sort(key=lambda x: x.get("request_ts", ""), reverse=True)

        total = token_total + session_total
        paginated = unified_requests[offset:offset + limit]

        return {
            "requests": paginated,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def fetch_by_upstream(self, period: str) -> dict:
        """按上游维度获取统计数据，合并 token_stats + sessions 数据源。

        Args:
            period: 时间周期

        Returns:
            {upstreams: [{upstream_id, base_url, request_count, input_tokens,
             output_tokens, cached_read_tokens, cached_write_tokens,
             total_tokens, estimated_cost_usd}]}
        """
        token_dao = self._get_dao()
        upstream_map = self._load_upstream_map()

        # 1. token_stats 按 upstream 聚合
        token_stats_data = token_dao.aggregate_by_upstream(period, upstream_map)

        # 2. sessions 按 model 聚合，再映射到 upstream
        session_dao = self._get_session_dao()
        session_model_data = session_dao.aggregate_by_model(period)

        # 3. 将 sessions 按 upstream_name 聚合
        session_upstream_data: dict = {}
        for row in session_model_data:
            model = row["model"]
            upstream_info = self._resolve_upstream(model)
            upstream_name = upstream_info["upstream_name"]

            if upstream_name not in session_upstream_data:
                session_upstream_data[upstream_name] = {
                    "upstream": upstream_name,
                    "request_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_read_tokens": 0,
                    "cached_write_tokens": 0,
                }

            agg = session_upstream_data[upstream_name]
            agg["request_count"] += row["request_count"] or 0
            agg["input_tokens"] += row["input_tokens"] or 0
            agg["output_tokens"] += row["output_tokens"] or 0
            agg["cached_read_tokens"] += row["cached_read_tokens"] or 0
            agg["cached_write_tokens"] += row["cached_write_tokens"] or 0

        # 4. 合并两个数据源（同名 upstream 累加）
        merged: dict = {}

        for row in token_stats_data:
            name = row["upstream"]
            # 将 aggregate_by_upstream 的 'Other' 统一为 '__unknown__'
            if name == "Other":
                name = "__unknown__"
            if name not in merged:
                merged[name] = {
                    "upstream": name,
                    "request_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_read_tokens": 0,
                    "cached_write_tokens": 0,
                }
            m = merged[name]
            m["request_count"] += row["request_count"] or 0
            m["input_tokens"] += row["input_tokens"] or 0
            m["output_tokens"] += row["output_tokens"] or 0
            m["cached_read_tokens"] += row["cached_read_tokens"] or 0
            m["cached_write_tokens"] += row["cached_write_tokens"] or 0

        for name, sdata in session_upstream_data.items():
            if name not in merged:
                merged[name] = dict(sdata)
            else:
                m = merged[name]
                m["request_count"] += sdata["request_count"]
                m["input_tokens"] += sdata["input_tokens"]
                m["output_tokens"] += sdata["output_tokens"]
                m["cached_read_tokens"] += sdata["cached_read_tokens"]
                m["cached_write_tokens"] += sdata["cached_write_tokens"]

        # 5. 计算 total_tokens 和 estimated_cost_usd
        calculator = self._get_calculator()
        result = []
        for name, agg in merged.items():
            agg["total_tokens"] = (
                agg["input_tokens"]
                + agg["output_tokens"]
                + agg["cached_read_tokens"]
                + agg["cached_write_tokens"]
            )

            # 获取 base_url：从 upstream_map 反查任意一个 model 的 upstream
            base_url = None
            sample_model = None
            for model, up_name in upstream_map.items():
                if up_name == name:
                    sample_model = model
                    break

            if sample_model:
                info = self._resolve_upstream(sample_model)
                base_url = info.get("upstream_url")
            elif name == "__unknown__":
                base_url = None

            # 成本计算：用 sample_model 或 name 作为模型名
            cost_model = sample_model if sample_model else name
            cost = calculator.calculate(
                model=cost_model,
                input_tokens=agg["input_tokens"],
                output_tokens=agg["output_tokens"],
                cache_read_tokens=agg["cached_read_tokens"],
                cache_write_tokens=agg["cached_write_tokens"],
            )

            result.append({
                "upstream_id": name,
                "base_url": base_url,
                "request_count": agg["request_count"],
                "input_tokens": agg["input_tokens"],
                "output_tokens": agg["output_tokens"],
                "cached_read_tokens": agg["cached_read_tokens"],
                "cached_write_tokens": agg["cached_write_tokens"],
                "total_tokens": agg["total_tokens"],
                "estimated_cost_usd": round(cost, 6),
            })

        # 6. 按 estimated_cost_usd DESC 排序
        result.sort(key=lambda x: x["estimated_cost_usd"], reverse=True)
        return {"upstreams": result}

    def fetch_trend(self, period: str) -> list:
        """获取时间趋势数据。

        Args:
            period: 时间周期

        Returns:
            趋势数据点列表
        """
        dao = self._get_dao()
        return dao.aggregate_trend(period)

    def fetch_summary(self, period: str) -> dict:
        """获取汇总统计数据。

        Args:
            period: 时间周期

        Returns:
            汇总统计 dict
        """
        dao = self._get_dao()
        return dao.aggregate_summary(period)

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

        # 3. 合并两个列表,统一添加 estimated_cost_usd 字段
        calculator = self._get_calculator()
        unified_requests = []

        for row in token_rows:
            row_dict = dict(row) if hasattr(row, 'keys') else dict(row)
            row_dict["_source"] = "proxy"
            row_dict["estimated_cost_usd"] = calculator.calculate(
                model=row_dict.get("target_model", row_dict.get("model", "")),
                input_tokens=row_dict.get("input_tokens", 0),
                output_tokens=row_dict.get("output_tokens", 0),
                cache_read_tokens=row_dict.get("cached_read_tokens", 0),
                cache_write_tokens=row_dict.get("cached_write_tokens", 0),
            )
            unified_requests.append(row_dict)

        for rec in session_rows:
            rec["estimated_cost_usd"] = calculator.calculate(
                model=rec.get("target_model", rec.get("model", "")),
                input_tokens=rec.get("input_tokens", 0),
                output_tokens=rec.get("output_tokens", 0),
                cache_read_tokens=rec.get("cached_read_tokens", 0),
                cache_write_tokens=rec.get("cached_write_tokens", 0),
            )
            unified_requests.append(rec)

        # 4. 按 request_ts DESC 排序
        unified_requests.sort(key=lambda x: x.get("request_ts", ""), reverse=True)

        # 5. 切片返回
        total = token_total + session_total
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
        """从 config.db 读取 target_model -> upstream_name 映射。"""
        model_map = self._upstream_resolver._model_map
        return {model: info["upstream_name"] for model, info in model_map.items()}

    def _resolve_upstream(self, target_model: str) -> dict:
        """解析 target_model 对应的 upstream 信息。"""""
        return self._upstream_resolver.resolve(target_model)

    def _get_calculator(self) -> _CostCalculator:
        """懒加载获取 _CostCalculator 单例。"""
        if not hasattr(self, "_cost_calculator"):
            self._cost_calculator = _CostCalculator(self.cc_switch_db_path)
        return self._cost_calculator

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
