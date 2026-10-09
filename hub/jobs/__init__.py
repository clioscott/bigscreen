"""Fetch jobs. Each job is a module with `run(cfg) -> dict` and is executed by the
scheduler as its own short-lived process (`python -m hub.jobs.<name>`), so a
leaking library (yfinance, google-api) can never accumulate memory across runs.
"""
