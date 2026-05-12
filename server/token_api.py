"""server 包 — Token 统计 API。"""

import re
from urllib.parse import unquote

from .common import json_response


def _check_stats_service(handler):
    """检查 StatsService 是否可用，不可用时发送 503 并返回 False。"""
    if handler.stats_service is None:
        json_response(handler, {"error": "Stats service unavailable"}, 503)
        return False
    return True


def handle_get(path, qs, handler) -> bool:
    if path == "/api/token_stats":
        if not _check_stats_service(handler):
            return True
        period = qs.get("period", ["week"])[0]
        if period not in ("day", "week", "month"):
            period = "week"
        stats = handler.stats_service.fetch_summary(period)
        json_response(handler, stats)
        return True

    if path == "/api/token_stats/by_model":
        if not _check_stats_service(handler):
            return True
        period = qs.get("period", ["week"])[0]
        if period not in ("day", "week", "month"):
            period = "week"
        models = handler.stats_service.fetch_by_model(period)
        json_response(handler, {"models": models, "count": len(models)})
        return True

    if path == "/api/token_stats/trend":
        if not _check_stats_service(handler):
            return True
        period = qs.get("period", ["week"])[0]
        if period not in ("day", "week", "month"):
            period = "week"
        trends = handler.stats_service.fetch_trend(period)
        json_response(handler, {"trends": trends, "count": len(trends)})
        return True

    if path == "/api/token_stats/summary":
        if not _check_stats_service(handler):
            return True
        json_response(handler, handler.stats_service.fetch_all_summaries())
        return True

    if path == "/api/token_stats/requests":
        if not _check_stats_service(handler):
            return True
        period = qs.get("period", ["week"])[0]
        if period not in ("day", "week", "month"):
            json_response(handler, {"error": "Invalid period"}, 400)
            return True
        model = qs.get("model", [None])[0]
        request_type = qs.get("request_type", [None])[0]
        try:
            limit = int(qs.get("limit", ["50"])[0])
            offset = int(qs.get("offset", ["0"])[0])
        except (ValueError, TypeError):
            json_response(handler, {"error": "Invalid limit/offset"}, 400)
            return True
        if limit > 200:
            json_response(handler, {"error": "Limit exceeds maximum (200)"}, 400)
            return True
        result = handler.stats_service.fetch_requests(
            period=period,
            model=model,
            request_type=request_type,
            limit=limit,
            offset=offset,
        )
        json_response(handler, result)
        return True

    if path == "/api/token_stats/by_upstream":
        if not _check_stats_service(handler):
            return True
        period = qs.get("period", ["week"])[0]
        if period not in ("day", "week", "month"):
            json_response(handler, {"error": "Invalid period"}, 400)
            return True
        result = handler.stats_service.fetch_by_upstream(period=period)
        json_response(handler, result)
        return True

    # 路径参数匹配（必须在精确匹配之后）
    m = re.match(r"/api/token_stats/by_model/([^/]+)/requests$", path)
    if m:
        if not _check_stats_service(handler):
            return True
        model = unquote(m.group(1))
        period = qs.get("period", ["week"])[0]
        if period not in ("day", "week", "month"):
            json_response(handler, {"error": "Invalid period"}, 400)
            return True
        try:
            limit = int(qs.get("limit", ["50"])[0])
            offset = int(qs.get("offset", ["0"])[0])
        except (ValueError, TypeError):
            json_response(handler, {"error": "Invalid limit/offset"}, 400)
            return True
        if limit > 200:
            json_response(handler, {"error": "Limit exceeds maximum (200)"}, 400)
            return True
        result = handler.stats_service.fetch_by_model_requests(
            model=model, period=period, limit=limit, offset=offset
        )
        json_response(handler, result)
        return True

    return False
