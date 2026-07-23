"""Productionized HTTP API for the human-in-the-loop gel review widget.

Reuses the same tested `neband.core`/`neband.purity.analysis` calls as the
disposable local prototype (`scripts/hitl_ui_server.py`) -- see GH issue #1
(https://github.com/nebiolabs/neband/issues/1) for the full design and
rationale. No algorithm changes live here; this package is glue (HTTP
routes, image-cache bookkeeping) the same way the prototype server was.
"""
