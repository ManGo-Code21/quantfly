#!/Users/shj/quantfly/.venv/bin/python3
"""
A股新闻突发信号推送脚本
- 运行 NewsPipeline 采集
- 查询近30分钟突发信号
- DRY_RUN=1: 只输出，不发飞书
- 正常模式: 有信号则推送飞书群
"""
import subprocess, json, sqlite3, sys, os, requests
from datetime import datetime, timedelta

PY = "/Users/shj/quantfly/.venv/bin/python"
DB = "/Users/shj/quantfly/.signals.db"
FLY_ENV = "/Users/shj/.hermes/.env"
CHAT_ID = "oc_588a05c5a177864a9bc6635a05ddf4ba"
STATE_FILE = "/Users/shj/quantfly/.signals_seen.json"

DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

# Load Feishu credentials
env = {}
with open(FLY_ENV) as f:
    for line in f:
        if "FEISHU" in line and "=" in line:
            k, v = line.strip().split("=", 1)
            env[k] = v

APP_ID = env.get("FEISHU_APP_ID", "cli_a97e6559e9b8dbd5")
APP_SECRET = env.get("FEISHU_APP_SECRET", "vWE2WoYyHu3JepDj0gOjUdDnLvAwwsgk")

def get_token():
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        headers={"Content-Type": "application/json"},
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=10
    )
    return r.json().get("tenant_access_token", "")

def send_feishu(text):
    token = get_token()
    if not token:
        print("ERROR: no token")
        return
    resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        json={"receive_id": CHAT_ID, "msg_type": "text", "content": json.dumps({"text": text})},
        timeout=10
    )
    result = resp.json()
    if result.get("code") == 0:
        print(f"OK: message_id={result['data']['message_id']}")
    else:
        print(f"ERROR: {result}")

def run_pipeline():
    r = subprocess.run(
        [PY, "-c", """
import asyncio, sys; sys.path.insert(0, '/Users/shj/quantfly')
from quantfly.hot_topics.news_processor import NewsPipeline
p = NewsPipeline(llm_api_key='sk-da51b427a0584732a4dda0bc18d19b3e', llm_model='deepseek-v4-flash', provider='deepseek', max_llm_news=5)
asyncio.run(p.run())
print('PIPELINE_DONE')
"""],
        capture_output=True, text=True, timeout=120, cwd="/Users/shj/quantfly"
    )
    if r.returncode != 0:
        print(f"PIPELINE ERROR: {r.stderr[-300:]}")
    return r.returncode == 0

def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen), f)

def query_signals():
    cutoff = (datetime.now() - timedelta(minutes=35)).strftime('%Y-%m-%d %H:%M:%S')
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT industry, title, trade_signal, sentiment, reasoning, related_stocks, url, timestamp
        FROM signals WHERE timestamp >= ? AND is_breaking = 1
        ORDER BY timestamp DESC LIMIT 5
    """, (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def format_signal(r):
    stocks = json.loads(r['related_stocks']) if r['related_stocks'] else []
    lines = [
        f"【{r['industry']}】{r['title'][:50]}",
        f"  信号:{r['trade_signal']} | 情绪:{r['sentiment']}",
        f"  {r['reasoning'][:70]}",
    ]
    if stocks:
        lines.append(f"  A股相关: {', '.join(stocks[:3])}")
    if r['url']:
        lines.append(f"  {r['url'][:75]}")
    return '\n'.join(lines)

if __name__ == "__main__":
    mode = "DRY_RUN" if DRY_RUN else "LIVE"
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{mode}] Starting news signal check...")

    # Step 1: Run pipeline
    run_pipeline()

    # Step 2: Query signals
    signals = query_signals()
    print(f"[{mode}] Found {len(signals)} breaking signals total")

    if not signals:
        print(f"[{mode}] No new signals.")
    else:
        # Step 3: Deduplicate against seen signals
        seen = load_seen()
        seen_keys = {s['title'][:60] for s in signals}  # raw current keys
        new_keys = seen_keys - seen
        new_signals = [s for s in signals if s['title'][:60] in new_keys]

        print(f"[{mode}] {len(new_signals)} NEW (deduped from {len(signals)})")

        if not new_signals:
            print(f"[{mode}] All signals already seen, skip.")
        else:
            # Update seen state
            merged = seen | new_keys
            save_seen(merged)

            # Format output
            header = f"🔴 A股新闻突发信号 {datetime.now().strftime('%H:%M')} | {len(new_signals)}条新增"
            body = '\n\n'.join(format_signal(r) for r in new_signals)
            output = header + '\n\n' + body

            if DRY_RUN:
                print(f"\n{output}")
            else:
                send_feishu(output)

            print(f"[{mode}] Done.")
