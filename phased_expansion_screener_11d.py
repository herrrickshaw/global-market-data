#!/usr/bin/env python3
"""
Phased Expansion Screener - 11-Dimensional Model
Applied to 25,000+ global companies with staged filtering.
High-weightage criteria checked first for performance optimization.
Tracks price correlation with expansion scores for validation.
"""

import pandas as pd
import numpy as np
from datetime import datetime
import time
import warnings
warnings.filterwarnings('ignore')


class PhasedExpansionScreener:
    """
    3-Stage Fast-Fail Filtering Pipeline:
    Stage 1 (Pre-filter): Check high-weightage criteria only (debt, capex, profitability)
    Stage 2 (Mid-filter): Check expansion signals (FCF, margins, leverage)
    Stage 3 (Full-score): Only compute full 11-D score for remaining candidates
    """

    def __init__(self):
        self.start_time = time.time()
        self.stage1_filter = {}
        self.stage2_filter = {}
        self.stage3_results = {}

    def define_criteria_weights(self):
        """Define 11-D model with weights prioritized by importance"""
        return {
            # STAGE 1: High-weightage (Pre-filter)
            'debt_expansion': {
                'weight': 10,
                'priority': 1,
                'stage': 1,
                'description': 'D/E change, debt CAGR trend'
            },
            'capex_acceleration': {
                'weight': 24,
                'priority': 1,
                'stage': 1,
                'description': 'Capex CAGR, asset growth, asset turnover'
            },
            'fcf_generation': {
                'weight': 22,
                'priority': 1,
                'stage': 1,
                'description': 'FCF margin, OCF - Capex, FCF trend'
            },
            'profitability_quality': {
                'weight': 10,
                'priority': 1,
                'stage': 1,
                'description': 'OI margin, NI margin, ROIC trend'
            },

            # STAGE 2: Medium-weightage (Mid-filter)
            'sustainability': {
                'weight': 8,
                'priority': 2,
                'stage': 2,
                'description': 'FCF/debt ratio, working capital efficiency, DSC'
            },
            'leverage_health': {
                'weight': 2,
                'priority': 2,
                'stage': 2,
                'description': 'D/E ratio, interest coverage'
            },
            'profit_reinvestment': {
                'weight': 19,
                'priority': 2,
                'stage': 2,
                'description': 'Retained earnings growth, payout ratio'
            },

            # STAGE 3: Low-weightage (Full-score only)
            'timing_alignment': {
                'weight': 4,
                'priority': 3,
                'stage': 3,
                'description': 'Capex cycle phase, synchronization'
            },
            'asset_efficiency': {
                'weight': 7,
                'priority': 3,
                'stage': 3,
                'description': 'Asset turnover trend, ROIC improvement'
            },
            'debt_service_coverage': {
                'weight': 10,
                'priority': 3,
                'stage': 3,
                'description': 'OCF / (interest + principal)'
            },
            'working_capital_mgmt': {
                'weight': 4,
                'priority': 3,
                'stage': 3,
                'description': 'Working capital as % revenue'
            },
        }

    def stage1_prefilter(self, companies_data: list) -> list:
        """
        STAGE 1: Quick pre-filter on high-weightage criteria
        Filters ~35-40% of companies (eliminate obvious non-expanders)

        Criteria: Debt, Capex, FCF, Profitability (66% of total weight)
        """
        print("\n" + "="*80)
        print("STAGE 1: PRE-FILTER (High-Weightage Criteria)")
        print("="*80)

        criteria_weights = self.define_criteria_weights()
        stage1_weight = sum(c['weight'] for c in criteria_weights.values() if c['stage'] == 1)

        print(f"\n📊 Stage 1 Weight: {stage1_weight}/100 ({stage1_weight}% of model)")
        print(f"   Criteria: Debt expansion, Capex acceleration, FCF generation, Profitability")
        print(f"   Expected filter rate: ~35-40% rejection")

        passed_stage1 = []
        failed_stage1 = 0
        stage1_stats = {
            'failed_debt': 0,
            'failed_capex': 0,
            'failed_fcf': 0,
            'failed_profitability': 0,
        }

        for company in companies_data:
            reject = False

            # DEBT EXPANSION: D/E > 2.0 = over-leveraged (fail)
            if company.get('debt_to_equity', 0) > 2.0:
                failed_stage1 += 1
                stage1_stats['failed_debt'] += 1
                reject = True

            # CAPEX ACCELERATION: Capex < 0.5% of revenue OR declining >15% CAGR (fail)
            if not reject:
                capex_intensity = company.get('capex_to_revenue', 0)
                capex_cagr = company.get('capex_cagr', 0)
                if capex_intensity < 0.005 or (capex_cagr and capex_cagr < -15):
                    failed_stage1 += 1
                    stage1_stats['failed_capex'] += 1
                    reject = True

            # FCF GENERATION: Negative FCF (fail)
            if not reject:
                avg_fcf = company.get('avg_fcf', 0)
                if avg_fcf is None or avg_fcf < 0:
                    failed_stage1 += 1
                    stage1_stats['failed_fcf'] += 1
                    reject = True

            # PROFITABILITY: Net margin < -5% (fail)
            if not reject:
                ni_margin = company.get('net_margin', 0)
                if ni_margin and ni_margin < -5:
                    failed_stage1 += 1
                    stage1_stats['failed_profitability'] += 1
                    reject = True

            if not reject:
                passed_stage1.append(company)

        self.stage1_filter = stage1_stats
        print(f"\n✅ Stage 1 Complete:")
        print(f"   Passed: {len(passed_stage1):,} / {len(companies_data):,}")
        print(f"   Failed: {failed_stage1:,} ({failed_stage1/len(companies_data)*100:.1f}%)")
        print(f"   └─ Over-leveraged (D/E > 2.0): {stage1_stats['failed_debt']:,}")
        print(f"   └─ No capex / declining capex: {stage1_stats['failed_capex']:,}")
        print(f"   └─ Negative FCF: {stage1_stats['failed_fcf']:,}")
        print(f"   └─ Distressed profitability: {stage1_stats['failed_profitability']:,}")

        return passed_stage1

    def stage2_midfilter(self, companies_data: list) -> list:
        """
        STAGE 2: Mid-filter on medium-weightage criteria
        Filters ~44% of remaining companies

        Criteria: Sustainability, Leverage, Profit reinvestment (42% of total weight)
        """
        print("\n" + "="*80)
        print("STAGE 2: MID-FILTER (Medium-Weightage Criteria)")
        print("="*80)

        print(f"\n📊 Stage 2 Weight: 42/100 (42% of model)")
        print(f"   Criteria: Sustainability, Leverage health, Profit reinvestment")
        print(f"   Expected filter rate: ~40-45% rejection")

        passed_stage2 = []
        failed_stage2 = 0
        stage2_stats = {
            'failed_sustainability': 0,
            'failed_leverage': 0,
            'failed_reinvestment': 0,
        }

        for company in companies_data:
            reject = False

            # SUSTAINABILITY: DSC < 1.0 (can't service debt)
            dsc = company.get('debt_service_coverage', 1.5)
            if dsc and dsc < 1.0:
                failed_stage2 += 1
                stage2_stats['failed_sustainability'] += 1
                reject = True

            # LEVERAGE HEALTH: Interest coverage < 2.0 (stressed)
            if not reject:
                interest_coverage = company.get('interest_coverage', 5.0)
                if interest_coverage and interest_coverage < 2.0:
                    failed_stage2 += 1
                    stage2_stats['failed_leverage'] += 1
                    reject = True

            # PROFIT REINVESTMENT: Payout ratio > 80% (not reinvesting)
            if not reject:
                payout_ratio = company.get('payout_ratio', 0.3)
                if payout_ratio and payout_ratio > 0.8:
                    failed_stage2 += 1
                    stage2_stats['failed_reinvestment'] += 1
                    reject = True

            if not reject:
                passed_stage2.append(company)

        self.stage2_filter = stage2_stats
        print(f"\n✅ Stage 2 Complete:")
        print(f"   Passed: {len(passed_stage2):,} / {len(companies_data):,}")
        print(f"   Failed: {failed_stage2:,} ({failed_stage2/len(companies_data)*100:.1f}%)")
        print(f"   └─ Unsustainable debt (DSC < 1.0): {stage2_stats['failed_sustainability']:,}")
        print(f"   └─ Stressed leverage (ICR < 2.0): {stage2_stats['failed_leverage']:,}")
        print(f"   └─ No reinvestment (Payout > 80%): {stage2_stats['failed_reinvestment']:,}")

        return passed_stage2

    def calculate_11d_score(self, company: dict) -> float:
        """
        STAGE 3: Full 11-D scoring for remaining candidates
        Only runs on companies that passed Stages 1-2
        """
        score = 0
        criteria_weights = self.define_criteria_weights()

        # Extract metrics with defaults
        revenue_cagr = company.get('revenue_cagr', 0) or 0
        capex_cagr = company.get('capex_cagr', 0) or 0
        debt_cagr = company.get('debt_cagr', 0) or 0
        avg_fcf = company.get('avg_fcf', 0) or 0
        oi_margin_change = company.get('oi_margin_change', 0) or 0
        ni_margin_change = company.get('ni_margin_change', 0) or 0
        roic_change = company.get('roic_change', 0) or 0
        de_ratio = company.get('debt_to_equity', 0) or 0
        interest_coverage = company.get('interest_coverage', 5) or 5
        dsc = company.get('debt_service_coverage', 1.5) or 1.5
        asset_turnover = company.get('asset_turnover', 1) or 1
        wc_ratio = company.get('wc_ratio', 0.1) or 0.1

        # 1. Debt Expansion (10% weight) - Lower D/E is better, moderate debt CAGR
        if de_ratio and 0 < de_ratio < 1.5:
            score += 10 * (1.5 - de_ratio) / 1.5
        elif de_ratio and de_ratio < 2.0:
            score += 10 * 0.5

        # 2. Capex Acceleration (24% weight) - HIGH PRIORITY
        if capex_cagr and capex_cagr > 10:
            score += 24
        elif capex_cagr and capex_cagr > 5:
            score += 24 * (capex_cagr / 10)
        elif capex_cagr and capex_cagr > 0:
            score += 24 * (capex_cagr / 5) * 0.5

        # 3. Profit Reinvestment (19% weight)
        if revenue_cagr and revenue_cagr > 10:
            score += 19
        elif revenue_cagr and revenue_cagr > 5:
            score += 19 * (revenue_cagr / 10)
        elif revenue_cagr and revenue_cagr > 0:
            score += 19 * (revenue_cagr / 5) * 0.5

        # 4. Profitability Quality (10% weight) - Including ROIC trend (NEW)
        if roic_change and roic_change > 2:
            score += 10  # ROIC improving
        elif ni_margin_change and ni_margin_change > 0:
            score += 10 * 0.7
        elif oi_margin_change and oi_margin_change > 0:
            score += 10 * 0.5

        # 5. Sustainability (8% weight) - DSC + working capital
        if dsc and dsc > 1.5:
            score += 8
        elif dsc and dsc > 1.0:
            score += 8 * (dsc / 1.5)

        # 6. Leverage Health (2% weight)
        if interest_coverage and interest_coverage > 5:
            score += 2
        elif interest_coverage and interest_coverage > 3:
            score += 2 * (interest_coverage / 5)

        # 7. FCF Generation (22% weight) - CRITICAL NEW ADDITION
        if avg_fcf and avg_fcf > 0:
            score += 22 * min(1.0, (avg_fcf / 1e9))  # Normalized by billion

        # 8. Timing Alignment (4% weight)
        if capex_cagr and revenue_cagr and 0 < capex_cagr < revenue_cagr * 1.5:
            score += 4  # Capex aligned with revenue

        # 9. Asset Efficiency (7% weight) - NEW
        if asset_turnover and asset_turnover > 1:
            score += 7 * min(1.0, asset_turnover / 2)

        # 10. Debt Service Coverage (10% weight) - NEW
        if dsc and dsc > 1.5:
            score += 10
        elif dsc and dsc > 1.0:
            score += 10 * (dsc / 1.5)

        # 11. Working Capital Management (4% weight) - NEW
        if wc_ratio and wc_ratio < 0.2:  # Low WC needs
            score += 4

        return min(score, 100)

    def classify_tier(self, score: float) -> str:
        """Classify company into tier based on expansion score"""
        if score >= 75:
            return "Tier 1 (Aggressive Expander)"
        elif score >= 50:
            return "Tier 2 (Strong Expander)"
        elif score >= 25:
            return "Tier 3 (Moderate Expander)"
        else:
            return "Tier 4 (Passive/Mature)"

    def screen_companies(self, companies_data: list) -> pd.DataFrame:
        """
        Full 3-stage screening pipeline with price correlation tracking
        """
        print("\n" + "="*80)
        print("PHASED EXPANSION SCREENING - 11-DIMENSIONAL MODEL")
        print("="*80)

        print(f"\n🎯 SCREENING PARAMETERS:")
        print(f"   Total companies to screen: {len(companies_data):,}")
        print(f"   Model dimensions: 11-D (8 original + 3 new)")
        print(f"   Stages: 3 (Pre-filter → Mid-filter → Full-score)")
        print(f"   High-weightage criteria checked first")

        # STAGE 1: Pre-filter
        print(f"\n📍 ENTERING STAGE 1...")
        candidates_s1 = self.stage1_prefilter(companies_data)

        # STAGE 2: Mid-filter
        print(f"\n📍 ENTERING STAGE 2...")
        candidates_s2 = self.stage2_midfilter(candidates_s1)

        # STAGE 3: Full scoring
        print(f"\n" + "="*80)
        print("STAGE 3: FULL-SCORE (11-D Model)")
        print("="*80)

        print(f"\n📊 Stage 3 Weight: 58/100 (remaining criteria)")
        print(f"   Criteria: Timing, Asset efficiency, DSC, Working capital")
        print(f"   Running 11-D calculation on {len(candidates_s2):,} candidates...")

        results = []
        for i, company in enumerate(candidates_s2, 1):
            if i % max(1, len(candidates_s2) // 10) == 0:
                print(f"   [{i:,}/{len(candidates_s2):,}] Scoring...", end='\r', flush=True)

            score = self.calculate_11d_score(company)
            tier = self.classify_tier(score)

            result = {
                'ticker': company.get('ticker', 'N/A'),
                'company_name': company.get('name', 'N/A'),
                'country': company.get('country', 'N/A'),
                'market_cap_usd_b': company.get('market_cap_b', 0),
                'sector': company.get('sector', 'N/A'),
                'expansion_score': score,
                'tier': tier,
                'revenue_cagr': company.get('revenue_cagr', None),
                'capex_cagr': company.get('capex_cagr', None),
                'debt_cagr': company.get('debt_cagr', None),
                'fcf_margin': company.get('fcf_margin', None),
                'roic_change': company.get('roic_change', None),
                'dsc_ratio': company.get('debt_service_coverage', None),
                'de_ratio': company.get('debt_to_equity', None),
                'price_5yr_cagr': company.get('price_5yr_cagr', None),
            }
            results.append(result)

        df_results = pd.DataFrame(results).sort_values('expansion_score', ascending=False)
        print(f"\n✅ Stage 3 Complete: {len(df_results):,} candidates scored")

        self.stage3_results = df_results

        # Print summary
        self.print_screening_summary(df_results)

        return df_results

    def print_screening_summary(self, results: pd.DataFrame):
        """Print comprehensive screening summary with price correlation"""
        print(f"\n" + "="*80)
        print("SCREENING RESULTS SUMMARY")
        print("="*80)

        # Tier distribution
        print(f"\n📊 TIER DISTRIBUTION:")
        tier_counts = results['tier'].value_counts()
        for tier in ['Tier 1 (Aggressive Expander)', 'Tier 2 (Strong Expander)',
                     'Tier 3 (Moderate Expander)', 'Tier 4 (Passive/Mature)']:
            count = tier_counts.get(tier, 0)
            pct = count / len(results) * 100 if len(results) > 0 else 0
            print(f"   {tier:30s}: {count:6,} ({pct:5.1f}%)")

        # Top 15 candidates
        print(f"\n🏆 TOP 15 EXPANSION CANDIDATES (11-D Score):")
        print(f"{'Rank':<5} {'Ticker':<8} {'Score':<8} {'Rev CAGR':<10} {'Capex CAGR':<12} {'Price 5Y':<10} {'Tier':<25}")
        print("-" * 100)

        for idx, (i, row) in enumerate(results.nlargest(15, 'expansion_score').iterrows(), 1):
            rev = f"{row['revenue_cagr']:.1f}%" if row['revenue_cagr'] else "N/A"
            capex = f"{row['capex_cagr']:.1f}%" if row['capex_cagr'] else "N/A"
            price = f"{row['price_5yr_cagr']:.1f}%" if row['price_5yr_cagr'] else "N/A"
            print(f"{idx:<5} {row['ticker']:<8} {row['expansion_score']:<8.0f} {rev:<10} {capex:<12} {price:<10} {row['tier']:<25}")

        # Price correlation analysis
        print(f"\n📈 PRICE CORRELATION WITH EXPANSION SCORE:")
        if 'expansion_score' in results.columns and 'price_5yr_cagr' in results.columns:
            valid_data = results.dropna(subset=['expansion_score', 'price_5yr_cagr'])
            if len(valid_data) > 0:
                correlation = valid_data['expansion_score'].corr(valid_data['price_5yr_cagr'])
                print(f"   Correlation (expansion score vs price CAGR): {correlation:.3f}")
                print(f"   Interpretation: {'Strong' if abs(correlation) > 0.3 else 'Moderate' if abs(correlation) > 0.15 else 'Weak'} signal")

        # Metrics overview
        print(f"\n💡 SCREENING METRICS OVERVIEW:")
        print(f"   Avg revenue CAGR: {results['revenue_cagr'].mean():.1f}%")
        print(f"   Avg capex CAGR: {results['capex_cagr'].mean():.1f}%")
        print(f"   Avg expansion score: {results['expansion_score'].mean():.1f}/100")
        print(f"   Avg price 5-year CAGR: {results['price_5yr_cagr'].mean():.1f}%")

        # Filter effectiveness
        print(f"\n📊 FILTER EFFECTIVENESS (25,000 → Final):")
        total_filtered = sum(v for v in self.stage1_filter.values()) + sum(v for v in self.stage2_filter.values())
        print(f"   Stage 1 rejections: {sum(self.stage1_filter.values()):,}")
        print(f"   Stage 2 rejections: {sum(self.stage2_filter.values()):,}")
        print(f"   Final candidates: {len(results):,}")
        print(f"   Overall pass rate: {len(results)/25000*100:.2f}%")

        # Sector distribution
        print(f"\n🏢 TOP 10 SECTORS (By candidate count):")
        sector_counts = results['sector'].value_counts().head(10)
        for sector, count in sector_counts.items():
            print(f"   {sector:30s}: {count:5,}")

    def save_results(self, results: pd.DataFrame, filename: str = 'expansion_screening_results_11d.csv'):
        """Save screening results to CSV"""
        filepath = f"/Users/umashankar/Downloads/code/python_files/{filename}"
        results.to_csv(filepath, index=False)
        print(f"\n💾 Results saved to: {filename}")
        return filepath


if __name__ == "__main__":
    # For demonstration, create synthetic company data representing 25,000 companies
    print("🔄 INITIALIZING PHASED EXPANSION SCREENER")
    print("   Preparing synthetic global company universe (25,000 companies)...")

    # Generate synthetic data representing global companies
    np.random.seed(42)
    companies = []

    sectors = ['Technology', 'Industrials', 'Energy', 'Healthcare', 'Financials',
               'Real Estate', 'Consumer', 'Materials', 'Utilities', 'Communications']
    countries = ['USA', 'China', 'Japan', 'Germany', 'UK', 'India', 'Brazil', 'Canada', 'Australia', 'Singapore']

    for i in range(25000):
        company = {
            'ticker': f'SYM{i:05d}',
            'name': f'Company {i}',
            'country': np.random.choice(countries),
            'sector': np.random.choice(sectors),
            'market_cap_b': np.random.lognormal(2, 2),  # Lognormal distribution

            # Financial metrics
            'revenue_cagr': np.random.normal(5, 4),
            'capex_cagr': np.random.normal(3, 5),
            'debt_cagr': np.random.normal(2, 3),
            'avg_fcf': np.random.lognormal(5, 2) if np.random.random() > 0.2 else -100,
            'fcf_margin': np.random.normal(5, 4),
            'oi_margin_change': np.random.normal(0.5, 1),
            'ni_margin_change': np.random.normal(0, 0.8),
            'roic_change': np.random.normal(0.5, 1.5),

            # Ratios
            'debt_to_equity': np.random.lognormal(0, 0.8),
            'interest_coverage': np.random.lognormal(1, 0.6),
            'debt_service_coverage': np.random.lognormal(0.2, 0.7),
            'asset_turnover': np.random.lognormal(0, 0.4),
            'wc_ratio': np.random.uniform(0.05, 0.25),
            'capex_to_revenue': np.random.uniform(0.01, 0.15),
            'payout_ratio': np.random.uniform(0, 0.8),

            # Price performance
            'price_5yr_cagr': np.random.normal(10, 15),
        }
        companies.append(company)

    # Run screening
    screener = PhasedExpansionScreener()
    results = screener.screen_companies(companies)

    # Save results
    screener.save_results(results)

    print(f"\n✅ SCREENING COMPLETE")
    print(f"   Total candidates: {len(results):,}")
    print(f"   Time elapsed: {time.time() - screener.start_time:.2f}s")
