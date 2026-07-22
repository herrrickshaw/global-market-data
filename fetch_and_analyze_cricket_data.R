# =============================================================================
# Cricket Predictability Analysis — Data Acquisition via {cricketdata}
# =============================================================================
# Purpose:
#   Pull canonical, complete match-level data for the IPL and both ICC men's
#   World Cups (ODI + T20) directly from Cricsheet and ESPNCricinfo, using the
#   {cricketdata} R package (https://github.com/robjhyndman/cricketdata).
#
#   This replaces the GitHub-mirror datasets used in an earlier pass of this
#   analysis (which were necessary because the sandbox that produced them
#   could not reach cricsheet.org or espncricinfo.com directly). Run this
#   script on a machine with normal internet access.
#
# Requirements:
#   install.packages(c("cricketdata", "dplyr", "tidyr", "stringr", "readr",
#                       "lubridate", "ggplot2"))
#   (cricketdata depends on: cli, dplyr, jsonlite, lubridate, readr, rvest,
#    stringr, tibble, tidyr, xml2 — installed automatically as dependencies)
#
# Output:
#   Five CSV files written to ./output/, ready to re-upload for analysis:
#     ipl_matches.csv
#     odi_world_cup_matches.csv
#     t20_world_cup_matches.csv
#     ipl_pairing_predictability.csv
#     ipl_venue_fortress.csv
#   Plus two summary tables printed to console.
# =============================================================================

library(cricketdata)
library(dplyr)
library(tidyr)
library(stringr)
library(readr)
library(lubridate)

dir.create("output", showWarnings = FALSE)

# -----------------------------------------------------------------------------
# 0. DIAGNOSTIC / ROBUST DOWNLOAD HELPER
#    fetch_cricsheet() can fail with "cannot open the connection" if:
#      (a) a previous failed attempt left a corrupt/0-byte zip in tempdir(),
#          which the package then tries to reuse instead of re-downloading;
#      (b) the default download.file() method has trouble with the HTTPS
#          connection on this machine/R build;
#      (c) something between you and cricsheet.org (proxy, firewall) is
#          blocking or altering the request.
#    This helper downloads the zip itself with clearer error reporting and
#    a forced re-download, then hands the local file path back to you.
# -----------------------------------------------------------------------------
robust_fetch_cricsheet <- function(competition, gender = "male", type = "match") {
  destfile_name <- paste0(competition, "_", gender, "_csv2.zip")
  url <- paste0("https://cricsheet.org/downloads/", destfile_name)
  local_path <- file.path(tempdir(), destfile_name)

  # Always force a fresh download — clears out any stale/corrupt file from
  # a previous failed attempt, which is the most common cause of this error.
  if (file.exists(local_path)) {
    cat("Removing stale cached file:", local_path, "\n")
    unlink(local_path)
  }

  cat("Downloading:", url, "\n")
  status <- tryCatch(
    download.file(url, local_path, mode = "wb", method = "libcurl", quiet = FALSE),
    error = function(e) {
      cat("download.file() raised an error:\n  ", conditionMessage(e), "\n")
      return(NA)
    }
  )

  if (is.na(status) || !file.exists(local_path)) {
    stop(
      "Download failed before a local file was created.\n",
      "Things to check:\n",
      "  1. Can you open this URL in a normal web browser right now? ", url, "\n",
      "  2. Are you behind a corporate VPN/proxy/firewall that might block cricsheet.org?\n",
      "  3. Try running: download.file('", url, "', 'test.zip', mode='wb', method='libcurl')\n",
      "     directly in your R console and see what error appears.\n"
    )
  }

  file_size <- file.info(local_path)$size
  cat("Downloaded file size:", file_size, "bytes\n")

  if (is.na(file_size) || file_size < 1000) {
    # A real Cricsheet zip is at minimum tens of KB; anything under ~1KB is
    # almost certainly an error page or empty response, not real data.
    first_lines <- tryCatch(readLines(local_path, n = 5, warn = FALSE), error = function(e) "")
    stop(
      "Downloaded file is suspiciously small (", file_size, " bytes) — ",
      "this is very likely an error page, not a real zip file.\n",
      "First lines of the file:\n  ", paste(first_lines, collapse = "\n  "), "\n",
      "This usually means the URL is wrong, the file has moved, or a proxy ",
      "intercepted the request. Check ", url, " in a browser to confirm it's a working link.\n"
    )
  }

  # Confirm it's actually a valid zip before handing off to fetch_cricsheet's
  # internal unzip logic, so any failure is caught here with a clear message
  # instead of surfacing later as a cryptic "cannot open the connection".
  valid_zip <- tryCatch({
    unzip(local_path, list = TRUE)
    TRUE
  }, error = function(e) FALSE)

  if (!valid_zip) {
    stop(
      "The downloaded file is not a valid zip archive (failed unzip() test).\n",
      "File path: ", local_path, " (", file_size, " bytes)\n",
      "Try deleting it and re-downloading manually in a browser to inspect what came through.\n"
    )
  }

  cat("Zip file verified OK. Proceeding with fetch_cricsheet()...\n\n")

  # Now that we know a good zip is sitting in tempdir() under the exact name
  # fetch_cricsheet() expects, calling it will skip its own (fragile) download
  # step — its `if (!file.exists(destfile))` check will see our verified file
  # and use it directly.
  fetch_cricsheet(type = type, gender = gender, competition = competition)
}

# -----------------------------------------------------------------------------
# 1. FETCH IPL MATCH DATA (Cricsheet)
# -----------------------------------------------------------------------------
cat("Fetching IPL match data from Cricsheet...\n")
ipl_matches <- robust_fetch_cricsheet(competition = "ipl", gender = "male", type = "match")

cat("IPL matches fetched:", nrow(ipl_matches), "\n")
cat("Seasons covered:", paste(range(ipl_matches$season, na.rm = TRUE), collapse = " to "), "\n\n")

write_csv(ipl_matches, "output/ipl_matches.csv")

# -----------------------------------------------------------------------------
# 2. FETCH ICC MEN'S WORLD CUP DATA (Cricsheet)
#    NOTE: Cricsheet bundles the ODI World Cup and T20 World Cup together
#    under a single "cup" archive for each gender. We split them apart below
#    using the `event` field, which names the specific tournament.
# -----------------------------------------------------------------------------
cat("Fetching ICC Men's World Cup match data from Cricsheet (ODI + T20 combined)...\n")
cup_matches <- robust_fetch_cricsheet(competition = "cup", gender = "male", type = "match")

cat("Total ICC men's World Cup matches fetched:", nrow(cup_matches), "\n")

# Inspect the actual event names present, so the filter below stays correct
# even if Cricsheet's naming changes slightly across seasons.
cat("Distinct event names found:\n")
print(sort(unique(cup_matches$event)))

# Split by event name. Cricsheet typically labels these:
#   "ICC World Cup"        -> ODI World Cup (older editions sometimes just "World Cup")
#   "ICC Men's T20 World Cup" / "ICC World Twenty20" -> T20 World Cup
odi_world_cup <- cup_matches %>%
  filter(str_detect(event, regex("T20|Twenty20", ignore_case = TRUE)) == FALSE)

t20_world_cup <- cup_matches %>%
  filter(str_detect(event, regex("T20|Twenty20", ignore_case = TRUE)) == TRUE)

cat("\nODI World Cup matches:", nrow(odi_world_cup),
    "| seasons:", paste(range(odi_world_cup$season, na.rm = TRUE), collapse = "-"), "\n")
cat("T20 World Cup matches:", nrow(t20_world_cup),
    "| seasons:", paste(range(t20_world_cup$season, na.rm = TRUE), collapse = "-"), "\n\n")

write_csv(odi_world_cup, "output/odi_world_cup_matches.csv")
write_csv(t20_world_cup, "output/t20_world_cup_matches.csv")

# -----------------------------------------------------------------------------
# 3. OPTIONAL: FETCH SUPPLEMENTARY CAREER STATS FROM ESPNCRICINFO (Statsguru)
#    Useful for player-level context (e.g. weighting predictability by squad
#    strength), not required for the match-outcome predictability analysis
#    itself. Commented out by default since it adds runtime; uncomment to use.
# -----------------------------------------------------------------------------
# cat("Fetching ESPNCricinfo T20I men's batting career data...\n")
# t20i_batting <- fetch_cricinfo(matchtype = "t20", sex = "men",
#                                 activity = "batting", type = "career")
# write_csv(t20i_batting, "output/espncricinfo_t20i_batting_career.csv")

# -----------------------------------------------------------------------------
# 4. CLEANING — normalize franchise renames and venue name variants
# -----------------------------------------------------------------------------
rename_map <- c(
  "Delhi Daredevils" = "Delhi Capitals",
  "Kings XI Punjab" = "Punjab Kings",
  "Royal Challengers Bangalore" = "Royal Challengers Bengaluru",
  "Rising Pune Supergiant" = "Rising Pune Supergiants"
)

normalize_team <- function(x) {
  ifelse(x %in% names(rename_map), rename_map[x], x)
}

ipl_clean <- ipl_matches %>%
  mutate(
    team1 = normalize_team(team1),
    team2 = normalize_team(team2),
    winner = normalize_team(winner),
    toss_winner = normalize_team(toss_winner)
  ) %>%
  filter(!is.na(winner), winner != "") # keep only decisive matches

cat("IPL matches after cleaning (decisive only):", nrow(ipl_clean), "\n\n")

# -----------------------------------------------------------------------------
# 5. PREDICTABILITY SCORE
#    score = 1 - H(p), where p = win share of the more successful side in a
#    pairing (or at a venue), and H is binary entropy. Ranges 0 (coin flip)
#    to 1 (one side has won every meeting). Minimum sample size enforced to
#    avoid small-sample noise.
# -----------------------------------------------------------------------------
binary_entropy <- function(p) {
  ifelse(p <= 0 | p >= 1, 0, -(p * log2(p) + (1 - p) * log2(1 - p)))
}
predictability_score <- function(p) 1 - binary_entropy(p)

pairing_predictability <- function(df, min_matches = 3) {
  df <- df %>%
    rowwise() %>%
    mutate(pairing = paste(sort(c(team1, team2)), collapse = " vs ")) %>%
    ungroup()

  df %>%
    group_by(pairing) %>%
    filter(n() >= min_matches) %>%
    count(winner, name = "wins", pairing) %>%
    group_by(pairing) %>%
    mutate(n_matches = sum(wins)) %>%
    slice_max(wins, n = 1, with_ties = FALSE) %>%
    ungroup() %>%
    mutate(
      dominance_pct = round(100 * wins / n_matches, 1),
      predictability_score = round(predictability_score(wins / n_matches), 3)
    ) %>%
    rename(dominant_side = winner, dominant_wins = wins) %>%
    select(pairing, n_matches, dominant_side, dominant_wins, dominance_pct, predictability_score) %>%
    arrange(desc(predictability_score), desc(n_matches))
}

ipl_pairing_pred <- pairing_predictability(ipl_clean, min_matches = 3)
t20wc_pairing_pred <- pairing_predictability(
  t20_world_cup %>% filter(!is.na(winner), winner != ""),
  min_matches = 3
)
odiwc_pairing_pred <- pairing_predictability(
  odi_world_cup %>% filter(!is.na(winner), winner != ""),
  min_matches = 3
)

cat("=== Top 10 most predictable IPL pairings (min 3 meetings) ===\n")
print(head(ipl_pairing_pred, 10))

cat("\n=== Top 10 most predictable T20 World Cup pairings (min 3 meetings) ===\n")
print(head(t20wc_pairing_pred, 10))

cat("\n=== Top 10 most predictable ODI World Cup pairings (min 3 meetings) ===\n")
print(head(odiwc_pairing_pred, 10))

weighted_predictability <- function(pred_df) {
  sum(pred_df$predictability_score * pred_df$n_matches) / sum(pred_df$n_matches)
}

cat("\n=== Overall match-weighted predictability (0 = coinflip, 1 = certain) ===\n")
cat("IPL:           ", round(weighted_predictability(ipl_pairing_pred), 3), "\n")
cat("T20 World Cup: ", round(weighted_predictability(t20wc_pairing_pred), 3), "\n")
cat("ODI World Cup: ", round(weighted_predictability(odiwc_pairing_pred), 3), "\n\n")

write_csv(ipl_pairing_pred, "output/ipl_pairing_predictability.csv")
write_csv(t20wc_pairing_pred, "output/t20wc_pairing_predictability.csv")
write_csv(odiwc_pairing_pred, "output/odiwc_pairing_predictability.csv")

# -----------------------------------------------------------------------------
# 6. VENUE "FORTRESS" EFFECT (IPL)
#    For each team and venue with sufficient sample, compare win rate at that
#    venue vs win rate everywhere else. Large positive lift = home advantage;
#    large negative lift = a ground that genuinely seems to disadvantage them.
# -----------------------------------------------------------------------------
venue_fortress <- function(df, min_at_venue = 8, min_elsewhere = 10) {
  teams <- union(unique(df$team1), unique(df$team2))
  results <- list()

  for (team in teams) {
    team_matches <- df %>% filter(team1 == team | team2 == team) %>%
      mutate(team_won = winner == team)

    venues <- unique(team_matches$venue)
    for (v in venues) {
      at_venue <- team_matches %>% filter(venue == v)
      elsewhere <- team_matches %>% filter(venue != v)
      if (nrow(at_venue) < min_at_venue || nrow(elsewhere) < min_elsewhere) next

      results[[length(results) + 1]] <- tibble(
        team = team,
        venue = v,
        matches_at_venue = nrow(at_venue),
        win_pct_at_venue = round(100 * mean(at_venue$team_won), 1),
        win_pct_elsewhere = round(100 * mean(elsewhere$team_won), 1)
      )
    }
  }

  bind_rows(results) %>%
    mutate(lift_pct_pts = round(win_pct_at_venue - win_pct_elsewhere, 1)) %>%
    arrange(desc(lift_pct_pts))
}

ipl_fortress <- venue_fortress(ipl_clean)

cat("=== Top 10 IPL home-fortress effects (win rate at venue minus elsewhere) ===\n")
print(head(ipl_fortress, 10))

cat("\n=== Top 10 IPL venue disadvantages ===\n")
print(tail(ipl_fortress, 10))

write_csv(ipl_fortress, "output/ipl_venue_fortress.csv")

# -----------------------------------------------------------------------------
# 7. TOSS IMPACT (IPL) — sanity check against the earlier mirror-data finding
# -----------------------------------------------------------------------------
toss_win_rate <- mean(ipl_clean$toss_winner == ipl_clean$winner, na.rm = TRUE)
cat("\nIPL toss-winner win rate:", round(100 * toss_win_rate, 1), "% (50% = no effect)\n")

cat("\nDone. CSVs written to ./output/\n")
cat("Re-upload these files to continue the analysis, or proceed locally using\n")
cat("the predictability_score() and venue_fortress() functions defined above.\n")
