# -*- encoding: utf-8 -*-
"""
Windows QMT Mini xtquant 连接测试
================================
在 Windows 上用 QMT Mini 自带的 Python 运行此脚本，
验证 xtquant 分钟K线数据能否正常获取。

使用方法:
  1. 打开 QMT Mini，登录交易服务器
  2. 在 QMT 的 Python 环境（或本机Python装了xtquant）运行:
     python test_qmt_minute.py

  示例:
     D:\QMT交易端\GlodonTAPy\python.exe test_qmt_minute.py
"""
import sys

print("Python:", sys.executable)
print("Python version:", sys.version)

# Test 1: xtquant 导入
print("\n=== Test 1: xtquant 导入 ===")
try:
    import xtquant
    print(f"✓ xtquant {xtquant.__version__} 已安装")
except ImportError as e:
    print(f"✗ xtquant 未安装: {e}")
    print("  安装命令: pip install xtquant")
    sys.exit(1)

# Test 2: xtdatacenter 连接
print("\n=== Test 2: xtdatacenter 连接 ===")
try:
    import xtquant.xtdatacenter as dc
    import xtquant.xtconstant as xtc
    print("✓ xtdatacenter 导入成功")

    # 尝试连接本地QMT数据服务
    # 默认端口5860
    try:
        dc.set_data_back_addr("127.0.0.1:5860")
        print("✓ 数据服务地址已设置: 127.0.0.1:5860")
    except Exception as e:
        print(f"  设置地址失败(可能正常): {e}")

except Exception as e:
    print(f"✗ xtdatacenter 导入失败: {e}")
    sys.exit(1)

# Test 3: 获取分钟K线数据
print("\n=== Test 3: 获取分钟K线数据 ===")
test_codes = ["000001.XSHG", "600256.XSHG", "002594.XSHE"]

for code in test_codes:
    clean_code = code.split(".")[0]
    market = "XSHG" if code.endswith("XSHG") else "XSHE"
    print(f"\n  测试: {code}")

    try:
        data = dc.get_market_data(
            stock_list=[clean_code],
            start_time=None,
            end_time=None,
            count=10,  # 只取最近10条
            period="1m",
            fields=["open", "high", "low", "close", "volume", "amount"],
            dividend_type="none",
        )

        if data is None or data.empty:
            print(f"    ✗ 返回空数据")
            continue

        print(f"    ✓ 获取成功: {len(data)} 条")
        print(f"    列名: {list(data.columns[:6])}")
        print(f"    最新: {data.iloc[-1].to_dict() if len(data) > 0 else 'N/A'}")

    except Exception as e:
        print(f"    ✗ 获取失败: {e}")

# Test 4: 获取实时tick
print("\n=== Test 4: 获取实时行情 ===")
try:
    import xtquant.xtdata as xd

    for code in test_codes[:1]:
        clean_code = code.split(".")[0]
        print(f"  {code}:")
        try:
            # get_market_data 1分钟K线
            df = xd.get_market_data(
                stock_list=[clean_code],
                start_time=None,
                end_time=None,
                count=5,
                period="1m",
            )
            print(f"    ✓ xtdata.get_market_data: {len(df)} 条")
        except Exception as e:
            print(f"    xtdata 失败: {e}")
except Exception as e:
    print(f"  xtquant.xtdata 不可用: {e}")

# Test 5: 测试行业ETF
print("\n=== Test 5: 行业ETF分钟数据 ===")
etf_codes = [
    "512760.XSHG",  # 芯片半导体
    "515790.XSHG",  # 光伏
    "159995.XSHG",  # 人工智能
]

for code in etf_codes:
    clean_code = code.split(".")[0]
    try:
        data = dc.get_market_data(
            stock_list=[clean_code],
            count=5,
            period="1m",
            fields=["close", "volume"],
            dividend_type="none",
        )
        if data is not None and not data.empty:
            print(f"  ✓ {code}: {len(data)} 条, 最新收盘 {data['close'].iloc[-1] if 'close' in data.columns else 'N/A'}")
        else:
            print(f"  ✗ {code}: 空数据")
    except Exception as e:
        print(f"  ✗ {code}: {e}")

print("\n=== 测试完成 ===")
print("\n如全部 ✓，可运行 qmt_live_rank.py 获取完整选股结果")
print("如 xtquant 不可用，自动切换到 akshare 数据源（数据有延迟）")
