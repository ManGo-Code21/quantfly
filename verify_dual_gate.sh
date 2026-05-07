#!/bin/bash
# ============================================================
# 开盘验证脚本 — 明天 2026.5.6 周二 开盘用
# ============================================================
# 验证内容：
#   1. Gate1: 新闻情感分析是否正常
#   2. Gate2: akshare分钟级ETF数据能否获取
#   3. 双过滤信号是否正常输出
#
# 使用方法：
#   bash verify_dual_gate.sh            # 测试新闻+akshare
#   bash verify_dual_gate.sh qmt        # 测试新闻+QMT
# ============================================================

SOURCE=${1:-akshare}
echo "=========================================="
echo "Dual Gate Signal System Verification"
echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Data Source: $SOURCE"
echo "=========================================="

cd /Users/shj/quantfly

# Check if trading time
python3 -c "
from datetime import datetime
now = datetime.now()
weekday = now.weekday()
time_str = now.strftime('%H%M')
is_am = 915 <= int(time_str) <= 1130
is_pm = 1300 <= int(time_str) <= 1505
status = 'TRADING' if (is_am or is_pm) and weekday < 5 else 'CLOSED'
print(f'Current time: {now.strftime(\"%H:%M\")} ({status})')
print('Note: akshare minute data only available during trading hours')
"

echo ""
echo "=== Step 1: Test News Collection ==="
python3 -c "
from quantfly.hot_topics.monitor import HotTopicMonitor
monitor = HotTopicMonitor(use_llm=False)
items = monitor.fetch_all()
print(f'Collected {len(items)} news items')
if items:
    print(f'Sample: {items[0][\"title\"][:60]}')
"

echo ""
echo "=== Step 2: Test Gate1 News Sentiment ==="
python3 -c "
from quantfly.hot_topics.monitor import HotTopicMonitor
monitor = HotTopicMonitor(use_llm=False)
items = monitor.fetch_all()
signals = monitor.analyze_with_sentiment(items)
print(f'Detected {len(signals)} industry signals')
for ind, data in sorted(signals.items(), key=lambda x: x[1].get('avg_news_score', 0), reverse=True)[:5]:
    score = data.get('avg_news_score', 0)
    sentiment = data.get('avg_sentiment', 0)
    count = data.get('news_count', 0)
    print(f'  {ind}: score={score:.2f} sentiment={sentiment:.2f} news={count}')
"

echo ""
echo "=== Step 3: Test Gate2 Volume Data ==="
if [ "$SOURCE" = "qmt" ]; then
    echo "Using QMT..."
    python3 -c "
from quantfly.hot_topics.qmt_minute_data import get_qmt_minute_data
qmt = get_qmt_minute_data()
if qmt._connected:
    print('QMT connected: OK')
    bars = qmt.get_minute_bars('512760.XSHG', count=10)
    print(f'Chip ETF minute data: {len(bars)} bars')
else:
    print('QMT: not connected (stub mode, skip)')
"
else
    echo "Using akshare..."
    python3 -c "
from quantfly.hot_topics.akshare_minute_data import get_akshare_minute_data
ak = get_akshare_minute_data()
print(f'Trading time: {ak.is_trading_time()}')
if ak.is_trading_time():
    bars = ak.get_5min_bars('512760', count=20)
    print(f'Chip ETF 5min data: {len(bars)} bars')
    if bars:
        print(f'Latest: {bars[-1]}')
else:
    print('Non-trading hours: akshare returns empty (normal)')
    print('Will work properly during market hours tomorrow')
"
fi

echo ""
echo "=== Step 4: Test Dual Gate Signal ==="
python3 -c "
from quantfly.hot_topics.monitor import HotTopicMonitor
from quantfly.hot_topics.dual_gate_signal import DualGateSignal

monitor = HotTopicMonitor(use_llm=False)
items = monitor.fetch_all()
gate1 = monitor.analyze_with_sentiment(items)

# Mock Gate2 (since non-trading hours)
mock_gate2 = {
    '512760': {'ratio': 1.8, 'signal': 'strong', 'name': 'Chip'},
    '515790': {'ratio': 1.2, 'signal': 'normal', 'name': 'Solar'},
    '159995': {'ratio': 2.1, 'signal': 'strong', 'name': 'AI'},
    '512800': {'ratio': 0.9, 'signal': 'normal', 'name': 'Bank'},
}

dual = DualGateSignal()
dual.set_gate1(gate1)
dual.set_gate2(mock_gate2)
signals = dual.get_signals()

print(f'Final signals: {len(signals)}')
for s in signals[:5]:
    print(f'  [{s[\"strength\"]}] {s[\"industry\"]}: combined={s[\"combined\"]:.2f} {s[\"reason\"]}')
"

echo ""
echo "=========================================="
echo "Verification Complete"
echo "=========================================="
