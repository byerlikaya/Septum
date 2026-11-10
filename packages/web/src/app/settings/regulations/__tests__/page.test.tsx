import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import RegulationsPage from "../page";

const mockGet = jest.fn();

jest.mock("../../../../lib/language", () => ({
  useLanguage: () => ({ language: "en" })
}));

jest.mock("../../../../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    patch: jest.fn()
  }
}));

jest.mock("../../../../components/settings/CustomRuleBuilderPanel", () => ({
  CustomRuleBuilderPanel: () => null
}));

const ruleset = (overrides: Record<string, unknown>) => ({
  region: "EU / EEA",
  description: null,
  official_url: null,
  entity_types: [],
  is_builtin: true,
  is_active: true,
  custom_notes: null,
  ...overrides
});

function mountWith(rulesets: unknown[]) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/regulations") return Promise.resolve({ data: rulesets });
    return Promise.resolve({ data: [] });
  });
  return render(<RegulationsPage />);
}

beforeEach(() => {
  jest.clearAllMocks();
});

it("shows the translated description for a built-in regulation", async () => {
  mountWith([
    ruleset({
      id: "gdpr",
      display_name: "General Data Protection Regulation",
      description: "Regulation (EU) 2016/679. Personal data: Art. 4(1)."
    })
  ]);

  expect(
    await screen.findByText(
      "Comprehensive data protection regulation for the European Union and EEA."
    )
  ).toBeInTheDocument();
});

it("shows the API description for a ruleset without a translation key, never the raw key", async () => {
  const { container } = mountWith([
    ruleset({
      id: "custom_ruleset",
      display_name: "Custom Ruleset",
      region: "Internal",
      is_builtin: false,
      description: "Organisation-specific entities."
    })
  ]);

  expect(await screen.findByText("Organisation-specific entities.")).toBeInTheDocument();
  expect(container.textContent).not.toContain("regulations.desc.");
});

it("renders no description line for an unknown id without an API description", async () => {
  const { container } = mountWith([
    ruleset({
      id: "unknown_regulation",
      display_name: "Unknown Regulation",
      region: "Elsewhere",
      is_builtin: true,
      description: null
    })
  ]);

  expect(await screen.findByText("Unknown Regulation")).toBeInTheDocument();
  expect(container.textContent).not.toContain("regulations.desc.");
});

it("labels each regulation switch with a translated action and the regulation name", async () => {
  mountWith([
    ruleset({ id: "gdpr", display_name: "General Data Protection Regulation", is_active: true }),
    ruleset({ id: "hipaa", display_name: "Health Insurance Portability and Accountability Act", is_active: false })
  ]);

  expect(
    await screen.findByRole("switch", { name: "Deactivate General Data Protection Regulation" })
  ).toBeInTheDocument();
  expect(
    screen.getByRole("switch", {
      name: "Activate Health Insurance Portability and Accountability Act"
    })
  ).toBeInTheDocument();
});

it("states that e-mail is always masked even when no regulation is active", async () => {
  mountWith([
    ruleset({ id: "gdpr", display_name: "General Data Protection Regulation", is_active: false })
  ]);

  expect(
    await screen.findByText("E-mail addresses are always masked, whatever regulations are active.")
  ).toBeInTheDocument();
});

describe("contextual layer note", () => {
  const mountWithFlag = async (flag: boolean) => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/regulations")
        return Promise.resolve({
          data: [
            ruleset({
              id: "gdpr",
              display_name: "General Data Protection Regulation",
              entity_types: ["EMAIL_ADDRESS", "DIAGNOSIS"]
            })
          ]
        });
      if (url === "/api/settings")
        return Promise.resolve({ data: { use_ollama_semantic_layer: flag } });
      return Promise.resolve({ data: [] });
    });
    render(<RegulationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /General Data Protection/i }));
    await screen.findByText("DIAGNOSIS");
  };

  it("shows the note and a Privacy settings link while the layer is off", async () => {
    await mountWithFlag(false);
    expect(
      await screen.findByText(/requires Ollama semantic detection, currently off/i)
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /privacy settings/i })).toHaveAttribute(
      "href",
      "/settings?tab=privacy"
    );
  });

  it("hides the note while the layer is on", async () => {
    await mountWithFlag(true);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText(/currently off/i)).not.toBeInTheDocument();
  });

  it("shows no note and logs the error when settings fail to load", async () => {
    const errorSpy = jest.spyOn(console, "error").mockImplementation(() => {});
    const failure = new Error("settings unavailable");
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/regulations")
        return Promise.resolve({
          data: [
            ruleset({
              id: "gdpr",
              display_name: "General Data Protection Regulation",
              entity_types: ["EMAIL_ADDRESS", "DIAGNOSIS"]
            })
          ]
        });
      if (url === "/api/settings") return Promise.reject(failure);
      return Promise.resolve({ data: [] });
    });
    render(<RegulationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /General Data Protection/i }));
    await screen.findByText("DIAGNOSIS");
    await waitFor(() => expect(errorSpy).toHaveBeenCalledWith(failure));
    expect(screen.queryByText(/currently off/i)).not.toBeInTheDocument();
    errorSpy.mockRestore();
  });
});
