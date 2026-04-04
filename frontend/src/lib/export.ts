/**
 * Trigger a file download in the browser.
 */
export function downloadJSON(data: unknown, filename: string): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function downloadChatPDF(
  messages: { role: string; content: string }[],
  title: string,
  filename: string
): Promise<void> {
  const { jsPDF } = await import("jspdf");
  const doc = new jsPDF({ unit: "mm", format: "a4" });
  const pageWidth = doc.internal.pageSize.getWidth();
  const margin = 15;
  const usable = pageWidth - margin * 2;
  let y = 20;

  doc.setFontSize(16);
  doc.text(title, margin, y);
  y += 8;
  doc.setFontSize(8);
  doc.setTextColor(120);
  doc.text(`Exported ${new Date().toISOString()}`, margin, y);
  y += 10;
  doc.setTextColor(0);

  for (const msg of messages) {
    const label = msg.role === "user" ? "You" : "Assistant";
    doc.setFontSize(9);
    doc.setFont("helvetica", "bold");
    const labelLines = doc.splitTextToSize(`${label}:`, usable);
    if (y + 6 > doc.internal.pageSize.getHeight() - 15) {
      doc.addPage();
      y = 15;
    }
    doc.text(labelLines, margin, y);
    y += labelLines.length * 4 + 2;

    doc.setFont("helvetica", "normal");
    doc.setFontSize(9);
    const lines = doc.splitTextToSize(msg.content, usable);
    for (const line of lines) {
      if (y > doc.internal.pageSize.getHeight() - 15) {
        doc.addPage();
        y = 15;
      }
      doc.text(line, margin, y);
      y += 4;
    }
    y += 4;
  }

  doc.save(filename);
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
