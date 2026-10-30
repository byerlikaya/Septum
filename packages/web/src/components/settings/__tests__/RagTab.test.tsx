import { fireEvent, render, screen } from "@testing-library/react";
import { RagTab } from "../RagTab";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

const baseSettings = {
  chunk_size: 1000,
  chunk_overlap: 100,
  top_k_retrieval: 5,
  rag_relevance_threshold: 0.3,
  pdf_chunk_size: 800,
  audio_chunk_size: 600,
  spreadsheet_chunk_size: 500
};

function setup(overrides: Partial<typeof baseSettings> = {}, onChangeImpl?: () => unknown) {
  const onChange = jest.fn().mockImplementation(async () => onChangeImpl?.() ?? null);
  render(
    <RagTab
      settings={{ ...baseSettings, ...overrides } as never}
      onChange={onChange}
      isSaving={() => false}
    />
  );
  const fieldFor = (labelKey: string) =>
    screen.getByText(labelKey).parentElement!.querySelector("input")!;
  const commit = (labelKey: string, value: string) => {
    const input = fieldFor(labelKey);
    fireEvent.change(input, { target: { value } });
    fireEvent.blur(input);
    return input;
  };
  return { onChange, fieldFor, commit };
}

describe("RagTab chunk limits", () => {
  it("exposes native min and max on the chunk inputs", () => {
    const { fieldFor } = setup();
    const size = fieldFor("settings.rag.defaultChunkSize.label");
    expect(size).toHaveAttribute("min", "100");
    expect(size).toHaveAttribute("max", "20000");
    const overlap = fieldFor("settings.rag.chunkOverlap.label");
    expect(overlap).toHaveAttribute("min", "0");
    expect(overlap).toHaveAttribute("max", "500");
    expect(fieldFor("settings.rag.pdfChunkSize.label")).toHaveAttribute("min", "800");
    expect(fieldFor("settings.rag.audioChunkSize.label")).toHaveAttribute("min", "1");
    expect(fieldFor("settings.rag.spreadsheetChunkSize.label")).toHaveAttribute("min", "1");
  });

  it("clamps an oversized chunk size before sending", () => {
    const { onChange, commit } = setup();
    const input = commit("settings.rag.defaultChunkSize.label", "999999");
    expect(onChange).toHaveBeenCalledWith("chunk_size", 20000);
    expect(input).toHaveValue(20000);
  });

  it("raises a chunk size below the floor to 100", () => {
    const { onChange, commit } = setup({ chunk_overlap: 40 });
    commit("settings.rag.defaultChunkSize.label", "50");
    expect(onChange).toHaveBeenCalledWith("chunk_size", 100);
  });

  it("raises a PDF chunk size below the floor to 800", () => {
    const { onChange, commit } = setup();
    commit("settings.rag.pdfChunkSize.label", "100");
    expect(onChange).toHaveBeenCalledWith("pdf_chunk_size", 800);
  });

  it("clamps audio and spreadsheet sizes into 1..20000", () => {
    const { onChange, commit } = setup();
    commit("settings.rag.audioChunkSize.label", "0");
    expect(onChange).toHaveBeenLastCalledWith("audio_chunk_size", 1);
    commit("settings.rag.spreadsheetChunkSize.label", "30000");
    expect(onChange).toHaveBeenLastCalledWith("spreadsheet_chunk_size", 20000);
  });

  it("clamps chunk overlap to half of the current chunk size", () => {
    const { onChange, commit } = setup();
    commit("settings.rag.chunkOverlap.label", "900");
    expect(onChange).toHaveBeenCalledWith("chunk_overlap", 500);
    commit("settings.rag.chunkOverlap.label", "-5");
    expect(onChange).toHaveBeenLastCalledWith("chunk_overlap", 0);
  });

  it("caps chunk overlap at 10000 for the largest chunk size", () => {
    const { onChange, commit } = setup({ chunk_size: 20000 });
    commit("settings.rag.chunkOverlap.label", "15000");
    expect(onChange).toHaveBeenCalledWith("chunk_overlap", 10000);
  });

  it("sends in-range values unchanged", () => {
    const { onChange, commit } = setup();
    commit("settings.rag.defaultChunkSize.label", "1500");
    expect(onChange).toHaveBeenCalledWith("chunk_size", 1500);
  });

  it("lowers the overlap before the chunk size when the new size would refuse it", () => {
    const { onChange, commit } = setup({ chunk_size: 1000, chunk_overlap: 400 });
    commit("settings.rag.defaultChunkSize.label", "300");
    return Promise.resolve().then(() => Promise.resolve()).then(() => {
      expect(onChange.mock.calls).toEqual([
        ["chunk_overlap", 150],
        ["chunk_size", 300]
      ]);
    });
  });

  it("does not send the chunk size when lowering the overlap failed", async () => {
    const { onChange, commit } = setup(
      { chunk_size: 1000, chunk_overlap: 400 },
      () => new Error("422")
    );
    commit("settings.rag.defaultChunkSize.label", "300");
    await Promise.resolve();
    await Promise.resolve();
    expect(onChange.mock.calls).toEqual([["chunk_overlap", 150]]);
  });

  it("sends only the chunk size when the stored overlap still fits", async () => {
    const { onChange, commit } = setup({ chunk_size: 1000, chunk_overlap: 100 });
    commit("settings.rag.defaultChunkSize.label", "300");
    await Promise.resolve();
    expect(onChange.mock.calls).toEqual([["chunk_size", 300]]);
  });
});
