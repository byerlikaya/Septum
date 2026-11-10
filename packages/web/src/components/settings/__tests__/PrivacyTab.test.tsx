import { render, screen } from "@testing-library/react";
import { PrivacyTab } from "../PrivacyTab";

jest.mock("../../../lib/i18n", () => ({
  useI18n: () => (key: string) => key
}));

const baseSettings = {
  deanon_enabled: true,
  require_approval: false,
  approval_timeout_seconds: 0,
  show_json_output: false,
  use_presidio_layer: true,
  use_ner_layer: true,
  use_ollama_validation_layer: false,
  use_ollama_layer: false,
  use_ollama_semantic_layer: false
};

function renderTab() {
  render(
    <PrivacyTab
      settings={baseSettings as never}
      onChange={jest.fn()}
      isSaving={() => false}
    />
  );
}

describe("PrivacyTab sanitization layers", () => {
  it("does not offer the alias detection toggle", () => {
    renderTab();
    expect(screen.queryByText("settings.privacy.layers.ollama.label")).toBeNull();
  });

  it("describes the contextual detection layer", () => {
    renderTab();
    expect(screen.getByText("settings.privacy.layers.ollamaSemantic.label")).toBeInTheDocument();
    expect(screen.getByText("settings.privacy.layers.ollamaSemantic.description")).toBeInTheDocument();
  });
});
