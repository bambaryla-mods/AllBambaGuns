# Player site

The page at https://bambaryla-mods.github.io/AllBambaGuns/ shows players what the mod adds. It isn't part of the mod: `_PublisherPlus.xml` keeps this folder out of the Workshop upload.

## How it works

`build.py` reads the defs the way RimWorld loads them, twice:

| Mode | Folders | Notes |
|---|---|---|
| vanilla | root, `Launchers` | |
| ce | root, `CE` | a CE def replaces the root def with the same defName |

It resolves inheritance between the mod's own defs and picks up the research projects the BOR patches add. Every stat, icon and research cost on the page comes from there, so the tables follow rebalances by themselves. Other patches (like `CE/Patches/Ultra_Availability.xml`) aren't applied; the page text explains them instead.

The site has a home page and one page per tab in the top bar. The written text lives in `templates/pages/` (one file per page, `index.html` is the home page text) and has to be updated by hand when a mechanic changes. The tables come from `templates/macros.html`; `base.html`, `home.html` and `page.html` are the shell shared with the Armors, Melee and Turrets sites.

The tabs, their order, the home page card text and card icons are in `NAV` and `PAGES` at the top of `build.py`. Family names and order are in `FAMILIES`.

## Building and previewing

```
pip install -r site/requirements.txt
python site/build.py --serve
```

Then open http://localhost:8000. `python site/build.py --dump` prints the extracted data instead.

Pushing to `main` rebuilds and publishes the site through `.github/workflows/pages.yml`. One-time setup on GitHub: **Settings > Pages > Source: GitHub Actions**.
