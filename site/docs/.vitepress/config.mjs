import { defineConfig } from "vitepress";

// Header carries every page. Sidebar is disabled — the top nav is the
// single navigator.
const navEn = [
  { text: "Home", link: "/" },
  { text: "Installation", link: "/installation" },
  { text: "Benchmark", link: "/benchmark" },
  { text: "Features", link: "/features" },
  { text: "Architecture", link: "/architecture" },
  { text: "Document Ingestion", link: "/document-ingestion" },
  { text: "Screenshots", link: "/screenshots" },
];

const navTr = [
  { text: "Ana Sayfa", link: "/tr/" },
  { text: "Kurulum", link: "/tr/installation" },
  { text: "Benchmark", link: "/tr/benchmark" },
  { text: "Özellikler", link: "/tr/features" },
  { text: "Mimari", link: "/tr/architecture" },
  { text: "Doküman İşleme", link: "/tr/document-ingestion" },
  { text: "Ekran Görüntüleri", link: "/tr/screenshots" },
];

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
  // frontmatter.
  transformPageData(pageData) {
    if (pageData.frontmatter.aside === undefined) {
      pageData.frontmatter.aside = "left";
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

      const rewrite = (raw, env) => {
        if (!raw) return raw;
        if (/^https?:|^mailto:|^data:/.test(raw)) return raw;
        if (raw.startsWith("#") || raw.startsWith("/")) return raw;

        const [pathPart, hashPart = ""] = raw.split("#", 2);
        const hash = hashPart ? `#${hashPart}` : "";
        const isTr = (env?.relativePath ?? "").startsWith("tr/");
        const localePrefix = isTr ? "/tr" : "";

        // ../assets/foo.svg → /foo.svg (served from public/)
        const asset = pathPart.match(/^(?:\.\.\/)+assets\/(.+)$/);
        if (asset) return `/${asset[1]}${hash}`;

        // ../packages/x/...md → GitHub blob
        const pkg = pathPart.match(/^(?:\.\.\/)+packages\/(.+)$/);
        if (pkg) return `${GH_BLOB}/packages/${pkg[1]}${hash}`;

        // ../README.{,tr.}md → locale root
        if (/^(?:\.\.\/)+README(?:\.tr)?\.md$/.test(pathPart))
          return `${localePrefix}/${hash}`;

        // ../{CHANGELOG,CONTRIBUTING,LICENSE,…}{,.md} → GitHub blob
        const rootMd = pathPart.match(
          /^(?:\.\.\/)+([A-Z][A-Z_\-]*(?:\.tr)?)(\.md)?$/,
        );
        if (rootMd)
          return `${GH_BLOB}/${rootMd[1]}${rootMd[2] ?? ""}${hash}`;

        // Sibling docs FEATURES.md / FEATURES.tr.md → /features/
        const sibling = pathPart.match(/^([A-Z][A-Z_\-]*)(?:\.tr)?\.md$/);
        if (sibling) {
          const slug = sibling[1].toLowerCase().replace(/_/g, "-");
          return `${localePrefix}/${slug}${hash}`;
        }

        return raw;
      };

      const patchRule = (name, attr) => {
        const prev = md.renderer.rules[name];
        md.renderer.rules[name] = (tokens, idx, opts, env, self) => {
          const t = tokens[idx];
          const i = t.attrIndex(attr);
          if (i >= 0) t.attrs[i][1] = rewrite(t.attrs[i][1], env);
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
        tok.content = tok.content
          .replace(/src="([^"]+)"/g, (_, v) => `src="${rewrite(v, env)}"`)
          .replace(/href="([^"]+)"/g, (_, v) => `href="${rewrite(v, env)}"`);
        return prevHtmlBlock
          ? prevHtmlBlock(tokens, idx, opts, env, self)
          : tok.content;
      };

      const prevHtmlInline = md.renderer.rules.html_inline;
      md.renderer.rules.html_inline = (tokens, idx, opts, env, self) => {
        const tok = tokens[idx];
        tok.content = tok.content
          .replace(/src="([^"]+)"/g, (_, v) => `src="${rewrite(v, env)}"`)
          .replace(/href="([^"]+)"/g, (_, v) => `href="${rewrite(v, env)}"`);
        return prevHtmlInline
          ? prevHtmlInline(tokens, idx, opts, env, self)
          : tok.content;
      };
    },
  },

  head: [
    ["link", { rel: "icon", type: "image/png", href: "/Septum/septum_logo.png" }],
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
      },
    },
  },

  themeConfig: {
    logo: { src: "/septum_logo.png", alt: "Septum" },
    siteTitle: false,
    socialLinks: [
      { icon: "github", link: "https://github.com/byerlikaya/Septum" },
    ],
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
    editLink: {
      pattern: "https://github.com/byerlikaya/Septum/edit/main/docs/:path",
      text: "Edit this page on GitHub",
    },
    footer: {
      message: "Released under the MIT License.",
      copyright: "Copyright © Barış Yerlikaya",
    },
  },

  vite: {
    resolve: {
      // The mirrored markdown files under site/docs/ are symlinks into
      // the repo-root /docs/. Preserve the logical path so Vite resolves
      // imports (vue, etc.) against site/node_modules, not the repo root.
      preserveSymlinks: true,
    },
  },
});
