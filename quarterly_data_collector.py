#!/usr/bin/env python3
"""
Quarterly Data Collection - Phase 1 of Backtest Data Pipeline
Collects 5 years of quarterly financial data for 60 representative companies.
Analyzes trends before diving into 1,825 days of daily price data.
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import json
import pickle
warnings.filterwarnings('ignore')


class QuarterlyDataCollector:
    """Collect 5 years of quarterly financial data from yfinance"""

    def __init__(self):
        self.quarterly_data = {}
        self.collection_start = datetime.now()

    def select_sample_companies(self, n_companies: int = 60) -> list:
        """Select 60 representative companies across sectors"""
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

    def fetch_quarterly_data(self, ticker: str) -> pd.DataFrame:
        """Fetch 5 years of quarterly financial data"""
        try:
            stock = yf.Ticker(ticker)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=5*365)

            # Try to get quarterly financials
            quarterly_info = []

            # Get quarterly cash flow statement
            try:
                qtr_cf = stock.quarterly_cashflow
                if qtr_cf is not None and len(qtr_cf.columns) > 0:
                    qtr_cf = qtr_cf.iloc[:, :20]  # Last 20 quarters (5 years)

                    # Get quarterly income statement
                    qtr_is = stock.quarterly_income_stmt
                    if qtr_is is not None and len(qtr_is.columns) > 0:
                        qtr_is = qtr_is.iloc[:, :20]

                        # Get quarterly balance sheet
                        qtr_bs = stock.quarterly_balance_sheet
                        if qtr_bs is not None and len(qtr_bs.columns) > 0:
                            qtr_bs = qtr_bs.iloc[:, :20]

                            # Extract metrics
                            quarters = qtr_cf.columns.tolist()
                            for qtr in quarters[:20]:  # Last 20 quarters
                                metrics = {
                                    'ticker': ticker,
                                    'quarter': qtr.strftime('%Y-Q') + str((qtr.month-1)//3 + 1),
                                    'date': qtr,
                                }

                                # Income statement metrics
                                try:
                                    metrics['revenue'] = qtr_is.loc['Total Revenue', qtr] if 'Total Revenue' in qtr_is.index else None
                                    metrics['operating_income'] = qtr_is.loc['Operating Income', qtr] if 'Operating Income' in qtr_is.index else None
                                    metrics['net_income'] = qtr_is.loc['Net Income', qtr] if 'Net Income' in qtr_is.index else None
                                except:
                                    metrics['revenue'] = metrics['operating_income'] = metrics['net_income'] = None

                                # Cash flow metrics
                                try:
                                    metrics['ocf'] = qtr_cf.loc['Operating Cash Flow', qtr] if 'Operating Cash Flow' in qtr_cf.index else None
                                    metrics['capex'] = abs(qtr_cf.loc['Capital Expenditure', qtr]) if 'Capital Expenditure' in qtr_cf.index else None
                                except:
                                    metrics['ocf'] = metrics['capex'] = None

                                # Balance sheet metrics
                                try:
                                    metrics['total_assets'] = qtr_bs.loc['Total Assets', qtr] if 'Total Assets' in qtr_bs.index else None
                                    metrics['total_debt'] = qtr_bs.loc['Total Debt', qtr] if 'Total Debt' in qtr_bs.index else None
                                    metrics['total_equity'] = qtr_bs.loc['Total Stockholder Equity', qtr] if 'Total Stockholder Equity' in qtr_bs.index else None
                                except:
                                    metrics['total_assets'] = metrics['total_debt'] = metrics['total_equity'] = None

                                quarterly_info.append(metrics)
            except:
                pass

            if quarterly_info:
                df = pd.DataFrame(quarterly_info)
                return df.sort_values('date').reset_index(drop=True)
            else:
                return pd.DataFrame()

        except Exception as e:
            return pd.DataFrame()

    def calculate_metrics(self, df: pd.DataFrame) -> dict:
        """Calculate expansion metrics from quarterly data"""
        metrics = {}

        if df.empty or len(df) < 4:
            return metrics

        # CAGR calculations (5-year / 4 periods per year)
        def cagr(start_val, end_val, years=5):
            if start_val is None or end_val is None or start_val <= 0:
                return None
            return ((end_val / start_val) ** (1/years) - 1) * 100

        try:
            # Revenue CAGR
            revenue_vals = df['revenue'].dropna()
            if len(revenue_vals) >= 2:
                metrics['revenue_cagr'] = cagr(revenue_vals.iloc[-1], revenue_vals.iloc[0])

            # Capex CAGR
            capex_vals = df['capex'].dropna()
            if len(capex_vals) >= 2:
                metrics['capex_cagr'] = cagr(capex_vals.iloc[-1], capex_vals.iloc[0])

            # Debt CAGR
            debt_vals = df['total_debt'].dropna()
            if len(debt_vals) >= 2:
                metrics['debt_cagr'] = cagr(debt_vals.iloc[-1], debt_vals.iloc[0])

            # Average metrics
            metrics['avg_ocf'] = df['ocf'].mean()
            metrics['avg_capex'] = df['capex'].mean()

            # FCF average
            fcf = df['ocf'] - df['capex']
            metrics['avg_fcf'] = fcf.mean()

            # Latest D/E ratio
            latest = df.iloc[-1]
            if latest['total_equity'] and latest['total_equity'] > 0:
                metrics['latest_de'] = latest['total_debt'] / latest['total_equity']

            # Operating margin trend
            oi_margin = (df['operating_income'] / df['revenue'] * 100).dropna()
            if len(oi_margin) >= 2:
                metrics['oi_margin_trend'] = oi_margin.iloc[0] - oi_margin.iloc[-1]

            # Net margin trend
            ni_margin = (df['net_income'] / df['revenue'] * 100).dropna()
            if len(ni_margin) >= 2:
                metrics['ni_margin_trend'] = ni_margin.iloc[0] - ni_margin.iloc[-1]

        except:
            pass

        return metrics

    def collect_data(self, tickers: list) -> dict:
        """Fetch quarterly data for all companies"""
        print(f"\n" + "="*80)
        print(f"PHASE 1: QUARTERLY FUNDAMENTALS COLLECTION (5 YEARS)")
        print(f"="*80)

        print(f"\n🎯 COMPANIES TO ANALYZE: {len(tickers)}")
        print(f"   Data period: 5 years = 20 quarterly reports per company")
        print(f"   Total data points: ~{len(tickers) * 20:,} quarterly records expected")

        collected = {}
        success_count = 0

        for i, ticker in enumerate(tickers, 1):
            print(f"\r   [{i:2d}/{len(tickers)}] Fetching {ticker}...", end='', flush=True)

            df = self.fetch_quarterly_data(ticker)
            if not df.empty:
                metrics = self.calculate_metrics(df)
                collected[ticker] = {
                    'quarterly_data': df,
                    'metrics': metrics,
                }
                success_count += 1

        print(f"\n✅ Collection complete: {success_count}/{len(tickers)} companies")
        return collected

    def analyze_trends(self, collected: dict):
        """Analyze 5-year trends"""
        print(f"\n📊 QUARTERLY METRICS COLLECTED:")
        print(f"   • Revenue trends (CAGR)")
        print(f"   • Operating income & margins")
        print(f"   • Capex acceleration (CAGR)")
        print(f"   • Debt trajectory (CAGR)")
        print(f"   • Operating cash flow")
        print(f"   • Free cash flow (OCF - Capex)")
        print(f"   • Debt-to-equity ratios")
        print(f"   • Capex intensity")

        print(f"\n📈 EXPANSION SCORE SUMMARY:")

        expansion_scores = []
        for ticker, data in collected.items():
            metrics = data['metrics']
            if metrics:
                # Simple expansion score
                score = 0
                if metrics.get('revenue_cagr', 0) and metrics['revenue_cagr'] > 10:
                    score += 25
                if metrics.get('capex_cagr', 0) and metrics['capex_cagr'] > 15:
                    score += 25
                if metrics.get('avg_fcf', 0) and metrics['avg_fcf'] > 0:
                    score += 25
                if metrics.get('latest_de', 0) and 0 < metrics['latest_de'] < 1:
                    score += 25

                expansion_scores.append({'ticker': ticker, 'score': score})

        if expansion_scores:
            df_scores = pd.DataFrame(expansion_scores).sort_values('score', ascending=False)

            high_expansion = len(df_scores[df_scores['score'] >= 75])
            medium_expansion = len(df_scores[(df_scores['score'] >= 50) & (df_scores['score'] < 75)])
            low_expansion = len(df_scores[df_scores['score'] < 50])

            print(f"\n   High expansion (≥75): {high_expansion} companies")
            print(f"   Medium expansion (50-75): {medium_expansion} companies")
            print(f"   Low expansion (<50): {low_expansion} companies")

            print(f"\n   Top 10 expansion candidates:")
            for i, row in df_scores.head(10).iterrows():
                print(f"      {i+1}. {row['ticker']:6s} - Score: {row['score']:.0f}/100")

        return collected


if __name__ == "__main__":
    collector = QuarterlyDataCollector()
    tickers = collector.select_sample_companies(60)

    # Fetch quarterly data
    collected = collector.collect_data(tickers)

    # Analyze trends
    collector.analyze_trends(collected)

    # Summary
    print(f"\n✅ PHASE 1 COMPLETE")
    print(f"   Companies analyzed: {len(collected)}")
    print(f"   Estimated quarterly records: ~{len(collected) * 20}")
    print(f"   Next: Phase 2 - Collect daily price data for correlation analysis")
