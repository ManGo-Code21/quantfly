#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
"""
QuantFly CLI — 统一命令行入口
"""
import argparse
import logging
import sys
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("QuantFly")


def cmd_hot():
    """热点监控"""
    from quantfly.hot_topics.monitor import HotTopicMonitor
    monitor = HotTopicMonitor()
    items = monitor.fetch_all()
    print(f"\n🔥 热点监控 ({datetime.now():%H:%M:%S})\n")
    print(f"{'来源':<16} {'板块':<12} {'涨幅':>6} {'成交额':>12}")
    print("-" * 50)
    for item in items[:15]:
        src = item.get("source", "")[:16]
        title = item.get("title", "")[:12]
        chg = item.get("change_pct", 0)
        amt = item.get("amount", 0) or 0
        print(f"{src:<16} {title:<12} {chg:>+5.1f}% {amt:>10.0f}万")
    print(f"\n共 {len(items)} 条热点")


def cmd_screen():
    """选股扫描"""
    from quantfly.screener.stock_picker import TopicDrivenScreener
    from quantfly.hot_topics.industry_mapper import get_sector_list
    
    industries = get_sector_list()
    screener = TopicDrivenScreener()
    
    print(f"\n📊 选股三原则扫描 ({datetime.now():%H:%M:%S})\n")
    
    all_buyable = []
    for industry in industries:
        results = screener.screen(industry, top_n=5)
        buyable = [r for r in results if r['is_buyable']]
        
        print(f"[{industry}] {len(results)}候选 {len(buyable)}可买:")
        for r in results[:5]:
            tag = "🟢" if r['is_buyable'] else "⚪"
            print(f"  {tag} {r['code']} {r['name']:<8} "
                  f"分={r['total_score']:.1f} 涨={r['chg_pct']:+.1f}% "
                  f"量比={r['vol_ratio']:.1f} 位置={r['rel_pos']:.2f}")
        all_buyable.extend(buyable)
    
    print(f"\n📈 总计: {len(all_buyable)} 只可买")
    return all_buyable


def cmd_backtest():
    """回测"""
    from quantfly.backtest.engine import BacktestBroker
    from quantfly.screener.stock_picker import get_kline_em
    import numpy as np
    
    print("\n🧪 回测验证\n")
    
    # Use screener results from cmd_screen
    import io
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    buyable = cmd_screen()
    sys.stdout = old_stdout
    
    if not buyable:
        print("无可用信号")
        return
    
    print(f"\n回测 {len(buyable)} 只股票...\n")
    results = []
    for r in buyable[:10]:
        code = r['code']
        name = r['name']
        df = get_kline_em(code, count=50)
        if df.empty or len(df) < 10:
            continue
        
        close = df['close'].values[-5:]
        buy_price = close[-1]
        for hold, label in [(1,'1D'), (2,'2D'), (3,'3D')]:
            if len(close) > hold:
                ret = (close[-1-hold] - buy_price) / buy_price * 100
                r[f'ret_{label}'] = round(ret, 2)
        results.append(r)
        ret_str = " ".join(f"{label}={r.get(f'ret_{label}',0):+.1f}%" for label in ['1D','2D','3D'] if f'ret_{label}' in r)
        print(f"  {code} {name}: {ret_str}")
    
    # Summary
    for label in ['1D', '2D', '3D']:
        rets = [r[f'ret_{label}'] for r in results if f'ret_{label}' in r]
        if rets:
            avg = np.mean(rets)
            wins = sum(1 for r in rets if r > 0)
            print(f"\n  {label}: avg={avg:+.2f}%  win={wins}/{len(rets)}")


def cmd_report():
    """生成每日报告"""
    from quantfly.hot_topics.monitor import HotTopicMonitor
    from quantfly.screener.stock_picker import TopicDrivenScreener
    from quantfly.hot_topics.industry_mapper import get_sector_list
    
    print(f"\n📄 QuantFly 每日报告 ({datetime.now():%Y-%m-%d %H:%M})\n")
    print("=" * 60)
    
    # Hot topics
    monitor = HotTopicMonitor()
    items = monitor.fetch_all()
    print(f"\n🔥 今日热点 TOP5:")
    for item in items[:5]:
        print(f"  [{item.get('topic','')}] {item.get('title','')} {item.get('change_pct',0):+.1f}%")
    
    # Screener
    screener = TopicDrivenScreener()
    print(f"\n📊 选股扫描:")
    for industry in get_sector_list()[:4]:
        results = screener.screen(industry, top_n=5)
        buyable = [r for r in results if r['is_buyable']]
        print(f"  [{industry}] {len(buyable)} buyable / {len(results)} scanned")
        for r in buyable[:2]:
            print(f"    🟢 {r['code']} {r['name']}: 分={r['total_score']:.1f} 涨={r['chg_pct']:+.1f}%")
    
    print("\n" + "=" * 60)
    print("报告生成完毕")


def cmd_api():
    """启动FastAPI"""
    import uvicorn
    print("🚀 Starting QuantFly API on http://0.0.0.0:8765")
    uvicorn.run("quantfly.api.main:app", host="0.0.0.0", port=8765, reload=False)


def main():
    parser = argparse.ArgumentParser(description="QuantFly — A股量化交易框架")
    parser.add_argument("command", nargs="?", default="report",
                       choices=["hot", "screen", "backtest", "report", "api"],
                       help="Command to run")
    args = parser.parse_args()
    
    commands = {
        "hot": cmd_hot,
        "screen": cmd_screen,
        "backtest": cmd_backtest,
        "report": cmd_report,
        "api": cmd_api,
    }
    commands[args.command]()


if __name__ == "__main__":
    main()
