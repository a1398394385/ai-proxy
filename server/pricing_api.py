"""server 包 — 计费表 API。"""

import re
import sqlite3

from .common import json_response, _read_json, pricing_db


def _invalidate_pricing_cache(handler):
    """安全地使 pricing 缓存失效，stats_service 不可用时静默跳过。"""
    if handler.stats_service is not None:
        handler.stats_service.invalidate_pricing_cache()


def handle_get(path, qs, handler) -> bool:
    pricing_m = re.match(r"/api/pricing/?$", path)
    if pricing_m:
        search = qs.get("search", [None])[0]
        with pricing_db() as db:
            result = db.list_pricings(search=search)
        json_response(handler, {"pricings": result})
        return True

    pricing_detail_m = re.match(r"/api/pricing/([^/]+)$", path)
    if pricing_detail_m:
        with pricing_db() as db:
            result = db.get_pricing(pricing_detail_m.group(1))
        if not result:
            json_response(handler, {"error": "Not found"}, 404)
            return True
        json_response(handler, result)
        return True

    return False


def handle_post(path, handler) -> bool:
    if path != "/api/pricing":
        return False

    data = _read_json(handler)
    if not data:
        return True
    with pricing_db() as db:
        try:
            model_id = db.add_pricing(data)
        except (sqlite3.IntegrityError, ValueError) as e:
            json_response(handler, {"error": str(e)}, 400)
            return True
    _invalidate_pricing_cache(handler)
    json_response(handler, {"id": model_id, "message": "Created"}, 201)
    return True


def handle_put(path, handler) -> bool:
    pricing_m = re.match(r"/api/pricing/([^/]+)$", path)
    if pricing_m:
        data = _read_json(handler)
        if not data:
            return True
        with pricing_db() as db:
            try:
                ok = db.update_pricing(pricing_m.group(1), data)
            except ValueError as e:
                json_response(handler, {"error": str(e)}, 400)
                return True
            if not ok:
                json_response(handler, {"error": "Not found"}, 404)
                return True
        _invalidate_pricing_cache(handler)
        json_response(handler, {"message": "Updated"})
        return True
    return False


def handle_delete(path, handler) -> bool:
    pricing_m = re.match(r"/api/pricing/([^/]+)$", path)
    if pricing_m:
        with pricing_db() as db:
            ok = db.delete_pricing(pricing_m.group(1))
        if not ok:
            json_response(handler, {"error": "Not found"}, 404)
            return True
        _invalidate_pricing_cache(handler)
        json_response(handler, {"message": "Deleted"})
        return True
    return False
