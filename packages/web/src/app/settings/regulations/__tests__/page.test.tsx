import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import RegulationsPage from "../page";

const mockGet = jest.fn();
const mockStatus = jest.fn();

jest.mock("../../../../lib/language", () => ({
  useLanguage: () => ({ language: "en" })
}));

jest.mock("../../../../lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    patch: jest.fn()
  },
  getContextualLayerStatus: (...args: unknown[]) => mockStatus(...args)
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
  mockStatus.mockResolvedValue({
    state: "covered",
    entity_types: [],
    local_model_reachable: true
  });
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
  const mountWithStatus = async (status: unknown) => {
    if (status instanceof Error) mockStatus.mockRejectedValue(status);
    else mockStatus.mockResolvedValue(status);
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
      return Promise.resolve({ data: [] });
    });
    render(<RegulationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /General Data Protection/i }));
    await screen.findByText("EMAIL_ADDRESS");
  };

  it("links to Privacy settings when the layer is off and the local model is reachable", async () => {
    await mountWithStatus({
      state: "layer_off",
      entity_types: ["DIAGNOSIS"],
      local_model_reachable: true
    });
    expect(
      await screen.findByText(/requires Ollama semantic detection, which is off:/i)
    ).toBeInTheDocument();
    expect(screen.getByText("EMAIL_ADDRESS").textContent).toBe("EMAIL_ADDRESS");
    expect(
      screen.getAllByText("DIAGNOSIS").some((el) => /which is off/i.test(el.textContent ?? ""))
    ).toBe(true);
    const links = screen.getAllByRole("link", { name: /privacy/i });
    expect(links.length).toBe(2);
    links.forEach((link) => expect(link).toHaveAttribute("href", "/settings?tab=privacy"));
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("treats an unprobed model as unreachable: setup link, no Privacy link", async () => {
    await mountWithStatus({
      state: "layer_off",
      entity_types: ["DIAGNOSIS"],
      local_model_reachable: null
    });
    expect(await screen.findByText(/no local model is reachable/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /privacy/i })).not.toBeInTheDocument();
    screen
      .getAllByRole("link", { name: /set up the local model/i })
      .forEach((link) => expect(link).toHaveAttribute("href", "/settings?tab=llm-provider"));
  });

  it("points to the local model setup, not the turn-on link, when the layer is off and the model is unreachable", async () => {
    await mountWithStatus({
      state: "layer_off",
      entity_types: ["DIAGNOSIS"],
      local_model_reachable: false
    });
    expect(await screen.findByText(/no local model is reachable/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /privacy/i })).not.toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: /set up the local model/i });
    expect(links.length).toBe(2);
    links.forEach((link) => expect(link).toHaveAttribute("href", "/settings?tab=llm-provider"));
  });

  it("points to the local model setup when the layer is on but the model is unavailable", async () => {
    await mountWithStatus({
      state: "local_model_unavailable",
      entity_types: ["DIAGNOSIS"],
      local_model_reachable: false
    });
    expect(await screen.findByText(/cannot be reached right now/i)).toBeInTheDocument();
    expect(screen.queryByText(/which is off/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /privacy/i })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("shows no note and no banner when special categories are covered", async () => {
    await mountWithStatus({ state: "covered", entity_types: ["DIAGNOSIS"], local_model_reachable: true });
    await waitFor(() => expect(mockStatus).toHaveBeenCalled());
    await mockStatus.mock.results[0].value;
    expect(screen.queryByText(/which is off/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows only the neutral notice, no per-entity note, when the status call fails", async () => {
    const errorSpy = jest.spyOn(console, "error").mockImplementation(() => undefined);
    await mountWithStatus(new Error("status unavailable"));
    await waitFor(() => expect(mockStatus).toHaveBeenCalled());
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Could not verify special-category masking coverage"
    );
    expect(screen.queryByText(/which is off/i)).not.toBeInTheDocument();
    expect(screen.getByText("DIAGNOSIS").textContent).toBe("DIAGNOSIS");
    expect(errorSpy).toHaveBeenCalled();
    errorSpy.mockRestore();
  });
});
