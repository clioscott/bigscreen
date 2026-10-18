# Regenerating the README media

`tools/readme.py` renders every screen with the display's own code into `docs/media/` and rewrites the README's
generated sections. Run it on the server after a change to how anything looks:

```bash
cd ~/bigscreen   # or a checkout of your branch
~/.venvs/bigscreen-tools/bin/python -m tools.readme all                 # every group, then the text
~/.venvs/bigscreen-tools/bin/python -m tools.readme media alerts bc     # only some groups
python -m tools.readme text                                             # text only (any machine)
```

Groups: `wall` (emulated display, synthetic calendar and tickers), `alerts`, `half`, `bc`, `eew`, `maps` and `panel`
(a second hub instance on the sandbox, screenshotted with Playwright).

## Privacy

`media` builds a sandbox state dir (`~/bigscreen-state/cache/readme-sandbox`) instead of using the live one:

- the config is copied with calendars, title rules and tickers replaced (`scrub_config`);
- the calendar and client calls are synthetic (`SAMPLE_EVENTS`), and so are the quotes (`SAMPLE_TICKERS`);
- alert and BC screens render their built-in samples, labelled SAMPLE;
- only public data is copied in: the forecast, headlines, radar/satellite packs and basemaps.

Check the renders before committing them anyway.

## The tools venv (once per server)

The panel screenshots need Playwright, which stays out of the hub's venv:

```bash
python3 -m venv ~/.venvs/bigscreen-tools
~/.venvs/bigscreen-tools/bin/pip install -r ~/bigscreen/requirements-hub.txt pytest playwright
~/.venvs/bigscreen-tools/bin/playwright install chromium
```

Without Playwright, `media` skips the panel group and keeps the previous screenshots.
