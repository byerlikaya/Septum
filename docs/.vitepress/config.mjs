import { defineConfig } from "vitepress";

// Header carries every page. Sidebar is disabled — the top nav is the
// single navigator.
// Header nav: every doc page in reading order. Contributing + Changelog
// were pulled out — they live in the footer instead — so the bar stays
// a single row.  flatNav* mirrors navEn/navTr for prev/next pager logic
// (kept as separate constants in case header nav diverges later).
const navEn = [
  { text: "Home", link: "/" },
  { text: "Installation", link: "/installation" },
  { text: "Benchmark", link: "/benchmark" },
  { text: "Features", link: "/features" },
  { text: "Workflows", link: "/workflows" },
  { text: "Use Cases", link: "/use-cases" },
  { text: "Architecture", link: "/architecture" },
  { text: "Document Ingestion", link: "/document-ingestion" },
  { text: "Screenshots", link: "/screenshots" },
  { text: "Contribute", link: "/contributing" },
];

const flatNavEn = [
  ...navEn,
  { text: "Contributing", link: "/contributing" },
  { text: "Changelog", link: "/changelog" },
];

const navTr = [
  { text: "Ana Sayfa", link: "/tr/" },
  { text: "Kurulum", link: "/tr/installation" },
  { text: "Benchmark", link: "/tr/benchmark" },
  { text: "Özellikler", link: "/tr/features" },
  { text: "Akışlar", link: "/tr/workflows" },
  { text: "Senaryolar", link: "/tr/use-cases" },
  { text: "Mimari", link: "/tr/architecture" },
  { text: "Doküman İşleme", link: "/tr/document-ingestion" },
  { text: "Ekran Görüntüleri", link: "/tr/screenshots" },
  { text: "Katkıda Bulun", link: "/tr/contributing" },
];

const flatNavTr = [
  ...navTr,
  { text: "Katkı", link: "/tr/contributing" },
  { text: "Changelog", link: "/tr/changelog" },
];

const footerMessageEn =
  '<a href="/Septum/changelog">Changelog</a>' +
  '&nbsp;·&nbsp;Released under the MIT License.';

const footerMessageTr =
  '<a href="/Septum/tr/changelog">Changelog</a>' +
  '&nbsp;·&nbsp;MIT Lisansı altında yayımlanmıştır.';


export default defineConfig({
  title: "Septum",
  description:
    "Privacy-first AI middleware — anonymize documents, chat with any LLM, no raw PII leaves your machine.",
  lang: "en-US",
  base: "/Septum/",
  cleanUrls: true,
  lastUpdated: true,
  // Markdown in docs/ also lives on GitHub, where links like
  // FEATURES.md and ../README.md make sense. Those won't always resolve
  // inside VitePress — we warn on them rather than fail.
  ignoreDeadLinks: true,

  // Default the per-page outline (Bu sayfada / On this page) to the
  // left column, mirroring the request to keep navigation on the left.
  // Pages can override with `aside: false` or `aside: right` in their
  // frontmatter. Also wires up the bottom-of-page Prev / Next buttons
  // by deriving them from the locale's nav order, so every doc page
  // shows where the reader came from and where to go next.
  transformPageData(pageData) {
    if (pageData.frontmatter.aside === undefined) {
      pageData.frontmatter.aside = "left";
    }

    const order = pageData.relativePath.startsWith("tr/")
      ? flatNavTr
      : flatNavEn;
    const slug = "/" + pageData.relativePath
      .replace(/\.md$/, "")
      .replace(/(^|\/)index$/, "$1");
    const idx = order.findIndex((item) => item.link === slug || item.link === slug + "/");
    if (idx !== -1) {
      const prev = order[idx - 1];
      const next = order[idx + 1];
      if (prev && pageData.frontmatter.prev === undefined) {
        pageData.frontmatter.prev = { text: prev.text, link: prev.link };
      }
      if (next && pageData.frontmatter.next === undefined) {
        pageData.frontmatter.next = { text: next.text, link: next.link };
      }
    }
  },

  // The canonical markdown lives in /docs/ and is also rendered by
  // GitHub. There it relies on GitHub-relative paths and on manual
  // `<p align="center">…</p>` nav bars at the top + bottom. VitePress
  // already exposes the sidebar nav, and asset paths use a different
  // resolution scheme — the markdown-it hook below normalises both at
  // build time so the same source files render correctly in both views.
  markdown: {
    config(md) {
      const GH_BLOB = "https://github.com/byerlikaya/Septum/blob/main";
      const BASE = "/Septum";  // matches `base` above; raw <a href> tags
                                // bypass VitePress' router and need the
                                // full base baked in so the browser
                                // doesn't navigate to host root.

      // Rewriter outputs base-LESS URLs (e.g. `/installation`,
      // `/tr/installation`). VitePress' markdown link/image rules
      // prepend the configured `base` automatically at runtime; the
      // `annotateHtml` helper prepends BASE manually for raw HTML
      // tags (which bypass VitePress' router). Returning a pre-baked
      // BASE here would double-prefix to `/Septum/Septum/...`.
      const rewrite = (raw, env) => {
        if (!raw) return raw;
        if (/^https?:|^mailto:|^data:/.test(raw)) return raw;
        if (raw.startsWith("#") || raw.startsWith("/")) return raw;

        const [pathPart, hashPart = ""] = raw.split("#", 2);
        const hash = hashPart ? `#${hashPart}` : "";

        // assets/foo.svg or ../assets/foo.svg → /foo.svg (public dir).
        const asset = pathPart.match(/^(?:\.\.\/)*assets\/(.+)$/);
        if (asset) return `/${asset[1]}${hash}`;

        // packages/x/... or ../packages/x/... → GitHub blob
        const pkg = pathPart.match(/^(?:\.\.\/)*packages\/(.+)$/);
        if (pkg) return `${GH_BLOB}/packages/${pkg[1]}${hash}`;

        // README{,.tr}.md → locale root determined BY THE LINK ITSELF,
        // not the current page. Lets the cross-language badges
        // ("English" on the TR page, "Türkçe" on the EN page) route to
        // the other locale instead of looping back.
        if (/^(?:\.\.\/)*README(?:\.tr)?\.md$/.test(pathPart)) {
          const targetIsTr = /\.tr\.md$/i.test(pathPart);
          return `${targetIsTr ? "/tr" : ""}/${hash}`;
        }

        // CHANGELOG / CONTRIBUTING at the repo root.
        if (/^(?:\.\.\/)*CHANGELOG(?:\.md)?$/i.test(pathPart))
          return `/changelog${hash}`;
        if (/^(?:\.\.\/)*CONTRIBUTING(?:\.tr)?(?:\.md)?$/i.test(pathPart)) {
          const targetIsTr = /\.tr/i.test(pathPart);
          return `${targetIsTr ? "/tr" : ""}/contributing${hash}`;
        }

        // Other root-level .md files (LICENSE, etc.) → GitHub blob
        const rootMd = pathPart.match(
          /^(?:\.\.\/)*([A-Z][A-Z_\-]*(?:\.tr)?)(\.md)?$/,
        );
        if (rootMd)
          return `${GH_BLOB}/${rootMd[1]}${rootMd[2] ?? ""}${hash}`;

        // Repo-root `docs/foo.md` or `docs/tr/foo.md` form used by
        // README/CHANGELOG/CONTRIBUTING (which live at the repo root
        // and link into the docs tree). Sibling `./foo.md` inside the
        // docs tree is handled natively by VitePress.
        const docsRel = pathPart.match(
          /^docs\/(?:tr\/)?([a-z][a-z0-9_\-]*)\.md$/,
        );
        if (docsRel) {
          const targetIsTr = /^docs\/tr\//.test(pathPart);
          return `${targetIsTr ? "/tr" : ""}/${docsRel[1]}${hash}`;
        }

        return raw;
      };

      const patchRule = (name, attr) => {
        const prev = md.renderer.rules[name];
        md.renderer.rules[name] = (tokens, idx, opts, env, self) => {
          const t = tokens[idx];
          const i = t.attrIndex(attr);
          if (i >= 0) t.attrs[i][1] = rewrite(t.attrs[i][1], env);
          // External links open in a new tab so the docs site itself
          // is never replaced by a click on a badge / GitHub link / etc.
          if (
            name === "link_open" &&
            i >= 0 &&
            /^https?:\/\//i.test(t.attrs[i][1])
          ) {
            if (t.attrIndex("target") < 0) t.attrPush(["target", "_blank"]);
            if (t.attrIndex("rel") < 0)
              t.attrPush(["rel", "noopener noreferrer"]);
          }
          return prev
            ? prev(tokens, idx, opts, env, self)
            : self.renderToken(tokens, idx, opts);
        };
      };

      patchRule("image", "src");
      patchRule("link_open", "href");

      // Strip the manual `## Table of Contents` / `## İçindekiler`
      // section. VitePress already shows the same outline in the
      // sidebar, so the in-body copy is duplicate noise. We hide the
      // heading itself, the list that follows it, and the trailing
      // <hr/> divider — but keep them in the source markdown so the
      // GitHub render still has its in-page TOC.
      const isTocHeading = (t) => {
        if (!t) return false;
        const norm = t.trim().toLocaleLowerCase("tr");
        return (
          norm === "table of contents" ||
          norm === "içindekiler" ||
          norm === "i̇çindekiler"
        );
      };

      const headingTextOf = (_open, inline) => {
        if (!inline || inline.type !== "inline") return "";
        return inline.children
          ?.filter((c) => c.type === "text" || c.type === "code_inline")
          .map((c) => c.content)
          .join("")
          .trim() ?? "";
      };

      md.core.ruler.push("strip-toc-section", (state) => {
        const tokens = state.tokens;
        const hide = new Set();
        for (let i = 0; i < tokens.length; i++) {
          const tok = tokens[i];
          if (tok.type !== "heading_open" || tok.tag !== "h2") continue;
          const inline = tokens[i + 1];
          const text = headingTextOf(tok, inline);
          if (!isTocHeading(text)) continue;

          // Hide heading_open + inline + heading_close.
          hide.add(i);
          hide.add(i + 1);
          hide.add(i + 2);

          // Hide everything until the next heading_open. Includes the
          // bullet list and any thematic break / blank lines.
          let j = i + 3;
          while (j < tokens.length && tokens[j].type !== "heading_open") {
            hide.add(j);
            j++;
          }

          // Also hide the optional <hr/> immediately preceding the next
          // heading: in our docs, the pattern is `## TOC … --- ## Next`
          // and the dash already got captured above. But the divider
          // BEFORE the TOC heading is harder to spot — leave it; the
          // VitePress sidebar already has its own visual separator.
          i = j - 1;
        }
        state.tokens = tokens.filter((_, idx) => !hide.has(idx));
      });

      // Strip the manual `<p align="center">…</p>` nav bars (top + bottom)
      // and the horizontal rule that flanks them. They carry one of the
      // nav emojis we use in /docs — ordinary centered logos and badge
      // groups don't, so they survive untouched.
      const NAV_EMOJI_RE = /🏠|🚀|📈|✨|🏗️|📊|📸|📝|🤝/;
      const NAV_BLOCK_RE =
        /<p align="center">[\s\S]*?<\/p>/i;

      // Rewriter for raw HTML strings: rewrites src/href URLs AND adds
      // target=_blank + rel=noopener noreferrer to anchors that point
      // to absolute http(s) URLs (badges, social links, dataset links
      // embedded as raw <a> tags).
      // Raw HTML <img> and <a> tags bypass VitePress' router and asset
      // resolution, so absolute asset paths need BASE baked in here.
      // Markdown-it image/link rules don't — VitePress prepends base
      // itself for those.
      const withBase = (url) => {
        if (!url) return url;
        if (/^https?:|^mailto:|^data:|^#/.test(url)) return url;
        if (url.startsWith(BASE + "/") || url === BASE) return url;
        if (url.startsWith("/")) return `${BASE}${url}`;
        return url;
      };

      const annotateHtml = (raw, envIn) =>
        raw
          // Image src attributes only get the rewriter (no withBase).
          // VitePress' asset pipeline scans rendered HTML for `/foo.svg`
          // and resolves it from publicDir at build time, prepending the
          // configured `base` automatically. Pre-baking BASE here makes
          // Rollup treat it as an unresolvable bundled module.
          .replace(
            /src="([^"]+)"/g,
            (_, v) => `src="${rewrite(v, envIn)}"`,
          )
          .replace(/<a\s([^>]*?)href="([^"]+)"([^>]*)>/g, (_m, pre, href, post) => {
            const newHref = withBase(rewrite(href, envIn));
            const isExternal = /^https?:\/\//i.test(newHref);
            const blob = `${pre}href="${newHref}"${post}`;
            if (!isExternal) return `<a ${blob}>`;
            const hasTarget = /\btarget=/.test(blob);
            const hasRel = /\brel=/.test(blob);
            const extra =
              (hasTarget ? "" : ' target="_blank"') +
              (hasRel ? "" : ' rel="noopener noreferrer"');
            return `<a ${blob}${extra}>`;
          });

      const prevHtmlBlock = md.renderer.rules.html_block;
      md.renderer.rules.html_block = (tokens, idx, opts, env, self) => {
        const tok = tokens[idx];
        const looksLikeNav =
          NAV_BLOCK_RE.test(tok.content) && NAV_EMOJI_RE.test(tok.content);
        if (looksLikeNav) {
          // Skip the surrounding <hr/> if present.
          const prev = idx > 0 ? tokens[idx - 1] : null;
          const next = idx + 1 < tokens.length ? tokens[idx + 1] : null;
          if (prev && prev.type === "hr") prev.hidden = true;
          if (next && next.type === "hr") next.hidden = true;
          return "";
        }
        tok.content = annotateHtml(tok.content, env);
        return prevHtmlBlock
          ? prevHtmlBlock(tokens, idx, opts, env, self)
          : tok.content;
      };

      const prevHtmlInline = md.renderer.rules.html_inline;
      md.renderer.rules.html_inline = (tokens, idx, opts, env, self) => {
        const tok = tokens[idx];
        tok.content = annotateHtml(tok.content, env);
        return prevHtmlInline
          ? prevHtmlInline(tokens, idx, opts, env, self)
          : tok.content;
      };
    },
  },

  head: [
    ["link", { rel: "icon", type: "image/svg+xml", href: "/Septum/septum_logo.svg" }],
  ],

  // The canonical markdown lives in /docs/*.md at the repo root. VitePress
  // sees them through symlinks under site/docs/ and site/docs/tr/.
  srcExclude: ["**/README.md"],

  locales: {
    root: {
      label: "English",
      lang: "en",
      themeConfig: {
        nav: navEn,
        outline: { level: [2, 4], label: "On this page" },
        socialLinks: [
          { icon: "github", link: "https://github.com/byerlikaya/Septum" },
        ],
        footer: {
          message: footerMessageEn,
          copyright: "Copyright © Barış Yerlikaya",
        },
      },
    },
    tr: {
      label: "Türkçe",
      lang: "tr",
      themeConfig: {
        nav: navTr,
        outline: { level: [2, 4], label: "Bu sayfada" },
        docFooter: { prev: "Önceki sayfa", next: "Sonraki sayfa" },
        returnToTopLabel: "Başa dön",
        sidebarMenuLabel: "Menü",
        darkModeSwitchLabel: "Tema",
        lightModeSwitchTitle: "Aydınlık temaya geç",
        darkModeSwitchTitle: "Karanlık temaya geç",
        socialLinks: [
          { icon: "github", link: "https://github.com/byerlikaya/Septum" },
        ],
        footer: {
          message: footerMessageTr,
          copyright: "Telif Hakkı © Barış Yerlikaya",
        },
      },
    },
  },

  themeConfig: {
    logo: { src: "/septum_logo.svg", alt: "Septum" },
    siteTitle: false,
    // socialLinks live per-locale (in `locales[…].themeConfig`) so the
    // Contributing icon can point at the locale-correct page.
    search: {
      provider: "local",
      options: {
        locales: {
          tr: {
            translations: {
              button: { buttonText: "Ara", buttonAriaLabel: "Ara" },
              modal: {
                displayDetails: "Ayrıntıları göster",
                resetButtonTitle: "Sıfırla",
                backButtonTitle: "Geri dön",
                noResultsText: "Sonuç bulunamadı",
                footer: {
                  selectText: "seç",
                  navigateText: "dolaş",
                  closeText: "kapat",
                },
              },
            },
          },
        },
      },
    },
  },

  vite: {
    resolve: {
      // README/CHANGELOG/CONTRIBUTING are symlinked into docs/{en,tr}/
      // from the repo root. Preserve the symlinked path so Vite resolves
      // imports (vue, etc.) against docs/node_modules, not repo root.
      preserveSymlinks: true,
    },
    // Use the repo-root /assets directory as VitePress's static dir.
    // Markdown still references images as `../assets/foo.svg`; the
    // markdown-it transform rewrites those to `/Septum/foo.svg`, and
    // Vite serves them from here at runtime.
    publicDir: "../assets",
  },
});
