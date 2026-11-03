"""Project-wide constants. Change REPO_SLUG once if the GitHub repository is renamed."""

__version__ = "0.1.0"

REPO_SLUG = "Jaudat150/global-debt-explorer"
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO_SLUG}/{BRANCH}/data/exports"
REPO_URL = f"https://github.com/{REPO_SLUG}"
SITE_URL = f"https://{REPO_SLUG.split('/')[0].lower()}.github.io/{REPO_SLUG.split('/')[1]}/"
