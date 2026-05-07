import subprocess, json, sqlite3, sys
from datetime import datetime, timedelta

# 1. 运行 pipeline
r = subprocess.run(
    [sys.executable, "-c", """
import asyncio, sys; sys.path.insert(0, '/Users/shj/quantfly')
from quantfly.hot_topics.news_processor import NewsPipeline
p = NewsPipeline(llm_api_key='sk-da51b427a0584732a4dda0bc18d19b3e', llm_model='deepseek-v4-flash', provider='deepseek', max_llm_news=5)
asyncio.run(p.run())
print('DONE')
"""],
    capture_output=True, text=True, timeout=120, cwd="/Users/shj/quantfly"
)

print("=== STDOUT ===")
print(r.stdout)
print("=== STDERR ===")
print(r.stderr)
print("=== RETURN CODE ===", r.returncode)

# 2. 查询近30分钟突发信号
conn = sqlite3.connect('/Users/shj/quantfly/.signals.db')
conn.row_factory = sqlite3.Row
cutoff = (datetime.now() - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
rows = conn.execute("""
    SELECT industry, title, trade_signal, sentiment, reasoning, related_stocks, url
    FROM signals WHERE timestamp >= ? AND is_breaking = 1
    ORDER BY timestamp DESC LIMIT 5
""", (cutoff,)).fetchall()
conn.close()

if not rows:
    print("无新突发信号")
else:
    lines = [f"🔴 A股新闻突发信号 {datetime.now().strftime('%H:%M')}"]
    for r in rows:
        stocks = json.loads(r['related_stocks']) if r['related_stocks'] else []
        lines.append(f"【{r['industry']}】{r['title'][:45]}")
        lines.append(f"  信号:{r['trade_signal']} 情绪:{r['sentiment']} {r['reasoning'][:40]}")
        if stocks:
            lines.append(f"  A股:{', '.join(stocks[:3])}")
        lines.append("")
    print('\n'.join(lines))
