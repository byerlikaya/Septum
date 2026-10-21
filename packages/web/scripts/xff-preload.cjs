// Makes this Node server the authoritative proxy for the client address:
// client-sent forwarding headers are dropped and X-Forwarded-For is set to
// the client address, so the API (which trusts only this server) cannot be
// fed a forged one. Load with `node -r ./scripts/xff-preload.cjs`.
//
// The client address is the TCP peer. Only when SEPTUM_TRUSTED_PROXIES
// (comma-separated IPs or CIDRs, e.g. a reverse proxy in front of this
// server) contains the peer is the incoming X-Forwarded-For read: the
// rightmost address in it that is not itself a trusted proxy is the client,
// as uvicorn resolves it.
"use strict";
const http = require("node:http");
const net = require("node:net");

function normalise(address) {
  const value = String(address || "").trim();
  return /^::ffff:\d+\.\d+\.\d+\.\d+$/i.test(value) ? value.slice(7) : value;
}

function familyOf(address) {
  const family = net.isIP(address);
  return family === 4 ? "ipv4" : family === 6 ? "ipv6" : null;
}

function parseTrustedProxies(value) {
  const entries = String(value || "").split(",").map((entry) => entry.trim()).filter(Boolean);
  if (entries.length === 0) return null;
  const list = new net.BlockList();
  for (const entry of entries) {
    const [address, prefix, extra] = entry.split("/");
    const family = familyOf(address);
    const bits = prefix === undefined ? null : /^\d+$/.test(prefix) ? Number(prefix) : NaN;
    const [minBits, maxBits] = family === "ipv4" ? [8, 32] : [32, 128];
    if (!family || extra !== undefined || (bits !== null && !(bits >= minBits && bits <= maxBits))) {
      throw new Error(
        `SEPTUM_TRUSTED_PROXIES: "${entry}" is not an IP address or a CIDR range of at least /8 (IPv4) or /32 (IPv6)`,
      );
    }
    if (bits === null) list.addAddress(address, family);
    else list.addSubnet(address, bits, family);
  }
  return list;
}

function isTrusted(trusted, address) {
  const family = familyOf(address);
  return family !== null && trusted.check(address, family);
}

function resolveClientAddress(peer, forwardedFor, trusted) {
  const client = normalise(peer);
  if (!trusted || !isTrusted(trusted, client)) return client;
  const hops = String(forwardedFor || "").split(",").map(normalise).filter(Boolean);
  for (let i = hops.length - 1; i >= 0; i--) {
    if (!familyOf(hops[i])) return client;
    if (!isTrusted(trusted, hops[i])) return hops[i];
  }
  return client;
}

const trustedProxies = parseTrustedProxies(process.env.SEPTUM_TRUSTED_PROXIES);

const originalEmit = http.Server.prototype.emit;
http.Server.prototype.emit = function emit(event, req, ...rest) {
  if ((event === "request" || event === "upgrade") && req && req.headers) {
    const forwardedFor = req.headers["x-forwarded-for"];
    for (const name of Object.keys(req.headers)) {
      if (name === "forwarded" || name === "x-real-ip" || name.startsWith("x-forwarded-")) {
        delete req.headers[name];
      }
    }
    const client = resolveClientAddress(req.socket && req.socket.remoteAddress, forwardedFor, trustedProxies);
    if (client) req.headers["x-forwarded-for"] = client;
  }
  return originalEmit.call(this, event, req, ...rest);
};

module.exports = { parseTrustedProxies, resolveClientAddress };
