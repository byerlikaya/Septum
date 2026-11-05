/**
 * Validates a message from the embedding page. Returns the parsed command, or
 * null for anything that is not a well-formed document or page command.
 */
export function parseParentMessage(data) {
  if (typeof data !== "object" || data === null) return null;
  if (data.type === "document" && data.data instanceof ArrayBuffer) {
    return { type: "document", data: data.data };
  }
  if (data.type === "page" && Number.isInteger(data.page) && data.page >= 1) {
    return { type: "page", page: data.page };
  }
  return null;
}
