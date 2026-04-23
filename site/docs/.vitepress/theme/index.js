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
      // Make every content image clickable-to-zoom. Skip images explicitly
      // marked .no-zoom and the small inline badges/icons.
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
