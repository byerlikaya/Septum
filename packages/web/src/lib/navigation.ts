/** Test seam for the browser navigation call. */
export const navigationAdapter = {
  assign: (url: string): void => window.location.assign(url),
};

/**
 * Navigate with a full page load. Used after login/logout and on session
 * expiry so that all in-memory client state is discarded. Only same-origin
 * destinations are accepted; the target is normalised through the URL parser
 * so that browser quirks (backslashes, stripped tabs/newlines) cannot turn an
 * apparent path into an external address.
 */
export function hardNavigate(path: string): void {
  const url = new URL(path, window.location.origin);
  if (!path.startsWith("/") || url.origin !== window.location.origin) {
    throw new Error("hardNavigate only accepts internal paths");
  }
  navigationAdapter.assign(url.href);
}
