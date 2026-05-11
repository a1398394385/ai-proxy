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

    # ─── TokenStatsDao 实例 ───

    def _get_dao(self) -> _TokenStatsDao:
        """获取 TokenStatsDao 实例。"""
        return _TokenStatsDao(self.access_log_db_path)

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
    ) -> list:
        """获取请求详情列表（分页）。

        Args:
            period: 时间周期
            model: 可选，按模型过滤
            request_type: 可选，按请求类型过滤
            limit: 每页数量
            offset: 偏移量

        Returns:
            请求详情列表
        """
        dao = self._get_dao()
        rows, _total = dao.query_token_stats(
            period=period,
            model=model,
            request_type=request_type,
            limit=limit,
            offset=offset,
        )
        return rows

    def fetch_by_upstream(self, period: str) -> list:
        """按上游维度获取统计数据。

        Args:
            period: 时间周期

        Returns:
            上游统计列表
        """
        dao = self._get_dao()
        # 从 config.db 读取 upstream 映射
        upstream_map = self._load_upstream_map()
        return dao.aggregate_by_upstream(period, upstream_map)

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
    ) -> list:
        """获取指定模型的请求详情列表（分页）。

        Args:
            model: 模型名称
            period: 时间周期
            limit: 每页数量
            offset: 偏移量

        Returns:
            请求详情列表
        """
        dao = self._get_dao()
        rows, _total = dao.query_token_stats(
            period=period,
            model=model,
            limit=limit,
            offset=offset,
        )
        return rows

    # ─── 辅助方法 ───

    def _load_upstream_map(self) -> dict:
        """从 config.db 读取 target_model -> upstream_name 映射。"""
        if not self.config_db_path.exists():
            return {}
        conn = sqlite3.connect(str(self.config_db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT tm.name as model_name, u.base_url as upstream_url
                FROM target_models tm
                JOIN upstreams u ON tm.upstream_id = u.id
                WHERE tm.name IS NOT NULL AND u.base_url IS NOT NULL
                """
            ).fetchall()
            return {row["model_name"]: row["upstream_url"] for row in rows}
        finally:
            conn.close()
