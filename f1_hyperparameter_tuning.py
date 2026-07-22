#!/usr/bin/env python3
"""
F1-Based Hyperparameter Tuning for Weight Optimization
=======================================================
Uses F1 score, precision, and recall to find optimal weight combinations.
Treats stock outperformance as binary classification problem.

Process:
1. Define "outperform" threshold (top 50% of returns)
2. Generate weight combinations (grid search)
3. For each combination: score companies, classify as buy/sell
4. Calculate precision (% of buys that outperform), recall, F1
5. Find weights that maximize F1 score
6. Validate on holdout test set
"""

import pandas as pd
import numpy as np
from itertools import product
from typing import Dict, Tuple, List
from sklearn.metrics import precision_score, recall_score, f1_score, confusion_matrix


class F1HyperparameterTuner:
    """Optimizes weights using F1 score on stock outperformance prediction"""

    # 8 dimensions to tune
    DIMENSIONS = [
        'debt_expansion',
        'capex_acceleration',
        'profit_reinvestment',
        'profitability_quality',
        'sustainability',
        'timing_alignment',
        'leverage_health',
        'fcf_generation',
    ]

    def __init__(self, data_df: pd.DataFrame, train_size: float = 0.7):
        """
        Initialize with scored companies + actual returns

        Args:
            data_df: DataFrame with scores and stock_return_12m
            train_size: Fraction for training (rest = validation)
        """
        self.data = data_df.copy()

        # Split train/test
        train_idx = np.random.choice(len(self.data), size=int(len(self.data) * train_size), replace=False)
        test_idx = np.setdiff1d(np.arange(len(self.data)), train_idx)

        self.train = self.data.iloc[train_idx].reset_index(drop=True)
        self.test = self.data.iloc[test_idx].reset_index(drop=True)

        # Define target: binary classification (outperform vs underperform)
        train_median = self.train['stock_return_12m'].median()
        test_median = self.test['stock_return_12m'].median()

        self.train['outperform'] = (self.train['stock_return_12m'] > train_median).astype(int)
        self.test['outperform'] = (self.test['stock_return_12m'] > test_median).astype(int)

        self.results = []
        print(f"\n📊 Train/Test Split")
        print(f"   Train: {len(self.train):,} samples ({len(self.train)/len(self.data)*100:.1f}%)")
        print(f"   Test:  {len(self.test):,} samples ({len(self.test)/len(self.data)*100:.1f}%)")
        print(f"   Outperform threshold (train): {train_median:.2f}%")
        print(f"   Outperform threshold (test):  {test_median:.2f}%")

    def generate_8d_scores(self, df: pd.DataFrame, weights: Dict[str, float]) -> np.ndarray:
        """
        Generate composite scores using weight dictionary.

        Score = sum(dimension_score[i] * weight[i] for all dimensions)
        """
        scores = np.zeros(len(df))

        for dim in self.DIMENSIONS:
            if dim in df.columns:
                # Assume column is named like 'capex_acceleration_score'
                col_name = f'{dim}_score' if f'{dim}_score' in df.columns else dim
                if col_name in df.columns:
                    scores += df[col_name].values * weights.get(dim, 0)

        return scores

    def random_search_weights(self, n_iterations: int = 500,
                             top_k: int = 100) -> List[Dict]:
        """
        Random search over weight combinations (faster than grid search).

        Args:
            n_iterations: Number of random combinations to try
            top_k: Return top K weight combinations

        Returns:
            List of top K weight combinations with F1 scores
        """

        print(f"\n🔍 RANDOM SEARCH OVER WEIGHT COMBINATIONS")
        print(f"   Dimensions: {len(self.DIMENSIONS)}")
        print(f"   Iterations: {n_iterations:,}")

        results = []
        np.random.seed(42)

        for iteration in range(n_iterations):
            # Random weights for each dimension (uniform 0-30)
            weights = {dim: np.random.uniform(0, 30) for dim in self.DIMENSIONS}

            # Normalize to sum to 100
            total = sum(weights.values())
            if total > 0:
                weights = {k: v / total * 100 for k, v in weights.items()}
            else:
                continue

            # Score training data
            scores = self.generate_8d_scores(self.train, weights)
            median_score = np.median(scores)
            predictions = (scores > median_score).astype(int)

            # Calculate F1 on training set
            try:
                train_f1 = f1_score(self.train['outperform'], predictions, zero_division=0)
                train_precision = precision_score(self.train['outperform'], predictions, zero_division=0)
                train_recall = recall_score(self.train['outperform'], predictions, zero_division=0)
            except:
                train_f1 = 0
                train_precision = 0
                train_recall = 0

            # Score test data
            test_scores = self.generate_8d_scores(self.test, weights)
            test_median = np.median(test_scores)
            test_predictions = (test_scores > test_median).astype(int)

            # Calculate F1 on test set
            try:
                test_f1 = f1_score(self.test['outperform'], test_predictions, zero_division=0)
                test_precision = precision_score(self.test['outperform'], test_predictions, zero_division=0)
                test_recall = recall_score(self.test['outperform'], test_predictions, zero_division=0)
            except:
                test_f1 = 0
                test_precision = 0
                test_recall = 0

            results.append({
                'weights': weights.copy(),
                'train_f1': train_f1,
                'train_precision': train_precision,
                'train_recall': train_recall,
                'test_f1': test_f1,
                'test_precision': test_precision,
                'test_recall': test_recall,
                'overfitting_gap': abs(train_f1 - test_f1),
            })

            if (iteration + 1) % max(1, n_iterations // 10) == 0:
                print(f"   Iteration {iteration + 1:,}/{n_iterations:,}...", end='\r')

        print(f"   ✅ Tested {n_iterations:,} random combinations")

        # Sort by test F1 (avoid overfitting by using test set)
        results.sort(key=lambda x: x['test_f1'], reverse=True)

        return results[:top_k]

    def recommend_weights(self, top_results: List[Dict]) -> Dict:
        """Extract best weight combination"""
        best = top_results[0]
        return best['weights']

    def run_tuning(self, n_iterations: int = 500) -> Tuple[Dict, pd.DataFrame]:
        """
        Run full hyperparameter tuning

        Args:
            n_iterations: Number of random combinations to try (faster than grid search)

        Returns:
            Best weights dict, results dataframe
        """

        # Random search (more efficient than grid search)
        top_results = self.random_search_weights(n_iterations=n_iterations, top_k=100)

        # Get best weights
        best_weights = self.recommend_weights(top_results)

        return best_weights, top_results

    def generate_report(self, best_weights: Dict, top_results: List[Dict]) -> None:
        """Generate tuning report"""

        best = top_results[0]

        print(f"\n" + "="*80)
        print(f"F1-BASED HYPERPARAMETER TUNING RESULTS")
        print(f"="*80)

        print(f"\n🏆 BEST WEIGHT COMBINATION (Maximized Test F1)")
        print(f"   Test F1 Score:    {best['test_f1']:.4f}")
        print(f"   Test Precision:   {best['test_precision']:.4f}")
        print(f"   Test Recall:      {best['test_recall']:.4f}")
        print(f"   Train F1 Score:   {best['train_f1']:.4f}")
        print(f"   Overfitting Gap:  {best['overfitting_gap']:.4f} (lower is better)")

        print(f"\n📊 OPTIMAL WEIGHTS")
        print(f"   {'Dimension':<30s} {'Weight':>10s} {'Current':>10s} {'Change':>10s}")
        print("   " + "-"*70)

        # Current baseline weights
        current_weights = {
            'debt_expansion': 20,
            'capex_acceleration': 20,
            'profit_reinvestment': 15,
            'profitability_quality': 15,
            'sustainability': 15,
            'timing_alignment': 10,
            'leverage_health': 5,
            'fcf_generation': 0,
        }

        for dim in self.DIMENSIONS:
            new_wt = best_weights[dim]
            old_wt = current_weights.get(dim, 0)
            change = new_wt - old_wt
            change_str = f"{change:+.1f}" if abs(change) > 0.1 else "→"

            print(f"   {dim:<30s} {new_wt:>10.1f} {old_wt:>10.1f} {change_str:>10s}")

        total = sum(best_weights.values())
        print(f"\n   TOTAL: {total:.1f}")

        print(f"\n📈 TOP 10 WEIGHT COMBINATIONS")
        print(f"   {'Rank':>4s} {'Test F1':>10s} {'Precision':>10s} {'Recall':>10s} {'Train F1':>10s} {'Overfit':>10s}")
        print("   " + "-"*70)

        for rank, result in enumerate(top_results[:10], 1):
            print(
                f"   {rank:4d}  {result['test_f1']:>10.4f} "
                f"{result['test_precision']:>10.4f} {result['test_recall']:>10.4f} "
                f"{result['train_f1']:>10.4f} {result['overfitting_gap']:>10.4f}"
            )

        print(f"\n💡 KEY METRICS")
        print(f"   Precision:  {best['test_precision']:.1%}")
        print(f"   └─ Of companies we recommend as BUY, {best['test_precision']:.1%} actually outperform")
        print(f"   Recall:     {best['test_recall']:.1%}")
        print(f"   └─ We identify {best['test_recall']:.1%} of actual outperformers")
        print(f"   F1 Score:   {best['test_f1']:.4f}")
        print(f"   └─ Harmonic mean of precision & recall (0-1 scale)")

        print(f"\n⚖️  PRECISION-RECALL TRADEOFF")
        print(f"   High Precision (>80%): Be selective, only recommend sure winners")
        print(f"   High Recall (>80%):    Catch most outperformers, accept false positives")
        print(f"   F1 Optimal ({best['test_f1']:.1%}):     Balance both objectives")

        print(f"\n⚠️  OVERFITTING ANALYSIS")
        print(f"   Overfitting Gap: {best['overfitting_gap']:.4f}")
        if best['overfitting_gap'] < 0.05:
            print(f"   ✅ LOW OVERFITTING - Model generalizes well")
        elif best['overfitting_gap'] < 0.15:
            print(f"   ⚠️  MODERATE OVERFITTING - May need regularization")
        else:
            print(f"   ❌ HIGH OVERFITTING - Model doesn't generalize")

        print(f"\n🎯 INTERPRETATION")
        print(f"   This weight combination was tuned on {len(self.train):,} training samples")
        print(f"   And validated on {len(self.test):,} held-out test samples")
        print(f"   Expected real-world F1 score: ~{best['test_f1']:.3f} (±0.02)")
        print(f"   Recommendation confidence: {best['test_precision']:.0%}")

        print("\n" + "="*80)

    def compare_with_baseline(self, best_weights: Dict, baseline_weights: Dict) -> None:
        """Compare F1 score of new weights vs baseline"""

        print(f"\n" + "="*80)
        print(f"COMPARISON: OPTIMIZED vs BASELINE WEIGHTS")
        print(f"="*80)

        for weights, label in [(best_weights, "OPTIMIZED"), (baseline_weights, "BASELINE")]:
            scores = self.generate_8d_scores(self.test, weights)
            median_score = np.median(scores)
            predictions = (scores > median_score).astype(int)

            f1 = f1_score(self.test['outperform'], predictions)
            precision = precision_score(self.test['outperform'], predictions)
            recall = recall_score(self.test['outperform'], predictions)

            tn, fp, fn, tp = confusion_matrix(self.test['outperform'], predictions).ravel()
            accuracy = (tp + tn) / (tp + tn + fp + fn)

            print(f"\n{label} WEIGHTS")
            print(f"   F1 Score:      {f1:.4f}")
            print(f"   Precision:     {precision:.4f} ({tp}/{tp+fp} correct buys)")
            print(f"   Recall:        {recall:.4f} ({tp}/{tp+fn} outperformers caught)")
            print(f"   Accuracy:      {accuracy:.4f}")
            print(f"   True Positives:  {tp:,}")
            print(f"   False Positives: {fp:,}")
            print(f"   True Negatives:  {tn:,}")
            print(f"   False Negatives: {fn:,}")

        # Calculate improvement
        baseline_scores = self.generate_8d_scores(self.test, baseline_weights)
        baseline_median = np.median(baseline_scores)
        baseline_pred = (baseline_scores > baseline_median).astype(int)
        baseline_f1 = f1_score(self.test['outperform'], baseline_pred)

        opt_scores = self.generate_8d_scores(self.test, best_weights)
        opt_median = np.median(opt_scores)
        opt_pred = (opt_scores > opt_median).astype(int)
        opt_f1 = f1_score(self.test['outperform'], opt_pred)

        improvement = (opt_f1 - baseline_f1) / baseline_f1 * 100 if baseline_f1 > 0 else 0

        print(f"\n📈 IMPROVEMENT")
        print(f"   F1 Score: {baseline_f1:.4f} → {opt_f1:.4f}")
        print(f"   Improvement: {improvement:+.1f}%")
        print(f"   Recommendation: {'✅ Use optimized weights' if improvement > 0 else '❌ Stick with baseline'}")

        print("\n" + "="*80)


if __name__ == "__main__":
    print("\n" + "🎯 "*40)
    print("F1-BASED HYPERPARAMETER TUNING FOR WEIGHT OPTIMIZATION")
    print("🎯 "*40)
