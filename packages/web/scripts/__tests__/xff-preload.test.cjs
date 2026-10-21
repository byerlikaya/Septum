/**
 * @jest-environment node
 */
"use strict";
const http = require("node:http");

const pristineEmit = http.Server.prototype.emit;

let server;
let seen;

beforeAll(async () => {
  require("../xff-preload.cjs");
  server = http.createServer((req, res) => {
    seen = { ...req.headers };
    res.end();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
});

afterAll(async () => {
  await new Promise((resolve) => server.close(resolve));
  http.Server.prototype.emit = pristineEmit;
});

function send(headers) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: "127.0.0.1", port: server.address().port, path: "/", headers },
      (res) => {
        res.resume();
        res.on("end", resolve);
      },
    );
    req.on("error", reject);
    req.end();
  });
}

describe("xff-preload", () => {
  it("replaces a forged X-Forwarded-For with the TCP peer", async () => {
    await send({ "X-Forwarded-For": "203.0.113.9, 10.0.0.1" });

    expect(seen["x-forwarded-for"]).toBe("127.0.0.1");
  });

  it("drops every other client-sent forwarding header", async () => {
    await send({
      Forwarded: "for=203.0.113.9",
      "X-Real-IP": "203.0.113.9",
      "X-Forwarded-Host": "evil.example",
      "X-Forwarded-Proto": "https",
    });

    expect(seen.forwarded).toBeUndefined();
    expect(seen["x-real-ip"]).toBeUndefined();
    expect(seen["x-forwarded-host"]).toBeUndefined();
    expect(seen["x-forwarded-proto"]).toBeUndefined();
    expect(seen["x-forwarded-for"]).toBe("127.0.0.1");
  });

  it("sets the peer even when the client sent no forwarding header", async () => {
    await send({});

    expect(seen["x-forwarded-for"]).toBe("127.0.0.1");
  });

  it("folds an IPv4-mapped peer on a dual-stack listener", async () => {
    let mapped;
    const dualStack = http.createServer((req, res) => {
      mapped = req.headers["x-forwarded-for"];
      res.end();
    });
    await new Promise((resolve) => dualStack.listen({ port: 0, host: "::", ipv6Only: false }, resolve));
    try {
      await new Promise((resolve, reject) => {
        http
          .get({ host: "127.0.0.1", port: dualStack.address().port, path: "/" }, (res) => {
            res.resume();
            res.on("end", resolve);
          })
          .on("error", reject);
      });
    } finally {
      await new Promise((resolve) => dualStack.close(resolve));
    }

    expect(mapped).toBe("127.0.0.1");
  });

  it("keeps unrelated headers", async () => {
    await send({ "X-Setup-Token": "tok", "X-Forwarded-For": "203.0.113.9" });

    expect(seen["x-setup-token"]).toBe("tok");
  });
});

describe("SEPTUM_TRUSTED_PROXIES", () => {
  const { parseTrustedProxies, resolveClientAddress } = require("../xff-preload.cjs");
  const trusted = parseTrustedProxies("10.0.0.5, 192.168.0.0/16, fd00::/32");

  it("is off by default: the peer is the client whatever the header says", () => {
    expect(parseTrustedProxies(undefined)).toBeNull();
    expect(parseTrustedProxies("  ")).toBeNull();
    expect(resolveClientAddress("10.0.0.5", "203.0.113.9", null)).toBe("10.0.0.5");
  });

  it("ignores X-Forwarded-For from a peer that is not trusted", () => {
    expect(resolveClientAddress("198.51.100.7", "203.0.113.9", trusted)).toBe("198.51.100.7");
  });

  it("takes the rightmost untrusted hop behind a trusted proxy", () => {
    expect(resolveClientAddress("10.0.0.5", "1.1.1.1, 203.0.113.9, 192.168.4.4", trusted)).toBe("203.0.113.9");
  });

  it("folds IPv4-mapped addresses in the peer and the hops", () => {
    expect(resolveClientAddress("::ffff:10.0.0.5", "::ffff:203.0.113.9", trusted)).toBe("203.0.113.9");
  });

  it("matches IPv6 ranges", () => {
    expect(resolveClientAddress("fd00::1", "2001:db8::9", trusted)).toBe("2001:db8::9");
  });

  it("falls back to the peer when the header is missing, all trusted, or not an address", () => {
    expect(resolveClientAddress("10.0.0.5", undefined, trusted)).toBe("10.0.0.5");
    expect(resolveClientAddress("10.0.0.5", "192.168.1.1, 10.0.0.5", trusted)).toBe("10.0.0.5");
    expect(resolveClientAddress("10.0.0.5", "203.0.113.9, not-an-ip", trusted)).toBe("10.0.0.5");
  });

  it("accepts the broadest allowed ranges", () => {
    expect(() => parseTrustedProxies("10.0.0.0/8, 2001:db8::/32")).not.toThrow();
  });

  it.each([
    "*",
    "0.0.0.0/0",
    "::/0",
    "0.0.0.0/00",
    "10.0.0.0/0.0.0.0",
    "10.0.0.0/33",
    "10.0.0.0/7",
    "2001:db8::/31",
    "web",
    "10.0.0.1/8/1",
  ])(
    "refuses %s",
    (entry) => {
      expect(() => parseTrustedProxies(entry)).toThrow("SEPTUM_TRUSTED_PROXIES");
    },
  );

  it("applies the list to live requests when set before loading", async () => {
    const saved = http.Server.prototype.emit;
    http.Server.prototype.emit = pristineEmit;
    process.env.SEPTUM_TRUSTED_PROXIES = "127.0.0.1";
    let header;
    const proxied = http.createServer((req, res) => {
      header = req.headers["x-forwarded-for"];
      res.end();
    });
    try {
      jest.isolateModules(() => require("../xff-preload.cjs"));
      await new Promise((resolve) => proxied.listen(0, "127.0.0.1", resolve));
      await new Promise((resolve, reject) => {
        http
          .get(
            {
              host: "127.0.0.1",
              port: proxied.address().port,
              path: "/",
              headers: { "X-Forwarded-For": "1.2.3.4, 203.0.113.9", "X-Real-IP": "9.9.9.9" },
            },
            (res) => {
              res.resume();
              res.on("end", resolve);
            },
          )
          .on("error", reject);
      });
    } finally {
      delete process.env.SEPTUM_TRUSTED_PROXIES;
      await new Promise((resolve) => proxied.close(resolve));
      http.Server.prototype.emit = saved;
    }
    expect(header).toBe("203.0.113.9");
  });
});
