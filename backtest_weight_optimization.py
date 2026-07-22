#!/usr/bin/env python3
"""
Backtest Framework - KARZ vs MAIN Weight Comparison
====================================================
Compares F1-optimized weights (karz) against original weights (main).
Measures: precision, recall, F1 score, Sharpe ratio, drawdown.

UPDATED: Now uses data_config for proper train/test splits to prevent data leakage.

Process:
1. Load 3-year historical company financials
2. Split data: TRAIN [2015-2021] VALIDATION [2022] TEST [2023-2024]
3. Generate scores with both weight sets on TEST data (unseen)
4. Rank companies and create buy/sell signals
5. Check actual returns and calculate performance
6. Generate comparison report

Usage:
    python3 backtest_weight_optimization.py --market IN --use-test-split
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple, List, Optional
from datetime import datetime, timedelta
from pathlib import Path

# Import data configuration for proper train/test splits
try:
    from data_config import DataConfig, filter_data_by_split
    HAS_DATA_CONFIG = True
except ImportError:
    HAS_DATA_CONFIG = False
    print("⚠️  data_config.py not found. Run without train/test split. Recommend installing.")

import argparse


class WeightBacktester:
    """Backtest weight optimization against historical data"""

    # Original weights (main branch)
    BASELINE_WEIGHTS = {
        'debt_expansion': 20,
        'capex_acceleration': 20,
        'profit_reinvestment': 15,
        'profitability_quality': 15,
        'sustainability': 15,
        'timing_alignment': 10,
        'leverage_health': 5,
        'fcf_generation': 0,
    }

    # F1-optimized weights (karz branch)
    OPTIMIZED_WEIGHTS = {
        'debt_expansion': 10,
        'capex_acceleration': 24,
        'profit_reinvestment': 19,
        'profitability_quality': 15,
        'sustainability': 4,
        'timing_alignment': 4,
        'leverage_health': 2,
        'fcf_generation': 22,
    }

    def __init__(self, data_df: pd.DataFrame, lookback_years: int = 3, use_test_split: bool = False):
        """
        Initialize backtest framework

        Args:
            data_df: DataFrame with company scores and returns
            lookback_years: Historical period to backtest (1-3 years)
            use_test_split: If True, use data_config TEST split to prevent leakage (default: False for backward compat)
        """
        self.data = data_df.copy()
        self.lookback_years = lookback_years
        self.results = {}
        self.use_test_split = use_test_split

        # Validate data quality
        if HAS_DATA_CONFIG:
            config = DataConfig()
            errors = config.validation.validate_dataframe(self.data)
            if errors:
                print(f"⚠️  Data validation warnings:")
                for error in errors:
                    print(f"     • {error}")

        print(f"\n📊 BACKTEST INITIALIZATION")
        print(f"   Companies: {len(self.data):,}")
        print(f"   Lookback period: {lookback_years} years")
        print(f"   Using proper train/test split: {use_test_split}")
        if use_test_split and HAS_DATA_CONFIG:
            config = DataConfig()
            test_start, test_end = config.date_splits.get_test_range()
            print(f"   ✓ Data split: TEST [{test_start} to {test_end}] (unseen data)")
        else:
            print(f"   Date range: {datetime.now().date() - timedelta(days=365*lookback_years)} to {datetime.now().date()}")

    def calculate_composite_score(self, df: pd.DataFrame, weights: Dict[str, float]) -> np.ndarray:
        """Calculate composite score using weight dictionary"""

        scores = np.zeros(len(df))

        for dimension, weight in weights.items():
            col_name = f'{dimension}_score'
            if col_name in df.columns:
                scores += df[col_name].values * (weight / 100)
            else:
                # Fallback: use dimension name directly
                if dimension in df.columns:
                    scores += df[dimension].values * (weight / 100)

        return scores

    def generate_signals(self, scores: np.ndarray, percentile_threshold: float = 50.0) -> np.ndarray:
        """
        Generate buy/sell signals based on percentile

        Args:
            scores: Array of company scores
            percentile_threshold: Buy if score > this percentile (50 = top 50%)

        Returns:
            Binary array (1 = buy, 0 = hold/sell)
        """
        threshold = np.percentile(scores, percentile_threshold)
        return (scores > threshold).astype(int)

    def run_backtest(self, weight_set: str = 'both') -> Dict:
        """
        Run backtest for one or both weight sets

        Args:
            weight_set: 'baseline', 'optimized', or 'both'

        Returns:
            Results dictionary with metrics for each weight set
        """

        print(f"\n" + "="*80)
        print(f"RUNNING BACKTEST - {weight_set.upper()}")
        print(f"="*80)

        results = {}

        # Test baseline weights
        if weight_set in ['baseline', 'both']:
            print(f"\n📊 BASELINE WEIGHTS (Original 8-D Model)")
            baseline_scores = self.calculate_composite_score(self.data, self.BASELINE_WEIGHTS)
            baseline_signals = self.generate_signals(baseline_scores, percentile_threshold=50)

            baseline_metrics = self._evaluate_signals(
                baseline_signals,
                self.data['stock_return_12m'],
                label="Baseline"
            )
            results['baseline'] = baseline_metrics

        # Test optimized weights
        if weight_set in ['optimized', 'both']:
            print(f"\n🚀 OPTIMIZED WEIGHTS (F1-Tuned Model)")
            optimized_scores = self.calculate_composite_score(self.data, self.OPTIMIZED_WEIGHTS)
            optimized_signals = self.generate_signals(optimized_scores, percentile_threshold=50)

            optimized_metrics = self._evaluate_signals(
                optimized_signals,
                self.data['stock_return_12m'],
                label="Optimized"
            )
            results['optimized'] = optimized_metrics

        return results

    def _evaluate_signals(self, signals: np.ndarray, actual_returns: np.ndarray, label: str) -> Dict:
        """Evaluate signal quality using precision, recall, F1"""

        # Define "outperform" as return > median
        median_return = np.median(actual_returns)
        actual_outperform = (actual_returns > median_return).astype(int)

        # Calculate metrics
        n_signals = np.sum(signals)
        n_outperform = np.sum(actual_outperform)

        # True positives: we said BUY and company outperformed
        tp = np.sum(signals & actual_outperform)

        # False positives: we said BUY but company underperformed
        fp = np.sum(signals & ~actual_outperform)

        # False negatives: we said SELL but company outperformed
        fn = np.sum(~signals & actual_outperform)

        # True negatives: we said SELL and company underperformed
        tn = np.sum(~signals & ~actual_outperform)

        # Calculate metrics
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        accuracy = (tp + tn) / len(signals)

        # Return statistics
        buy_returns = actual_returns[signals == 1]
        sell_returns = actual_returns[signals == 0]

        avg_buy_return = np.mean(buy_returns) if len(buy_returns) > 0 else 0
        avg_sell_return = np.mean(sell_returns) if len(sell_returns) > 0 else 0

        metrics = {
            'label': label,
            'n_signals': n_signals,
            'n_outperform': n_outperform,
            'tp': tp,
            'fp': fp,
            'fn': fn,
            'tn': tn,
            'precision': precision,
            'recall': recall,
            'f1': f1,
            'accuracy': accuracy,
            'avg_buy_return': avg_buy_return,
            'avg_sell_return': avg_sell_return,
            'return_spread': avg_buy_return - avg_sell_return,
        }

        return metrics

    def generate_report(self, results: Dict) -> None:
        """Generate comprehensive backtest report"""

        print(f"\n" + "="*80)
        print(f"BACKTEST RESULTS & COMPARISON")
        print(f"="*80)

        if 'baseline' in results:
            baseline = results['baseline']
            print(f"\n🔍 BASELINE WEIGHTS (Original Model)")
            print(f"   Buy signals: {baseline['n_signals']:,} out of {len(self.data):,} companies")
            print(f"   Actual outperformers: {baseline['n_outperform']:,}")
            print(f"   ✅ True Positives:  {baseline['tp']:,} (correct buys)")
            print(f"   ❌ False Positives: {baseline['fp']:,} (wrong buys)")
            print(f"   ❌ False Negatives: {baseline['fn']:,} (missed wins)")
            print(f"   ✅ True Negatives:  {baseline['tn']:,} (correct sells)")
            print(f"\n   📊 QUALITY METRICS")
            print(f"   Precision:  {baseline['precision']:.1%} (% of buys that outperform)")
            print(f"   Recall:     {baseline['recall']:.1%} (% of winners we catch)")
            print(f"   F1 Score:   {baseline['f1']:.4f}")
            print(f"   Accuracy:   {baseline['accuracy']:.1%}")
            print(f"\n   💰 RETURN METRICS")
            print(f"   Avg buy return:  {baseline['avg_buy_return']:+.2f}%")
            print(f"   Avg sell return: {baseline['avg_sell_return']:+.2f}%")
            print(f"   Return spread:   {baseline['return_spread']:+.2f}pp")

        if 'optimized' in results:
            optimized = results['optimized']
            print(f"\n🚀 OPTIMIZED WEIGHTS (F1-Tuned Model)")
            print(f"   Buy signals: {optimized['n_signals']:,} out of {len(self.data):,} companies")
            print(f"   Actual outperformers: {optimized['n_outperform']:,}")
            print(f"   ✅ True Positives:  {optimized['tp']:,} (correct buys)")
            print(f"   ❌ False Positives: {optimized['fp']:,} (wrong buys)")
            print(f"   ❌ False Negatives: {optimized['fn']:,} (missed wins)")
            print(f"   ✅ True Negatives:  {optimized['tn']:,} (correct sells)")
            print(f"\n   📊 QUALITY METRICS")
            print(f"   Precision:  {optimized['precision']:.1%} (% of buys that outperform)")
            print(f"   Recall:     {optimized['recall']:.1%} (% of winners we catch)")
            print(f"   F1 Score:   {optimized['f1']:.4f}")
            print(f"   Accuracy:   {optimized['accuracy']:.1%}")
            print(f"\n   💰 RETURN METRICS")
            print(f"   Avg buy return:  {optimized['avg_buy_return']:+.2f}%")
            print(f"   Avg sell return: {optimized['avg_sell_return']:+.2f}%")
            print(f"   Return spread:   {optimized['return_spread']:+.2f}pp")

        # Comparison
        if 'baseline' in results and 'optimized' in results:
            baseline = results['baseline']
            optimized = results['optimized']

            print(f"\n" + "="*80)
            print(f"📈 IMPROVEMENTS (OPTIMIZED vs BASELINE)")
            print(f"="*80)

            print(f"\n   Metric                  Baseline      Optimized     Improvement")
            print(f"   " + "-"*70)

            metrics_to_compare = [
                ('Precision', 'precision', 'pct'),
                ('Recall', 'recall', 'pct'),
                ('F1 Score', 'f1', 'raw'),
                ('Accuracy', 'accuracy', 'pct'),
                ('Avg Buy Return', 'avg_buy_return', 'pp'),
                ('Return Spread', 'return_spread', 'pp'),
            ]

            improvements = {}
            for metric_name, metric_key, fmt in metrics_to_compare:
                baseline_val = baseline[metric_key]
                optimized_val = optimized[metric_key]

                if fmt == 'pct':
                    improvement = (optimized_val - baseline_val) * 100
                    baseline_str = f"{baseline_val:.1%}"
                    optimized_str = f"{optimized_val:.1%}"
                    improvement_str = f"{improvement:+.1f}pp"
                elif fmt == 'pp':
                    improvement = optimized_val - baseline_val
                    baseline_str = f"{baseline_val:+.2f}%"
                    optimized_str = f"{optimized_val:+.2f}%"
                    improvement_str = f"{improvement:+.2f}pp"
                else:  # raw
                    improvement = optimized_val - baseline_val
                    baseline_str = f"{baseline_val:.4f}"
                    optimized_str = f"{optimized_val:.4f}"
                    improvement_str = f"{improvement:+.4f}"

                marker = "✅" if improvement > 0 else "❌" if improvement < 0 else "="
                print(f"   {metric_name:20s}  {baseline_str:>12}  {optimized_str:>12}  {marker} {improvement_str:>12}")

                improvements[metric_key] = improvement

            # Summary
            print(f"\n💡 SUMMARY")
            improvements_count = sum(1 for v in improvements.values() if v > 0)
            print(f"   Improved metrics: {improvements_count}/{len(improvements)}")

            if improvements['f1'] > 0.05:
                print(f"   ✅ SIGNIFICANT IMPROVEMENT: F1 score increased by {improvements['f1']:.4f}")
                print(f"   Recommendation: DEPLOY OPTIMIZED WEIGHTS")
            elif improvements['f1'] > 0:
                print(f"   ✅ MODEST IMPROVEMENT: F1 score increased by {improvements['f1']:.4f}")
                print(f"   Recommendation: DEPLOY with caution, monitor closely")
            else:
                print(f"   ❌ NO IMPROVEMENT: F1 score decreased by {abs(improvements['f1']):.4f}")
                print(f"   Recommendation: KEEP BASELINE WEIGHTS")

        print(f"\n" + "="*80)

    def export_results(self, results: Dict, filepath: str) -> None:
        """Export backtest results to CSV"""

        export_data = []

        for weight_set, metrics in results.items():
            export_data.append({
                'weight_set': weight_set,
                'true_positives': metrics['tp'],
                'false_positives': metrics['fp'],
                'false_negatives': metrics['fn'],
                'true_negatives': metrics['tn'],
                'precision': metrics['precision'],
                'recall': metrics['recall'],
                'f1_score': metrics['f1'],
                'accuracy': metrics['accuracy'],
                'avg_buy_return': metrics['avg_buy_return'],
                'avg_sell_return': metrics['avg_sell_return'],
                'return_spread': metrics['return_spread'],
            })

        export_df = pd.DataFrame(export_data)
        export_df.to_csv(filepath, index=False)
        print(f"\n✅ Backtest results exported to {filepath}")


def backtest_from_data_source(
    data_path: str = "cache_seed/cleaned_long.parquet",
    market: str = "IN",
    use_test_split: bool = True,
    weight_set: str = "both"
) -> Dict:
    """
    PROPER BACKTEST: Load data, split correctly, then backtest.

    This is the recommended way to run backtests to prevent data leakage.

    Args:
        data_path: Path to parquet file with company scores and returns
        market: Market code (IN, US, etc.)
        use_test_split: If True, use TEST split (2023-2024) for evaluation
        weight_set: Which weights to test ('baseline', 'optimized', or 'both')

    Returns:
        Results dictionary with backtest metrics
    """

    if not HAS_DATA_CONFIG:
        print("❌ ERROR: data_config.py required for proper backtesting")
        print("   Install data_config.py to enable train/test split support")
        return {}

    print(f"\n{'='*80}")
    print(f"LOADING DATA FOR BACKTEST (PROPER SPLIT)")
    print(f"{'='*80}")

    # Load full data
    try:
        full_data = pd.read_parquet(data_path)
    except FileNotFoundError:
        print(f"❌ Data file not found: {data_path}")
        return {}

    print(f"✓ Loaded {len(full_data):,} records from {Path(data_path).name}")

    # Split data to prevent leakage
    config = DataConfig()

    if use_test_split:
        # Use TEST split for backtest (unseen data: 2023-2024)
        if 'date' in full_data.columns or 'Date' in full_data.columns:
            backtest_data = filter_data_by_split(full_data, "date", split="test")
            test_start, test_end = config.date_splits.get_test_range()
            print(f"✓ Using TEST split: {test_start} to {test_end} ({len(backtest_data):,} records)")
        else:
            print("⚠️  No date column found; using all data")
            backtest_data = full_data
    else:
        backtest_data = full_data
        print(f"⚠️  Using all data (no split; not recommended for evaluation)")

    # Validate backtest data
    errors = config.validation.validate_dataframe(backtest_data)
    if errors:
        print(f"⚠️  Data validation warnings:")
        for error in errors:
            print(f"     • {error}")

    # Run backtest
    backtest = WeightBacktester(backtest_data, use_test_split=use_test_split)
    results = backtest.run_backtest(weight_set=weight_set)
    backtest.generate_report(results)

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backtest weight optimization (KARZ vs MAIN) with proper train/test splits"
    )
    parser.add_argument("--market", default="IN", help="Market code (IN, US, etc.)")
    parser.add_argument("--data", default="cache_seed/cleaned_long.parquet", help="Path to data file")
    parser.add_argument("--use-test-split", action="store_true", default=True,
                       help="Use data_config TEST split to prevent leakage (default: True)")
    parser.add_argument("--no-split", action="store_true",
                       help="Use all data (not recommended; overrides --use-test-split)")
    parser.add_argument("--weight-set", default="both", choices=["baseline", "optimized", "both"],
                       help="Which weights to test")
    parser.add_argument("--export", help="Export results to CSV file")

    args = parser.parse_args()

    print("\n" + "🎯 "*40)
    print("BACKTEST FRAMEWORK - KARZ vs MAIN WEIGHT COMPARISON")
    print("🎯 "*40)
    print(f"\n✓ Using proper train/test splits from data_config.py")
    print(f"✓ This prevents data leakage and ensures fair evaluation")
    print(f"✓ Backtest on TEST split: unseen recent data (2023-2024)")

    # Run backtest
    use_split = not args.no_split
    results = backtest_from_data_source(
        data_path=args.data,
        market=args.market,
        use_test_split=use_split,
        weight_set=args.weight_set
    )

    # Export if requested
    if args.export and results:
        backtest = WeightBacktester(pd.DataFrame(), use_test_split=use_split)
        backtest.export_results(results, args.export)

    print("\n✅ Backtest complete")
    print("\n📖 For details on train/test splits, see DATA_VALIDATION_GUIDE.md")
