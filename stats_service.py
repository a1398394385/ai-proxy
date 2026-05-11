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

    # ─── Provider 接口 ───

    def fetch_by_model(self, period: str) -> list:
        """按模型维度获取统计数据。

        Args:
            period: 时间周期，如 "24h", "7d", "30d"

        Returns:
            模型统计列表
        """
        raise NotImplementedError("未实现")

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
        raise NotImplementedError("未实现")

    def fetch_by_upstream(self, period: str) -> list:
        """按上游维度获取统计数据。

        Args:
            period: 时间周期

        Returns:
            上游统计列表
        """
        raise NotImplementedError("未实现")

    def fetch_trend(self, period: str) -> list:
        """获取时间趋势数据。

        Args:
            period: 时间周期

        Returns:
            趋势数据点列表
        """
        raise NotImplementedError("未实现")

    def fetch_summary(self, period: str) -> dict:
        """获取汇总统计数据。

        Args:
            period: 时间周期

        Returns:
            汇总统计 dict
        """
        raise NotImplementedError("未实现")

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
        raise NotImplementedError("未实现")
