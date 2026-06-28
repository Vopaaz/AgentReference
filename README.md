# AgentReference

GitHub Pages raw hosting:

- Base URL: <https://vopaaz.github.io/AgentReference/>
- Manifest: <https://vopaaz.github.io/AgentReference/index.json>
- Text listing: <https://vopaaz.github.io/AgentReference/index.txt>
- Example file: <https://vopaaz.github.io/AgentReference/text/rolling_one_year_p5_returns.txt>

The Pages artifact is generated from Git-tracked files. Local caches, ignored files,
and uncommitted temporary files are not published.

## Scripts

Run all scripts:

```powershell
python scripts/run_all.py
```

Available standalone scripts:

- `python scripts/rolling_one_year_return_moomoo.py`
- `python scripts/amzn_open_close_avg_p5_moomoo.py`

## Publish

GitHub Actions publishes the site after every push to `master`. To build the same
files locally:

```powershell
python scripts/build_pages.py --output site
```
