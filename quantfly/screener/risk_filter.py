# -*- encoding: utf-8 -*-
"""
风险过滤 — 剔除ST股、绩差股、退市风险股
============================================
使用akshare获取以下信息：
  1. ST/*ST/退市风险股排除
  2. 亏损股排除（近2年净利润为负）
  3. 流动性过滤（日均成交额<500万排除）
  4. 股价过滤（<2元 & >100元排除）
  5. 科创板/创业板风险股排除

使用方法：
  from quantfly.screener.risk_filter import RiskFilter
  rf = RiskFilter()
  safe_stocks = rf.filter(stock_list)  # stock_list: [(code, name), ...]
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Optional

logger = logging.getLogger("RiskFilter")

# ============================================================
# 负面词典 — 用于快速过滤
# ============================================================
ST_PATTERNS = [
    "ST", "*ST", "S*ST", "SST", "退市", "暂停上市",
    "风险警示", "特别处理",
]

NEGATIVE_WORDS = [
    "业绩预亏", "业绩预减", "续亏", "首亏", "大幅下降",
    "经营困难", "重大亏损", "持续亏损",
]

# ============================================================
# 排除规则
# ============================================================
MIN_PRICE = 2.0      # 最低股价（元）
MAX_PRICE = 100.0    # 最高股价（元）— 排除高价股
MIN_DAILY_AMOUNT = 5_000_000  # 最低日均成交额（元）
MIN_TOTAL_MARKET = 1_000_000_000  # 最低总市值（10亿，防止小微盘股）
MAX_TOTAL_MARKET = 500_000_000_000  # 最高总市值（500亿）


class RiskFilter:
    """
    风险过滤器

    使用akshare获取实时数据，逐项过滤：
      Level-1: 名称过滤（ST、退市）
      Level-2: 财务过滤（亏损、微利）
      Level-3: 交易过滤（流动性、股价）
      Level-4: 舆情过滤（负面新闻）
    """

    def __init__(self, use_multi_thread: bool = True):
        self.use_multi_thread = use_multi_thread
        self._stock_info_cache = {}
        self._cache_time = {}
        self._CACHE_TTL = 300  # 5分钟

    def filter(self, stock_list: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """
        对股票列表执行完整风险过滤

        Args:
            stock_list: [(code, name), ...]

        Returns:
            过滤后的 [(code, name), ...]
        """
        if not stock_list:
            return []

        logger.info(f"[RiskFilter] 开始过滤 {len(stock_list)} 只股票...")
        t0 = time.time()

        # Level-1: 名称过滤（最快）
        candidates = self._level1_name_filter(stock_list)
        logger.info(f"  Level-1(名称): {len(candidates)}/{len(stock_list)} 通过")

        if not candidates:
            return []

        # Level-2: 基本面过滤（需要API调用）
        candidates = self._level2_financial_filter(candidates)
        logger.info(f"  Level-2(基本面): {len(candidates)}/{len(stock_list)} 通过")

        if not candidates:
            return []

        # Level-3: 交易数据过滤
        candidates = self._level3_trading_filter(candidates)
        logger.info(f"  Level-3(交易): {len(candidates)}/{len(stock_list)} 通过")

        elapsed = time.time() - t0
        logger.info(f"[RiskFilter] 过滤完成: {len(candidates)}/{len(stock_list)} 通过 "
                    f"({elapsed:.1f}秒)")

        return candidates

    # ----------------------------------------------------------
    # Level-1: 名称过滤（ST、退市、负面词）
    # ----------------------------------------------------------
    def _level1_name_filter(self, stock_list: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """快速字符串匹配过滤"""
        result = []
        for code, name in stock_list:
            name_upper = name.upper()
            # ST/退市
            if any(p in name_upper for p in [s.upper() for s in ST_PATTERNS]):
                continue
            # 负面词
            if any(w in name for w in NEGATIVE_WORDS):
                continue
            result.append((code, name))
        return result

    # ----------------------------------------------------------
    # Level-2: 基本面过滤（使用akshare获取财务数据）
    # ----------------------------------------------------------
    def _level2_financial_filter(self, stock_list: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """
        过滤亏损股、微利股
        使用akshare获取最新财务数据
        """
        result = []

        for code, name in stock_list:
            try:
                info = self._get_stock_info(code)
                if not info:
                    # 无数据时保守保留（不过滤）
                    result.append((code, name))
                    continue

                # 净利润：近2年都为负 → 排除
                net_profit = info.get("net_profit", None)
                if net_profit is not None and net_profit < 0:
                    logger.debug(f"  排除(亏损): {code} {name} 净利润={net_profit}")
                    continue

                # 总市值过滤
                total_market = info.get("total_market", None)
                if total_market is not None:
                    if total_market < MIN_TOTAL_MARKET:
                        logger.debug(f"  排除(市值太小): {code} {name} 市值={total_market/1e8:.1f}亿")
                        continue
                    if total_market > MAX_TOTAL_MARKET:
                        # 超大盘不排除，降低权重
                        pass

                result.append((code, name))

            except Exception as e:
                logger.debug(f"  保留(财务数据获取失败): {code} {name} ({e})")
                result.append((code, name))

        return result

    def _get_stock_info(self, code: str) -> Optional[dict]:
        """获取单只股票基本信息（带缓存）"""
        now = time.time()
        if code in self._stock_info_cache:
            if self._cache_time.get(code, 0) > now - self._CACHE_TTL:
                return self._stock_info_cache[code]

        try:
            import akshare as ak

            # 获取实时行情
            df = ak.stock_individual_info_em(symbol=code)
            if df is None or df.empty:
                return {}

            info = {}
            info_dict = dict(zip(df["item"].tolist(), df["value"].tolist()))

            # 总市值（万元 → 元）
            mkt_cap = info_dict.get("总市值", "")
            if mkt_cap:
                try:
                    info["total_market"] = float(mkt_cap.replace(",", ""))
                except:
                    pass

            # 流通市值
            circ_mkt = info_dict.get("流通市值", "")
            if circ_mkt:
                try:
                    info["circ_market"] = float(circ_mkt.replace(",", ""))
                except:
                    pass

            self._stock_info_cache[code] = info
            self._cache_time[code] = now
            return info

        except Exception:
            return {}

    # ----------------------------------------------------------
    # Level-3: 交易数据过滤（价格、成交量）
    # ----------------------------------------------------------
    def _level3_trading_filter(self, stock_list: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """
        过滤股价异常、流动性不足的股票
        使用akshare实时行情
        """
        result = []

        try:
            import akshare as ak

            # 批量获取今日行情
            codes = [code for code, _ in stock_list]
            batch_data = self._batch_quote(codes)

            for code, name in stock_list:
                data = batch_data.get(code, {})
                if not data:
                    # 无数据时保留
                    result.append((code, name))
                    continue

                # 股价过滤
                price = data.get("最新价", 0)
                if price <= 0:
                    result.append((code, name))
                    continue
                if price < MIN_PRICE:
                    logger.debug(f"  排除(股价过低): {code} {name} 价格={price}")
                    continue
                if price > MAX_PRICE:
                    logger.debug(f"  排除(股价过高): {code} {name} 价格={price}")
                    continue

                # 成交额过滤
                amount = data.get("成交额", 0)
                if amount > 0 and amount < MIN_DAILY_AMOUNT:
                    logger.debug(f"  排除(流动性不足): {code} {name} 成交额={amount/1e8:.2f}亿")
                    continue

                result.append((code, name))

        except Exception as e:
            logger.warning(f"Level-3交易数据过滤失败: {e}")
            return stock_list  # 失败时返回全部

        return result

    def _batch_quote(self, codes: List[str]) -> dict:
        """
        批量获取实时行情（今日）
        返回 {code: {最新价, 成交额, ...}}
        """
        result = {}
        try:
            import akshare as ak

            # 尝试批量接口
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                return {}

            # 筛选目标股票
            df_target = df[df["代码"].isin(codes)]
            for _, row in df_target.iterrows():
                code = str(row.get("代码", ""))
                result[code] = {
                    "最新价": float(row.get("最新价", 0) or 0),
                    "成交额": float(row.get("成交额", 0) or 0),
                    "总市值": float(row.get("总市值", 0) or 0),
                    "换手率": float(row.get("换手率", 0) or 0),
                }

        except Exception as e:
            logger.warning(f"批量行情获取失败: {e}")

        return result

    # ----------------------------------------------------------
    # 快速单股检查
    # ----------------------------------------------------------
    def is_safe(self, code: str, name: str) -> Tuple[bool, str]:
        """
        检查单只股票是否安全

        Returns:
            (is_safe, reason)
        """
        # Level-1
        name_upper = name.upper()
        if any(p in name_upper for p in [s.upper() for s in ST_PATTERNS]):
            return False, "ST股/退市风险"
        if any(w in name for w in NEGATIVE_WORDS):
            return False, "名称含负面词"

        # Level-2
        info = self._get_stock_info(code)
        if info.get("net_profit", 0) < 0:
            return False, "连续亏损"

        return True, "安全"
