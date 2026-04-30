#!/usr/bin/env python
"""Hermes Data Browser — 本地浏览 Hermes 内部数据的 Web 工具
支持：Fact Store 浏览 + Token 使用统计
"""
import json
import os
import sqlite3
import re
import time
import http.client
import socket
from datetime import datetime, timedelta
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

from config_manager import ConfigDB, Migrations
from common import get_port, get_host, load_config, CONFIG, CONFIG_PATH

# 配置
DB_PATH = os.path.expanduser("~/.hermes/memory_store.db")
CONFIG_DB_PATH = Path(__file__).resolve().parent / "data" / "access_log.db"


def get_config_db():
    return ConfigDB(CONFIG_DB_PATH)


MAX_BODY_SIZE = 10 * 1024 * 1024  # 10MB


def _read_json(handler):
    """读取请求体 JSON，错误时发送 400 并返回 None。"""
    length = int(handler.headers.get("Content-Length", 0))
    if length > MAX_BODY_SIZE:
        json_response(handler, {"error": "Request body too large"}, 413)
        return None
    body = handler.rfile.read(length)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        json_response(handler, {"error": "Invalid JSON"}, 400)
        return None


def _test_upstream_connectivity(upstream: dict) -> dict:
    """测试上游连通性：TCP + HTTP GET。"""
    parsed = urlparse(upstream["base_url"])
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    result = {"reachable": False, "http_status": None, "latency_ms": 0}

    start = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect((host, port))
        result["latency_ms"] = int((time.time() - start) * 1000)
    except (socket.timeout, OSError) as e:
        result["error"] = str(e)
        return result
    finally:
        sock.close()

    http_path = parsed.path.rstrip("/") + "/" if parsed.path else "/"
    start = time.time()
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", http_path)
        resp = conn.getresponse()
        result["reachable"] = True
        result["http_status"] = resp.status
        result["latency_ms"] = int((time.time() - start) * 1000)
        if resp.status == 401:
            result["warning"] = "返回 401，API Key 可能无效，但网络可达"
        if resp.status == 404:
            result["warning"] = "返回 404，端点可能不存在，但服务存活"
    except Exception as e:
        result["error"] = str(e)
    finally:
        conn.close()

    return result
STATE_DB_PATH = os.path.expanduser("~/.hermes/state.db")
CC_SWITCH_DB_PATH = os.path.expanduser("~/.cc-switch/cc-switch.db")
load_config(CONFIG_PATH)
HOST = get_host("data_browser", "127.0.0.1")
PORT = get_port("data_browser", 18742)

# 缓存计费规则
_pricing_cache = {}
_pricing_cache_time = 0

def get_cc_switch_db():
    """连接到 cc-switch 数据库读取计费规则"""
    if not os.path.exists(CC_SWITCH_DB_PATH):
        return None
    conn = sqlite3.connect(CC_SWITCH_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_model_pricing():
    """获取模型计费规则（带缓存）"""
    global _pricing_cache, _pricing_cache_time
    
    # 缓存 5 分钟
    if time.time() - _pricing_cache_time < 300 and _pricing_cache:
        return _pricing_cache
    
    conn = get_cc_switch_db()
    if not conn:
        return {}
    
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
        _pricing_cache = pricing
        _pricing_cache_time = time.time()
        return pricing
    except Exception as e:
        print(f"Error reading model pricing: {e}")
        return {}
    finally:
        conn.close()


def calculate_cost(model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens):
    """根据模型计费规则计算成本"""
    pricing = get_model_pricing()
    
    # 如果没有计费规则，返回 0
    if not pricing or model not in pricing:
        return 0
    
    p = pricing[model]
    
    # 计算成本（每百万 token 的价格）
    input_cost = (input_tokens or 0) / 1_000_000 * p["input_cost"]
    output_cost = (output_tokens or 0) / 1_000_000 * p["output_cost"]
    cache_read_cost = (cache_read_tokens or 0) / 1_000_000 * p["cache_read_cost"]
    cache_write_cost = (cache_write_tokens or 0) / 1_000_000 * p["cache_creation_cost"]
    
    return input_cost + output_cost + cache_read_cost + cache_write_cost


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_state_db():
    conn = sqlite3.connect(STATE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def json_response(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def row_to_dict(row):
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, bytes):
            d[k] = None
    return d


# ===== Fact Store 功能 =====
def get_all_facts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM facts ORDER BY fact_id DESC").fetchall()
    facts = [row_to_dict(r) for r in rows]
    for f in facts:
        entities = conn.execute(
            """SELECT e.name, e.entity_type FROM entities e
               JOIN fact_entities fe ON e.entity_id = fe.entity_id
               WHERE fe.fact_id = ?""", (f["fact_id"],)
        ).fetchall()
        f["entities"] = [dict(e)["name"] for e in entities]
    conn.close()
    return facts


def search_facts(query):
    conn = get_db()
    rows = conn.execute(
        """SELECT f.* FROM facts f
           JOIN facts_fts ON facts_fts.rowid = f.fact_id
           WHERE facts_fts MATCH ?
           ORDER BY f.fact_id DESC""", (query,)
    ).fetchall()
    facts = [row_to_dict(r) for r in rows]
    for f in facts:
        entities = conn.execute(
            """SELECT e.name FROM entities e
               JOIN fact_entities fe ON e.entity_id = fe.entity_id
               WHERE fe.fact_id = ?""", (f["fact_id"],)
        ).fetchall()
        f["entities"] = [dict(e)["name"] for e in entities]
    conn.close()
    return facts


# ===== Token 统计功能 =====
def get_time_range(period):
    now = datetime.now()
    if period == "day":
        start = now - timedelta(days=1)
    elif period == "week":
        start = now - timedelta(weeks=1)
    elif period == "month":
        start = now - timedelta(days=30)
    else:
        start = now - timedelta(days=7)
    return start.timestamp(), now.timestamp()


def get_token_stats(period="week"):
    start_ts, end_ts = get_time_range(period)
    conn = get_state_db()
    
    # 首先从 sessions 表获取基础数据
    rows = conn.execute(
        """SELECT 
            s.id,
            s.model,
            s.input_tokens,
            s.output_tokens,
            s.cache_read_tokens,
            s.cache_write_tokens,
            s.message_count,
            COALESCE(SUM(m.token_count), 0) as msg_tokens
        FROM sessions s
        LEFT JOIN messages m ON s.id = m.session_id
        WHERE s.started_at >= ? AND s.started_at <= ?
        AND s.input_tokens IS NOT NULL
        GROUP BY s.id""",
        (start_ts, end_ts)
    ).fetchall()
    
    # 按照模型计费规则计算总成本
    total_cost = 0
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0
    total_requests = 0  # API请求次数
    
    for r in rows:
        # 如果 session 的 token 为 0 但有消息 token，使用消息 token
        input_t = r["input_tokens"] or 0
        output_t = r["output_tokens"] or 0
        msg_tokens = r["msg_tokens"] or 0
        
        # 如果 input_tokens 为 0 但有消息 token，将消息 token 计入 input
        if input_t == 0 and msg_tokens > 0:
            input_t = msg_tokens
        
        total_input += input_t
        total_output += output_t
        total_cache_read += r["cache_read_tokens"] or 0
        total_cache_write += r["cache_write_tokens"] or 0
        total_requests += r["message_count"] or 0
        total_cost += calculate_cost(
            r["model"],
            input_t,
            output_t,
            r["cache_read_tokens"],
            r["cache_write_tokens"]
        )
    
    stats = {
        "period": period,
        "request_count": total_requests,  # API请求次数
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_read_tokens": total_cache_read,
        "cache_write_tokens": total_cache_write,
        "total_tokens": total_input + total_output + total_cache_read + total_cache_write,
        "estimated_cost_usd": round(total_cost, 4)
    }
    conn.close()
    return stats


def get_token_stats_by_model(period="week"):
    start_ts, end_ts = get_time_range(period)
    conn = get_state_db()
    
    rows = conn.execute(
        """SELECT 
            model,
            SUM(message_count) as request_count,
            SUM(input_tokens) as total_input,
            SUM(output_tokens) as total_output,
            SUM(cache_read_tokens) as total_cache_read,
            SUM(cache_write_tokens) as total_cache_write
        FROM sessions 
        WHERE started_at >= ? AND started_at <= ?
        AND input_tokens IS NOT NULL
        AND model IS NOT NULL
        GROUP BY model
        ORDER BY total_input + total_output DESC""",
        (start_ts, end_ts)
    ).fetchall()
    
    models = []
    for r in rows:
        model = r["model"]
        input_t = r["total_input"] or 0
        output_t = r["total_output"] or 0
        cache_read_t = r["total_cache_read"] or 0
        cache_write_t = r["total_cache_write"] or 0
        
        # 按照计费规则计算成本
        cost = calculate_cost(model, input_t, output_t, cache_read_t, cache_write_t)
        
        models.append({
            "model": model,
            "request_count": r["request_count"] or 0,
            "input_tokens": input_t,
            "output_tokens": output_t,
            "cache_read_tokens": cache_read_t,
            "cache_write_tokens": cache_write_t,
            "total_tokens": input_t + output_t + cache_read_t + cache_write_t,
            "estimated_cost_usd": round(cost, 4)
        })
    
    conn.close()
    return models


def get_daily_token_trend(period="week"):
    """获取每日 token 使用趋势
    - day: 24小时，返回完整 24 个小时点
    - week: 7天，返回完整 7 个日期点  
    - month: 30天，返回完整 30 个日期点
    无数据的时间点补 0，成本按模型计费规则重新计算
    """
    from datetime import datetime, timedelta
    
    conn = get_state_db()
    now = datetime.now()
    
    # 预加载计费规则
    pricing = get_model_pricing()
    
    def calc_cost_for_model(model, input_t, output_t, cache_read_t, cache_write_t):
        """根据计费规则计算单个模型的成本"""
        if not pricing or model not in pricing:
            return 0
        p = pricing[model]
        return (
            (input_t or 0) / 1_000_000 * p["input_cost"] +
            (output_t or 0) / 1_000_000 * p["output_cost"] +
            (cache_read_t or 0) / 1_000_000 * p["cache_read_cost"] +
            (cache_write_t or 0) / 1_000_000 * p["cache_creation_cost"]
        )
    
    if period == "day":
        # 24小时 - 返回24个点，从左到右是 (now-23h) 到 now
        end_ts = now.timestamp()
        start_ts = (now - timedelta(hours=24)).timestamp()
        
        # 按小时和模型分组查询
        rows = conn.execute(
            """SELECT 
                strftime('%Y-%m-%d %H', datetime(started_at, 'unixepoch', 'localtime')) as hour_slot,
                model,
                SUM(input_tokens) as total_input,
                SUM(output_tokens) as total_output,
                SUM(cache_read_tokens) as total_cache_read,
                SUM(cache_write_tokens) as total_cache_write
            FROM sessions 
            WHERE started_at >= ? AND started_at <= ?
            AND input_tokens IS NOT NULL
            AND model IS NOT NULL
            GROUP BY hour_slot, model
            ORDER BY hour_slot""",
            (start_ts, end_ts)
        ).fetchall()
        
        # 构建数据映射（hour_slot -> [model_data]）
        data_by_hour = {}
        for r in rows:
            hour_slot = r["hour_slot"]
            if hour_slot not in data_by_hour:
                data_by_hour[hour_slot] = []
            data_by_hour[hour_slot].append(r)
        
        # 生成24个点
        trends = []
        for i in range(24):
            point_time = now - timedelta(hours=23-i)
            hour_slot = point_time.strftime('%Y-%m-%d %H')
            
            model_rows = data_by_hour.get(hour_slot, [])
            
            input_tokens = output_tokens = cache_read = cache_write = cost = 0
            for r in model_rows:
                input_tokens += r["total_input"] or 0
                output_tokens += r["total_output"] or 0
                cache_read += r["total_cache_read"] or 0
                cache_write += r["total_cache_write"] or 0
                cost += calc_cost_for_model(
                    r["model"],
                    r["total_input"],
                    r["total_output"],
                    r["total_cache_read"],
                    r["total_cache_write"]
                )
            
            trends.append({
                "date": point_time.strftime('%Y-%m-%d %H:%M'),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
                "total_tokens": input_tokens + output_tokens + cache_read + cache_write,
                "estimated_cost_usd": round(cost, 4)
            })
        
        conn.close()
        return trends
    
    elif period == "week":
        # 7天 - 返回完整 7 个日期点
        dates = [(now - timedelta(days=6-i)).strftime('%Y-%m-%d') for i in range(7)]
        start_ts = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        end_ts = now.timestamp()
        
        # 按日期和模型分组查询
        rows = conn.execute(
            """SELECT 
                date(started_at, 'unixepoch', 'localtime') as date,
                model,
                SUM(input_tokens) as total_input,
                SUM(output_tokens) as total_output,
                SUM(cache_read_tokens) as total_cache_read,
                SUM(cache_write_tokens) as total_cache_write
            FROM sessions 
            WHERE started_at >= ? AND started_at <= ?
            AND input_tokens IS NOT NULL
            AND model IS NOT NULL
            GROUP BY date, model
            ORDER BY date""",
            (start_ts, end_ts)
        ).fetchall()
        
        # 构建数据映射（date -> [model_data]）
        data_by_date = {}
        for r in rows:
            date_str = r["date"]
            if date_str not in data_by_date:
                data_by_date[date_str] = []
            data_by_date[date_str].append(r)
        
        trends = []
        for date_str in dates:
            model_rows = data_by_date.get(date_str, [])
            
            input_tokens = output_tokens = cache_read = cache_write = cost = 0
            for r in model_rows:
                input_tokens += r["total_input"] or 0
                output_tokens += r["total_output"] or 0
                cache_read += r["total_cache_read"] or 0
                cache_write += r["total_cache_write"] or 0
                cost += calc_cost_for_model(
                    r["model"],
                    r["total_input"],
                    r["total_output"],
                    r["total_cache_read"],
                    r["total_cache_write"]
                )
            
            trends.append({
                "date": date_str,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
                "total_tokens": input_tokens + output_tokens + cache_read + cache_write,
                "estimated_cost_usd": round(cost, 4)
            })
        conn.close()
        return trends
    
    elif period == "month":
        # 30天 - 返回完整 30 个日期点
        dates = [(now - timedelta(days=29-i)).strftime('%Y-%m-%d') for i in range(30)]
        start_ts = (now - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        end_ts = now.timestamp()
        
        # 按日期和模型分组查询
        rows = conn.execute(
            """SELECT 
                date(started_at, 'unixepoch', 'localtime') as date,
                model,
                SUM(input_tokens) as total_input,
                SUM(output_tokens) as total_output,
                SUM(cache_read_tokens) as total_cache_read,
                SUM(cache_write_tokens) as total_cache_write
            FROM sessions 
            WHERE started_at >= ? AND started_at <= ?
            AND input_tokens IS NOT NULL
            AND model IS NOT NULL
            GROUP BY date, model
            ORDER BY date""",
            (start_ts, end_ts)
        ).fetchall()
        
        # 构建数据映射（date -> [model_data]）
        data_by_date = {}
        for r in rows:
            date_str = r["date"]
            if date_str not in data_by_date:
                data_by_date[date_str] = []
            data_by_date[date_str].append(r)
        
        trends = []
        for date_str in dates:
            model_rows = data_by_date.get(date_str, [])
            
            input_tokens = output_tokens = cache_read = cache_write = cost = 0
            for r in model_rows:
                input_tokens += r["total_input"] or 0
                output_tokens += r["total_output"] or 0
                cache_read += r["total_cache_read"] or 0
                cache_write += r["total_cache_write"] or 0
                cost += calc_cost_for_model(
                    r["model"],
                    r["total_input"],
                    r["total_output"],
                    r["total_cache_read"],
                    r["total_cache_write"]
                )
            
            trends.append({
                "date": date_str,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
                "total_tokens": input_tokens + output_tokens + cache_read + cache_write,
                "estimated_cost_usd": round(cost, 4)
            })
        conn.close()
        return trends
    
    conn.close()
    return []


class HermesDataHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[HermesData] {args[0]}")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # ===== 模型配置 API =====
        if path == "/api/upstreams":
            db = get_config_db()
            upstreams = db.list_upstreams()
            db.close()
            return json_response(self, {"upstreams": upstreams})

        m = re.match(r"/api/upstreams/([^/]+)$", path)
        if m:
            db = get_config_db()
            u = db.get_upstream(m.group(1))
            db.close()
            if u:
                return json_response(self, u)
            return json_response(self, {"error": "Not found"}, 404)

        if path == "/api/models":
            upstream_filter = qs.get("upstream_id", [None])[0]
            db = get_config_db()
            models = db.list_models(upstream_id=upstream_filter)
            db.close()
            return json_response(self, {"models": models})

        m = re.match(r"/api/models/(\d+)$", path)
        if m:
            db = get_config_db()
            model = db.get_model(int(m.group(1)))
            db.close()
            if model:
                return json_response(self, model)
            return json_response(self, {"error": "Not found"}, 404)

        if path == "/api/routes":
            proxy_type = qs.get("proxy_type", [None])[0]
            db = get_config_db()
            routes = db.list_routes(proxy_type=proxy_type)
            db.close()
            return json_response(self, {"routes": routes})

        m = re.match(r"/api/routes/(\d+)$", path)
        if m:
            db = get_config_db()
            route = db.get_route(int(m.group(1)))
            db.close()
            if route:
                return json_response(self, route)
            return json_response(self, {"error": "Not found"}, 404)

        if path == "/api/config/status":
            db = get_config_db()
            counts = db.get_counts()
            db.close()
            proxy_reachable = False
            try:
                proxy_port = get_port("codex_proxy", 48743)
                conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=2)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                proxy_reachable = resp.status == 200
                conn.close()
            except Exception:
                pass
            pass_through_reachable = False
            try:
                pt_port = get_port("pass_through", 48744)
                conn = http.client.HTTPConnection("127.0.0.1", pt_port, timeout=2)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                pass_through_reachable = resp.status == 200
                conn.close()
            except Exception:
                pass
            return json_response(self, {
                "proxy_reachable": proxy_reachable,
                "pass_through_reachable": pass_through_reachable,
                "config_db": counts,
            })

        # ===== Fact Store API =====
        if path == "/api/facts":
            q = qs.get("q", [None])[0]
            category = qs.get("category", [None])[0]
            if q:
                facts = search_facts(q)
            else:
                facts = get_all_facts()
            if category:
                facts = [f for f in facts if f["category"] == category]
            return json_response(self, {"facts": facts, "count": len(facts)})

        if path.startswith("/api/facts/"):
            fact_id = path.split("/")[-1]
            try:
                fact_id = int(fact_id)
            except ValueError:
                return json_response(self, {"error": "Invalid ID"}, 400)
            conn = get_db()
            row = conn.execute("SELECT * FROM facts WHERE fact_id = ?", (fact_id,)).fetchone()
            if not row:
                conn.close()
                return json_response(self, {"error": "Not found"}, 404)
            fact = row_to_dict(row)
            entities = conn.execute(
                """SELECT e.name FROM entities e
                   JOIN fact_entities fe ON e.entity_id = fe.entity_id
                   WHERE fe.fact_id = ?""", (fact_id,)
            ).fetchall()
            fact["entities"] = [dict(e)["name"] for e in entities]
            conn.close()
            return json_response(self, fact)

        if path == "/api/categories":
            conn = get_db()
            rows = conn.execute(
                "SELECT DISTINCT category, COUNT(*) as cnt FROM facts GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            cats = [{"category": r["category"], "count": r["cnt"]} for r in rows]
            conn.close()
            return json_response(self, {"categories": cats})

        if path == "/api/stats":
            conn = get_db()
            total = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            cats = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM facts GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            categories = {r["category"]: r["cnt"] for r in cats}
            top_entities = conn.execute(
                """SELECT e.name, COUNT(*) as cnt FROM entities e
                   JOIN fact_entities fe ON e.entity_id = fe.entity_id
                   GROUP BY e.name ORDER BY cnt DESC LIMIT 20"""
            ).fetchall()
            top_entities = [{"name": r["name"], "count": r["cnt"]} for r in top_entities]
            conn.close()
            return json_response(self, {"total": total, "categories": categories, "top_entities": top_entities})

        # ===== Token 统计 API =====
        if path == "/api/token_stats":
            period = qs.get("period", ["week"])[0]
            if period not in ("day", "week", "month"):
                period = "week"
            stats = get_token_stats(period)
            return json_response(self, stats)

        if path == "/api/token_stats/by_model":
            period = qs.get("period", ["week"])[0]
            if period not in ("day", "week", "month"):
                period = "week"
            models = get_token_stats_by_model(period)
            return json_response(self, {"models": models, "count": len(models)})

        if path == "/api/token_stats/trend":
            period = qs.get("period", ["week"])[0]
            if period not in ("day", "week", "month"):
                period = "week"
            trends = get_daily_token_trend(period)
            return json_response(self, {"trends": trends, "count": len(trends)})

        if path == "/api/token_stats/summary":
            # 返回所有周期的汇总
            return json_response(self, {
                "day": get_token_stats("day"),
                "week": get_token_stats("week"),
                "month": get_token_stats("month")
            })

        # 静态文件
        static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        file_path = os.path.join(static_dir, path.lstrip("/"))
        if path == "/":
            file_path = os.path.join(static_dir, "index.html")
        real_file = os.path.realpath(file_path)
        real_static = os.path.realpath(static_dir)
        if not real_file.startswith(real_static + os.sep):
            self.send_response(403)
            self.end_headers()
            return
        if os.path.isfile(file_path):
            ext_map = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}
            ext = os.path.splitext(file_path)[1]
            mime = ext_map.get(ext, "application/octet-stream")
            with open(file_path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", f"{mime}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        # ===== 模型配置 API =====
        if parsed.path == "/api/migrate":
            try:
                result = Migrations(Path.home() / ".hermes" / "config.db").migrate()
                return json_response(self, result)
            except Exception as e:
                return json_response(self, {"error": str(e)}, 500)

        if parsed.path == "/api/upstreams":
            data = _read_json(self)
            if not data:
                return
            db = get_config_db()
            try:
                uid = db.add_upstream(data)
                db.close()
                return json_response(self, {"id": uid, "message": "Created"}, 201)
            except sqlite3.IntegrityError as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        test_m = re.match(r"/api/upstreams/([^/]+)/test$", parsed.path)
        if test_m:
            uid = test_m.group(1)
            db = get_config_db()
            u = db.get_upstream(uid)
            db.close()
            if not u:
                return json_response(self, {"error": "Not found"}, 404)
            result = _test_upstream_connectivity(u)
            return json_response(self, result)

        if parsed.path == "/api/models":
            data = _read_json(self)
            if not data:
                return
            db = get_config_db()
            try:
                mid = db.add_model(data)
                db.close()
                return json_response(self, {"id": mid, "message": "Created"}, 201)
            except sqlite3.IntegrityError as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        if parsed.path == "/api/routes":
            data = _read_json(self)
            if not data:
                return
            # 校验 proxy_type
            proxy_type = data.get("proxy_type", "codex")
            if proxy_type not in ("codex", "claude", "pass_through"):
                return json_response(self, {
                    "error": "proxy_type must be one of: codex, claude, pass_through"
                }, 400)
            db = get_config_db()
            model = db.get_model(data["target_model_id"])
            if not model:
                db.close()
                return json_response(self, {"error": "target_model_id 不存在"}, 400)
            if not model.get("upstream_active"):
                db.close()
                return json_response(self, {"error": "目标模型所属上游已禁用"}, 400)
            try:
                rid = db.add_route(data)
                db.close()
                return json_response(self, {"id": rid, "message": "Created"}, 201)
            except (sqlite3.IntegrityError, ValueError) as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        if parsed.path == "/api/config/reload":
            result = {}
            # Reload codex proxy
            try:
                proxy_port = get_port("codex_proxy", 48743)
                conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=5)
                conn.request("POST", "/admin/reload")
                resp = conn.getresponse()
                body = json.loads(resp.read())
                conn.close()
                result["proxy"] = body
            except Exception as e:
                result["proxy"] = {"status": "error", "message": str(e)}
            # Reload pass_through proxy
            try:
                pt_port = get_port("pass_through", 48744)
                conn = http.client.HTTPConnection("127.0.0.1", pt_port, timeout=5)
                conn.request("POST", "/admin/reload")
                resp = conn.getresponse()
                body = json.loads(resp.read())
                conn.close()
                result["pass_through"] = body
            except Exception as e:
                result["pass_through"] = {"status": "error", "message": str(e)}
            return json_response(self, result)

        # ===== Fact Store API =====
        if parsed.path == "/api/facts":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return json_response(self, {"error": "Invalid JSON"}, 400)
            conn = get_db()
            try:
                cursor = conn.execute(
                    """INSERT INTO facts (content, category, tags, trust_score)
                       VALUES (?, ?, ?, ?)""",
                    (data.get("content", ""), data.get("category", "general"),
                     data.get("tags", ""), data.get("trust_score", 0.5)),
                )
                conn.commit()
                fact_id = cursor.lastrowid
                if "entities" in data and isinstance(data["entities"], list):
                    for ename in data["entities"]:
                        entity = conn.execute(
                            "SELECT entity_id FROM entities WHERE name = ?", (ename,)
                        ).fetchone()
                        if not entity:
                            ec = conn.execute("INSERT INTO entities (name) VALUES (?)", (ename,))
                            eid = ec.lastrowid
                        else:
                            eid = dict(entity)["entity_id"]
                        conn.execute(
                            "INSERT OR IGNORE INTO fact_entities (fact_id, entity_id) VALUES (?, ?)",
                            (fact_id, eid),
                        )
                    conn.commit()
                conn.close()
                return json_response(self, {"fact_id": fact_id, "message": "Created"}, 201)
            except sqlite3.IntegrityError as e:
                conn.close()
                return json_response(self, {"error": str(e)}, 409)

        if re.match(r"/api/facts/\d+/feedback", parsed.path):
            fact_id = int(parsed.path.split("/")[-2])
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)
            action = data.get("action", "helpful")
            delta = 0.1 if action == "helpful" else -0.1
            conn = get_db()
            conn.execute(
                "UPDATE facts SET trust_score = MAX(0, MIN(1, trust_score + ?)), helpful_count = helpful_count + 1 WHERE fact_id = ?",
                (delta, fact_id),
            )
            conn.commit()
            conn.close()
            return json_response(self, {"message": "Feedback recorded"})

        return json_response(self, {"error": "Not found"}, 404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        # ===== 模型配置 API =====
        m = re.match(r"/api/upstreams/([^/]+)$", parsed.path)
        if m:
            data = _read_json(self)
            if not data:
                return
            db = get_config_db()
            try:
                db.update_upstream(m.group(1), data)
                db.close()
                return json_response(self, {"message": "Updated"})
            except sqlite3.IntegrityError as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        m = re.match(r"/api/models/(\d+)$", parsed.path)
        if m:
            data = _read_json(self)
            if not data:
                return
            db = get_config_db()
            try:
                db.update_model(int(m.group(1)), data)
                db.close()
                return json_response(self, {"message": "Updated"})
            except sqlite3.IntegrityError as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        m = re.match(r"/api/routes/(\d+)$", parsed.path)
        if m:
            data = _read_json(self)
            if not data:
                return
            db = get_config_db()
            try:
                db.update_route(int(m.group(1)), data)
                db.close()
                return json_response(self, {"message": "Updated"})
            except (sqlite3.IntegrityError, ValueError) as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        # ===== Fact Store API =====
        m = re.match(r"/api/facts/(\d+)$", parsed.path)
        if m:
            fact_id = int(m.group(1))
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return json_response(self, {"error": "Invalid JSON"}, 400)
            conn = get_db()
            existing = conn.execute("SELECT * FROM facts WHERE fact_id = ?", (fact_id,)).fetchone()
            if not existing:
                conn.close()
                return json_response(self, {"error": "Not found"}, 404)
            conn.execute(
                """UPDATE facts SET content = ?, category = ?, tags = ?, trust_score = ?
                   WHERE fact_id = ?""",
                (data.get("content", existing["content"]),
                 data.get("category", existing["category"]),
                 data.get("tags", existing["tags"]),
                 data.get("trust_score", existing["trust_score"]),
                 fact_id),
            )
            conn.commit()
            conn.close()
            return json_response(self, {"message": "Updated"})
        return json_response(self, {"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        # ===== 模型配置 API =====
        m = re.match(r"/api/upstreams/([^/]+)$", parsed.path)
        if m:
            uid = m.group(1)
            db = get_config_db()
            u = db.get_upstream(uid)
            if not u:
                db.close()
                return json_response(self, {"error": "Not found"}, 404)
            active_routes = db.upstream_active_routes(uid)
            if active_routes:
                db.close()
                return json_response(self, {
                    "error": "上游有活跃路由引用，无法禁用",
                    "referenced_routes": active_routes,
                }, 409)
            db.disable_upstream(uid)
            db.close()
            return json_response(self, {"message": "Disabled"})

        m = re.match(r"/api/models/(\d+)$", parsed.path)
        if m:
            mid = int(m.group(1))
            db = get_config_db()
            result = db.delete_model(mid)
            db.close()
            if "error" in result:
                return json_response(self, result, 409)
            return json_response(self, {"message": "Deleted"})

        m = re.match(r"/api/routes/(\d+)$", parsed.path)
        if m:
            rid = int(m.group(1))
            db = get_config_db()
            route = db.get_route(rid)
            if not route:
                db.close()
                return json_response(self, {"error": "Not found"}, 404)
            if route["source"] == "*":
                routes = db.list_routes()
                star_count = sum(1 for r in routes if r["source"] == "*")
                if star_count <= 1:
                    db.close()
                    return json_response(self, {
                        "error": "不能删除最后一条 * fallback 路由",
                    }, 409)
            try:
                db.delete_route(rid)
                db.close()
                return json_response(self, {"message": "Deleted"})
            except sqlite3.IntegrityError as e:
                db.close()
                return json_response(self, {"error": str(e)}, 409)

        # ===== Fact Store API =====
        m = re.match(r"/api/facts/(\d+)$", parsed.path)
        if m:
            fact_id = int(m.group(1))
            conn = get_db()
            existing = conn.execute("SELECT fact_id FROM facts WHERE fact_id = ?", (fact_id,)).fetchone()
            if not existing:
                conn.close()
                return json_response(self, {"error": "Not found"}, 404)
            conn.execute("DELETE FROM facts WHERE fact_id = ?", (fact_id,))
            conn.commit()
            conn.close()
            return json_response(self, {"message": "Deleted"})
        return json_response(self, {"error": "Not found"}, 404)


def main():
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        return
    if not os.path.exists(STATE_DB_PATH):
        print(f"Warning: State database not found: {STATE_DB_PATH}")
        print("Token statistics will not be available.")
    
    server = HTTPServer((HOST, PORT), HermesDataHandler)
    print(f"=" * 50)
    print(f"Hermes Data Browser")
    print(f"=" * 50)
    print(f"访问地址: http://{HOST}:{PORT}")
    print(f"功能: Fact Store + Token 使用统计")
    print(f"按 Ctrl+C 停止")
    print(f"=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭...")
        server.server_close()


if __name__ == "__main__":
    main()
