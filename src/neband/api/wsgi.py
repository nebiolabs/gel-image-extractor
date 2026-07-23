"""Gunicorn entrypoint: `gunicorn --workers 4 'neband.api.wsgi:app'`.

Multiple worker *processes*, not threads (GH issue #1 Fix #2) -- the work
here (`detect_lanes`/`detect_bands`/`calibrate_ladder`) is CPU-bound
`numpy`/`scipy` code that holds the GIL, so threads within one process
would just serialize behind each other anyway. Size `--workers` to
available cores. All app configuration (cache limits, API key, upload
size cap) comes from environment variables here -- see `app.create_app`'s
docstring for which ones.
"""

from neband.api.app import create_app

app = create_app()
