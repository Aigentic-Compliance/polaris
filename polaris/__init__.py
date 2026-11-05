"""POLARIS -- Aigentic Compliance benchmark-construction pipeline.

Package layout mirrors the three components in scope for this build:

    polaris.scraper      WP1 -- source acquisition + document-level acceptance (G0)
    polaris.classifier   WP2 -- segmentation + 18-LET classification cascade
    polaris.evaluator    WP6 -- scripted evaluation of classifier output against gold
    polaris.recommender  WP7 -- customer-pack vs benchmark comparator

WP3 (atomic object normalisation/RRL-DRL mapping) and WP4 (benchmark
aggregation) are represented only as the PolicyObject contract needed by the
recommender to have something to compare -- they are not built out here,
per the agreed scope. See README.md, "What this repo does not do".
"""

__version__ = "0.1.0"
