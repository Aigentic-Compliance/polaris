# Put your own documents here -- for local testing only

Drop a real policy PDF here to run it through the pipeline on your own
machine (`scripts/ingest_local_document.py`). This folder is **gitignored**
(see `.gitignore` at repo root) and must stay that way.

Why it's gitignored rather than just "please don't commit real files":
C3.8 in the WP1 Target Capability & Acceptance Framework is explicit --
"Originals never redistributed (no raw third-party files in code
repositories or shared drives outside the raw store)." A real scraped or
customer-supplied policy PDF sitting inside a git repo is exactly the
thing that rule forbids, regardless of intent. Anything you drop here for
a quick local test should never end up in a commit.

This is NOT the real "raw store" the framework describes (C4.1 -- a
content-addressed, write-once object store, separate from the operational
database and separate from code). It's a dev convenience for running the
pipeline against a real file on your laptop before that infrastructure
exists. Don't build automation that assumes this folder is durable or
production-facing.
