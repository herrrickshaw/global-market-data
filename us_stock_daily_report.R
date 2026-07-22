# us_stock_daily_report.R
# ========================
# Daily stock report for US-listed equities (NYSE / NASDAQ / AMEX).
# R equivalent of us_stock_daily_report.py
#
# All data sourced from Yahoo Finance via quantmod + tidyquant.
# Scans: Darvas Box, Piotroski F-Score, US-adapted Coffee Can.
#
# Install dependencies (run once in Colab):
#   install.packages(c("quantmod","tidyquant","dplyr","purrr",
#                      "lubridate","openxlsx","jsonlite"))
#
# Usage:
#   source("us_stock_daily_report.R")
#   run("AAPL")
#   run("NVDA", show_options = FALSE, run_scans = TRUE)
#   run_batch(symbols = c("AAPL","MSFT","NVDA"), run_scans = TRUE)
#   run_batch(symbols = DOW_JONES_30)
#   run_batch(symbols = NASDAQ_50, run_scans = TRUE)

suppressPackageStartupMessages({
  library(quantmod)
  library(tidyquant)
  library(dplyr)
  library(purrr)
  library(lubridate)
  library(openxlsx)
})

if (!requireNamespace("jsonlite", quietly = TRUE)) install.packages("jsonlite")
library(jsonlite)

OUTPUT_DIR     <- "./us_stock_data"
DARVAS_CONFIRM <- 3L
dir.create(OUTPUT_DIR, showWarnings = FALSE, recursive = TRUE)

DOW_JONES_30 <- c(
  "AAPL","AMGN","AMZN","AXP","BA","CAT","CRM","CSCO","CVX","DIS",
  "DOW","GS","HD","HON","IBM","JNJ","JPM","KO","MCD","MMM",
  "MRK","MSFT","NKE","NVDA","PG","TRV","UNH","V","VZ","WMT"
)

NASDAQ_50 <- c(
  "AAPL","MSFT","NVDA","AMZN","META","TSLA","GOOGL","GOOG","AVGO","COST",
  "NFLX","AMD","ADBE","QCOM","AMAT","MU","CSCO","INTU","TXN","AMGN",
  "HON","SBUX","GILD","ADI","VRTX","REGN","ISRG","PANW","CRWD","MELI",
  "LRCX","KLAC","SNPS","CDNS","ORLY","FTNT","MRVL","WDAY","PCAR","MNST",
  "ODFL","FAST","PAYX","BIIB","IDXX","VRSK","EXC","CEG","CSGP","APP"
)


# ── Null-coalescing helpers ───────────────────────────────────────────────────

`%||%` <- function(a, b) if (!is.null(a) && length(a) > 0) a else b
`%|0%` <- function(a, b) if (!is.null(a) && !is.na(a)) a else b


# ── Formatting helpers ────────────────────────────────────────────────────────

fmt_simple <- function(val, prefix = "$", decimals = 2) {
  if (is.null(val) || length(val) == 0 || is.na(val)) return("N/A")
  v <- suppressWarnings(as.numeric(val))
  if (is.na(v)) return("N/A")
  paste0(prefix, formatC(v, format = "f", digits = decimals, big.mark = ","))
}

fmt_large <- function(val, prefix = "$") {
  if (is.null(val) || is.na(val)) return("N/A")
  v <- suppressWarnings(as.numeric(val))
  if (is.na(v)) return("N/A")
  if      (abs(v) >= 1e12) sprintf("%s%.2fT", prefix, v / 1e12)
  else if (abs(v) >= 1e9)  sprintf("%s%.2fB", prefix, v / 1e9)
  else if (abs(v) >= 1e6)  sprintf("%s%.2fM", prefix, v / 1e6)
  else if (abs(v) >= 1e3)  sprintf("%s%.2fK", prefix, v / 1e3)
  else                     sprintf("%s%.2f",  prefix, v)
}

pct_str <- function(val) {
  if (is.null(val) || is.na(val)) return("N/A")
  v <- suppressWarnings(as.numeric(val))
  if (is.na(v)) return("N/A")
  arrow <- if (v >= 0) "+" else "-"
  sprintf("%s%.2f%%", arrow, abs(v))
}

section <- function(title) {
  w <- 60L
  cat(sprintf("\n%s\n  %s\n%s\n", strrep("-", w), toupper(title), strrep("-", w)))
}

row_print <- function(label, value, width = 30L) {
  cat(sprintf("  %-*s %s\n", width, label, value))
}


# ── quantmod financial helpers ────────────────────────────────────────────────

.get_financials <- function(symbol) {
  tryCatch({
    fin <- getFinancials(symbol, src = "yahoo", auto.assign = FALSE)
    list(
      IS  = tryCatch(viewFinancials(fin, type = "IS", period = "A"), error = function(e) NULL),
      BS  = tryCatch(viewFinancials(fin, type = "BS", period = "A"), error = function(e) NULL),
      CF  = tryCatch(viewFinancials(fin, type = "CF", period = "A"), error = function(e) NULL),
      ok  = TRUE
    )
  }, error = function(e)
    list(IS = NULL, BS = NULL, CF = NULL, ok = FALSE, error = conditionMessage(e)))
}

.get_row <- function(mat, ..., col = 1L) {
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


# ── Data fetch functions ──────────────────────────────────────────────────────

fetch_quote <- function(symbol) {
  tryCatch({
    q <- getQuote(symbol, what = yahooQF(c(
      "Name", "Last Trade (Price Only)", "Previous Close", "Open",
      "Days High", "Days Low", "Volume", "Average Daily Volume",
      "52-week High", "52-week Low", "Market Capitalization",
      "P/E Ratio", "Price/EPS Estimate Next Year", "EPS", "Book Value",
      "Dividend Yield", "Beta", "1 yr Target Price"
    )))
    ltp  <- as.numeric(q[["Last Trade (Price Only)"]])
    prev <- as.numeric(q[["Previous Close"]])
    list(
      name         = rownames(q)[1],
      exchange     = symbol,
      ltp          = ltp,
      prev_close   = prev,
      open         = as.numeric(q[["Open"]]),
      day_high     = as.numeric(q[["Days High"]]),
      day_low      = as.numeric(q[["Days Low"]]),
      volume       = as.numeric(q[["Volume"]]),
      avg_volume   = as.numeric(q[["Average Daily Volume"]]),
      w52_high     = as.numeric(q[["52-week High"]]),
      w52_low      = as.numeric(q[["52-week Low"]]),
      market_cap   = as.numeric(q[["Market Capitalization"]]),
      trailing_pe  = as.numeric(q[["P/E Ratio"]]),
      forward_pe   = as.numeric(q[["Price/EPS Estimate Next Year"]]),
      eps          = as.numeric(q[["EPS"]]),
      book_value   = as.numeric(q[["Book Value"]]),
      div_yield    = as.numeric(q[["Dividend Yield"]]),
      beta         = as.numeric(q[["Beta"]]),
      target_price = as.numeric(q[["1 yr Target Price"]])
    )
  }, error = function(e) {
    # Fallback: tq_get stock prices
    tryCatch({
      df  <- tq_get(symbol, get = "stock.prices", from = Sys.Date() - 5, to = Sys.Date())
      lr  <- tail(df, 1)
      pr  <- if (nrow(df) >= 2) df[nrow(df) - 1, ] else lr
      list(ltp = lr$close, prev_close = pr$close, open = lr$open,
           day_high = lr$high, day_low = lr$low, volume = lr$volume)
    }, error = function(e2) list(error = conditionMessage(e2)))
  })
}

fetch_historical <- function(symbol, period_days = 30L) {
  from <- Sys.Date() - period_days
  tryCatch({
    df <- tq_get(symbol, get = "stock.prices", from = from, to = Sys.Date())
    if (!is.null(df) && nrow(df) > 0) df[order(df$date), ] else data.frame()
  }, error = function(e) {
    message(sprintf("  [Historical error] %s: %s", symbol, conditionMessage(e)))
    data.frame()
  })
}

fetch_dividends_splits <- function(symbol) {
  tryCatch({
    divs   <- getDividends(symbol, auto.assign = FALSE)
    splits <- getSplits(symbol, auto.assign = FALSE)
    list(
      dividends = if (!is.null(divs)   && length(divs)   > 0)
        data.frame(date = as.character(tail(index(divs),   5)), amount = as.numeric(tail(divs,   5)))
        else data.frame(),
      splits    = if (!is.null(splits) && length(splits) > 0)
        data.frame(date = as.character(tail(index(splits), 3)), ratio  = as.numeric(tail(splits, 3)))
        else data.frame()
    )
  }, error = function(e) list(dividends = data.frame(), splits = data.frame()))
}


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 1 — DARVAS BOX
# ═══════════════════════════════════════════════════════════════════════════════

compute_darvas_box <- function(df, confirm = DARVAS_CONFIRM) {
  if (is.null(df) || nrow(df) < confirm + 5L)
    return(list(signal = "INSUFFICIENT_DATA", box_top = NA, box_bottom = NA,
                note = sprintf("Need >= %d rows; got %d", confirm + 5L, nrow(df))))

  all_highs  <- as.numeric(df$high);  all_highs [is.na(all_highs )] <- 0
  all_lows   <- as.numeric(df$low);   all_lows  [is.na(all_lows  )] <- 0
  all_closes <- as.numeric(df$close); all_closes[is.na(all_closes)] <- 0

  n <- length(all_highs)
  if (n < confirm + 5L)
    return(list(signal = "INSUFFICIENT_DATA", box_top = NA, box_bottom = NA))

  current <- all_closes[n]
  highs   <- all_highs [-n]
  lows    <- all_lows  [-n]
  nh      <- length(highs)

  # Step 1: most recent confirmed box top
  box_top_idx <- NA_integer_; box_top <- NA_real_
  for (i in seq(nh - confirm - 1L, 1L, by = -1L)) {
    cand <- highs[i]
    if (cand == 0) next
    win <- highs[(i + 1L):(i + confirm)]
    if (length(win) == confirm && all(win < cand)) {
      box_top_idx <- i; box_top <- cand; break
    }
  }
  if (is.na(box_top))
    return(list(signal = "NO_BOX", box_top = NA, box_bottom = NA,
                note = "No confirmed box top found"))

  # Step 2: confirmed box bottom
  seg <- lows[box_top_idx:nh]; ns <- length(seg)
  box_bottom <- NA_real_
  for (i in seq_len(ns - confirm)) {
    cand <- seg[i]
    if (cand == 0) next
    win <- seg[(i + 1L):(i + confirm)]
    if (length(win) == confirm && all(win > cand)) { box_bottom <- cand; break }
  }
  if (is.na(box_bottom)) {
    valid <- seg[seg > 0]
    box_bottom <- if (length(valid) > 0) min(valid) else NA_real_
  }
  if (is.na(box_bottom))
    return(list(signal = "NO_BOX", box_top = round(box_top, 2), box_bottom = NA))

  # Step 3: classify
  signal <- if (current > box_top) "BREAKOUT_BUY" else
            if (current < box_bottom) "BREAKDOWN_SELL" else "IN_BOX"

  box_range     <- box_top - box_bottom
  pos_in_box    <- if (box_range > 0) (current - box_bottom) / box_range * 100 else 0
  upside_to_top <- if (current > 0)  (box_top - current) / current * 100 else 0

  list(signal = signal,
       box_top             = round(box_top,    2),
       box_bottom          = round(box_bottom, 2),
       current_price       = round(current,    2),
       box_range           = round(box_range,  2),
       position_in_box_pct = round(pos_in_box,    1),
       upside_to_top_pct   = round(upside_to_top, 2),
       confirm_days        = confirm,
       data_points         = n)
}

display_darvas_box <- function(result) {
  section("Darvas Box Scan")
  sig    <- result$signal %||% "N/A"
  labels <- c(
    BREAKOUT_BUY      = "* BREAKOUT BUY  -- close above box top",
    BREAKDOWN_SELL    = "* BREAKDOWN SELL -- close below box bottom",
    IN_BOX            = "* IN BOX        -- consolidating",
    NO_BOX            = "No confirmed Darvas box in look-back window",
    INSUFFICIENT_DATA = "Insufficient data"
  )
  cat(sprintf("\n  Signal: %s\n", labels[sig] %||% sig))
  if (!is.na(result$box_top %||% NA)) {
    cat("\n")
    row_print("Box Top",             fmt_simple(result$box_top))
    row_print("Box Bottom",          fmt_simple(result$box_bottom))
    row_print("Current Price",       fmt_simple(result$current_price))
    row_print("Box Range",           fmt_simple(result$box_range))
    row_print("Position in Box",     sprintf("%.1f%%", result$position_in_box_pct %||% 0))
    row_print("Upside to Top",       sprintf("%.2f%%", result$upside_to_top_pct   %||% 0))
    row_print("Confirmation (days)", as.character(result$confirm_days %||% DARVAS_CONFIRM))
    row_print("OHLC bars used",      as.character(result$data_points  %||% "-"))
  } else if (!is.null(result$note)) {
    cat(sprintf("  Note: %s\n", result$note))
  }
}


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN 2 — PIOTROSKI F-SCORE
# ═══════════════════════════════════════════════════════════════════════════════

compute_piotroski_score <- function(symbol) {
  fin <- .get_financials(symbol)
  if (!fin$ok) return(list(symbol = symbol, error = fin$error %||% "Fetch failed"))

  is_m <- fin$IS; bs_m <- fin$BS; cf_m <- fin$CF
  if (is.null(is_m)) return(list(symbol = symbol, error = "No income statement"))

  scores  <- integer(9); names(scores) <- paste0("F", 1:9)
  details <- list()

  ni0  <- .get_row(is_m, "Net Income",   col = 1L)
  ta0  <- .get_row(bs_m, "Total Assets", col = 1L)
  ni1  <- .get_row(is_m, "Net Income",   col = 2L)
  ta1  <- .get_row(bs_m, "Total Assets", col = 2L)
  roa0 <- if (!is.na(ni0) && !is.na(ta0) && ta0 != 0) ni0 / ta0 else NA_real_
  roa1 <- if (!is.na(ni1) && !is.na(ta1) && ta1 != 0) ni1 / ta1 else NA_real_

  scores["F1"] <- if (!is.na(roa0) && roa0 > 0) 1L else 0L
  details$ROA_current_pct <- if (!is.na(roa0)) round(roa0 * 100, 2) else "N/A"

  ocf0 <- .get_row(cf_m, "Operating Cash Flow", "Total Cash From Operating", col = 1L)
  scores["F2"] <- if (!is.na(ocf0) && ocf0 > 0) 1L else 0L
  details$OCF_current_M <- if (!is.na(ocf0)) round(ocf0 / 1e6, 1) else "N/A"

  scores["F3"] <- if (!is.na(roa0) && !is.na(roa1) && roa0 > roa1) 1L else 0L
  details$ROA_prev_pct <- if (!is.na(roa1)) round(roa1 * 100, 2) else "N/A"

  scores["F4"] <- if (!is.na(ocf0) && !is.na(ta0) && !is.na(roa0) &&
                       ta0 > 0 && (ocf0 / ta0) > roa0) 1L else 0L

  ltd0  <- .get_row(bs_m, "Long.term Debt", "Long Term Debt", col = 1L) %|0% 0
  ltd1  <- .get_row(bs_m, "Long.term Debt", "Long Term Debt", col = 2L) %|0% 0
  lev0  <- if (!is.na(ta0) && ta0 > 0) ltd0 / ta0 else NA_real_
  lev1  <- if (!is.na(ta1) && ta1 > 0) ltd1 / ta1 else NA_real_
  scores["F5"] <- if (!is.na(lev0) && !is.na(lev1) && lev0 < lev1) 1L else 0L
  details$LTD_ratio_curr_pct <- if (!is.na(lev0)) round(lev0 * 100, 2) else "N/A"

  ca0  <- .get_row(bs_m, "Total Current Assets",      "Current Assets",      col = 1L)
  cl0  <- .get_row(bs_m, "Total Current Liabilities", "Current Liabilities", col = 1L)
  ca1  <- .get_row(bs_m, "Total Current Assets",      "Current Assets",      col = 2L)
  cl1  <- .get_row(bs_m, "Total Current Liabilities", "Current Liabilities", col = 2L)
  cr0  <- if (!is.na(ca0) && !is.na(cl0) && cl0 > 0) ca0 / cl0 else NA_real_
  cr1  <- if (!is.na(ca1) && !is.na(cl1) && cl1 > 0) ca1 / cl1 else NA_real_
  scores["F6"] <- if (!is.na(cr0) && !is.na(cr1) && cr0 > cr1) 1L else 0L
  details$CurrentRatio_curr <- if (!is.na(cr0)) round(cr0, 2) else "N/A"

  sh0  <- .get_row(bs_m, "Share Issued", "Shares", col = 1L)
  sh1  <- .get_row(bs_m, "Share Issued", "Shares", col = 2L)
  scores["F7"] <- if (!is.na(sh0) && !is.na(sh1)) (if (sh0 <= sh1) 1L else 0L) else 1L
  details$Shares_curr_M <- if (!is.na(sh0)) round(sh0 / 1e6, 1) else "N/A"

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
    F1 = "F1  ROA > 0",                F2 = "F2  Operating Cash Flow > 0",
    F3 = "F3  ROA improving YoY",      F4 = "F4  Earnings cash-backed",
    F5 = "F5  Long-term debt ratio down",
    F6 = "F6  Current ratio up",       F7 = "F7  No new shares issued",
    F8 = "F8  Gross margin up",        F9 = "F9  Asset turnover up"
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
# SCAN 3 — COFFEE CAN (US-ADAPTED)
# ═══════════════════════════════════════════════════════════════════════════════

compute_coffee_can <- function(symbol) {
  fin <- .get_financials(symbol)
  if (!fin$ok) return(list(symbol = symbol, error = fin$error %||% "Fetch failed"))

  is_m <- fin$IS; bs_m <- fin$BS; cf_m <- fin$CF
  if (is.null(is_m)) return(list(symbol = symbol, error = "No income statement"))

  criteria <- integer(6); names(criteria) <- paste0("C", 1:6)
  details  <- list()

  # C1: Revenue CAGR > 10%
  revs <- .get_series(is_m, "Total Revenue")
  if (length(revs) >= 2) {
    yrs  <- length(revs) - 1L
    cagr <- if (revs[length(revs)] > 0)
              ((revs[1] / revs[length(revs)]) ^ (1 / yrs) - 1) * 100 else NA_real_
    criteria["C1"] <- if (!is.na(cagr) && cagr > 10) 1L else 0L
    details$Revenue_CAGR_pct <- if (!is.na(cagr)) round(cagr, 2) else "N/A"
    details$Revenue_years    <- yrs
  } else {
    details$Revenue_CAGR_pct <- "N/A"
  }

  # C2: ROE > 15% avg
  ni_s <- .get_series(is_m, "Net Income")
  eq_s <- .get_series(bs_m, "Total Equity", "Stockholders Equity", "Total Stockholder Equity")
  roe_list <- numeric(0)
  for (i in seq_len(min(length(ni_s), length(eq_s)))) {
    if (eq_s[i] > 0) roe_list <- c(roe_list, ni_s[i] / eq_s[i] * 100)
  }
  if (length(roe_list) > 0) {
    avg_roe <- mean(roe_list)
    criteria["C2"]  <- if (avg_roe > 15) 1L else 0L
    details$ROE_avg_pct <- round(avg_roe, 2)
    details$ROE_min_pct <- round(min(roe_list), 2)
  } else {
    details$ROE_avg_pct <- "N/A"
  }

  # C3: D/E < 1
  ltd_s <- .get_series(bs_m, "Long.term Debt", "Long Term Debt")
  de    <- NA_real_
  if (length(ltd_s) > 0 && length(eq_s) > 0 && abs(eq_s[1]) > 0) {
    de_raw <- ltd_s[1] / abs(eq_s[1])
    de <- if (abs(de_raw) > 10) de_raw / 100 else de_raw
  }
  criteria["C3"]        <- if (!is.na(de) && de < 1) 1L else 0L
  details$Debt_to_Equity <- if (!is.na(de)) round(de, 2) else "N/A"

  # C4: Market Cap >= $1B
  mcap <- tryCatch({
    q <- getQuote(symbol, what = yahooQF("Market Capitalization"))
    as.numeric(q[["Market Capitalization"]])
  }, error = function(e) NA_real_)
  criteria["C4"]     <- if (!is.na(mcap) && mcap >= 1e9) 1L else 0L
  details$Market_Cap  <- fmt_large(mcap)

  # C5: No loss year
  if (length(ni_s) > 0) {
    criteria["C5"]     <- if (all(ni_s > 0)) 1L else 0L
    details$Loss_years  <- sum(ni_s <= 0)
    details$Years_analysed <- length(ni_s)
  }

  # C6: Free Cash Flow > 0 (US bonus criterion)
  fcf_s <- .get_series(cf_m, "Free Cash Flow")
  if (length(fcf_s) > 0) {
    criteria["C6"]       <- if (fcf_s[1] > 0) 1L else 0L
    details$FCF_latest_M  <- round(fcf_s[1] / 1e6, 1)
  } else {
    ocf_s   <- .get_series(cf_m, "Operating Cash Flow", "Total Cash From Operating")
    capex_s <- .get_series(cf_m, "Capital Expenditure", "Capital Expenditures")
    if (length(ocf_s) > 0 && length(capex_s) > 0) {
      fcf <- ocf_s[1] - abs(capex_s[1])
      criteria["C6"]       <- if (fcf > 0) 1L else 0L
      details$FCF_latest_M  <- round(fcf / 1e6, 1)
    } else {
      details$FCF_latest_M <- "N/A"
    }
  }

  total    <- sum(criteria)
  max_pts  <- length(criteria)
  qualifies <- total == max_pts

  list(symbol = symbol, qualifies = qualifies,
       score = sprintf("%d/%d", total, max_pts),
       criteria = criteria, details = details)
}

display_coffee_can <- function(result) {
  section("Coffee Can Portfolio Screen (US)")
  if (!is.null(result$error)) { cat("  Warning:", result$error, "\n"); return(invisible()) }
  badge <- if (result$qualifies) "[QUALIFIES]" else "[DOES NOT QUALIFY]"
  cat(sprintf("\n  Result: %s   (%s criteria met)\n", badge, result$score))
  cat("\n  -- Criteria --\n")
  labels <- c(
    C1 = "C1  Revenue CAGR > 10%",   C2 = "C2  ROE > 15% (avg)",
    C3 = "C3  Debt/Equity < 1",      C4 = "C4  Market Cap >= $1B",
    C5 = "C5  No loss-making year",  C6 = "C6  Free Cash Flow > 0"
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


# ── Report display helpers ────────────────────────────────────────────────────

display_price_summary <- function(quote, symbol) {
  section("Live Quote & Valuation")
  ltp  <- quote$ltp
  prev <- quote$prev_close
  chg  <- if (!is.null(ltp) && !is.null(prev)) ltp - prev else NA
  chg_pct <- if (!is.na(chg) && !is.null(prev) && prev > 0) chg / prev * 100 else NA

  row_print("Symbol",          symbol)
  if (!is.null(quote$name)) row_print("Company", quote$name)
  cat("\n")
  row_print("Last Price",     fmt_simple(ltp))
  row_print("Prev Close",     fmt_simple(prev))
  row_print("Change",         sprintf("%s  %s", fmt_simple(chg), pct_str(chg_pct)))
  row_print("Open",           fmt_simple(quote$open))
  row_print("Day High",       fmt_simple(quote$day_high))
  row_print("Day Low",        fmt_simple(quote$day_low))
  cat("\n")
  row_print("52-Week High",   fmt_simple(quote$w52_high))
  row_print("52-Week Low",    fmt_simple(quote$w52_low))
  row_print("Volume",         if (!is.null(quote$volume))
            formatC(as.numeric(quote$volume), format = "d", big.mark = ",") else "N/A")
  row_print("Avg Vol (3m)",   if (!is.null(quote$avg_volume))
            formatC(as.numeric(quote$avg_volume), format = "d", big.mark = ",") else "N/A")
  row_print("Market Cap",     fmt_large(quote$market_cap))
  cat("\n")
  row_print("Trailing P/E",   fmt_simple(quote$trailing_pe, prefix = ""))
  row_print("Forward P/E",    fmt_simple(quote$forward_pe,  prefix = ""))
  row_print("EPS (TTM)",      fmt_simple(quote$eps))
  row_print("Dividend Yield", if (!is.null(quote$div_yield) && !is.na(quote$div_yield))
            sprintf("%.2f%%", as.numeric(quote$div_yield) * 100) else "N/A")
  row_print("Beta",           fmt_simple(quote$beta,         prefix = ""))
  row_print("Analyst Target", fmt_simple(quote$target_price))
}

display_corporate_actions <- function(acts) {
  section("Corporate Actions")
  divs   <- acts$dividends
  splits <- acts$splits
  if (!is.null(divs) && nrow(divs) > 0) {
    cat("  Recent Dividends:\n")
    for (i in seq_len(nrow(divs)))
      cat(sprintf("    * %s  %s per share\n", divs$date[i], fmt_simple(divs$amount[i])))
  } else cat("  No recent dividends.\n")
  if (!is.null(splits) && nrow(splits) > 0) {
    cat("  Recent Splits:\n")
    for (i in seq_len(nrow(splits)))
      cat(sprintf("    * %s  %.0f:1 split\n", splits$date[i], splits$ratio[i]))
  }
}

display_historical_summary <- function(df) {
  section("Historical Summary (Last 30 Days)")
  if (is.null(df) || nrow(df) == 0) { cat("  No historical data.\n"); return(invisible()) }
  closes <- as.numeric(df$close); closes <- closes[!is.na(closes)]
  if (length(closes) < 2) { cat("  Insufficient data.\n"); return(invisible()) }
  ret_pct <- (closes[length(closes)] - closes[1]) / closes[1] * 100
  row_print("Trading days", as.character(length(closes)))
  row_print("Period High",  fmt_simple(max(closes)))
  row_print("Period Low",   fmt_simple(min(closes)))
  row_print("Avg Close",    fmt_simple(mean(closes)))
  row_print("Std Dev",      fmt_simple(sd(closes)))
  row_print("Period Return", pct_str(ret_pct))
}


# ── Main report ───────────────────────────────────────────────────────────────

run <- function(symbol, show_options = FALSE, output_format = "text",
                run_scans = FALSE) {
  symbol <- toupper(trimws(symbol))
  w <- 60L
  cat(sprintf("\n%s\n  US STOCK REPORT -- %s\n  Generated: %s\n%s\n",
              strrep("=", w), symbol,
              format(Sys.time(), "%d %b %Y  %H:%M:%S"), strrep("=", w)))

  all_data <- list(symbol = symbol, timestamp = format(Sys.time(), "%Y-%m-%dT%H:%M:%S"))

  quote   <- fetch_quote(symbol)
  hist_df <- fetch_historical(symbol, period_days = 30L)
  acts    <- fetch_dividends_splits(symbol)

  darvas_r    <- list(); piotroski_r <- list(); coffee_r <- list()
  if (run_scans) {
    cat("  Running quantitative scans ...\n")
    hist_6mo    <- fetch_historical(symbol, period_days = 180L)
    darvas_r    <- compute_darvas_box(hist_6mo)
    piotroski_r <- compute_piotroski_score(symbol)
    coffee_r    <- compute_coffee_can(symbol)
  }

  if (output_format == "text") {
    display_price_summary(quote, symbol)
    display_corporate_actions(acts)
    display_historical_summary(hist_df)
    if (run_scans) {
      display_darvas_box(darvas_r)
      display_piotroski_score(piotroski_r)
      display_coffee_can(coffee_r)
    }
    cat(sprintf("\n%s\n  Data: Yahoo Finance via quantmod + tidyquant\n%s\n",
                strrep("=", w), strrep("=", w)))
  } else if (output_format == "json") {
    all_data$quote    <- quote
    all_data$history  <- if (nrow(hist_df) > 0) hist_df else list()
    all_data$actions  <- acts
    if (run_scans)
      all_data$scans  <- list(darvas = darvas_r, piotroski = piotroski_r, coffee_can = coffee_r)
    out <- file.path(OUTPUT_DIR, sprintf("%s_report_%s.json", symbol, format(Sys.Date(), "%Y%m%d")))
    write_json(all_data, out, pretty = TRUE, auto_unbox = TRUE)
    cat(sprintf("  JSON saved -> %s\n", out))
  }

  invisible(all_data)
}


# ── Batch report ──────────────────────────────────────────────────────────────
# Uses furrr (parallel purrr) when available for significant speedup.
# Falls back to sequential purrr if furrr is not installed.

run_batch <- function(symbols = NULL, show_options = FALSE,
                      output_format = "json", run_scans = FALSE,
                      parallel = TRUE, n_workers = 4L) {
  targets <- symbols %||% DOW_JONES_30
  w <- 60L
  cat(sprintf("\n%s\n  US BATCH REPORT -- %d stocks\n  Started: %s\n%s\n",
              strrep("#", w), length(targets),
              format(Sys.time(), "%d %b %Y  %H:%M:%S"), strrep("#", w)))

  # ── Parallel processing with furrr (major speedup over sequential) ──────────
  # Python version sleeps 1.5s between stocks; here we process in parallel
  # with up to n_workers concurrent connections.
  use_parallel <- parallel && requireNamespace("furrr", quietly = TRUE)

  if (use_parallel) {
    library(furrr)
    future::plan(future::multisession, workers = min(n_workers, length(targets)))
    cat(sprintf("  Parallel mode: %d workers\n\n", min(n_workers, length(targets))))
    map_fn <- function(...) furrr::future_map(..., .progress = TRUE)
  } else {
    if (parallel) message("  furrr not installed; running sequentially. Install with: install.packages('furrr')")
    map_fn <- purrr::map
  }

  results <- map_fn(targets, function(sym) {
    tryCatch(
      run(sym, show_options = show_options, output_format = output_format,
          run_scans = run_scans),
      error = function(e) {
        message(sprintf("  FAILED: %s -- %s", sym, conditionMessage(e)))
        list(symbol = sym, error = conditionMessage(e))
      }
    )
  })
  names(results) <- targets

  if (use_parallel) future::plan(future::sequential)

  # Write summary XLSX
  .write_summary_xlsx_us(results, include_scans = run_scans)

  failed <- names(Filter(function(d) !is.null(d$error), results))
  cat(sprintf("\n%s\n  Done. %d OK, %d failed.\n",
              strrep("#", w), length(results) - length(failed), length(failed)))
  if (length(failed) > 0) cat(sprintf("  Failed: %s\n", paste(failed, collapse = ", ")))
  cat(strrep("#", w), "\n")

  invisible(results)
}

.write_summary_xlsx_us <- function(results, include_scans = FALSE) {
  rows <- lapply(results, function(d) {
    if (is.null(d) || !is.null(d$error)) return(NULL)
    q    <- d$quote %||% list()
    ltp  <- suppressWarnings(as.numeric(q$ltp  %||% NA))
    prev <- suppressWarnings(as.numeric(q$prev_close %||% NA))
    chg_pct <- if (!is.na(ltp) && !is.na(prev) && prev > 0)
                 round((ltp - prev) / prev * 100, 2) else NA_real_
    base <- data.frame(
      Symbol        = d$symbol %||% "",
      Name          = q$name   %||% "",
      Price         = ltp,
      Change_pct    = chg_pct,
      MarketCap     = suppressWarnings(as.numeric(q$market_cap  %||% NA)),
      TrailingPE    = suppressWarnings(as.numeric(q$trailing_pe %||% NA)),
      ForwardPE     = suppressWarnings(as.numeric(q$forward_pe  %||% NA)),
      Beta          = suppressWarnings(as.numeric(q$beta        %||% NA)),
      DivYield_pct  = if (!is.null(q$div_yield) && !is.na(q$div_yield))
                        round(as.numeric(q$div_yield) * 100, 3) else NA_real_,
      AnalystTarget = suppressWarnings(as.numeric(q$target_price %||% NA)),
      W52High       = suppressWarnings(as.numeric(q$w52_high    %||% NA)),
      W52Low        = suppressWarnings(as.numeric(q$w52_low     %||% NA)),
      Timestamp     = d$timestamp %||% "",
      stringsAsFactors = FALSE
    )
    if (include_scans) {
      scans <- d$scans %||% list()
      darv  <- scans$darvas     %||% list()
      piofr <- scans$piotroski  %||% list()
      coff  <- scans$coffee_can %||% list()
      base$Darvas_Signal    <- darv$signal                                     %||% NA
      base$Darvas_BoxTop    <- suppressWarnings(as.numeric(darv$box_top      %||% NA))
      base$Darvas_BoxBottom <- suppressWarnings(as.numeric(darv$box_bottom   %||% NA))
      base$Piotroski_Score  <- suppressWarnings(as.integer(piofr$f_score     %||% NA))
      base$CoffeeCan        <- if (!is.null(coff$qualifies)) ifelse(coff$qualifies, "YES", "NO") else NA
      base$CoffeeCan_Score  <- coff$score %||% NA
    }
    base
  })
  df <- do.call(rbind, Filter(Negate(is.null), rows))
  if (is.null(df) || nrow(df) == 0) return(invisible())

  if (!requireNamespace("openxlsx", quietly = TRUE)) {
    message("  openxlsx not installed. Run: install.packages('openxlsx')")
    tag <- if (include_scans) "us_scan" else "us_batch"
    out <- file.path(OUTPUT_DIR, sprintf("%s_summary_%s.csv", tag, format(Sys.Date(), "%Y%m%d")))
    write.csv(df, out, row.names = FALSE)
    cat(sprintf("\n  Fallback CSV -> %s\n", out)); return(invisible())
  }
  library(openxlsx)

  hdr_style  <- createStyle(fontColour = "#FFFFFF", fgFill = "#2E4057",
                             halign = "CENTER", textDecoration = "Bold", border = "Bottom")
  buy_style  <- createStyle(fgFill = "#C6EFCE", fontColour = "#276221")
  sell_style <- createStyle(fgFill = "#FFC7CE", fontColour = "#9C0006")
  box_style  <- createStyle(fgFill = "#FFEB9C", fontColour = "#9C5700")

  wb  <- createWorkbook()
  tag <- if (include_scans) "us_scan" else "us_batch"
  out <- file.path(OUTPUT_DIR, sprintf("%s_summary_%s.xlsx", tag, format(Sys.Date(), "%Y%m%d")))

  .add_sheet <- function(name, data, tab_colour = "#2E4057") {
    addWorksheet(wb, name, tabColour = tab_colour)
    if (is.null(data) || nrow(data) == 0) {
      writeData(wb, name, data.frame(Note = "No data")); return(invisible())
    }
    writeData(wb, name, data, headerStyle = hdr_style)
    freezePane(wb, name, firstRow = TRUE)
    setColWidths(wb, name, cols = seq_len(ncol(data)), widths = "auto")
    if ("Darvas_Signal" %in% names(data)) {
      for (i in seq_len(nrow(data))) {
        sig <- data$Darvas_Signal[i]
        if (!is.na(sig)) {
          sty <- if (sig == "BREAKOUT_BUY") buy_style else
                 if (sig == "BREAKDOWN_SELL") sell_style else box_style
          addStyle(wb, name, sty, rows = i + 1L, cols = seq_len(ncol(data)), gridExpand = TRUE)
        }
      }
    }
    if ("Piotroski_Score" %in% names(data)) {
      pc <- which(names(data) == "Piotroski_Score")
      for (i in seq_len(nrow(data))) {
        fs <- suppressWarnings(as.integer(data$Piotroski_Score[i]))
        if (!is.na(fs)) {
          sty <- if (fs >= 7) buy_style else if (fs >= 4) box_style else sell_style
          addStyle(wb, name, sty, rows = i + 1L, cols = pc)
        }
      }
    }
  }

  .add_sheet("All Results", df)

  if (include_scans) {
    if ("Darvas_Signal" %in% names(df))
      .add_sheet("Darvas Breakouts",
                 df[!is.na(df$Darvas_Signal) & df$Darvas_Signal == "BREAKOUT_BUY", ],
                 "#276221")
    if ("Piotroski_Score" %in% names(df)) {
      df$Piotroski_Score <- suppressWarnings(as.integer(df$Piotroski_Score))
      st <- df[!is.na(df$Piotroski_Score) & df$Piotroski_Score >= 7, ]
      .add_sheet("Piotroski Strong", st[order(-st$Piotroski_Score), ], "#1F6AA5")
    }
    if ("CoffeeCan" %in% names(df))
      .add_sheet("Coffee Can", df[!is.na(df$CoffeeCan) & df$CoffeeCan == "YES", ], "#7B2D8B")
    if (all(c("Darvas_Signal","Piotroski_Score","CoffeeCan") %in% names(df))) {
      tr <- df[!is.na(df$Darvas_Signal)   & df$Darvas_Signal == "BREAKOUT_BUY" &
               !is.na(df$Piotroski_Score) & df$Piotroski_Score >= 7 &
               !is.na(df$CoffeeCan)       & df$CoffeeCan == "YES", ]
      .add_sheet("Triple Hits", tr, "#C00000")
    }
  }

  # Summary stats
  addWorksheet(wb, "Summary Stats", tabColour = "#888888")
  stats <- data.frame(
    Metric = c("Run Date", "Total Stocks"),
    Value  = c(format(Sys.Date(), "%Y-%m-%d"), nrow(df)),
    stringsAsFactors = FALSE
  )
  if (include_scans && "Darvas_Signal" %in% names(df)) {
    triple_count <- if (all(c("Piotroski_Score","CoffeeCan") %in% names(df)))
      sum(!is.na(df$Darvas_Signal)   & df$Darvas_Signal == "BREAKOUT_BUY" &
          !is.na(df$Piotroski_Score) & df$Piotroski_Score >= 7 &
          !is.na(df$CoffeeCan)       & df$CoffeeCan == "YES") else 0
    stats <- rbind(stats, data.frame(
      Metric = c("Darvas BREAKOUT_BUY","Darvas IN_BOX","Darvas BREAKDOWN_SELL",
                 "Piotroski STRONG (>=7)","Coffee Can QUALIFIES","Triple Hits"),
      Value  = c(sum(df$Darvas_Signal=="BREAKOUT_BUY",  na.rm=TRUE),
                 sum(df$Darvas_Signal=="IN_BOX",         na.rm=TRUE),
                 sum(df$Darvas_Signal=="BREAKDOWN_SELL", na.rm=TRUE),
                 sum(!is.na(df$Piotroski_Score) & df$Piotroski_Score >= 7),
                 sum(df$CoffeeCan=="YES", na.rm=TRUE),
                 triple_count),
      stringsAsFactors = FALSE
    ))
  }
  writeData(wb, "Summary Stats", stats, headerStyle = hdr_style)
  setColWidths(wb, "Summary Stats", cols = 1:2, widths = c(36, 16))

  saveWorkbook(wb, out, overwrite = TRUE)
  cat(sprintf("\n  Summary XLSX -> %s\n", out))
  invisible(out)
}

run_scans_only <- function(symbol) {
  symbol <- toupper(trimws(symbol))
  cat(sprintf("\n%s\n  SCANS -- %s\n%s\n", strrep("=", 60), symbol, strrep("=", 60)))
  hist_6mo <- fetch_historical(symbol, period_days = 180L)
  darv     <- compute_darvas_box(hist_6mo)
  piotr    <- compute_piotroski_score(symbol)
  coff     <- compute_coffee_can(symbol)
  display_darvas_box(darv)
  display_piotroski_score(piotr)
  display_coffee_can(coff)
  invisible(list(symbol = symbol, darvas = darv, piotroski = piotr, coffee_can = coff))
}


# ── Example usage (uncomment to run) ─────────────────────────────────────────
# run("AAPL")
# run("NVDA", run_scans = TRUE)
# run_batch(symbols = DOW_JONES_30)
# run_batch(symbols = NASDAQ_50, run_scans = TRUE, n_workers = 4)
