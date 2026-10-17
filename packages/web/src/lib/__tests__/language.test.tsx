type LanguageModule = typeof import("../language");
type RTL = typeof import("@testing-library/react/pure");
type ReactNS = typeof import("react");

interface Env {
  mod: LanguageModule;
  rtl: RTL;
  React: ReactNS;
}

/** Fresh module graph per test: language.tsx caches the language at module level. */
async function load(): Promise<Env> {
  jest.resetModules();
  return {
    React: await import("react"),
    rtl: await import("@testing-library/react/pure"),
    mod: await import("../language")
  };
}

function mountProbe(env: Env) {
  active = env;
  const { React, rtl, mod } = env;
  const { LanguageProvider, useLanguage } = mod;
  function Probe() {
    const { language, setLanguage } = useLanguage();
    return React.createElement(
      "div",
      null,
      React.createElement("span", { "data-testid": "lang" }, language),
      React.createElement("button", { onClick: () => setLanguage("tr") }, "switch")
    );
  }
  return rtl.render(React.createElement(LanguageProvider, null, React.createElement(Probe)));
}

let active: Env | null = null;

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  active?.rtl.cleanup();
  active = null;
});

it("defaults to en when nothing is stored", async () => {
  const env = await load();
  mountProbe(env);
  expect(env.rtl.screen.getByTestId("lang")).toHaveTextContent("en");
});

it("reads a stored valid value", async () => {
  window.localStorage.setItem("septum.language", "tr");
  const env = await load();
  mountProbe(env);
  expect(env.rtl.screen.getByTestId("lang")).toHaveTextContent("tr");
});

it("ignores an invalid stored value", async () => {
  window.localStorage.setItem("septum.language", "xx");
  const env = await load();
  mountProbe(env);
  expect(env.rtl.screen.getByTestId("lang")).toHaveTextContent("en");
});

it("persists the chosen language and updates subscribers", async () => {
  const env = await load();
  mountProbe(env);
  env.rtl.act(() => env.rtl.screen.getByText("switch").click());
  expect(env.rtl.screen.getByTestId("lang")).toHaveTextContent("tr");
  expect(window.localStorage.getItem("septum.language")).toBe("tr");
});

it("throws when used outside the provider", async () => {
  const env = await load();
  active = env;
  const { React, rtl, mod } = env;
  const spy = jest.spyOn(console, "error").mockImplementation(() => {});
  function Bare() {
    mod.useLanguage();
    return null;
  }
  expect(() => rtl.render(React.createElement(Bare))).toThrow(/LanguageProvider/);
  spy.mockRestore();
});
