# Token 统计合并代理请求数据 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 access_log.db 的 token_stats 表数据合并到现有 token 统计页面的三个 API 端点中，前端零改动。

**Architecture:** 在 server.py 中新增三个辅助函数查询 token_stats 表，改造三个主函数将两份数据源合并返回。辅助函数与 sessions 查询完全解耦，便于后续剔除 Hermes 数据。

**Tech Stack:** Python 标准库 (sqlite3, datetime), 纯后端改动

---

### Task 1: 新增 `_get_proxy_token_aggregate()` 辅助函数

**Files:**
- Modify: `server.py` (插入位置：`get_token_stats()` 函数之前，约第 319 行)

- [ ] **Step 1: 在 server.py 中 `_get_proxy_token_aggregate` 函数**

在 `get_time_range()` 函数之后、`get_token_stats()` 之前插入：

```python
def _get_proxy_token_aggregate(period):
    """查询 token_stats 表的汇总数据。incomplete 表示流式中断不计入。"""
    from datetime import datetime
    conn = get_access_log_db()
    start_ts, end_ts = get_time_range(period)
    start_str = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S")
    end_str = datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        """SELECT COUNT(*) as request_count,
                  COALESCE(SUM(input_tokens), 0) as total_input,
                  COALESCE(SUM(output_tokens), 0) as total_output,
                  COALESCE(SUM(cached_read_tokens), 0) as total_cache_read,
                  COALESCE(SUM(cached_write_tokens), 0) as total_cache_write
           FROM token_stats
           WHERE request_ts >= ? AND request_ts <= ? AND status = 'completed'""",
        (start_str, end_str)
    ).fetchone()
    conn.close()
    return {
        "request_count": row["request_count"] or 0,
        "total_input": row["total_input"] or 0,
        "total_output": row["total_output"] or 0,
        "total_cache_read": row["total_cache_read"] or 0,
        "total_cache_write": row["total_cache_write"] or 0,
    }
```

- [ ] **Step 2: 验证函数可调用**

Run: `python3 -c "from server import _get_proxy_token_aggregate; print(_get_proxy_token_aggregate('week'))"`
Expected: 输出一个包含 request_count/total_input 等字段的 dict

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "feat: 新增 _get_proxy_token_aggregate 辅助函数"
```

---

### Task 2: 改造 `get_token_stats()` 合并两份数据

**Files:**
- Modify: `server.py:320-385`

- [ ] **Step 1: 修改 `get_token_stats()` 函数，在 sessions 汇总之后合并 token_stats 数据**

将函数改为：

```python
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
    
    total_cost = 0
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0
    total_requests = 0
    
    for r in rows:
        input_t = r["input_tokens"] or 0
        output_t = r["output_tokens"] or 0
        msg_tokens = r["msg_tokens"] or 0
        
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
    
    conn.close()
    
    # 合并 token_stats 代理请求数据
    proxy = _get_proxy_token_aggregate(period)
    if proxy:
        total_input += proxy["total_input"]
        total_output += proxy["total_output"]
        total_cache_read += proxy["total_cache_read"]
        total_cache_write += proxy["total_cache_write"]
        total_requests += proxy["request_count"]
    
    stats = {
        "period": period,
        "request_count": total_requests,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_read_tokens": total_cache_read,
        "cache_write_tokens": total_cache_write,
        "total_tokens": total_input + total_output + total_cache_read + total_cache_write,
        "estimated_cost_usd": round(total_cost, 4)
    }
    return stats
```

关键变更：
- `conn.close()` 从 `return stats` 前移到 sessions 查询完成后
- 新增 `proxy = _get_proxy_token_aggregate(period)` 及合并逻辑
- `estimated_cost_usd` 暂时未包含代理请求成本（Task 5 补充）

- [ ] **Step 2: 验证 API 返回**

Run: `python3 -c "from server import get_token_stats; print(get_token_stats('week'))"`
Expected: 输出 dict 中 request_count 应包含两份数据之和

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "feat: get_token_stats 合并 token_stats 代理请求数据"
```

---

### Task 3: 新增 `_get_proxy_token_by_model()` 并改造 `get_token_stats_by_model()`

**Files:**
- Modify: `server.py`

- [ ] **Step 1: 在 `_get_proxy_token_aggregate` 之后插入 `_get_proxy_token_by_model`**

```python
def _get_proxy_token_by_model(period):
    """查询 token_stats 按 target_model 分组。request_count=COUNT(*) 即单次 API 调用次数。"""
    from datetime import datetime
    conn = get_access_log_db()
    start_ts, end_ts = get_time_range(period)
    start_str = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S")
    end_str = datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT target_model as model,
                  COUNT(*) as request_count,
                  SUM(input_tokens) as total_input,
                  SUM(output_tokens) as total_output,
                  SUM(cached_read_tokens) as total_cache_read,
                  SUM(cached_write_tokens) as total_cache_write
           FROM token_stats
           WHERE request_ts >= ? AND request_ts <= ? AND status = 'completed'
           GROUP BY target_model""",
        (start_str, end_str)
    ).fetchall()
    conn.close()
    return [
        {
            "model": r["model"],
            "request_count": r["request_count"] or 0,
            "input_tokens": r["total_input"] or 0,
            "output_tokens": r["total_output"] or 0,
            "cache_read_tokens": r["total_cache_read"] or 0,
            "cache_write_tokens": r["total_cache_write"] or 0,
        }
        for r in rows
    ]
```

- [ ] **Step 2: 修改 `get_token_stats_by_model()` 合并两份数据**

```python
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
    conn.close()
    
    # 以模型名为 key 合并两份数据
    model_map = {}
    
    for r in rows:
        model = r["model"]
        model_map[model] = {
            "model": model,
            "request_count": r["request_count"] or 0,
            "input_tokens": r["total_input"] or 0,
            "output_tokens": r["total_output"] or 0,
            "cache_read_tokens": r["total_cache_read"] or 0,
            "cache_write_tokens": r["total_cache_write"] or 0,
        }
    
    for p in _get_proxy_token_by_model(period):
        name = p["model"]
        if name in model_map:
            model_map[name]["request_count"] += p["request_count"]
            model_map[name]["input_tokens"] += p["input_tokens"]
            model_map[name]["output_tokens"] += p["output_tokens"]
            model_map[name]["cache_read_tokens"] += p["cache_read_tokens"]
            model_map[name]["cache_write_tokens"] += p["cache_write_tokens"]
        else:
            model_map[name] = p
    
    # 计算成本
    models = []
    for m in model_map.values():
        cost = calculate_cost(
            m["model"],
            m["input_tokens"],
            m["output_tokens"],
            m["cache_read_tokens"],
            m["cache_write_tokens"]
        )
        m["total_tokens"] = m["input_tokens"] + m["output_tokens"] + m["cache_read_tokens"] + m["cache_write_tokens"]
        m["estimated_cost_usd"] = round(cost, 4)
        models.append(m)
    
    models.sort(key=lambda m: m["total_tokens"], reverse=True)
    return models
```

- [ ] **Step 3: 验证 API 返回**

Run: `python3 -c "from server import get_token_stats_by_model; r = get_token_stats_by_model('week'); print(len(r), 'models'); print(r[0] if r else 'no data')"`
Expected: 模型列表中应包含 sessions 和 token_stats 两个数据源的模型，同名模型已合并

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "feat: get_token_stats_by_model 合并 token_stats 代理请求数据"
```

---

### Task 4: 新增 `_get_proxy_token_trend()` 并改造 `get_daily_token_trend()`

**Files:**
- Modify: `server.py`

这是最复杂的改动。`_get_proxy_token_trend()` 需返回与 sessions 趋势完全相同的完整时间线结构（含补 0），主函数只需逐点相加。

- [ ] **Step 1: 在 `_get_proxy_token_by_model` 之后插入 `_get_proxy_token_trend`**

```python
def _get_proxy_token_trend(period):
    """查询 token_stats 按时间粒度分组，返回完整时间线（与 sessions 趋势结构对齐，含补 0）。
    
    返回 list of dict，每个 dict 包含：
    - date: 时间标签
    - input_tokens / output_tokens / cache_read_tokens / cache_write_tokens
    - model_data: list of {model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens}
    """
    from datetime import datetime, timedelta
    
    conn = get_access_log_db()
    now = datetime.now()
    start_ts, end_ts = get_time_range(period)
    start_str = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S")
    end_str = datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M:%S")
    
    if period == "day":
        rows = conn.execute(
            """SELECT strftime('%Y-%m-%d %H', request_ts) as time_slot,
                      target_model as model,
                      SUM(input_tokens) as total_input,
                      SUM(output_tokens) as total_output,
                      SUM(cached_read_tokens) as total_cache_read,
                      SUM(cached_write_tokens) as total_cache_write
               FROM token_stats
               WHERE request_ts >= ? AND request_ts <= ? AND status = 'completed'
               GROUP BY time_slot, target_model
               ORDER BY time_slot""",
            (start_str, end_str)
        ).fetchall()
        
        data_by_slot = {}
        for r in rows:
            slot = r["time_slot"]
            if slot not in data_by_slot:
                data_by_slot[slot] = []
            data_by_slot[slot].append(r)
        
        trends = []
        for i in range(24):
            point_time = now - timedelta(hours=23 - i)
            slot = point_time.strftime('%Y-%m-%d %H')
            model_rows = data_by_slot.get(slot, [])
            
            input_tokens = output_tokens = cache_read = cache_write = 0
            model_data = []
            for r in model_rows:
                it = r["total_input"] or 0
                ot = r["total_output"] or 0
                cr = r["total_cache_read"] or 0
                cw = r["total_cache_write"] or 0
                input_tokens += it
                output_tokens += ot
                cache_read += cr
                cache_write += cw
                model_data.append({
                    "model": r["model"],
                    "input_tokens": it,
                    "output_tokens": ot,
                    "cache_read_tokens": cr,
                    "cache_write_tokens": cw,
                })
            
            trends.append({
                "date": point_time.strftime('%Y-%m-%d %H:%M'),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
                "model_data": model_data,
            })
        conn.close()
        return trends
    
    elif period in ("week", "month"):
        days = 7 if period == "week" else 30
        dates = [(now - timedelta(days=days - 1 - i)).strftime('%Y-%m-%d') for i in range(days)]
        
        rows = conn.execute(
            """SELECT date(request_ts) as time_slot,
                      target_model as model,
                      SUM(input_tokens) as total_input,
                      SUM(output_tokens) as total_output,
                      SUM(cached_read_tokens) as total_cache_read,
                      SUM(cached_write_tokens) as total_cache_write
               FROM token_stats
               WHERE request_ts >= ? AND request_ts <= ? AND status = 'completed'
               GROUP BY time_slot, target_model
               ORDER BY time_slot""",
            (start_str, end_str)
        ).fetchall()
        
        data_by_slot = {}
        for r in rows:
            slot = r["time_slot"]
            if slot not in data_by_slot:
                data_by_slot[slot] = []
            data_by_slot[slot].append(r)
        
        trends = []
        for date_str in dates:
            model_rows = data_by_slot.get(date_str, [])
            
            input_tokens = output_tokens = cache_read = cache_write = 0
            model_data = []
            for r in model_rows:
                it = r["total_input"] or 0
                ot = r["total_output"] or 0
                cr = r["total_cache_read"] or 0
                cw = r["total_cache_write"] or 0
                input_tokens += it
                output_tokens += ot
                cache_read += cr
                cache_write += cw
                model_data.append({
                    "model": r["model"],
                    "input_tokens": it,
                    "output_tokens": ot,
                    "cache_read_tokens": cr,
                    "cache_write_tokens": cw,
                })
            
            trends.append({
                "date": date_str,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
                "model_data": model_data,
            })
        conn.close()
        return trends
    
    conn.close()
    return []
```

- [ ] **Step 2: 修改 `get_daily_token_trend()`，在 sessions 趋势生成后逐点合并 proxy 趋势**

在 `get_daily_token_trend()` 的三个分支（day/week/month）中，每个分支末尾 `return trends` 之前，插入合并逻辑：

```python
        # 合并 token_stats 代理请求趋势
        pricing = get_model_pricing()
        proxy_trends = _get_proxy_token_trend(period)
        for i, pt in enumerate(proxy_trends):
            if i < len(trends):
                trends[i]["input_tokens"] += pt["input_tokens"]
                trends[i]["output_tokens"] += pt["output_tokens"]
                trends[i]["cache_read_tokens"] += pt["cache_read_tokens"]
                trends[i]["cache_write_tokens"] += pt["cache_write_tokens"]
                # 按 target_model 逐模型计算代理成本
                proxy_cost = 0
                for md in pt.get("model_data", []):
                    if pricing and md["model"] in pricing:
                        p = pricing[md["model"]]
                        proxy_cost += (
                            md["input_tokens"] / 1_000_000 * p["input_cost"] +
                            md["output_tokens"] / 1_000_000 * p["output_cost"] +
                            md["cache_read_tokens"] / 1_000_000 * p["cache_read_cost"] +
                            md["cache_write_tokens"] / 1_000_000 * p["cache_creation_cost"]
                        )
                trends[i]["estimated_cost_usd"] = round(trends[i]["estimated_cost_usd"] + proxy_cost, 4)
                trends[i]["total_tokens"] = (
                    trends[i]["input_tokens"] +
                    trends[i]["output_tokens"] +
                    trends[i]["cache_read_tokens"] +
                    trends[i]["cache_write_tokens"]
                )
        
        conn.close()
        return trends
```

需要将这段合并逻辑提取为一个内部辅助函数避免三个分支重复代码。在 `get_daily_token_trend()` 开头定义：

```python
    def _merge_proxy_trends(trends, proxy_trends, pricing):
        """逐点合并代理请求趋势到 sessions 趋势"""
        for i, pt in enumerate(proxy_trends):
            if i < len(trends):
                trends[i]["input_tokens"] += pt["input_tokens"]
                trends[i]["output_tokens"] += pt["output_tokens"]
                trends[i]["cache_read_tokens"] += pt["cache_read_tokens"]
                trends[i]["cache_write_tokens"] += pt["cache_write_tokens"]
                proxy_cost = 0
                for md in pt.get("model_data", []):
                    if pricing and md["model"] in pricing:
                        p = pricing[md["model"]]
                        proxy_cost += (
                            md["input_tokens"] / 1_000_000 * p["input_cost"] +
                            md["output_tokens"] / 1_000_000 * p["output_cost"] +
                            md["cache_read_tokens"] / 1_000_000 * p["cache_read_cost"] +
                            md["cache_write_tokens"] / 1_000_000 * p["cache_creation_cost"]
                        )
                trends[i]["estimated_cost_usd"] = round(trends[i]["estimated_cost_usd"] + proxy_cost, 4)
                trends[i]["total_tokens"] = (
                    trends[i]["input_tokens"] +
                    trends[i]["output_tokens"] +
                    trends[i]["cache_read_tokens"] +
                    trends[i]["cache_write_tokens"]
                )
        return trends
```

然后在三个分支的 `conn.close(); return trends` 前各调用：

```python
        trends = _merge_proxy_trends(trends, _get_proxy_token_trend(period), pricing)
        conn.close()
        return trends
```

- [ ] **Step 3: 验证趋势 API**

Run: `python3 -c "from server import get_daily_token_trend; r = get_daily_token_trend('week'); print(len(r), 'points'); print(r[0] if r else 'no data')"`
Expected: 7 个时间点，每个点包含 input/output/cache 字段，值应包含两份数据之和

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "feat: get_daily_token_trend 合并 token_stats 代理请求趋势数据"
```

---

### Task 5: 补充代理请求成本到 `get_token_stats()` 的 KPI

**Files:**
- Modify: `server.py`

Task 2 中 `get_token_stats()` 的 `estimated_cost_usd` 未包含代理请求成本，因为汇总查询只有总量没有按模型分组。需要在合并段增加按模型计算成本。

- [ ] **Step 1: 修改 `get_token_stats()` 合并段，使用 `_get_proxy_token_by_model` 计算代理成本**

将 Task 2 中的合并段从：

```python
    # 合并 token_stats 代理请求数据
    proxy = _get_proxy_token_aggregate(period)
    if proxy:
        total_input += proxy["total_input"]
        total_output += proxy["total_output"]
        total_cache_read += proxy["total_cache_read"]
        total_cache_write += proxy["total_cache_write"]
        total_requests += proxy["request_count"]
```

改为：

```python
    # 合并 token_stats 代理请求数据
    proxy = _get_proxy_token_aggregate(period)
    if proxy:
        total_input += proxy["total_input"]
        total_output += proxy["total_output"]
        total_cache_read += proxy["total_cache_read"]
        total_cache_write += proxy["total_cache_write"]
        total_requests += proxy["request_count"]
        # 按 target_model 逐模型计算代理成本
        for p in _get_proxy_token_by_model(period):
            total_cost += calculate_cost(
                p["model"],
                p["input_tokens"],
                p["output_tokens"],
                p["cache_read_tokens"],
                p["cache_write_tokens"]
            )
```

- [ ] **Step 2: 验证 KPI 成本包含代理部分**

Run: `python3 -c "from server import get_token_stats; r = get_token_stats('week'); print('cost:', r['estimated_cost_usd'])"`
Expected: 成本值应高于之前仅 sessions 的值

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "feat: get_token_stats KPI 补充代理请求成本计算"
```

---

### Task 6: 全量测试与端对端验证

**Files:**
- No code changes

- [ ] **Step 1: 运行全量测试确保无回归**

Run: `python3 -m pytest test/ -q`
Expected: 406 tests 全部通过

- [ ] **Step 2: 重启服务并在浏览器验证**

Run: `./server.sh restart`

在浏览器中访问 Token 统计页面，验证：
1. KPI 卡片数据包含代理请求
2. 趋势面积图正常显示
3. 模型表格包含代理请求的 target_model
4. Day/Week/Month 三个周期切换正常

- [ ] **Step 3: 最终 Commit（如有修复）**

```bash
git add -A
git commit -m "fix: 端对端验证后的修复（如有）"
```
