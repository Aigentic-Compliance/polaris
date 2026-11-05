# Synthetic pack fixtures -- NOT REAL PACK-GFT DATA

`benchmark_objects_synthetic.json` and `customer_objects_synthetic.json` in
this folder are invented for this build, shaped correctly against the
PolicyObject contract, but they are NOT extracted from PACK-GFT v1.4.0,
any real Aigentic pack, or any real customer policy. Aigentic's actual pack
content was never shared with this build and must not be reconstructed or
guessed at.

These exist only to prove `polaris/recommender/comparator.py` runs
end-to-end and produces sensible ALIGNED/STRONGER/WEAKER/MISSING/DIFFERENT/
EXTRA/NOT_COMPARABLE output. The moment Mada exports real objects.json from
PACK-GFT v1.4.0 (the agreed interim bootstrap target), swap these files for
the real export -- nothing else in polaris/recommender needs to change,
since it is written against the contract, not against this fixture.
