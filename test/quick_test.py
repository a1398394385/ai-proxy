#!/usr/bin/env python3
"""快速测试 Token 统计 API"""
import urllib.request
import json

def test_api():
    base_url = "http://127.0.0.1:18742"
    
    for period in ["day", "week", "month"]:
        try:
            url = f"{base_url}/api/token_stats/trend?period={period}"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                count = len(data.get("trends", []))
                print(f"{period:6} - Points: {count}")
                if count > 0:
                    first = data["trends"][0]["date"]
                    last = data["trends"][-1]["date"]
                    print(f"         Range: {first} ~ {last}")
        except Exception as e:
            print(f"{period:6} - Error: {e}")

if __name__ == "__main__":
    test_api()
