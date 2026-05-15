"""server 包 — Token 统计 API。"""

import re
from urllib.parse import unquote

from .common import json_response


_VALID_PERIODS = ("day", "week", "month")


def _require_stats(handler):
    """检查 StatsService 是否可用，不可用时发送 503 并返回 (None, True)。"""
    if handler.stats_service is None:
        json_response(handler, {"error": "Stats service unavailable"}, 503)
        return None, True
    return handler.stats_service, False


def _parse_period(qs, strict=False):
    """解析 period 参数。strict=True 时非法值返回错误。"""
    period = qs.get("period", ["week"])[0]
    if period not in _VALID_PERIODS:
        if strict:
            return None, ("Invalid period", 400)
        return "week", None
    return period, None


def _parse_pagination(qs):
    """解析 limit/offset，失败返回 (None, None, 错误元组)。"""
    try:
        limit = int(qs.get("limit", ["50"])[0])
        offset = int(qs.get("offset", ["0"])[0])
    except (ValueError, TypeError):
        return None, None, ("Invalid limit/offset", 400)
    if limit > 200:
        return None, None, ("Limit exceeds maximum (200)", 400)
    return limit, offset, None


# ─── 端点 handlers ───


def handle_get(path, qs, handler) -> bool:
    if path == "/api/token_stats":
        svc, done = _require_stats(handler)
        if done:
            return True
        period, _ = _parse_period(qs)
        json_response(handler, svc.fetch_summary(period))
        return True

    if path == "/api/token_stats/by_model":
        svc, done = _require_stats(handler)
        if done:
            return True
        period, _ = _parse_period(qs)
        models = svc.fetch_by_model(period)
        json_response(handler, {"models": models, "count": len(models)})
        return True

    if path == "/api/token_stats/trend":
        svc, done = _require_stats(handler)
        if done:
            return True
        period, _ = _parse_period(qs)
        trends = svc.fetch_trend(period)
        json_response(handler, {"trends": trends, "count": len(trends)})
        return True

    if path == "/api/token_stats/summary":
        svc, done = _require_stats(handler)
        if done:
            return True
        json_response(handler, svc.fetch_all_summaries())
        return True

    if path == "/api/token_stats/requests":
        svc, done = _require_stats(handler)
        if done:
            return True
        period, err = _parse_period(qs, strict=True)
        if err:
            json_response(handler, {"error": err[0]}, err[1])
            return True
        limit, offset, err = _parse_pagination(qs)
        if err:
            json_response(handler, {"error": err[0]}, err[1])
            return True
        model = qs.get("model", [None])[0]
        request_type = qs.get("request_type", [None])[0]
        source = qs.get("source", [None])[0]
        result = svc.fetch_requests(
            period=period,
            model=model,
            request_type=request_type,
            source=source,
            limit=limit,
            offset=offset,
        )
        json_response(handler, result)
        return True

    if path == "/api/token_stats/by_upstream":
        svc, done = _require_stats(handler)
        if done:
            return True
        period, err = _parse_period(qs, strict=True)
        if err:
            json_response(handler, {"error": err[0]}, err[1])
            return True
        result = svc.fetch_by_upstream(period=period)
        json_response(handler, result)
        return True

    # 路径参数匹配（必须在精确匹配之后）
    m = re.match(r"/api/token_stats/by_model/([^/]+)/requests$", path)
    if m:
        svc, done = _require_stats(handler)
        if done:
            return True
        model = unquote(m.group(1))
        period, err = _parse_period(qs, strict=True)
        if err:
            json_response(handler, {"error": err[0]}, err[1])
            return True
        limit, offset, err = _parse_pagination(qs)
        if err:
            json_response(handler, {"error": err[0]}, err[1])
            return True
        result = svc.fetch_requests(
            period=period, model=model, limit=limit, offset=offset
        )
        result["model"] = model
        json_response(handler, result)
        return True

    return False
