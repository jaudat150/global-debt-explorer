# Team playbook

How the maintainers run the Global Debt & Fiscal Atlas: collaboration, releases, search ranking and launch.

## 1. Team

| Role | Person | Owns |
|---|---|---|
| Creator & lead maintainer | Jaudat Faisal Al-Husein ([@Jaudat150](https://github.com/Jaudat150)) | Direction, releases, final review |

New maintainers are added here, in `.github/CODEOWNERS`, in `CITATION.cff` (authors) and in the README team section.
Suggested areas to split across the team:

- **Data** — `src/catalog.py`, `src/indicator_data.py`, `src/validate.py`; new indicators and sources; weekly validation report.
- **App** — `app.py`, `src/*_view.py`, maps; UX, accessibility, translations.
- **Library & API** — `debt_atlas/`; PyPI releases; docs and notebooks.
- **Website, SEO & community** — `src/website.py`; Search Console; launch posts; answering issues.

## 2. GitHub setup (lead, once)

1. **Invite collaborators:** Settings → Collaborators → *Add people* → each GitHub username → role *Write* (or *Maintain* for co-leads).
2. **Protect `main`:** Settings → Branches → *Add branch ruleset* for `main`:
   require a pull request, 1 approving review, review from Code Owners, status checks *Tests* passing; block force pushes.
3. **Labels:** `data`, `bug`, `enhancement`, `good first issue`, `help wanted`, `translation`, `seo`.
4. **Project board:** Projects → *New project* → Board with columns *To do / In progress / Review / Done*; add every roadmap item.
5. **Discussions:** Settings → Features → Discussions (questions and ideas stay out of the issue tracker).

## 3. Daily workflow

```bash
git checkout main && git pull
git checkout -b feature/interest-vs-health      # one branch per change
# ...edit, then:
python -m unittest discover -s tests            # must pass
git add . && git commit -m "Add interest vs health view"
git push -u origin feature/interest-vs-health
```

Open a pull request on GitHub, request a review, and merge after approval and green tests. Never push directly to `main`.

## 4. Releasing (lead)

1. Update `VERSION`, `debt_atlas/_meta.py` (`__version__`), `CITATION.cff` and `CHANGELOG.md`.
2. Merge to `main`, then GitHub → *Releases* → *Draft a new release* → tag `vX.Y.Z` → publish.
3. Automatically: Zenodo archives the release with a DOI; **PyPI** publishes `debt-atlas` (`publish-pypi.yml`).

One-time PyPI setup: create an account on pypi.org → *Your projects* → *Publishing* → *Add a pending publisher*:
project `debt-atlas`, owner `Jaudat150`, repository `global-debt-explorer`, workflow `publish-pypi.yml`, environment `pypi`.
On GitHub: Settings → Environments → *New environment* `pypi`. No token is ever stored.

## 5. Ranking first on Google

Streamlit apps are rendered with JavaScript and index poorly, so search traffic goes to the **static website**
(`src/website.py`): ~285 fast pages — one per country and per indicator — each with a unique title, a description
containing real numbers, schema.org `Dataset` / `FAQPage` / `BreadcrumbList` markup and a sitemap. They target what
people actually search: *"Kenya government debt"*, *"who does Pakistan owe"*, *"military spending by country"*.

**Setup (once):**

1. Settings → Pages → Source: **GitHub Actions**. The site appears at `https://jaudat150.github.io/global-debt-explorer/`.
2. Settings → Secrets and variables → Actions → **Variables**: `APP_URL` = the Streamlit app address.
3. **Google Search Console** (search.google.com/search-console): add the site as a *URL prefix* property → verify with
   *HTML tag* → put the `content` value in the variable `GOOGLE_SITE_VERIFICATION` → re-run *Publish website* → verify →
   *Sitemaps* → submit `sitemap.xml`.
4. **Bing Webmaster Tools**: import from Google Search Console (also feeds DuckDuckGo and ChatGPT search).
5. **Custom domain (strongly recommended):** a short domain such as `debtatlas.org` ranks and gets shared better than a
   github.io address. Buy it, set `SITE_BASE_URL`, add it under Settings → Pages → Custom domain.

**Ongoing:**

- Earn links from relevant places — this matters more than anything on the page:
  - pull requests to curated lists (*awesome-public-datasets*, *awesome-economics*, *awesome-streamlit*);
  - a short write-up on Kaggle Datasets and Hugging Face Datasets, linking back;
  - university course pages and teachers (offer it for class use);
  - journalists covering debt: every article that cites a number with a link helps.
- Publish a data story with every release (blog post, LinkedIn, Reddit) — each one is a new page that links back.
- Watch Search Console monthly: which queries bring visitors, which pages rank 5–20 (improve those first).
- Translations (Arabic, French, Spanish) multiply the searchable pages and face little competition.

## 6. Launch checklist

- [ ] App live on Streamlit Community Cloud; every link in the README works
- [ ] Website live on GitHub Pages; sitemap submitted to Google and Bing
- [ ] Screenshots and a 20-second GIF at the top of the README
- [ ] Release `v3.x` published → Zenodo DOI badge in README and `CITATION.cff`
- [ ] `debt-atlas` on PyPI; `pip install debt-atlas` works on a clean machine
- [ ] Topics, description and website set in the repository's *About*
- [ ] Launch posts: *Show HN*, r/dataisbeautiful, r/economics, LinkedIn — each with one striking chart and the link
- [ ] Every team member shares the launch post and answers comments on day one
- [ ] JOSS paper submitted (joss.theoj.org) after the first stable release
