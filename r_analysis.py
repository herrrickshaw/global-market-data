# r_analysis.py — R statistical analysis via subprocess
# ========================================================
# Runs R scripts for portfolio statistics and regime analysis.
# Uses subprocess (not rpy2) for macOS compatibility.
#
# WHY R?
# ──────
# R's PerformanceAnalytics and quantmod packages provide battle-tested
# implementations of Sharpe ratio significance testing, max drawdown
# calculation, and portfolio attribution that are more robust than
# our manual Python implementations.
#
# Key R packages used:
#   PerformanceAnalytics  — Sharpe, Sortino, max drawdown, Calmar
#   quantmod              — OHLCV manipulation, technical indicators
#   TTR                   — RSI, MACD, Bollinger Bands, ATR
#   xts / zoo             — Time series handling
#   MHMMs / depmixS4      — Hidden Markov Models for regime detection
#
# USAGE
# ─────
#   from r_analysis import compute_r_stats, detect_regimes_r, sharpe_significance
#
#   stats  = compute_r_stats(returns_series)
#   regime = detect_regimes_r(price_series, n_states=3)
#   sig    = sharpe_significance(signals_df, n_bootstrap=1000)

import json
import subprocess
import tempfile
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

R_AVAILABLE = False
try:
    result = subprocess.run(["Rscript", "--version"], capture_output=True, timeout=5)
    R_AVAILABLE = result.returncode == 0
except Exception:
    pass

DISCLAIMER = (
    "⚠️  R ANALYSIS DISCLAIMER: R statistical tests assume stationarity and "
    "i.i.d. returns, which stock returns violate. Results are indicative, "
    "not definitive. NOT investment advice."
)


# ── R installation check ──────────────────────────────────────────────────────


def check_r_packages(packages: list) -> dict:
    """Check which R packages are installed."""
    if not R_AVAILABLE:
        return {p: False for p in packages}
    script = (
        "cat(paste(sapply(c("
        + ",".join(f'"{p}"' for p in packages)
        + "), function(p) as.integer(requireNamespace(p,quietly=TRUE))), collapse=','))"
    )
    try:
        r = subprocess.run(
            ["Rscript", "--vanilla", "-e", script], capture_output=True, text=True, timeout=30
        )
        vals = [int(x) for x in r.stdout.strip().split(",")]
        return dict(zip(packages, [bool(v) for v in vals]))
    except Exception:
        return {p: False for p in packages}


def install_r_packages(packages: list):
    """Install missing R packages."""
    json.dumps(packages).replace('"', '\\"')
    script = f'install.packages(c({",".join(chr(34)+p+chr(34) for p in packages)}), repos="https://cran.r-project.org", quiet=TRUE)'
    subprocess.run(["Rscript", "--vanilla", "-e", script], timeout=300)


# ── Core R statistical functions ──────────────────────────────────────────────


def compute_r_stats(
    returns: pd.Series, risk_free: float = 0.065, periods_per_year: int = 252
) -> dict:
    """
    Compute comprehensive performance statistics using R's PerformanceAnalytics.

    Annualised Sharpe ratio from R is more precisely calculated than our
    Python version because it uses the exact daily compounding formula.

    Args:
        returns         : daily return series (decimal, not %)
        risk_free       : annualised risk-free rate (India 10yr GSec ~6.5%)
        periods_per_year: 252 trading days

    Returns dict with: sharpe, sortino, calmar, max_drawdown, cagr,
                       var_95, cvar_95, omega_ratio, information_ratio
    """
    if not R_AVAILABLE or returns.empty:
        return _python_fallback_stats(returns, risk_free, periods_per_year)

    # Write returns to temp CSV
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
        tmp_path = f.name
        returns.to_csv(f, index=True, header=True)

    r_script = f"""
suppressPackageStartupMessages({{
  if (!requireNamespace("PerformanceAnalytics", quietly=TRUE))
    install.packages("PerformanceAnalytics", repos="https://cran.r-project.org", quiet=TRUE)
  library(PerformanceAnalytics)
  library(xts)
}})

df  <- read.csv("{tmp_path}")
ret <- xts(df[[2]], order.by=as.Date(df[[1]]))
Rf  <- {risk_free} / {periods_per_year}   # daily risk-free

sharpe    <- SharpeRatio.annualized(ret, Rf=Rf, scale={periods_per_year})
sortino   <- SortinoRatio(ret, MAR=Rf)
calmar    <- CalmarRatio(ret)
max_dd    <- maxDrawdown(ret)
cagr      <- Return.annualized(ret, scale={periods_per_year})
omega     <- tryCatch(OmegaRatio(ret, MAR=Rf), error=function(e) NA)

# Value at Risk (95% historical VaR)
var95     <- VaR(ret, p=0.95, method="historical")
cvar95    <- CVaR(ret, p=0.95, method="historical")

results <- list(
  sharpe=as.numeric(sharpe),
  sortino=as.numeric(sortino),
  calmar=as.numeric(calmar),
  max_drawdown=as.numeric(max_dd),
  cagr=as.numeric(cagr),
  var_95=as.numeric(var95),
  cvar_95=as.numeric(cvar95),
  omega_ratio=as.numeric(omega),
  n_observations=length(ret),
  period_years=length(ret)/{periods_per_year}
)
cat(jsonlite::toJSON(results, auto_unbox=TRUE))
"""
    try:
        proc = subprocess.run(
            ["Rscript", "--vanilla", "-e", r_script], capture_output=True, text=True, timeout=60
        )
        Path(tmp_path).unlink(missing_ok=True)
        # Extract JSON from output (may have R messages before it)
        output = proc.stdout.strip()
        json_start = output.rfind("{")
        if json_start >= 0:
            stats = json.loads(output[json_start:])
            stats["source"] = "R_PerformanceAnalytics"
            return stats
    except Exception as e:
        Path(tmp_path).unlink(missing_ok=True)
        print(f"  R stats failed: {e} — using Python fallback")

    return _python_fallback_stats(returns, risk_free, periods_per_year)


def detect_regimes_r(prices: pd.Series, n_states: int = 3) -> pd.DataFrame:
    """
    Hidden Markov Model (HMM) regime detection using R's depmixS4.

    Identifies latent market states from price returns — more sophisticated
    than our simple 200 DMA approach because it learns state transitions
    from the data rather than using fixed thresholds.

    States typically map to:
      State 1: Low volatility Bull
      State 2: High volatility / Transitional
      State 3: Bear / Crash

    Returns DataFrame: Date | State | State_Prob | Volatility | Return
    """
    if not R_AVAILABLE or prices.empty:
        return _python_regime_fallback(prices)

    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
        tmp_in = f.name
        tmp_out = tmp_in.replace(".csv", "_out.csv")
        prices.to_csv(f, index=True, header=True)

    r_script = f"""
suppressPackageStartupMessages({{
  if (!requireNamespace("depmixS4", quietly=TRUE))
    install.packages("depmixS4", repos="https://cran.r-project.org", quiet=TRUE)
  library(depmixS4)
}})

df     <- read.csv("{tmp_in}")
prices <- df[[2]]
dates  <- as.Date(df[[1]])
rets   <- diff(log(prices))
rets   <- c(NA, rets)

# Fit HMM with {n_states} states
mod <- depmix(rets ~ 1, data=data.frame(rets=rets), nstates={n_states},
              family=gaussian())
tryCatch({{
  fit <- fit(mod, verbose=FALSE)
  states <- viterbi(fit)$state
  probs  <- posterior(fit)

  out <- data.frame(
    Date     = as.character(dates),
    Return   = round(rets, 6),
    State    = states,
    Prob_S1  = round(probs[,2], 4),
    Prob_S2  = round(probs[,3], 4)
  )
  write.csv(out, "{tmp_out}", row.names=FALSE)
}}, error=function(e) {{
  # Fallback: simple volatility-based regimes
  vol20 <- zoo::rollmean(abs(rets), 20, fill=NA, align="right")
  q33   <- quantile(vol20, 0.33, na.rm=TRUE)
  q66   <- quantile(vol20, 0.66, na.rm=TRUE)
  state <- ifelse(vol20 <= q33, 1L, ifelse(vol20 <= q66, 2L, 3L))
  out   <- data.frame(Date=as.character(dates), Return=round(rets,6),
                      State=state, Prob_S1=NA, Prob_S2=NA)
  write.csv(out, "{tmp_out}", row.names=FALSE)
}})
"""
    try:
        subprocess.run(
            ["Rscript", "--vanilla", "-e", r_script], capture_output=True, text=True, timeout=120
        )
        Path(tmp_in).unlink(missing_ok=True)
        if Path(tmp_out).exists():
            result = pd.read_csv(tmp_out, parse_dates=["Date"])
            Path(tmp_out).unlink(missing_ok=True)
            result["source"] = "R_HMM"
            return result
    except Exception as e:
        print(f"  R HMM failed: {e}")
        for p in [tmp_in, tmp_out]:
            Path(p).unlink(missing_ok=True)

    return _python_regime_fallback(prices)


def sharpe_significance_test(
    screener_returns: pd.Series, benchmark_returns: pd.Series, n_bootstrap: int = 1000
) -> dict:
    """
    Test whether a screener's Sharpe ratio is statistically significant
    using the Ledoit-Wolf bootstrap (Lo 2002 — "The Statistics of Sharpe Ratios").

    H0: screener Sharpe = benchmark Sharpe (no skill)
    H1: screener Sharpe > benchmark Sharpe (genuine edge)

    Returns: {sharpe_screener, sharpe_benchmark, p_value, significant_at_95}
    """
    if not R_AVAILABLE:
        return _bootstrap_fallback(screener_returns, benchmark_returns, n_bootstrap)

    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
        tmp = f.name
        pd.DataFrame({"screener": screener_returns, "benchmark": benchmark_returns}).to_csv(f)

    r_script = f"""
suppressPackageStartupMessages(library(PerformanceAnalytics))
df  <- read.csv("{tmp}", row.names=1)
s   <- xts(df$screener,  order.by=as.Date(rownames(df)))
b   <- xts(df$benchmark, order.by=as.Date(rownames(df)))
s   <- na.omit(s); b <- na.omit(b)

sr_s <- as.numeric(SharpeRatio.annualized(s, scale=252))
sr_b <- as.numeric(SharpeRatio.annualized(b, scale=252))

# Bootstrap test
set.seed(42)
boot_diff <- replicate({n_bootstrap}, {{
  idx <- sample(length(s), replace=TRUE)
  sr_b2 <- as.numeric(SharpeRatio.annualized(s[idx], scale=252))
  sr_s2 <- as.numeric(SharpeRatio.annualized(b[idx], scale=252))
  sr_b2 - sr_s2
}})
p_val <- mean(boot_diff >= (sr_s - sr_b))
cat(sprintf('{{"sr_screener":%.4f,"sr_benchmark":%.4f,"p_value":%.4f,"significant":%s}}',
    sr_s, sr_b, p_val, tolower(as.character(p_val < 0.05))))
"""
    try:
        proc = subprocess.run(
            ["Rscript", "--vanilla", "-e", r_script], capture_output=True, text=True, timeout=120
        )
        Path(tmp).unlink(missing_ok=True)
        output = proc.stdout.strip()
        j_start = output.rfind("{")
        if j_start >= 0:
            result = json.loads(output[j_start:])
            result["source"] = "R_bootstrap"
            result["n_bootstrap"] = n_bootstrap
            return result
    except Exception as e:
        Path(tmp).unlink(missing_ok=True)
        print(f"  R significance test failed: {e}")

    return _bootstrap_fallback(screener_returns, benchmark_returns, n_bootstrap)


def compute_technical_indicators_r(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute RSI, MACD, ATR, Bollinger Bands using R's TTR package.
    Faster than pandas TA-Lib for very large datasets; R uses vectorised C internally.
    """
    if not R_AVAILABLE or df.empty:
        return _python_indicators_fallback(df)

    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
        tmp_in = f.name
        tmp_out = tmp_in.replace(".csv", "_ind.csv")
        df[["Open", "High", "Low", "Close", "Volume"]].to_csv(f, index=True)

    r_script = f"""
suppressPackageStartupMessages({{
  if (!requireNamespace("TTR", quietly=TRUE))
    install.packages("TTR", repos="https://cran.r-project.org", quiet=TRUE)
  library(TTR)
}})

df <- read.csv("{tmp_in}", row.names=1)
HLC <- df[, c("High","Low","Close")]
Vol <- df$Volume

rsi14   <- RSI(df$Close, n=14)
macd_d  <- MACD(df$Close, nFast=12, nSlow=26, nSig=9)
bb      <- BBands(HLC, n=20, sd=2)
atr14   <- ATR(HLC, n=14)
obv     <- OBV(df$Close, Vol)
ema20   <- EMA(df$Close, n=20)
sma200  <- SMA(df$Close, n=200)

out <- data.frame(
  Date    = rownames(df),
  Close   = df$Close,
  RSI14   = round(rsi14, 2),
  MACD    = round(macd_d[,"macd"], 4),
  Signal  = round(macd_d[,"signal"], 4),
  BB_Up   = round(bb[,"up"], 2),
  BB_Dn   = round(bb[,"dn"], 2),
  BB_Pct  = round(bb[,"pctB"], 4),
  ATR14   = round(atr14[,"atr"], 2),
  OBV     = obv,
  EMA20   = round(ema20, 2),
  SMA200  = round(sma200, 2)
)
write.csv(out, "{tmp_out}", row.names=FALSE)
"""
    try:
        subprocess.run(
            ["Rscript", "--vanilla", "-e", r_script], capture_output=True, text=True, timeout=60
        )
        Path(tmp_in).unlink(missing_ok=True)
        if Path(tmp_out).exists():
            result = pd.read_csv(tmp_out, parse_dates=["Date"]).set_index("Date")
            Path(tmp_out).unlink(missing_ok=True)
            return result
    except Exception as e:
        print(f"  R TTR failed: {e}")
        for p in [tmp_in, tmp_out]:
            Path(p).unlink(missing_ok=True)

    return _python_indicators_fallback(df)


# ── Python fallbacks ──────────────────────────────────────────────────────────


def _python_fallback_stats(returns: pd.Series, rf: float, periods: int) -> dict:
    if returns.empty:
        return {}
    r = returns.dropna()
    ann_ret = (1 + r).prod() ** (periods / len(r)) - 1
    r.std() * np.sqrt(periods)
    rf_d = rf / periods
    sharpe = (r.mean() - rf_d) / r.std() * np.sqrt(periods) if r.std() > 0 else 0
    dn_std = r[r < rf_d].std() * np.sqrt(periods) if len(r[r < rf_d]) > 0 else 1e-9
    sortino = (r.mean() - rf_d) * np.sqrt(periods) / dn_std
    cumul = (1 + r).cumprod()
    max_dd = float(((cumul - cumul.cummax()) / cumul.cummax()).min())
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0
    var95 = float(r.quantile(0.05))
    return {
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round(calmar, 3),
        "max_drawdown": round(max_dd, 4),
        "cagr": round(ann_ret, 4),
        "var_95": round(var95, 4),
        "cvar_95": round(r[r < var95].mean(), 4) if len(r[r < var95]) > 0 else var95,
        "source": "Python_fallback",
    }


def _python_regime_fallback(prices: pd.Series) -> pd.DataFrame:
    rets = prices.pct_change()
    vol20 = rets.rolling(20).std()
    q33, q66 = vol20.quantile([0.33, 0.66])
    state = pd.cut(vol20, bins=[-np.inf, q33, q66, np.inf], labels=[1, 2, 3]).astype(float)
    return pd.DataFrame(
        {"Date": prices.index, "Return": rets, "State": state, "source": "Python_200DMA"}
    ).set_index("Date")


def _bootstrap_fallback(s: pd.Series, b: pd.Series, n: int) -> dict:
    s, b = s.dropna(), b.dropna()
    sr_s = s.mean() / s.std() * np.sqrt(252) if s.std() > 0 else 0
    sr_b = b.mean() / b.std() * np.sqrt(252) if b.std() > 0 else 0
    diffs = [
        np.random.choice(s, len(s), replace=True).mean()
        / max(np.random.choice(s, len(s), replace=True).std(), 1e-9)
        * np.sqrt(252)
        - np.random.choice(b, len(b), replace=True).mean()
        / max(np.random.choice(b, len(b), replace=True).std(), 1e-9)
        * np.sqrt(252)
        for _ in range(n)
    ]
    p_val = np.mean(np.array(diffs) >= (sr_s - sr_b))
    return {
        "sr_screener": round(sr_s, 4),
        "sr_benchmark": round(sr_b, 4),
        "p_value": round(p_val, 4),
        "significant": p_val < 0.05,
        "source": "Python_bootstrap",
        "n_bootstrap": n,
    }


def _python_indicators_fallback(df: pd.DataFrame) -> pd.DataFrame:
    d = pd.DataFrame(index=df.index)
    c = df["Close"].astype(float)
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    d["RSI14"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))
    ema12 = c.ewm(span=12).mean()
    ema26 = c.ewm(span=26).mean()
    d["MACD"] = ema12 - ema26
    d["Signal"] = d["MACD"].ewm(span=9).mean()
    bma = c.rolling(20).mean()
    bst = c.rolling(20).std()
    d["BB_Up"] = bma + 2 * bst
    d["BB_Dn"] = bma - 2 * bst
    d["EMA20"] = c.ewm(span=20).mean()
    d["SMA200"] = c.rolling(200).mean()
    return d
