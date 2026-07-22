# stock_daily_report.R
# ====================
# Daily stock report for NSE/BSE-listed Indian equities.
# R equivalent of stock_daily_report_improved.py
#
# Data sources:
#   • quantmod::getFinancials()  — Piotroski + Coffee Can (Yahoo Finance)
#   • tidyquant::tq_get()        — OHLC history (Yahoo Finance)
#   • quantmod::getQuote()       — Live price snapshot
#
# NSE-specific live data (bulk deals, option chains, NSE equity quote) requires
# browser-session cookies not available in R; those sections use Yahoo Finance.
#
# Install dependencies (run once in Colab):
#   install.packages(c("quantmod","tidyquant","dplyr","tidyr","purrr",
#                      "lubridate","openxlsx","cli","rlang"))
#
# Usage:
#   source("stock_daily_report.R")
#   run("RELIANCE")
#   run("TCS", run_scans = TRUE)
#   run_nifty50_batch(run_scans = TRUE)

suppressPackageStartupMessages({
  library(quantmod)
  library(tidyquant)
  library(dplyr)
  library(tidyr)
  library(purrr)
  library(lubridate)
  library(openxlsx)
})

OUTPUT_DIR     <- "./nse_bse_data"
DARVAS_CONFIRM <- 3L
dir.create(OUTPUT_DIR, showWarnings = FALSE, recursive = TRUE)

NIFTY_50_SYMBOLS <- c(
  "ADANIENT",  "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
  "BAJAJ-AUTO","BAJFINANCE", "BAJAJFINSV", "BPCL",       "BHARTIARTL",
  "BRITANNIA", "CIPLA",      "COALINDIA",  "DIVISLAB",   "DRREDDY",
  "EICHERMOT", "GRASIM",     "HCLTECH",    "HDFCBANK",   "HDFCLIFE",
  "HEROMOTOCO","HINDALCO",   "HINDUNILVR", "ICICIBANK",  "ITC",
  "INDUSINDBK","INFY",       "JSWSTEEL",   "KOTAKBANK",  "LT",
  "M&M",       "MARUTI",     "NTPC",       "NESTLEIND",  "ONGC",
  "POWERGRID", "RELIANCE",   "SBILIFE",    "SHRIRAMFIN", "SBIN",
  "SUNPHARMA", "TCS",        "TATACONSUM", "TATAMOTORS", "TATASTEEL",
  "TECHM",     "TITAN",      "TRENT",      "ULTRACEMCO", "WIPRO"
)


# ── Formatting helpers ────────────────────────────────────────────────────────

fmt <- function(val, prefix = "₹", decimals = 2) {
  if (is.null(val) || is.na(val)) return("N/A")
  tryCatch(
    sprintf(paste0(prefix, "%.", decimals, "f"), as.numeric(val)) |>
      (\(x) formatC(as.numeric(sub(prefix, "", x, fixed = TRUE)),
                     format = "f", digits = decimals, big.mark = ",") |>
         paste0(prefix, x = _))(),
    error = function(e) "N/A"
  )
}

fmt_simple <- function(val, prefix = "₹", decimals = 2) {
  if (is.null(val) || length(val) == 0 || is.na(val)) return("N/A")
  v <- suppressWarnings(as.numeric(val))
  if (is.na(v)) return("N/A")
  paste0(prefix, formatC(v, format = "f", digits = decimals, big.mark = ","))
}

pct_str <- function(val) {
  if (is.null(val) || is.na(val)) return("N/A")
  v <- suppressWarnings(as.numeric(val))
  if (is.na(v)) return("N/A")
  arrow <- if (v >= 0) "▲" else "▼"
  sprintf("%s %.2f%%", arrow, abs(v))
}

section <- function(title) {
  w <- 60
  cat("\n", strrep("-", w), "\n  ", toupper(title), "\n", strrep("-", w), "\n", sep = "")
}

row_print <- function(label, value, width = 28) {
  cat(sprintf("  %-*s %s\n", width, label, value))
}


# ── yfinance-equivalent helpers via quantmod ─────────────────────────────────

.get_financials <- function(yf_sym) {
  # Returns list(IS, BS, CF) as matrices (rows = items, cols = years, most recent first)
  tryCatch({
    fin <- getFinancials(yf_sym, src = "yahoo", auto.assign = FALSE)
    list(
      IS  = tryCatch(viewFinancials(fin, type = "IS", period = "A"), error = function(e) NULL),
      BS  = tryCatch(viewFinancials(fin, type = "BS", period = "A"), error = function(e) NULL),
      CF  = tryCatch(viewFinancials(fin, type = "CF", period = "A"), error = function(e) NULL),
      ok  = TRUE
    )
  }, error = function(e) list(IS = NULL, BS = NULL, CF = NULL, ok = FALSE, error = conditionMessage(e)))
}

.get_row <- function(mat, ..., col = 1L) {
  # Safely fetch a scalar from a quantmod financial matrix.
  # Tries each term name in order, partial match, case-insensitive.
  terms <- c(...)
  if (is.null(mat) || nrow(mat) == 0) return(NA_real_)
  for (term in terms) {
    idx <- grep(term, rownames(mat), ignore.case = TRUE)
    if (length(idx) > 0) {
      val <- suppressWarnings(as.numeric(mat[idx[1], col]))
      if (!is.na(val)) return(val)
    }
  }
  NA_real_
}

.get_series <- function(mat, ...) {
  # Return all columns for the first matching row, as numeric vector (most-recent first).
  terms <- c(...)
  if (is.null(mat) || nrow(mat) == 0) return(numeric(0))
  for (term in terms) {
    idx <- grep(term, rownames(mat), ignore.case = TRUE)
    if (length(idx) > 0) {
      vals <- suppressWarnings(as.numeric(mat[idx[1], ]))
      return(vals[!is.na(vals)])
    }
  }
  numeric(0)
}


# ── Live quote via Yahoo Finance ──────────────────────────────────────────────

fetch_quote <- function(symbol, suffix = ".NS") {
  yf_sym <- paste0(symbol, suffix)
  tryCatch({
    q <- getQuote(yf_sym, what = yahooQF(c(
      "Name", "Last Trade (Price Only)", "Previous Close",
      "Open", "Days High", "Days Low",
      "Volume", "52-week High", "52-week Low", "Market Capitalization"
    )))
    list(
      name       = rownames(q)[1],
      ltp        = as.numeric(q[["Last Trade (Price Only)"]]),
      prev_close = as.numeric(q[["Previous Close"]]),
      open       = as.numeric(q[["Open"]]),
      day_high   = as.numeric(q[["Days High"]]),
      day_low    = as.numeric(q[["Days Low"]]),
      volume     = as.numeric(q[["Volume"]]),
      w52_high   = as.numeric(q[["52-week High"]]),
      w52_low    = as.numeric(q[["52-week Low"]]),
      mcap       = as.numeric(q[["Market Capitalization"]])
    )
  }, error = function(e) {
    # Fallback: tidyquant snapshot
    tryCatch({
      snap <- tq_get(yf_sym, get = "stock.prices", from = Sys.Date() - 5, to = Sys.Date())
      if (nrow(snap) == 0) return(list(error = conditionMessage(e)))
      last_row <- tail(snap, 1)
      prev_row <- if (nrow(snap) >= 2) snap[nrow(snap) - 1, ] else last_row
      list(
        ltp        = last_row$close,
        prev_close = prev_row$close,
        open       = last_row$open,
        day_high   = last_row$high,
        day_low    = last_row$low,
        volume     = last_row$volume
      )
    }, error = function(e2) list(error = conditionMessage(e2)))
  })
}


# ── OHLC history ──────────────────────────────────────────────────────────────

fetch_ohlc_history <- function(symbol, days = 90L, suffix = ".NS") {
  yf_sym <- paste0(symbol, suffix)
  from   <- Sys.Date() - days
  tryCatch({
    df <- tq_get(yf_sym, get = "stock.prices", from = from, to = Sys.Date())
    if (is.null(df) || nrow(df) == 0) return(data.frame())
    df <- df[order(df$date), ]   # ascending: oldest first, newest last
    df
  }, error = function(e) {
    message(sprintf("  [OHLC fetch error] %s: %s", symbol, conditionMessage(e)))
    data.frame()
  })
}


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 1 — DARVAS BOX
# ═══════════════════════════════════════════════════════════════════════════════

compute_darvas_box <- function(df, confirm = DARVAS_CONFIRM) {
  # df must have columns: high, low, close (tidyquant column names)
  if (is.null(df) || nrow(df) < confirm + 5L) {
    return(list(signal = "INSUFFICIENT_DATA", box_top = NA, box_bottom = NA,
                note = sprintf("Need >= %d rows; got %d", confirm + 5L, nrow(df))))
  }

  all_highs  <- as.numeric(df$high)
  all_lows   <- as.numeric(df$low)
  all_closes <- as.numeric(df$close)

  # Remove NAs
  all_highs [is.na(all_highs )] <- 0
  all_lows  [is.na(all_lows  )] <- 0
  all_closes[is.na(all_closes)] <- 0

  n       <- length(all_highs)
  current <- all_closes[n]          # today's close (excluded from box formation)
  highs   <- all_highs  [-n]        # historical bars only
  lows    <- all_lows   [-n]
  nh      <- length(highs)

  # Step 1: most recent confirmed box top (scan backwards)
  box_top_idx <- NA_integer_
  box_top     <- NA_real_
  for (i in seq(nh - confirm - 1L, 1L, by = -1L)) {
    candidate <- highs[i]
    if (candidate == 0) next
    window <- highs[(i + 1L):(i + confirm)]
    if (length(window) == confirm && all(window < candidate)) {
      box_top_idx <- i
      box_top     <- candidate
      break
    }
  }

  if (is.na(box_top)) {
    return(list(signal = "NO_BOX", box_top = NA, box_bottom = NA,
                note = "No confirmed Darvas top found."))
  }

  # Step 2: confirmed box bottom from box-top day onward (historical only)
  segment    <- lows[box_top_idx:nh]
  ns         <- length(segment)
  box_bottom <- NA_real_

  for (i in seq_len(ns - confirm)) {
    candidate <- segment[i]
    if (candidate == 0) next
    window <- segment[(i + 1L):(i + confirm)]
    if (length(window) == confirm && all(window > candidate)) {
      box_bottom <- candidate
      break
    }
  }

  if (is.na(box_bottom)) {
    valid <- segment[segment > 0]
    box_bottom <- if (length(valid) > 0) min(valid) else NA_real_
  }

  if (is.na(box_bottom)) {
    return(list(signal = "NO_BOX", box_top = round(box_top, 2), box_bottom = NA,
                note = "Could not confirm box bottom."))
  }

  # Step 3: classify today's close
  signal <- if (current > box_top) "BREAKOUT_BUY" else
            if (current < box_bottom) "BREAKDOWN_SELL" else "IN_BOX"

  box_range      <- box_top - box_bottom
  pos_in_box     <- if (box_range > 0) (current - box_bottom) / box_range * 100 else 0
  upside_to_top  <- if (current > 0) (box_top - current) / current * 100 else 0

  list(
    signal              = signal,
    box_top             = round(box_top,    2),
    box_bottom          = round(box_bottom, 2),
    current_price       = round(current,    2),
    box_range           = round(box_range,  2),
    position_in_box_pct = round(pos_in_box,    1),
    upside_to_top_pct   = round(upside_to_top, 2),
    confirm_days        = confirm,
    data_points         = n
  )
}

display_darvas_box <- function(result) {
  section("Darvas Box Scan")
  sig <- result$signal %||% "N/A"
  labels <- c(
    BREAKOUT_BUY    = "* BREAKOUT BUY  -- price above box top",
    BREAKDOWN_SELL  = "* BREAKDOWN SELL -- price below box bottom",
    IN_BOX          = "* IN BOX        -- price consolidating",
    NO_BOX          = "No confirmed Darvas box found",
    INSUFFICIENT_DATA = "Insufficient OHLC data"
  )
  cat("\n  Signal:", labels[sig] %||% sig, "\n")

  if (!is.na(result$box_top)) {
    cat("\n")
    row_print("Box Top",             fmt_simple(result$box_top))
    row_print("Box Bottom",          fmt_simple(result$box_bottom))
    row_print("Current Price",       fmt_simple(result$current_price))
    row_print("Box Range",           fmt_simple(result$box_range))
    row_print("Position in Box",     sprintf("%.1f%%", result$position_in_box_pct %||% 0))
    row_print("Upside to Top",       sprintf("%.2f%%", result$upside_to_top_pct   %||% 0))
    row_print("Confirmation (days)", as.character(result$confirm_days %||% DARVAS_CONFIRM))
    row_print("Data points used",    as.character(result$data_points  %||% "-"))
  } else if (!is.null(result$note)) {
    cat("  Note:", result$note, "\n")
  }
}


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 2 — PIOTROSKI F-SCORE
# ═══════════════════════════════════════════════════════════════════════════════

compute_piotroski_score <- function(symbol, suffix = ".NS") {
  yf_sym <- paste0(symbol, suffix)
  fin    <- .get_financials(yf_sym)
  if (!fin$ok) return(list(symbol = symbol, error = fin$error %||% "Fetch failed"))

  is_m <- fin$IS; bs_m <- fin$BS; cf_m <- fin$CF
  if (is.null(is_m)) return(list(symbol = symbol, error = "No income statement"))

  scores  <- integer(9)
  names(scores) <- paste0("F", 1:9)
  details <- list()

  # Profitability
  ni0  <- .get_row(is_m, "Net Income",    col = 1L)
  ta0  <- .get_row(bs_m, "Total Assets",  col = 1L)
  ni1  <- .get_row(is_m, "Net Income",    col = 2L)
  ta1  <- .get_row(bs_m, "Total Assets",  col = 2L)

  roa0 <- if (!is.na(ni0) && !is.na(ta0) && ta0 != 0) ni0 / ta0 else NA_real_
  roa1 <- if (!is.na(ni1) && !is.na(ta1) && ta1 != 0) ni1 / ta1 else NA_real_

  scores["F1"] <- if (!is.na(roa0) && roa0 > 0) 1L else 0L
  details$ROA_current_pct <- if (!is.na(roa0)) round(roa0 * 100, 2) else "N/A"

  ocf0 <- .get_row(cf_m, "Operating Cash Flow", "Total Cash From Operating", col = 1L)
  scores["F2"] <- if (!is.na(ocf0) && ocf0 > 0) 1L else 0L
  details$OCF_current_Cr <- if (!is.na(ocf0)) round(ocf0 / 1e7, 2) else "N/A"

  scores["F3"] <- if (!is.na(roa0) && !is.na(roa1) && roa0 > roa1) 1L else 0L
  details$ROA_prev_pct <- if (!is.na(roa1)) round(roa1 * 100, 2) else "N/A"

  scores["F4"] <- if (!is.na(ocf0) && !is.na(ta0) && !is.na(roa0) && ta0 > 0 &&
                       (ocf0 / ta0) > roa0) 1L else 0L

  # Leverage & Liquidity
  ltd0 <- .get_row(bs_m, "Long.term Debt", "Long Term Debt", col = 1L) %|0% 0
  ltd1 <- .get_row(bs_m, "Long.term Debt", "Long Term Debt", col = 2L) %|0% 0
  lev0 <- if (!is.na(ta0) && ta0 > 0) ltd0 / ta0 else NA_real_
  lev1 <- if (!is.na(ta1) && ta1 > 0) ltd1 / ta1 else NA_real_
  scores["F5"] <- if (!is.na(lev0) && !is.na(lev1) && lev0 < lev1) 1L else 0L
  details$LTD_ratio_curr_pct <- if (!is.na(lev0)) round(lev0 * 100, 2) else "N/A"

  ca0  <- .get_row(bs_m, "Total Current Assets",       "Current Assets",       col = 1L)
  cl0  <- .get_row(bs_m, "Total Current Liabilities",  "Current Liabilities",  col = 1L)
  ca1  <- .get_row(bs_m, "Total Current Assets",       "Current Assets",       col = 2L)
  cl1  <- .get_row(bs_m, "Total Current Liabilities",  "Current Liabilities",  col = 2L)
  cr0  <- if (!is.na(ca0) && !is.na(cl0) && cl0 > 0) ca0 / cl0 else NA_real_
  cr1  <- if (!is.na(ca1) && !is.na(cl1) && cl1 > 0) ca1 / cl1 else NA_real_
  scores["F6"] <- if (!is.na(cr0) && !is.na(cr1) && cr0 > cr1) 1L else 0L
  details$CurrentRatio_curr <- if (!is.na(cr0)) round(cr0, 2) else "N/A"

  sh0  <- .get_row(bs_m, "Share Issued", "Shares", col = 1L)
  sh1  <- .get_row(bs_m, "Share Issued", "Shares", col = 2L)
  scores["F7"] <- if (!is.na(sh0) && !is.na(sh1)) (if (sh0 <= sh1) 1L else 0L) else 1L

  # Operating Efficiency
  rev0 <- .get_row(is_m, "Total Revenue", col = 1L)
  gp0  <- .get_row(is_m, "Gross Profit",  col = 1L)
  rev1 <- .get_row(is_m, "Total Revenue", col = 2L)
  gp1  <- .get_row(is_m, "Gross Profit",  col = 2L)
  gm0  <- if (!is.na(gp0) && !is.na(rev0) && rev0 > 0) gp0 / rev0 else NA_real_
  gm1  <- if (!is.na(gp1) && !is.na(rev1) && rev1 > 0) gp1 / rev1 else NA_real_
  scores["F8"] <- if (!is.na(gm0) && !is.na(gm1) && gm0 > gm1) 1L else 0L
  details$GrossMargin_curr_pct <- if (!is.na(gm0)) round(gm0 * 100, 2) else "N/A"

  at0  <- if (!is.na(rev0) && !is.na(ta0) && ta0 > 0) rev0 / ta0 else NA_real_
  at1  <- if (!is.na(rev1) && !is.na(ta1) && ta1 > 0) rev1 / ta1 else NA_real_
  scores["F9"] <- if (!is.na(at0) && !is.na(at1) && at0 > at1) 1L else 0L
  details$AssetTurnover_curr <- if (!is.na(at0)) round(at0, 3) else "N/A"

  total  <- sum(scores)
  interp <- if (total >= 7) "STRONG -- likely outperformer" else
            if (total >= 4) "MODERATE -- neutral stance" else
            "WEAK -- avoid or short candidate"

  list(symbol = symbol, f_score = total, interpretation = interp,
       component_scores = scores, details = details)
}

display_piotroski_score <- function(result) {
  section("Piotroski F-Score")
  if (!is.null(result$error)) { cat("  Warning:", result$error, "\n"); return(invisible()) }

  total <- result$f_score
  star  <- if (total >= 7) "***" else if (total >= 4) "**" else "*"
  cat(sprintf("\n  Score: %s %d/9 %s  -- %s\n", star, total, star, result$interpretation))

  cat("\n  -- Component Scores --\n")
  labels <- c(
    F1 = "F1  ROA > 0",              F2 = "F2  Operating Cash Flow > 0",
    F3 = "F3  ROA improving YoY",    F4 = "F4  Earnings cash-backed",
    F5 = "F5  Long-term debt ratio down",
    F6 = "F6  Current ratio up",     F7 = "F7  No new shares issued",
    F8 = "F8  Gross margin up",      F9 = "F9  Asset turnover up"
  )
  for (nm in names(labels)) {
    v    <- result$component_scores[nm] %||% 0L
    tick <- if (v) "[PASS]" else "[FAIL]"
    cat(sprintf("    %s  %s\n", tick, labels[nm]))
  }

  cat("\n  -- Key Financials --\n")
  for (nm in names(result$details))
    row_print(gsub("_", " ", nm), as.character(result$details[[nm]]))
}


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 3 — COFFEE CAN PORTFOLIO SCREEN
# ═══════════════════════════════════════════════════════════════════════════════

compute_coffee_can <- function(symbol, suffix = ".NS") {
  yf_sym <- paste0(symbol, suffix)
  fin    <- .get_financials(yf_sym)
  if (!fin$ok) return(list(symbol = symbol, error = fin$error %||% "Fetch failed"))

  is_m <- fin$IS; bs_m <- fin$BS; cf_m <- fin$CF
  if (is.null(is_m)) return(list(symbol = symbol, error = "No income statement"))

  # Additional info via tidyquant
  info <- tryCatch(tq_get(yf_sym, get = "key.ratios"), error = function(e) NULL)

  criteria <- integer(5)
  names(criteria) <- paste0("C", 1:5)
  details  <- list()

  # C1: Revenue CAGR > 10%
  revs <- .get_series(is_m, "Total Revenue")
  if (length(revs) >= 2) {
    years <- length(revs) - 1L
    cagr  <- if (revs[length(revs)] > 0) ((revs[1] / revs[length(revs)]) ^ (1 / years) - 1) * 100 else NA_real_
    criteria["C1"] <- if (!is.na(cagr) && cagr > 10) 1L else 0L
    details$Revenue_CAGR_pct <- if (!is.na(cagr)) round(cagr, 2) else "N/A"
    details$Revenue_years    <- years
  } else {
    details$Revenue_CAGR_pct <- "N/A"
  }

  # C2: ROCE > 15% avg (EBIT / Capital Employed)
  ebit_s <- .get_series(is_m, "EBIT", "Operating Income", "Ebit")
  ta_s   <- .get_series(bs_m, "Total Assets")
  cl_s   <- .get_series(bs_m, "Total Current Liabilities", "Current Liabilities")

  roce_list <- numeric(0)
  for (i in seq_len(min(length(ebit_s), length(ta_s), length(cl_s)))) {
    ce <- ta_s[i] - cl_s[i]
    if (ce > 0) roce_list <- c(roce_list, ebit_s[i] / ce * 100)
  }
  if (length(roce_list) > 0) {
    avg_roce <- mean(roce_list)
    criteria["C2"] <- if (avg_roce > 15) 1L else 0L
    details$ROCE_avg_pct <- round(avg_roce, 2)
    details$ROCE_min_pct <- round(min(roce_list), 2)
  } else {
    details$ROCE_avg_pct <- "N/A"
  }

  # C3: Debt/Equity < 1
  ltd_s <- .get_series(bs_m, "Long.term Debt", "Long Term Debt")
  eq_s  <- .get_series(bs_m, "Total Equity", "Stockholders Equity", "Total Stockholder Equity")
  de <- NA_real_
  if (length(ltd_s) > 0 && length(eq_s) > 0 && eq_s[1] != 0) {
    de_raw <- ltd_s[1] / eq_s[1]
    de <- if (abs(de_raw) > 10) de_raw / 100 else de_raw  # normalise if percent-encoded
  }
  criteria["C3"]        <- if (!is.na(de) && de < 1) 1L else 0L
  details$Debt_to_Equity <- if (!is.na(de)) round(de, 2) else "N/A"

  # C4: Market Cap >= INR 500 Cr
  # quantmod getQuote gives market cap
  mcap_cr <- tryCatch({
    q <- getQuote(yf_sym, what = yahooQF("Market Capitalization"))
    mc <- as.numeric(q[["Market Capitalization"]])
    if (!is.na(mc)) mc / 1e7 else NA_real_
  }, error = function(e) NA_real_)

  criteria["C4"]      <- if (!is.na(mcap_cr) && mcap_cr >= 500) 1L else 0L
  details$Market_Cap_Cr <- if (!is.na(mcap_cr)) round(mcap_cr, 2) else "N/A"

  # C5: No loss-making year
  ni_s <- .get_series(is_m, "Net Income")
  if (length(ni_s) > 0) {
    all_profit <- all(ni_s > 0)
    criteria["C5"]     <- if (all_profit) 1L else 0L
    details$Loss_years  <- sum(ni_s <= 0)
    details$Years_analysed <- length(ni_s)
  }

  total    <- sum(criteria)
  max_pts  <- length(criteria)
  qualifies <- total == max_pts

  list(symbol = symbol, qualifies = qualifies,
       score = sprintf("%d/%d", total, max_pts),
       criteria = criteria, details = details)
}

display_coffee_can <- function(result) {
  section("Coffee Can Portfolio Screen")
  if (!is.null(result$error)) { cat("  Warning:", result$error, "\n"); return(invisible()) }

  badge <- if (result$qualifies) "[QUALIFIES]" else "[DOES NOT QUALIFY]"
  cat(sprintf("\n  Result: %s   (%s criteria met)\n", badge, result$score))

  cat("\n  -- Criteria --\n")
  labels <- c(
    C1 = "C1  Revenue CAGR > 10%",
    C2 = "C2  ROCE > 15% (avg)",
    C3 = "C3  Debt/Equity < 1",
    C4 = "C4  Market Cap >= INR 500 Cr",
    C5 = "C5  No loss-making year"
  )
  for (nm in names(labels)) {
    v    <- result$criteria[nm] %||% 0L
    tick <- if (v) "[PASS]" else "[FAIL]"
    cat(sprintf("    %s  %s\n", tick, labels[nm]))
  }

  cat("\n  -- Supporting Data --\n")
  for (nm in names(result$details))
    row_print(gsub("_", " ", nm), as.character(result$details[[nm]]))
}


# ── Live price display ────────────────────────────────────────────────────────

display_price <- function(quote, symbol) {
  section(sprintf("NSE/BSE -- Live Price (%s via Yahoo Finance)", symbol))
  ltp     <- quote$ltp
  prev    <- quote$prev_close
  chg     <- if (!is.null(ltp) && !is.null(prev)) ltp - prev else NA
  chg_pct <- if (!is.na(chg) && !is.null(prev) && prev > 0) chg / prev * 100 else NA

  row_print("Symbol",        symbol)
  if (!is.null(quote$name))  row_print("Name", quote$name)
  cat("\n")
  row_print("Last Price",   fmt_simple(ltp))
  row_print("Prev Close",   fmt_simple(prev))
  row_print("Change",       sprintf("%s  %s", fmt_simple(chg), pct_str(chg_pct)))
  row_print("Open",         fmt_simple(quote$open))
  row_print("Day High",     fmt_simple(quote$day_high))
  row_print("Day Low",      fmt_simple(quote$day_low))
  cat("\n")
  row_print("52-Week High", fmt_simple(quote$w52_high))
  row_print("52-Week Low",  fmt_simple(quote$w52_low))
  row_print("Volume",       if (!is.null(quote$volume)) formatC(as.numeric(quote$volume), format = "d", big.mark = ",") else "N/A")
}

display_historical_summary <- function(df) {
  section("Historical Data Summary (Last 30 Days)")
  if (is.null(df) || nrow(df) == 0) { cat("  No historical data.\n"); return(invisible()) }
  closes <- as.numeric(df$close)
  closes <- closes[!is.na(closes)]
  if (length(closes) < 2) { cat("  Insufficient data.\n"); return(invisible()) }
  ret_pct <- (closes[length(closes)] - closes[1]) / closes[1] * 100
  row_print("Days of data",  as.character(length(closes)))
  row_print("Period High",   fmt_simple(max(closes)))
  row_print("Period Low",    fmt_simple(min(closes)))
  row_print("Average Close", fmt_simple(mean(closes)))
  row_print("Period Return", pct_str(ret_pct))
}


# ── Main report ───────────────────────────────────────────────────────────────

run <- function(symbol, output_format = "text", run_scans = FALSE, suffix = ".NS") {
  symbol <- toupper(trimws(symbol))
  w <- 60L
  cat(sprintf("\n%s\n  DAILY STOCK REPORT -- %s\n  Generated: %s\n%s\n",
              strrep("=", w), symbol,
              format(Sys.time(), "%d %b %Y  %H:%M:%S"),
              strrep("=", w)))

  all_data <- list(symbol = symbol, timestamp = format(Sys.time(), "%Y-%m-%dT%H:%M:%S"))

  # Fetch
  quote   <- fetch_quote(symbol, suffix = suffix)
  hist_df <- fetch_ohlc_history(symbol, days = 30L, suffix = suffix)

  # Scans
  darvas_r    <- list()
  piotroski_r <- list()
  coffee_r    <- list()

  if (run_scans) {
    cat("  Running quantitative scans ...\n")
    ohlc_90   <- fetch_ohlc_history(symbol, days = 90L, suffix = suffix)
    darvas_r  <- compute_darvas_box(ohlc_90)
    piotroski_r <- compute_piotroski_score(symbol, suffix = suffix)
    coffee_r    <- compute_coffee_can(symbol, suffix = suffix)
  }

  if (output_format == "text") {
    display_price(quote, symbol)
    display_historical_summary(hist_df)
    if (run_scans) {
      display_darvas_box(darvas_r)
      display_piotroski_score(piotroski_r)
      display_coffee_can(coffee_r)
    }
    cat(sprintf("\n%s\n  Data: Yahoo Finance via quantmod + tidyquant\n%s\n",
                strrep("=", w), strrep("=", w)))
  } else if (output_format == "json") {
    all_data$quote   <- quote
    all_data$history <- if (nrow(hist_df) > 0) hist_df else list()
    if (run_scans)
      all_data$scans <- list(darvas = darvas_r, piotroski = piotroski_r, coffee_can = coffee_r)
    out <- file.path(OUTPUT_DIR, sprintf("%s_report_%s.json", symbol, format(Sys.Date(), "%Y%m%d")))
    jsonlite::write_json(all_data, out, pretty = TRUE, auto_unbox = TRUE)
    cat(sprintf("  JSON saved -> %s\n", out))
  }

  invisible(all_data)
}


# ── Nifty 50 batch ────────────────────────────────────────────────────────────

run_nifty50_batch <- function(run_scans = FALSE, symbols = NULL, suffix = ".NS") {
  targets <- symbols %||% NIFTY_50_SYMBOLS
  w <- 60L
  cat(sprintf("\n%s\n  NIFTY 50 BATCH -- %d stocks\n  Started: %s\n%s\n",
              strrep("#", w), length(targets),
              format(Sys.time(), "%d %b %Y  %H:%M:%S"), strrep("#", w)))

  results <- list()
  failed  <- character(0)

  for (i in seq_along(targets)) {
    sym <- targets[i]
    cat(sprintf("\n[%02d/%02d] %s\n", i, length(targets), sym))
    tryCatch({
      data        <- run(sym, output_format = "json", run_scans = run_scans, suffix = suffix)
      results[[i]] <- data
    }, error = function(e) {
      cat(sprintf("  FAILED: %s\n", conditionMessage(e)))
      failed <<- c(failed, sym)
    })
  }

  # Write summary XLSX
  .write_summary_xlsx_indian(results, include_scans = run_scans)

  cat(sprintf("\n%s\n  Done. %d OK, %d failed.\n",
              strrep("#", w), length(results) - length(failed), length(failed)))
  if (length(failed) > 0)
    cat(sprintf("  Failed: %s\n", paste(failed, collapse = ", ")))
  cat(strrep("#", w), "\n")

  invisible(results)
}

.write_summary_xlsx_indian <- function(results, include_scans = FALSE) {
  rows <- lapply(results, function(d) {
    if (is.null(d)) return(NULL)
    q  <- d$quote %||% list()
    ltp <- q$ltp; prev <- q$prev_close
    chg_pct <- if (!is.null(ltp) && !is.null(prev) && prev > 0) (ltp - prev) / prev * 100 else NA
    base <- data.frame(
      Symbol     = d$symbol %||% "",
      LTP        = suppressWarnings(as.numeric(ltp  %||% NA)),
      Change_pct = round(suppressWarnings(as.numeric(chg_pct)), 2),
      DayHigh    = suppressWarnings(as.numeric(q$day_high  %||% NA)),
      DayLow     = suppressWarnings(as.numeric(q$day_low   %||% NA)),
      W52High    = suppressWarnings(as.numeric(q$w52_high  %||% NA)),
      W52Low     = suppressWarnings(as.numeric(q$w52_low   %||% NA)),
      Timestamp  = d$timestamp %||% "",
      stringsAsFactors = FALSE
    )
    if (include_scans) {
      scans <- d$scans %||% list()
      darv  <- scans$darvas     %||% list()
      piofr <- scans$piotroski  %||% list()
      coff  <- scans$coffee_can %||% list()
      base$Darvas_Signal   <- darv$signal    %||% NA
      base$Darvas_BoxTop   <- suppressWarnings(as.numeric(darv$box_top    %||% NA))
      base$Darvas_BoxBot   <- suppressWarnings(as.numeric(darv$box_bottom %||% NA))
      base$Piotroski_Score <- suppressWarnings(as.integer(piofr$f_score   %||% NA))
      base$CoffeeCan       <- if (!is.null(coff$qualifies)) ifelse(coff$qualifies, "YES", "NO") else NA
      base$CoffeeCan_Score <- coff$score %||% NA
    }
    base
  })
  df <- do.call(rbind, Filter(Negate(is.null), rows))
  if (is.null(df) || nrow(df) == 0) return(invisible())

  tag <- if (include_scans) "nifty50_scan" else "nifty50"
  out <- file.path(OUTPUT_DIR, sprintf("%s_summary_%s.xlsx", tag, format(Sys.Date(), "%Y%m%d")))
  .write_summary_xlsx(df, out, include_scans = include_scans, market = "Indian (NSE/BSE)")
  cat(sprintf("\n  Summary XLSX -> %s\n", out))
}

.write_summary_xlsx <- function(df, out_path, include_scans = FALSE, market = "") {
  if (!requireNamespace("openxlsx", quietly = TRUE)) {
    message("  openxlsx not installed. Run: install.packages('openxlsx')")
    fallback <- sub("\\.xlsx$", ".csv", out_path)
    write.csv(df, fallback, row.names = FALSE)
    cat(sprintf("  Fallback CSV -> %s\n", fallback))
    return(invisible())
  }
  library(openxlsx)

  hdr_style  <- createStyle(fontColour = "#FFFFFF", fgFill = "#2E4057",
                             halign = "CENTER", textDecoration = "Bold", border = "Bottom")
  buy_style  <- createStyle(fgFill = "#C6EFCE", fontColour = "#276221")
  sell_style <- createStyle(fgFill = "#FFC7CE", fontColour = "#9C0006")
  box_style  <- createStyle(fgFill = "#FFEB9C", fontColour = "#9C5700")
  num_style  <- createStyle(numFmt = "#,##0.00")

  wb <- createWorkbook()

  # All Results sheet
  addWorksheet(wb, "All Results", tabColour = "#2E4057")
  writeData(wb, "All Results", df, headerStyle = hdr_style)
  freezePane(wb, "All Results", firstRow = TRUE)
  setColWidths(wb, "All Results", cols = seq_len(ncol(df)), widths = "auto")

  # Darvas signal colouring
  if ("Darvas_Signal" %in% names(df)) {
    for (i in seq_len(nrow(df))) {
      sig <- df$Darvas_Signal[i]
      if (!is.na(sig)) {
        sty <- if (sig == "BREAKOUT_BUY") buy_style else
               if (sig == "BREAKDOWN_SELL") sell_style else box_style
        addStyle(wb, "All Results", sty, rows = i + 1L, cols = seq_len(ncol(df)), gridExpand = TRUE)
      }
    }
  }

  # Filtered sheets (only when scans were run)
  if (include_scans && "Darvas_Signal" %in% names(df)) {
    breakouts <- df[!is.na(df$Darvas_Signal) & df$Darvas_Signal == "BREAKOUT_BUY", ]
    addWorksheet(wb, "Darvas Breakouts", tabColour = "#276221")
    writeData(wb, "Darvas Breakouts", breakouts, headerStyle = hdr_style)
    freezePane(wb, "Darvas Breakouts", firstRow = TRUE)
    setColWidths(wb, "Darvas Breakouts", cols = seq_len(ncol(breakouts)), widths = "auto")
  }

  if (include_scans && "Piotroski_Score" %in% names(df)) {
    df$Piotroski_Score <- suppressWarnings(as.integer(df$Piotroski_Score))
    strong <- df[!is.na(df$Piotroski_Score) & df$Piotroski_Score >= 7, ]
    strong <- strong[order(-strong$Piotroski_Score), ]
    addWorksheet(wb, "Piotroski Strong", tabColour = "#1F6AA5")
    writeData(wb, "Piotroski Strong", strong, headerStyle = hdr_style)
    freezePane(wb, "Piotroski Strong", firstRow = TRUE)
    setColWidths(wb, "Piotroski Strong", cols = seq_len(ncol(strong)), widths = "auto")
  }

  if (include_scans && "CoffeeCan" %in% names(df)) {
    qual <- df[!is.na(df$CoffeeCan) & df$CoffeeCan == "YES", ]
    addWorksheet(wb, "Coffee Can", tabColour = "#7B2D8B")
    writeData(wb, "Coffee Can", qual, headerStyle = hdr_style)
    freezePane(wb, "Coffee Can", firstRow = TRUE)
    setColWidths(wb, "Coffee Can", cols = seq_len(ncol(qual)), widths = "auto")
  }

  if (include_scans && all(c("Darvas_Signal","Piotroski_Score","CoffeeCan") %in% names(df))) {
    df$Piotroski_Score <- suppressWarnings(as.integer(df$Piotroski_Score))
    triple <- df[!is.na(df$Darvas_Signal) & df$Darvas_Signal == "BREAKOUT_BUY" &
                 !is.na(df$Piotroski_Score) & df$Piotroski_Score >= 7 &
                 !is.na(df$CoffeeCan) & df$CoffeeCan == "YES", ]
    addWorksheet(wb, "Triple Hits", tabColour = "#C00000")
    writeData(wb, "Triple Hits", triple, headerStyle = hdr_style)
    freezePane(wb, "Triple Hits", firstRow = TRUE)
    setColWidths(wb, "Triple Hits", cols = seq_len(ncol(triple)), widths = "auto")
  }

  # Summary stats sheet
  addWorksheet(wb, "Summary Stats", tabColour = "#888888")
  stats <- data.frame(
    Metric = c("Run Date", "Market", "Total Stocks"),
    Value  = c(format(Sys.Date(), "%Y-%m-%d"), market, nrow(df)),
    stringsAsFactors = FALSE
  )
  if (include_scans && "Darvas_Signal" %in% names(df)) {
    stats <- rbind(stats, data.frame(
      Metric = c("Darvas BREAKOUT_BUY", "Darvas IN_BOX", "Darvas BREAKDOWN_SELL",
                 "Piotroski STRONG (>=7)", "Coffee Can QUALIFIES"),
      Value  = c(sum(df$Darvas_Signal == "BREAKOUT_BUY",  na.rm = TRUE),
                 sum(df$Darvas_Signal == "IN_BOX",         na.rm = TRUE),
                 sum(df$Darvas_Signal == "BREAKDOWN_SELL", na.rm = TRUE),
                 sum(!is.na(df$Piotroski_Score) & df$Piotroski_Score >= 7),
                 sum(df$CoffeeCan == "YES", na.rm = TRUE)),
      stringsAsFactors = FALSE
    ))
  }
  writeData(wb, "Summary Stats", stats, headerStyle = hdr_style)
  setColWidths(wb, "Summary Stats", cols = 1:2, widths = c(36, 16))

  saveWorkbook(wb, out_path, overwrite = TRUE)
  invisible(out_path)
}


# ── Convenience: scans only ───────────────────────────────────────────────────

run_scans_only <- function(symbol, suffix = ".NS") {
  symbol <- toupper(trimws(symbol))
  cat(sprintf("\n%s\n  SCANS -- %s\n%s\n", strrep("=", 60), symbol, strrep("=", 60)))
  ohlc    <- fetch_ohlc_history(symbol, days = 90L, suffix = suffix)
  darv    <- compute_darvas_box(ohlc)
  piotr   <- compute_piotroski_score(symbol, suffix = suffix)
  coff    <- compute_coffee_can(symbol, suffix = suffix)
  display_darvas_box(darv)
  display_piotroski_score(piotr)
  display_coffee_can(coff)
  invisible(list(darvas = darv, piotroski = piotr, coffee_can = coff))
}


# ── Null-coalescing helpers ───────────────────────────────────────────────────

`%||%` <- function(a, b) if (!is.null(a) && length(a) > 0) a else b
`%|0%` <- function(a, b) if (!is.null(a) && !is.na(a)) a else b


# ── Example (uncomment to run) ────────────────────────────────────────────────
# run("RELIANCE")
# run("TCS", run_scans = TRUE)
# run_nifty50_batch(run_scans = TRUE)
