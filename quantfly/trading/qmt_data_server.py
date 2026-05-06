# -*- encoding: utf-8 -*-
"""
QMT 数据服务 — HTTP API
=======================
在 Windows QMT 环境运行，把行情数据暴露成 HTTP 接口供其他机器调用。

用法（Windows QMT Python 环境）：
    python qmt_data_server.py

然后其他机器通过 http://<Windows-IP>:8888 调用。

依赖（QMT Python 环境）：
    pip install xtquant flask flask-cors

API 端点：
    GET /health              健康检查
    GET /quote?code=512760   实时行情（单只）
    GET /quotes?codes=512760,512660  实时行情（批量）
    GET /bars?code=512760&period=1m&count=100  K线数据
    GET /positions           持仓查询
"""

import logging
import sys
import os
import json
from datetime import datetime, time as dtime
from typing import Optional

from flask import Flask, jsonify, request
from flask_cors import CORS

# ---- QMT xtquant 导入 ----
try:
    import xtquant.xtdatacenter as dc
    import xtquant.xtconstant as xtconst
    XTQUANT_AVAILABLE = True
except ImportError:
    XTQUANT_AVAILABLE = False
    print("[WARNING] xtquant 未安装，服务将以 Stub 模式运行")

# ---- 日志配置 ----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("QMTDataServer")

# ---- Flask App ----
app = Flask(__name__)
CORS(app)


# =============================================================================
# QMT 数据访问封装
# =============================================================================

class QMTDataProvider:
    """QMT 数据提供器，封装 xtquant 调用"""

    def __init__(self, data_back_addr: str = "172.17.0.1:5860"):
        self._connected = False
        self._addr = data_back_addr
        if XTQUANT_AVAILABLE:
            try:
                dc.set_data_back_addr(data_back_addr)
                self._connected = True
                logger.info(f"QMT 数据服务已连接: {data_back_addr}")
            except Exception as e:
                logger.warning(f"QMT 连接失败: {e}，使用 Stub 模式")
        else:
            logger.warning("xtquant 不可用，使用 Stub 模式")

    # ---- 实时行情 ----

    def get_quote(self, code: str) -> dict:
        """
        获取单只证券实时行情

        Args:
            code: 证券代码，如 "512760.XSHG" 或 "512760"
        Returns:
            dict: 行情数据
        """
        if not self._connected:
            return self._stub_quote(code)

        code = self._normalize_code(code)
        try:
            result = dc.get_quote([code])
            if result is None or result.empty:
                return self._stub_quote(code)
            row = result.iloc[0]
            return {
                "code": code,
                "name": row.get("name", ""),
                "last_price": float(row.get("lastPrice", 0)),
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "volume": int(row.get("volume", 0)),
                "amount": float(row.get("amount", 0)),
                "bid1": float(row.get("bid1", 0)),
                "ask1": float(row.get("ask1", 0)),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.warning(f"get_quote [{code}] 失败: {e}")
            return self._stub_quote(code)

    def get_quotes(self, codes: list[str]) -> list[dict]:
        """批量获取实时行情"""
        results = []
        for code in codes:
            results.append(self.get_quote(code))
        return results

    # ---- K线数据 ----

    def get_bars(
        self,
        code: str,
        period: str = "1m",
        count: int = 100,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> list[dict]:
        """
        获取K线数据

        Args:
            code: 证券代码，如 "512760.XSHG"
            period: 周期 "1m"/"5m"/"15m"/"30m"/"1h"/"1d"
            count: 数量
            start_time: 开始时间 YYYYMMDDHHMMSS
            end_time: 结束时间 YYYYMMDDHHMMSS
        Returns:
            list of K线 bar
        """
        if not self._connected:
            return self._stub_bars(code, count)

        code = self._normalize_code(code)
        period_map = {
            "1m": "1m", "5m": "5m", "15m": "15m",
            "30m": "30m", "1h": "1h", "1d": "1d",
        }
        qmt_period = period_map.get(period, "1m")

        try:
            data = dc.get_market_data(
                stock_list=[code],
                start_time=start_time,
                end_time=end_time,
                count=count,
                period=qmt_period,
                fields=["open", "high", "low", "close", "volume", "amount"],
                dividend_type="none",
            )
            if data is None or data.empty:
                return []

            records = []
            for _, row in data.iterrows():
                records.append({
                    "time": str(row.name),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                    "amount": float(row["amount"]),
                })
            return records
        except Exception as e:
            logger.warning(f"get_bars [{code}] 失败: {e}")
            return self._stub_bars(code, count)

    # ---- 持仓查询 ----

    def get_positions(self) -> list[dict]:
        """获取当前持仓（需要 QMT 交易账户登录）"""
        if not self._connected:
            return []
        try:
            # xtquant 持仓查询
            import xtquant.tradecenter as tc
            positions = tc.get_positions()
            if positions is None:
                return []
            results = []
            for _, row in positions.iterrows():
                results.append({
                    "code": row.get("stock_code", ""),
                    "name": row.get("stock_name", ""),
                    "volume": int(row.get("volume", 0)),
                    "can_use_volume": int(row.get("can_use_volume", 0)),
                    "open_avg_price": float(row.get("open_avg_price", 0)),
                    "last_price": float(row.get("last_price", 0)),
                    "market_value": float(row.get("market_value", 0)),
                    "profit": float(row.get("profit", 0)),
                })
            return results
        except Exception as e:
            logger.warning(f"get_positions 失败: {e}")
            return []

    # ---- 工具方法 ----

    @staticmethod
    def _normalize_code(code: str) -> str:
        """统一代码格式，如 512760 -> 512760.XSHG"""
        code = code.strip()
        if "." in code:
            return code
        # A股规则：6开头沪市，0/3开头深市
        if code.startswith("6"):
            return f"{code}.XSHG"
        else:
            return f"{code}.XSHE"

    # ---- Stub 模式（xtquant 不可用时） ----

    @staticmethod
    def _stub_quote(code: str) -> dict:
        import random
        return {
            "code": code,
            "name": "STUB",
            "last_price": round(random.uniform(1, 100), 2),
            "open": round(random.uniform(1, 100), 2),
            "high": round(random.uniform(1, 100), 2),
            "low": round(random.uniform(1, 100), 2),
            "volume": random.randint(10000, 1000000),
            "amount": round(random.uniform(10000, 1000000), 2),
            "bid1": round(random.uniform(1, 100), 2),
            "ask1": round(random.uniform(1, 100), 2),
            "timestamp": datetime.now().isoformat(),
            "_stub": True,
        }

    @staticmethod
    def _stub_bars(code: str, count: int) -> list[dict]:
        import random
        from datetime import datetime, timedelta
        now = datetime.now()
        result = []
        base_price = 5.0
        for i in range(count):
            t = now - timedelta(minutes=count - i)
            open_p = round(base_price * random.uniform(0.98, 1.02), 3)
            close_p = round(base_price * random.uniform(0.98, 1.02), 3)
            high_p = round(max(open_p, close_p) * random.uniform(1.0, 1.02), 3)
            low_p = round(min(open_p, close_p) * random.uniform(0.98, 1.0), 3)
            vol = random.randint(10000, 500000)
            result.append({
                "time": t.strftime("%Y-%m-%d %H:%M:%S"),
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": vol,
                "amount": round(vol * close_p, 2),
            })
        return result


# =============================================================================
# Flask 路由
# =============================================================================

# 全局数据提供者
_provider: Optional[QMTDataProvider] = None


@app.before_request
def init_provider():
    global _provider
    if _provider is None:
        data_addr = os.environ.get("QMT_DATA_ADDR", "172.17.0.1:5860")
        _provider = QMTDataProvider(data_back_addr=data_addr)


@app.route("/health", methods=["GET"])
def health():
    """健康检查"""
    return jsonify({
        "status": "ok",
        "connected": _provider._connected if _provider else False,
        "xtquant": XTQUANT_AVAILABLE,
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/quote", methods=["GET"])
def quote():
    """单只证券实时行情"""
    code = request.args.get("code", "")
    if not code:
        return jsonify({"error": "缺少 code 参数"}), 400
    data = _provider.get_quote(code)
    return jsonify(data)


@app.route("/quotes", methods=["GET"])
def quotes():
    """批量实时行情，codes=code1,code2,..." ""
    codes_str = request.args.get("codes", "")
    if not codes_str:
        return jsonify({"error": "缺少 codes 参数"}), 400
    codes = [c.strip() for c in codes_str.split(",") if c.strip()]
    data = _provider.get_quotes(codes)
    return jsonify({"data": data})


@app.route("/bars", methods=["GET"])
def bars():
    """
    K线数据
    参数:
        code: 证券代码，如 512760（自动判断沪/深）
        period: 周期，默认 1m（1m/5m/15m/30m/1h/1d）
        count: 数量，默认 100
        start_time: 开始时间 YYYYMMDDHHMMSS（可选）
        end_time: 结束时间 YYYYMMDDHHMMSS（可选）
    """
    code = request.args.get("code", "")
    if not code:
        return jsonify({"error": "缺少 code 参数"}), 400
    period = request.args.get("period", "1m")
    count = int(request.args.get("count", "100"))
    start_time = request.args.get("start_time") or None
    end_time = request.args.get("end_time") or None

    data = _provider.get_bars(code, period, count, start_time, end_time)
    return jsonify({"code": code, "period": period, "count": len(data), "data": data})


@app.route("/positions", methods=["GET"])
def positions():
    """持仓查询"""
    data = _provider.get_positions()
    return jsonify({"data": data})


@app.route("/", methods=["GET"])
def index():
    """API 说明页"""
    return jsonify({
        "name": "QMT Data Server",
        "version": "1.0",
        "endpoints": [
            "GET /health",
            "GET /quote?code=512760",
            "GET /quotes?codes=512760,512660",
            "GET /bars?code=512760&period=1m&count=100",
            "GET /positions",
        ],
    })


# =============================================================================
# 启动入口
# =============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="QMT Data Server")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8888, help="监听端口（默认 8888）")
    parser.add_argument(
        "--data-addr", default="172.17.0.1:5860",
        help="QMT 数据服务地址（默认 172.17.0.1:5860）",
    )
    args = parser.parse_args()

    logger.info(f"=" * 50)
    logger.info(f"QMT Data Server 启动")
    logger.info(f"监听: http://{args.host}:{args.port}")
    logger.info(f"QMT 数据地址: {args.data_addr}")
    logger.info(f"xtquant 可用: {XTQUANT_AVAILABLE}")
    logger.info(f"=" * 50)

    # 设置环境变量供 before_request 使用
    os.environ["QMT_DATA_ADDR"] = args.data_addr

    app.run(
        host=args.host,
        port=args.port,
        debug=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
