#!/usr/bin/env Rscript
#
# Derive a threshold override file from DQD's own defaults.
#
#   Rscript scripts/make_thresholds.R
#
# WHY THIS EXISTS
#
# DQD's default `plausibleValueLow` for 52 date fields is '19500101' -- any
# clinical date before 1950 counts as implausible. That is a sensible default
# for a contemporary EHR extract, and wrong for this dataset: Synthea generates
# complete lifetimes, so a patient born in 1916 has genuine conditions,
# procedures and observation periods decades before 1950.
#
# Six checks fail on that basis alone. Their data is correct; the threshold
# does not apply.
#
# The floor is therefore moved to '18500101' -- a value DQD itself already uses
# for other fields, and comfortably before the earliest plausible birth in any
# living-memory dataset. Dates before 1850 remain implausible and will still be
# flagged, so the check keeps its teeth.
#
# NOTHING ELSE IS CHANGED. This file is regenerated from DQD's defaults rather
# than hand-edited, so upstream threshold changes are picked up automatically
# and the only local deviation is the one documented above.

DATE_FLOOR_FROM <- "'19500101'"
DATE_FLOOR_TO   <- "'18500101'"

out_dir <- "config"
dir.create(out_dir, showWarnings = FALSE)

src <- system.file("csv", "OMOP_CDMv5.4_Field_Level.csv",
                   package = "DataQualityDashboard")
stopifnot(nzchar(src))

f <- read.csv(src, stringsAsFactors = FALSE, colClasses = "character")

affected <- which(f$plausibleValueLow == DATE_FLOOR_FROM)
cat("fields carrying the", DATE_FLOOR_FROM, "floor:", length(affected), "\n")

f$plausibleValueLow[affected] <- DATE_FLOOR_TO

# Record the reason in DQD's own notes column, so it travels with the file and
# shows up in the dashboard rather than living only in this script.
note <- paste("Floor moved from 1950 to 1850 for this dataset: Synthea",
              "generates complete lifetimes, so pre-1950 clinical dates are",
              "genuine. See scripts/make_thresholds.R.")
if ("plausibleValueLowNotes" %in% names(f)) {
  f$plausibleValueLowNotes[affected] <- note
}

dest <- file.path(out_dir, "field_level_thresholds.csv")
write.csv(f, dest, row.names = FALSE, na = "")

cat("wrote", normalizePath(dest), "\n")
cat("derived from", src, "\n")
cat("\nUse it with:  Rscript scripts/run_dqd.R\n")
