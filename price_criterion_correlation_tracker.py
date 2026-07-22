#!/usr/bin/env python3
"""
Price Criterion Correlation Tracker
Measures how effectively each 11-D criterion predicts stock outperformance.
Shows which criteria have predictive power vs which are noise.
Validates weightings in the 11-D model.
"""

import pandas as pd
import numpy as np
from scipy.stats import spearmanr, pearsonr
import warnings
warnings.filterwarnings('ignore')


class PriceCriterionCorrelationTracker:
    """
    Measure correlation between each expansion criterion and actual price performance.
    Identifies which criteria drive outperformance (signal) vs which add noise.
    """

    def __init__(self, screening_results: pd.DataFrame):
        """
        Initialize with screening results (expansion scores + price data)
        """
        self.results = screening_results.copy()
        self.correlations = {}

    def calculate_correlations(self):
        """
        Calculate Spearman rank correlation for each criterion with price performance.
        Spearman is more robust to outliers than Pearson.
        """
        print("\n" + "="*80)
        print("PRICE CRITERION CORRELATION ANALYSIS")
        print("="*80)

        print(f"\n📊 Analyzing {len(self.results):,} companies")
        print(f"   Objective: Measure predictive power of each 11-D criterion")
        print(f"   Method: Spearman rank correlation (robust to outliers)")
        print(f"   Threshold: p-value < 0.05 = statistically significant")

        criteria_map = {
            'revenue_cagr': ('Revenue Growth CAGR', 'Profit Reinvestment'),
            'capex_cagr': ('Capex Acceleration CAGR', 'Capex Acceleration'),
            'debt_cagr': ('Debt Growth CAGR', 'Debt Expansion'),
            'fcf_margin': ('FCF Margin', 'FCF Generation'),
            'roic_change': ('ROIC Improvement', 'Profitability Quality (NEW)'),
            'de_ratio': ('Debt-to-Equity Ratio', 'Leverage Health'),
            'dsc_ratio': ('Debt Service Coverage', 'Debt Service Coverage (NEW)'),
            'asset_turnover': ('Asset Turnover', 'Asset Efficiency (NEW)'),
        }

        target = 'price_5yr_cagr'
        results_data = []

        print(f"\n🔍 CALCULATING CORRELATIONS WITH 5-YEAR PRICE CAGR:")
        print(f"{'Criterion':<35} {'Correlation':<15} {'P-Value':<12} {'Significance':<15} {'Direction':<10}")
        print("-" * 90)

        for col_name, (display_name, category) in criteria_map.items():
            if col_name not in self.results.columns or target not in self.results.columns:
                continue

            # Remove NaN values for correlation calculation
            valid_data = self.results[[col_name, target]].dropna()

            if len(valid_data) < 10:
                continue

            # Spearman correlation (non-parametric)
            spearman_corr, spearman_p = spearmanr(valid_data[col_name], valid_data[target])

            # Pearson correlation (parametric)
            pearson_corr, pearson_p = pearsonr(valid_data[col_name], valid_data[target])

            # Determine significance
            is_significant = spearman_p < 0.05
            significance = "✓ SIGNIFICANT" if is_significant else "✗ Not significant"
            direction = "Positive" if spearman_corr > 0 else "Negative"

            print(f"{display_name:<35} {spearman_corr:>7.4f}  {spearman_p:>12.6f} {significance:<15} {direction:<10}")

            results_data.append({
                'criterion': display_name,
                'category': category,
                'spearman_r': spearman_corr,
                'spearman_p': spearman_p,
                'pearson_r': pearson_corr,
                'pearson_p': pearson_p,
                'is_significant': is_significant,
                'n_samples': len(valid_data),
            })

        self.correlations = pd.DataFrame(results_data).sort_values('spearman_r', key=abs, ascending=False)
        return self.correlations

    def validate_model_weights(self, current_weights: dict):
        """
        Compare current model weights with correlation-based effectiveness.
        Identify over-weighted and under-weighted criteria.
        """
        print("\n" + "="*80)
        print("MODEL WEIGHT VALIDATION")
        print("="*80)

        print(f"\n🎯 CURRENT 11-D WEIGHTS vs CORRELATION EFFECTIVENESS:")
        print(f"{'Criterion':<35} {'Current Weight':<15} {'Correlation':<15} {'Match?':<10}")
        print("-" * 75)

        validation_results = []

        # Map criteria to weights
        weight_map = {
            'Capex Acceleration CAGR': current_weights.get('capex_acceleration', 24),
            'FCF Margin': current_weights.get('fcf_generation', 22),
            'Revenue Growth CAGR': current_weights.get('profit_reinvestment', 19),
            'Profitability Quality (NEW)': current_weights.get('profitability_quality', 10),
            'Debt Service Coverage (NEW)': current_weights.get('debt_service_coverage', 10),
            'Debt-to-Equity Ratio': current_weights.get('leverage_health', 2),
            'Asset Turnover (NEW)': current_weights.get('asset_efficiency', 7),
            'Debt Growth CAGR': current_weights.get('debt_expansion', 10),
        }

        for idx, row in self.correlations.iterrows():
            criterion = row['criterion']
            weight = weight_map.get(criterion, None)

            if weight is not None:
                correlation = abs(row['spearman_r'])

                # Determine match quality
                if weight > 20 and correlation > 0.04:
                    match = "✓ Good"
                elif weight > 10 and correlation > 0.02:
                    match = "✓ Good"
                elif weight > 0 and correlation > 0:
                    match = "⚠ Question"
                else:
                    match = "✗ Mismatch"

                print(f"{criterion:<35} {weight:>6}% {correlation:>12.4f}  {match:<10}")

                validation_results.append({
                    'criterion': criterion,
                    'weight': weight,
                    'effectiveness': correlation,
                    'match': match,
                })

        return pd.DataFrame(validation_results)

    def identify_signal_vs_noise(self):
        """
        Categorize criteria into:
        1. Strong Signal (correlation > 0.04, significant)
        2. Weak Signal (correlation 0.02-0.04, may need investigation)
        3. Noise (correlation < 0.02, not predictive)
        """
        print("\n" + "="*80)
        print("SIGNAL vs NOISE ANALYSIS")
        print("="*80)

        strong_signal = self.correlations[
            (abs(self.correlations['spearman_r']) > 0.04) &
            (self.correlations['is_significant'])
        ]

        weak_signal = self.correlations[
            (abs(self.correlations['spearman_r']) > 0.02) &
            (abs(self.correlations['spearman_r']) <= 0.04)
        ]

        noise = self.correlations[
            abs(self.correlations['spearman_r']) <= 0.02
        ]

        print(f"\n🟢 STRONG SIGNAL CRITERIA (correlation > 0.04, p < 0.05):")
        print(f"   Count: {len(strong_signal)}")
        for idx, row in strong_signal.iterrows():
            print(f"   • {row['criterion']:35s} | r={row['spearman_r']:>7.4f} | p={row['spearman_p']:.4f}")

        print(f"\n🟡 WEAK SIGNAL CRITERIA (correlation 0.02-0.04, needs investigation):")
        print(f"   Count: {len(weak_signal)}")
        for idx, row in weak_signal.iterrows():
            print(f"   • {row['criterion']:35s} | r={row['spearman_r']:>7.4f} | p={row['spearman_p']:.4f}")

        print(f"\n🔴 NOISE CRITERIA (correlation < 0.02, not predictive):")
        print(f"   Count: {len(noise)}")
        for idx, row in noise.iterrows():
            print(f"   • {row['criterion']:35s} | r={row['spearman_r']:>7.4f} | p={row['spearman_p']:.4f}")

        print(f"\n💡 RECOMMENDATIONS:")
        print(f"   ✓ Keep & increase weight: Strong signal criteria")
        print(f"   ⚠ Investigate: Weak signal criteria (may interact with others)")
        print(f"   ✗ Reduce/remove: Noise criteria (consider dropping)")

        return {
            'strong_signal': strong_signal,
            'weak_signal': weak_signal,
            'noise': noise,
        }

    def tier_price_performance(self):
        """
        Analyze whether tier classifications predict price outperformance.
        High-tier companies should outperform low-tier on average.
        """
        print("\n" + "="*80)
        print("TIER PRICE PERFORMANCE VALIDATION")
        print("="*80)

        tier_order = {
            'Tier 1 (Aggressive Expander)': 1,
            'Tier 2 (Strong Expander)': 2,
            'Tier 3 (Moderate Expander)': 3,
            'Tier 4 (Passive/Mature)': 4,
        }

        tier_performance = []

        print(f"\n📊 Average Price Performance by Tier:")
        print(f"{'Tier':<30} {'Count':<10} {'Avg Price CAGR':<15} {'Median Price':<15}")
        print("-" * 70)

        for tier, order in sorted(tier_order.items(), key=lambda x: x[1]):
            tier_data = self.results[self.results['tier'] == tier]

            if len(tier_data) > 0:
                avg_price = tier_data['price_5yr_cagr'].mean()
                median_price = tier_data['price_5yr_cagr'].median()
                count = len(tier_data)

                print(f"{tier:<30} {count:>8,} {avg_price:>12.1f}% {median_price:>12.1f}%")

                tier_performance.append({
                    'tier': tier,
                    'count': count,
                    'avg_price_cagr': avg_price,
                    'median_price_cagr': median_price,
                })

        df_tier_perf = pd.DataFrame(tier_performance)

        # Check if higher tiers outperform
        if len(df_tier_perf) > 1:
            tier1_perf = df_tier_perf.iloc[0]['avg_price_cagr'] if len(df_tier_perf) > 0 else 0
            tier4_perf = df_tier_perf.iloc[-1]['avg_price_cagr'] if len(df_tier_perf) > 0 else 0
            outperformance = tier1_perf - tier4_perf

            print(f"\n   Tier 1 vs Tier 4 outperformance: {outperformance:+.1f}%")
            if outperformance > 5:
                print(f"   ✓ Model VALIDATES: Higher tiers outperform by >5%")
            elif outperformance > 0:
                print(f"   ⚠ Weak validation: Outperformance <5%")
            else:
                print(f"   ✗ Model FAILS: Higher tiers underperform")

        return df_tier_perf

    def coefficient_of_determination(self):
        """
        Calculate R² (coefficient of determination) for expansion score vs price.
        Shows how much price variance is explained by expansion score.
        """
        print("\n" + "="*80)
        print("MODEL EXPLANATORY POWER (R²)")
        print("="*80)

        valid_data = self.results[['expansion_score', 'price_5yr_cagr']].dropna()

        if len(valid_data) > 2:
            # Calculate correlation
            correlation = valid_data['expansion_score'].corr(valid_data['price_5yr_cagr'])
            r_squared = correlation ** 2

            print(f"\n📊 MODEL STATISTICS:")
            print(f"   Correlation coefficient: {correlation:>7.4f}")
            print(f"   R² (explanatory power): {r_squared*100:>6.2f}%")
            print(f"   Interpretation:")

            if r_squared > 0.20:
                print(f"   ✓ STRONG: Model explains >{r_squared*100:.0f}% of price variance")
            elif r_squared > 0.10:
                print(f"   ✓ MODERATE: Model explains {r_squared*100:.1f}% of price variance")
            elif r_squared > 0.05:
                print(f"   ⚠ WEAK: Model explains only {r_squared*100:.1f}% of price variance")
            else:
                print(f"   ✗ VERY WEAK: Model explains <{r_squared*100:.1f}% of price variance")

            return {
                'correlation': correlation,
                'r_squared': r_squared,
            }

    def generate_recommendations(self):
        """
        Generate actionable recommendations based on correlation analysis.
        """
        print("\n" + "="*80)
        print("ACTIONABLE RECOMMENDATIONS")
        print("="*80)

        signal_noise = self.identify_signal_vs_noise()

        print(f"\n🎯 WEIGHT OPTIMIZATION RECOMMENDATIONS:")
        print(f"\n1. INCREASE WEIGHT (Strong signal, currently under-weighted):")

        strong = signal_noise['strong_signal']
        if len(strong) > 0:
            for idx, row in strong.iterrows():
                if row['criterion'] in ['Capex Acceleration CAGR', 'FCF Margin']:
                    print(f"   ✓ {row['criterion']:35s} (r={row['spearman_r']:.4f}) - Already well-weighted")
                else:
                    print(f"   ⚡ {row['criterion']:35s} (r={row['spearman_r']:.4f}) - Consider +5pp")

        print(f"\n2. KEEP (Weak but significant signal):")
        weak = signal_noise['weak_signal']
        if len(weak) > 0:
            for idx, row in weak.iterrows():
                print(f"   ⚠ {row['criterion']:35s} (r={row['spearman_r']:.4f})")
        else:
            print(f"   All criteria are either strong or noise")

        print(f"\n3. REDUCE/REMOVE (Noise, not predictive):")
        noise = signal_noise['noise']
        if len(noise) > 0:
            for idx, row in noise.iterrows():
                print(f"   ✗ {row['criterion']:35s} (r={row['spearman_r']:.4f}) - Consider removing")
        else:
            print(f"   No clear noise criteria identified")

        print(f"\n4. FURTHER INVESTIGATION NEEDED:")
        print(f"   • Test interaction effects (e.g., capex × ROIC)")
        print(f"   • Analyze by sector (criteria effectiveness varies by industry)")
        print(f"   • Test on global universe vs US-only")
        print(f"   • Backtest on historical periods (2015-2020 vs 2021-2026)")


if __name__ == "__main__":
    # Load screening results
    print("📊 Loading screening results...")
    try:
        results = pd.read_csv('/Users/umashankar/Downloads/code/python_files/expansion_screening_results_11d.csv')
    except:
        print("Results file not found. Using demo data...")
        # Create demo data if file doesn't exist
        np.random.seed(42)
        results = pd.DataFrame({
            'ticker': [f'SYM{i:05d}' for i in range(1000)],
            'expansion_score': np.random.uniform(0, 100, 1000),
            'tier': np.random.choice(['Tier 1', 'Tier 2', 'Tier 3', 'Tier 4'], 1000),
            'revenue_cagr': np.random.normal(5, 4, 1000),
            'capex_cagr': np.random.normal(3, 5, 1000),
            'debt_cagr': np.random.normal(2, 3, 1000),
            'fcf_margin': np.random.normal(5, 4, 1000),
            'roic_change': np.random.normal(0.5, 1.5, 1000),
            'de_ratio': np.random.lognormal(0, 0.8, 1000),
            'dsc_ratio': np.random.lognormal(0.2, 0.7, 1000),
            'asset_turnover': np.random.lognormal(0, 0.4, 1000),
            'price_5yr_cagr': np.random.normal(10, 15, 1000),
        })

    # Run correlation analysis
    tracker = PriceCriterionCorrelationTracker(results)

    # Calculate correlations
    correlations = tracker.calculate_correlations()

    # Validate model weights
    current_weights = {
        'capex_acceleration': 24,
        'fcf_generation': 22,
        'profit_reinvestment': 19,
        'profitability_quality': 10,
        'debt_service_coverage': 10,
        'debt_expansion': 10,
        'asset_efficiency': 7,
        'sustainability': 8,
        'leverage_health': 2,
        'timing_alignment': 4,
        'working_capital_mgmt': 4,
    }
    validation = tracker.validate_model_weights(current_weights)

    # Signal vs noise
    tracker.identify_signal_vs_noise()

    # Tier performance
    tier_perf = tracker.tier_price_performance()

    # R² analysis
    model_stats = tracker.coefficient_of_determination()

    # Recommendations
    tracker.generate_recommendations()

    print(f"\n✅ CORRELATION ANALYSIS COMPLETE")
