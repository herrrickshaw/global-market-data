#!/usr/bin/env python3
"""
Daily Price Collection - Phase 2 of Backtest Data Pipeline
Collects 1,825 trading days (5 years) of daily price data for 60 companies.
Used to correlate daily price movements with quarterly expansion metrics.
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')


class DailyPriceCollector:
    """Collect 5 years of daily price data from yfinance"""

    def __init__(self):
        self.end_date = datetime.now()
        self.start_date = self.end_date - timedelta(days=5*365)

    def select_sample_companies(self, n_companies: int = 60) -> list:
        """Same 60 companies as Phase 1"""
        tickers = [
            # Tech (15)
            'MSFT', 'NVDA', 'CRM', 'ADBE', 'AVGO', 'MSTR', 'FTNT', 'NET', 'DDOG', 'CRWD',
            'SNOW', 'PANW', 'ORCL', 'AAPL', 'IBM',
            # Industrials (15)
            'CAT', 'GE', 'LMT', 'BA', 'RTX', 'ISRG', 'ABB', 'EMR', 'DE', 'CARR',
            'TT', 'IRM', 'WAFER', 'FLEX', 'LOGI',
            # Energy (12)
            'CVX', 'COP', 'SLB', 'MPC', 'OKE', 'NUE', 'FCX', 'CF', 'ALB', 'LYB', 'DOW', 'HUN',
            # Transportation (6)
            'DAL', 'UAL', 'ALK', 'NCLH', 'CCL', 'RCL',
            # Real Estate (8)
            'PLD', 'DLR', 'EQIX', 'VICI', 'PSA', 'UMC', 'CCI', 'AMT',
            # Healthcare (8)
            'JNJ', 'PFE', 'MRK', 'BIIB', 'LLY', 'BMY', 'AMGN', 'GILD'
        ]
        return tickers[:n_companies]

    def fetch_price_data(self, ticker: str) -> pd.DataFrame:
        """Fetch 5 years of daily price data"""
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(start=self.start_date, end=self.end_date)

            if df.empty or len(df) < 100:
                return pd.DataFrame()

            # Calculate additional metrics
            df['returns'] = df['Close'].pct_change() * 100  # Daily returns %
            df['log_returns'] = np.log(df['Close'] / df['Close'].shift(1)) * 100
            df['volume_ma_20'] = df['Volume'].rolling(20).mean()
            df['price_momentum_20'] = df['Close'].pct_change(20) * 100  # 20-day momentum
            df['volatility_20'] = df['returns'].rolling(20).std()  # 20-day volatility

            df['ticker'] = ticker
            df.reset_index(inplace=True)

            return df

        except Exception as e:
            return pd.DataFrame()

    def collect_prices(self, tickers: list) -> dict:
        """Fetch daily price data for all companies"""
        print(f"\n" + "="*80)
        print(f"PHASE 2: DAILY PRICE DATA COLLECTION (5 YEARS)")
        print(f"="*80)

        print(f"\n🎯 COMPANIES TO ANALYZE: {len(tickers)}")
        print(f"   Time period: {self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}")
        print(f"   Trading days per company: ~1,825 (5 years)")
        print(f"   Total data points: ~{len(tickers) * 1825:,} daily records expected")

        collected = {}
        success_count = 0
        total_records = 0

        for i, ticker in enumerate(tickers, 1):
            print(f"\r   [{i:2d}/{len(tickers)}] Fetching {ticker}...", end='', flush=True)

            df = self.fetch_price_data(ticker)
            if not df.empty:
                collected[ticker] = df
                success_count += 1
                total_records += len(df)

        print(f"\n✅ Collection complete: {success_count}/{len(tickers)} companies")
        print(f"   Total records collected: {total_records:,}")
        print(f"   Average records per company: {total_records // success_count if success_count > 0 else 0}")

        return collected

    def analyze_price_metrics(self, collected: dict):
        """Analyze price data characteristics"""
        print(f"\n📊 PRICE DATA METRICS EXTRACTED:")
        print(f"   • Daily Open, High, Low, Close, Volume")
        print(f"   • Adjusted Close (split & dividend adjusted)")
        print(f"   • Daily Returns (% change)")
        print(f"   • Log Returns (log-scale for modeling)")
        print(f"   • Price Momentum (20-day & trend)")
        print(f"   • Volatility (20-day rolling std)")
        print(f"   • Volume Moving Average")

        print(f"\n📈 PRICE CHARACTERISTICS:")

        volatilities = []
        returns_list = []
        momentum_list = []

        for ticker, df in collected.items():
            if not df.empty:
                vol = df['volatility_20'].mean()
                ret = df['returns'].mean()
                mom = df['price_momentum_20'].mean()

                if not pd.isna(vol):
                    volatilities.append(vol)
                if not pd.isna(ret):
                    returns_list.append(ret)
                if not pd.isna(mom):
                    momentum_list.append(mom)

        if volatilities:
            print(f"\n   Volatility (20-day rolling):")
            print(f"      Mean: {np.mean(volatilities):.2f}%")
            print(f"      Median: {np.median(volatilities):.2f}%")
            print(f"      High: {np.max(volatilities):.2f}%")
            print(f"      Low: {np.min(volatilities):.2f}%")

        if returns_list:
            print(f"\n   Daily Returns:")
            print(f"      Mean: {np.mean(returns_list):.3f}%")
            print(f"      Median: {np.median(returns_list):.3f}%")
            print(f"      Positive days ratio: {sum(1 for r in returns_list if r > 0) / len(returns_list) * 100:.1f}%")

        if momentum_list:
            print(f"\n   20-Day Momentum:")
            print(f"      Mean: {np.mean(momentum_list):.2f}%")
            print(f"      Positive momentum: {sum(1 for m in momentum_list if m > 0)} companies")
            print(f"      Negative momentum: {sum(1 for m in momentum_list if m < 0)} companies")

        return collected

    def prepare_for_correlation(self, price_data: dict) -> pd.DataFrame:
        """Prepare price data for correlation with quarterly metrics"""
        print(f"\n📊 PREPARING FOR QUARTERLY CORRELATION ANALYSIS:")

        # Create aggregated metrics for each company
        correlation_data = []

        for ticker, df in price_data.items():
            if df.empty:
                continue

            # Most recent metrics (for matching with latest quarterly data)
            latest_price = df['Close'].iloc[-1]
            price_5yr_ago = df['Close'].iloc[0] if len(df) > 0 else latest_price

            if price_5yr_ago and price_5yr_ago > 0:
                price_cagr = ((latest_price / price_5yr_ago) ** (1/5) - 1) * 100
            else:
                price_cagr = 0

            # Calculate price momentum at different windows
            momentum_1y = df['price_momentum_20'].tail(252).mean() if len(df) > 252 else 0
            momentum_2y = df['price_momentum_20'].tail(504).mean() if len(df) > 504 else 0

            # Volatility at different windows
            vol_1y = df['volatility_20'].tail(252).mean() if len(df) > 252 else 0
            vol_3y = df['volatility_20'].tail(756).mean() if len(df) > 756 else 0

            # Return metrics
            total_return = ((latest_price / price_5yr_ago) - 1) * 100 if price_5yr_ago > 0 else 0
            sharpe_ratio = df['returns'].mean() / df['returns'].std() * np.sqrt(252) if df['returns'].std() > 0 else 0

            # Max drawdown
            cummax = df['Close'].cummax()
            drawdown = (df['Close'] - cummax) / cummax * 100
            max_dd = drawdown.min()

            correlation_data.append({
                'ticker': ticker,
                'price_5yr_cagr': price_cagr,
                'total_return': total_return,
                'momentum_1y': momentum_1y,
                'momentum_2y': momentum_2y,
                'volatility_1y': vol_1y,
                'volatility_3y': vol_3y,
                'sharpe_ratio': sharpe_ratio,
                'max_drawdown': max_dd,
                'latest_price': latest_price,
            })

        df_corr = pd.DataFrame(correlation_data)

        print(f"\n   Price-based features for correlation:")
        print(f"      ✓ 5-year price CAGR")
        print(f"      ✓ Total return")
        print(f"      ✓ 1-year & 2-year momentum")
        print(f"      ✓ Volatility (1-year & 3-year)")
        print(f"      ✓ Sharpe ratio (risk-adjusted return)")
        print(f"      ✓ Maximum drawdown")

        if not df_corr.empty:
            print(f"\n   Top performers by 5-year price CAGR:")
            top_performers = df_corr.nlargest(5, 'price_5yr_cagr')[['ticker', 'price_5yr_cagr', 'volatility_1y', 'sharpe_ratio']]
            for idx, row in top_performers.iterrows():
                print(f"      {row['ticker']:6s} - CAGR: {row['price_5yr_cagr']:6.1f}% | Vol: {row['volatility_1y']:5.1f}% | Sharpe: {row['sharpe_ratio']:5.2f}")

        return df_corr


if __name__ == "__main__":
    collector = DailyPriceCollector()
    tickers = collector.select_sample_companies(60)

    # Fetch daily prices
    price_data = collector.collect_prices(tickers)

    # Analyze price characteristics
    price_data = collector.analyze_price_metrics(price_data)

    # Prepare for correlation with quarterly data
    df_correlation = collector.prepare_for_correlation(price_data)

    # Summary
    print(f"\n✅ PHASE 2 COMPLETE")
    print(f"   Companies analyzed: {len(price_data)}")
    print(f"   Total price records: {sum(len(df) for df in price_data.values()):,}")
    print(f"   Ready for: Phase 3 - Correlation analysis (price vs quarterly metrics)")
    print(f"   Ready for: Phase 4 - Backtest (8-D vs 11-D model weights)")
