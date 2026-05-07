# -*- encoding: utf-8 -*-
"""
QMT 数据客户端 — 从 Mac 调用 Windows QMT 服务
==============================================
Usage:
    python -m quantfly.trading.qmt_client

依赖:
    pip install requests
"""

import logging
import sys
from typing import Optional

import requests

logger = logging.getLogger("QMTClient")

DEFAULT_BASE_URL = "http://10.6.98.168:8766"  # Windows QMT API


class QMTClient:
    """QMT HTTP API 客户端"""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "QMTClient/1.0"})

    # ---- 基础请求 ----

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"请求失败 [{path}]: {e}")
            return {"error": str(e)}

    # ---- API 方法 ----

    def health(self) -> dict:
        """健康检查"""
        return self._get("/live/health")

    def get_quote(self, code: str) -> dict:
        """获取单只证券实时行情"""
        code = code.replace(".XSHG", "").replace(".XSHE", "")
        return self._get("/data/quote", {"codes": code})

    def get_quotes(self, codes: list[str]) -> dict:
        """批量获取实时行情"""
        clean = [c.replace(".XSHG", "").replace(".XSHE", "") for c in codes]
        codes_str = ",".join(clean)
        return self._get("/data/quote", {"codes": codes_str})

    def get_bars(
        self,
        code: str,
        period: str = "1d",
        count: int = 100,
    ) -> dict:
        """获取K线数据"""
        code = code.replace(".XSHG", "").replace(".XSHE", "")
        return self._get("/data/kline", {
            "code": code,
            "period": period,
            "count": count,
        })

    def get_minute(
        self,
        code: str,
        period: str = "5m",
        count: int = 240,
    ) -> dict:
        """获取分钟K线"""
        code = code.replace(".XSHG", "").replace(".XSHE", "")
        return self._get("/data/minute", {
            "code": code,
            "period": period,
            "count": count,
        })

    def get_money_flow(self, codes: list[str], days: int = 5) -> dict:
        """获取资金流向"""
        clean = [c.replace(".XSHG", "").replace(".XSHE", "") for c in codes]
        codes_str = ",".join(clean)
        return self._get("/data/money_flow", {"codes": codes_str, "days": days})

    def get_sectors(self) -> dict:
        """获取板块列表"""
        return self._get("/data/sectors")

    def get_sector_stocks(self, sector: str) -> dict:
        """获取板块成分股"""
        return self._get("/data/sector/stocks", {"sector": sector})

    def get_positions(self) -> dict:
        """获取持仓"""
        return self._get("/live/positions")

    # ---- 便捷方法 ----

    def get_industry_etf_quotes(self) -> dict:
        """获取主要行业ETF实时行情"""
        codes = [
            "512760",  # 芯片半导体
            "515790",  # 光伏产业
            "512660",  # 国防军工
            "159928",  # 主要消费
            "512180",  # 生物医药
            "515050",  # 5G通信
            "159995",  # 人工智能
            "515980",  # 云计算
            "512800",  # 中证银行
            "512690",  # 中证酒
        ]
        return self.get_quotes(codes)


# =============================================================================
# CLI 入口
# =============================================================================

def main():
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="QMT Data Client")
    parser.add_argument("--host", default="10.6.98.168", help="Windows QMT 服务 IP")
    parser.add_argument("action", choices=["health", "quote", "quotes", "bars", "positions", "etfs"],
                        help="操作")
    parser.add_argument("--code", default="512760", help="证券代码")
    parser.add_argument("--period", default="1d", help="周期")
    parser.add_argument("--count", type=int, default=100, help="数量")
    args = parser.parse_args()

    base_url = f"http://{args.host}:8766"
    client = QMTClient(base_url=base_url)

    if args.action == "health":
        result = client.health()
    elif args.action == "quote":
        result = client.get_quote(args.code)
    elif args.action == "quotes":
        codes = input("输入代码（逗号分隔）: ").strip().split(",")
        result = client.get_quotes(codes)
    elif args.action == "bars":
        result = client.get_bars(args.code, args.period, args.count)
    elif args.action == "positions":
        result = client.get_positions()
    elif args.action == "etfs":
        result = client.get_industry_etf_quotes()

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
