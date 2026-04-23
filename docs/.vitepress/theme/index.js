import DefaultTheme from "vitepress/theme";
import mediumZoom from "medium-zoom";
import { onMounted, onUnmounted, watch, nextTick } from "vue";
import { useRoute } from "vitepress";

import "./custom.css";

export default {
  extends: DefaultTheme,
  setup() {
    const route = useRoute();

    const initZoom = () => {
      // Tag images that should NOT participate in click-to-zoom:
      //  - the homepage logo (decorative)
      //  - badges & navigation thumbnails — i.e. images wrapped in an
      //    <a> that goes anywhere except a same-page anchor. Those
      //    clicks must reach the target page (external URL, sibling
      //    locale README, license file, etc.) — the zoom layer would
      //    intercept the click and break navigation.
      // Same-page anchor diagrams (<a href="#how-it-works"><img></a>)
      // DO get zoom — the user wants to inspect the picture, and the
      // jump-to-section is a minor loss compared to a giant SVG that
      // can't be expanded.
      document
        .querySelectorAll(".vp-doc .home-logo img")
        .forEach((el) => el.classList.add("no-zoom"));
      document.querySelectorAll(".vp-doc a img").forEach((el) => {
        const a = el.closest("a");
        const href = a?.getAttribute("href") ?? "";
        if (!href.startsWith("#")) el.classList.add("no-zoom");
      });

      // Every other content image becomes clickable-to-zoom.
      mediumZoom(".vp-doc img:not(.no-zoom)", {
        background: "var(--vp-c-bg)",
        margin: 32,
      });
    };

    // Keep the sticky TOC visually in sync with the page scroll:
    //  - When the page is near the top, snap the TOC scroll back to 0
    //    so the first headings are visible.
    //  - When the active outline entry leaves the TOC viewport, scroll
    //    the TOC just enough to bring it back into view.
    //  - When the page is near the bottom, scroll the TOC fully down so
    //    the last entries are visible.
    let mo;
    let scrollScheduled = false;
    // Threshold above which the auto-tracking is worth doing. Below
    // this, the outline column easily fits in the viewport and any
    // movement would feel arbitrary.
    const OVERFLOW_LINK_COUNT = 12;

    const syncOutlinePosition = () => {
      if (scrollScheduled) return;
      scrollScheduled = true;
      requestAnimationFrame(() => {
        scrollScheduled = false;
        const container = document.querySelector(".VPDoc .aside-container");
        if (!container) return;

        const links = container.querySelectorAll(
          ".VPDocAsideOutline .outline-link",
        );
        if (links.length < OVERFLOW_LINK_COUNT) {
          if (container.scrollTop !== 0) container.scrollTop = 0;
          return;
        }

        const pageScrollY = window.scrollY || window.pageYOffset || 0;
        const pageHeight = document.documentElement.scrollHeight - window.innerHeight;
        const threshold = 64;

        if (pageScrollY <= threshold) {
          container.scrollTop = 0;
          return;
        }
        if (pageHeight - pageScrollY <= threshold) {
          container.scrollTop = container.scrollHeight;
          return;
        }

        const active = container.querySelector(
          ".VPDocAsideOutline .outline-link.active",
        );
        if (!active) return;

        const cRect = container.getBoundingClientRect();
        const aRect = active.getBoundingClientRect();
        const margin = 24;
        if (aRect.top < cRect.top + margin) {
          container.scrollTop += aRect.top - cRect.top - margin;
        } else if (aRect.bottom > cRect.bottom - margin) {
          container.scrollTop += aRect.bottom - cRect.bottom + margin;
        }
      });
    };

    const observeOutline = () => {
      const aside = document.querySelector(".VPDoc .aside");
      if (!aside) return;
      mo?.disconnect();
      mo = new MutationObserver(syncOutlinePosition);
      mo.observe(aside, { subtree: true, attributes: true, attributeFilter: ["class"] });
    };

    onMounted(() => {
      initZoom();
      observeOutline();
      window.addEventListener("scroll", syncOutlinePosition, { passive: true });
    });
    onUnmounted(() => {
      mo?.disconnect();
      window.removeEventListener("scroll", syncOutlinePosition);
    });
    watch(
      () => route.path,
      () =>
        nextTick(() => {
          initZoom();
          observeOutline();
          syncOutlinePosition();
        }),
    );
  },
};
