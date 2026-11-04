// Shared by the sample controllers: connect to the host named by ?host= (default
// https://lunardig.com), show status, and log every command any application issues.
//
// Nothing a sample shows is built from markup: the host origin comes from the URL, and
// every id, name and figure comes from whatever page is playing host, so all of it is set
// as text (SECURITY-REVIEW-ldcp F-01).
import { LdcpClient, hostOriginFrom, windowPort } from "./ldcp.js";

export const $ = (sel) => document.querySelector(sel);
export const num = (r) => (r ? Number(r.n) / Number(r.d) : 0);
/** The host to talk to: ?host= if it names an origin a host can have, else lunardig.com. */
export const hostOrigin = hostOriginFrom(new URLSearchParams(location.search).get("host"));

/** A table row of text cells; the last cell is left empty for a button. */
export function row(cells, trailing = false) {
  const tr = document.createElement("tr");
  for (const c of cells) {
    const td = document.createElement("td");
    td.textContent = String(c);
    tr.append(td);
  }
  if (trailing) tr.append(document.createElement("td"));
  return tr;
}

export function status(text, tone = "") {
  const el = $("#status");
  el.textContent = text;
  el.dataset.tone = tone;
}

export function log(text) {
  const li = document.createElement("li");
  li.textContent = text;
  $("#log").prepend(li);
  while ($("#log").children.length > 200) $("#log").lastChild.remove();
}

/** "who did what": every COMMAND event names the application that issued it. */
export function describeCommand(e) {
  const by = e.issued_by ? `${e.issued_by.app_name ?? ""} ${e.issued_by.origin}`.trim() : "unattributed";
  return `${e.command_id} ${e.status} by ${by}`;
}

export async function connect(app, scopes, handlers) {
  if (!window.opener) {
    const el = $("#status");
    el.dataset.tone = "bad";
    const a = document.createElement("a");
    a.href = `${hostOrigin}/?demo=controllers`;
    a.textContent = "start the demo in the game";
    const b = document.createElement("strong");
    b.textContent = "Controllers";
    el.replaceChildren("This is a controller for a Lunar Dig claim. It runs when the game opens it: ", a, " and open it from ", b, ".");
    throw new Error("no host");
  }
  status(`Waiting for the player to allow ${location.origin}…`);
  const host = await LdcpClient.connect({
    app,
    scopes,
    port: windowPort(hostOrigin),
    handlers: {
      ended: (reason) => status(`Disconnected: ${reason}.`, "bad"),
      ...handlers,
    },
  });
  status(`Connected to claim ${host.claim} as session ${host.session} (${host.scopes.join(", ")}).`, "ok");
  document.body.dataset.session = host.session;
  return host;
}

export async function run(host, op, args) {
  const out = await host.submit(op, args);
  log(out.outcome === "applied" ? `${op} applied as ${out.command_id}` : `${op} rejected (${out.stage} ${out.code}): ${out.message ?? ""}`);
  return out;
}
