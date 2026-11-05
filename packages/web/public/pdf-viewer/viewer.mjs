import "./vendor/pdf.worker.min.mjs";
import { getDocument } from "./vendor/pdf.min.mjs";
import { parseParentMessage } from "./protocol.mjs";

const vendorUrl = (directory) => new URL(`./vendor/${directory}/`, import.meta.url).href;
const assetUrls = {
  cMapUrl: vendorUrl("cmaps"),
  iccUrl: vendorUrl("iccs"),
  standardFontDataUrl: vendorUrl("standard_fonts"),
  wasmUrl: vendorUrl("wasm"),
};

const container = document.getElementById("pages");
let loadingTask = null;
let observer = null;

// A hostile PDF controls its page count; bound the placeholders and observers created for it.
const MAX_RENDERED_PAGES = 500;

function notify(type, extra = {}) {
  window.parent.postMessage({ type, ...extra }, "*");
}

function scrollToPage(page) {
  const target = container.children[page - 1]; // beyond the cap there is no element: ignored
  target?.scrollIntoView({ block: "start" });
}

async function renderPage(pdf, pageNumber, element) {
  const page = await pdf.getPage(pageNumber);
  const base = page.getViewport({ scale: 1 });
  element.style.aspectRatio = `${base.width} / ${base.height}`;
  const scale = (element.clientWidth * window.devicePixelRatio) / base.width;
  const viewport = page.getViewport({ scale });
  const canvas = document.createElement("canvas");
  canvas.width = Math.floor(viewport.width);
  canvas.height = Math.floor(viewport.height);
  element.replaceChildren(canvas);
  await page.render({ canvas, viewport }).promise;
}

async function openDocument(data) {
  observer?.disconnect();
  await loadingTask?.destroy();
  container.replaceChildren();

  loadingTask = getDocument({ data, ...assetUrls, enableScripting: false, enableXfa: false });
  const pdf = await loadingTask.promise;

  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        renderPage(pdf, Number(entry.target.dataset.page), entry.target).catch(() =>
          entry.target.classList.add("failed")
        );
      }
    },
    { rootMargin: "200% 0px" }
  );

  const first = (await pdf.getPage(1)).getViewport({ scale: 1 });
  const shownPages = Math.min(pdf.numPages, MAX_RENDERED_PAGES);
  for (let pageNumber = 1; pageNumber <= shownPages; pageNumber += 1) {
    const element = document.createElement("div");
    element.className = "page";
    element.dataset.page = String(pageNumber);
    element.style.aspectRatio = `${first.width} / ${first.height}`;
    container.append(element);
    observer.observe(element);
  }

  notify("loaded", { pageCount: pdf.numPages, shownPages });
}

window.addEventListener("message", (event) => {
  if (event.source !== window.parent) return;
  const command = parseParentMessage(event.data);
  if (command?.type === "document") {
    openDocument(command.data).catch(() => notify("error"));
  } else if (command?.type === "page") {
    scrollToPage(command.page);
  }
});

notify("ready");
