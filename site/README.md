# Septum docs site

VitePress site that publishes the canonical markdown under `/docs/` to
[byerlikaya.github.io/Septum](https://byerlikaya.github.io/Septum/).

No content lives here — every page is a symlink into the repo-root
`/docs/` folder. Files named `*.md` land in the English locale (site
root), files named `*.tr.md` land in the Turkish locale (`/tr/`). Images
referenced with GitHub-relative paths (`../assets/foo.gif`) are rewritten
at build time so they resolve to VitePress's public dir (which itself
symlinks to the repo-root `/assets/`).

## Requirements

- Node.js **22 LTS** (newer versions may work but 22 is the tested floor)

## Local development

```bash
cd site
npm install           # first time
npm run dev           # http://localhost:5173/Septum/
```

Edit any file under `/docs/*.md` and the dev server hot-reloads within
seconds.

## Production build

```bash
npm run build         # emits site/docs/.vitepress/dist/
npm run preview       # serves that dist locally for a final sanity check
```

## Deploy

`main` branch auto-deploys via `.github/workflows/docs-site.yml`
whenever `docs/`, `site/`, `assets/`, or the workflow itself changes.
