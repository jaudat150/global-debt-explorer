## What changes for users

## How it was tested
- [ ] `python -m unittest discover -s tests` passes
- [ ] New indicators: `python -m src.indicator_data show <id>` returns data, and `docs/DATA_SOURCES.md` is regenerated
- [ ] Every new number carries its source, series code and year; missing values are not shown as zero
