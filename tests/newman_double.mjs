// Double de test de Newman : rejoue une collection Postman (format v2.1) avec le fetch de Node.
//
// Il n'imite que ce qu'écrit agent-qa-playwright : les scripts « pré-requête » de la collection (avant chaque
// requête, comme Newman), pm.test, pm.response.to.have.status, pm.response.to.be.json, pm.response.json(),
// pm.expect(...).to.have.property, pm.variables, pm.collectionVariables et pm.environment. Les variables {{nom}}
// suivent l'ordre de Postman : locales, puis environnement, puis collection.
//
// La vraie Newman rejoue les collections générées dans GitHub Actions (job robot).
//
// Usage : node tests/newman_double.mjs collection.json [environnement.json] [--env-var nom=valeur]

import { readFileSync } from "node:fs";

const [collectionPath, ...rest] = process.argv.slice(2);
const collection = JSON.parse(readFileSync(collectionPath, "utf-8"));
const environmentPath = rest[0] && !rest[0].startsWith("--") ? rest.shift() : null;
const environment = environmentPath ? JSON.parse(readFileSync(environmentPath, "utf-8")) : { values: [] };

const locales = new Map();  // pm.variables : le temps d'un lancement
const env = new Map(environment.values.filter((v) => v.enabled !== false).map((v) => [v.key, v.value]));
const collectionVars = new Map((collection.variable || []).map((v) => [v.key, v.value]));
for (let i = 0; i < rest.length; i++) {
  if (rest[i] === "--env-var") {
    const [name, ...value] = rest[++i].split("=");
    env.set(name, value.join("="));
  }
}

const lookup = (name) => (locales.has(name) ? locales.get(name) : env.has(name) ? env.get(name)
  : collectionVars.has(name) ? collectionVars.get(name) : undefined);
const resolve = (text) => String(text ?? "").replace(/\{\{([^{}]+)\}\}/g, (all, name) => lookup(name) ?? all);

class AssertionError extends Error {}
const check = (ok, message) => { if (!ok) throw new AssertionError(message); };

function expectation(value) {
  return {
    to: {
      have: {
        property: (name) => check(value !== null && typeof value === "object" && name in value,
          `expected ${JSON.stringify(value)} to have property '${name}'`),
      },
      equal: (other) => check(value === other, `expected ${value} to equal ${other}`),
      be: { below: (limit) => check(value < limit, `expected ${value} to be below ${limit}`) },
    },
  };
}

function response(status, headers, text, time) {
  const res = {
    code: status,
    responseTime: time,
    headers: { get: (name) => headers.get(name) },
    text: () => text,
    json: () => JSON.parse(text),
    to: {
      have: {
        status: (code) => check(status === code, `expected response to have status code ${code} but got ${status}`),
        header: (name) => check(headers.has(name), `expected response to have header ${name}`),
      },
      be: {},
    },
  };
  Object.defineProperty(res.to.be, "json", {
    get() {
      check(/json/i.test(headers.get("content-type") || ""), "expected response to be json");
      JSON.parse(text);
      return true;
    },
  });
  return res;
}

let total = 0;
let failures = 0;

function run(lines, res) {
  const pm = {
    test(name, callback) {
      total++;
      try {
        callback();
        console.log(`  ✓ ${name}`);
      } catch (error) {
        failures++;
        console.log(`  ✗ ${name} : ${error.message}`);
      }
    },
    expect: expectation,
    response: res,
    variables: { get: (name) => lookup(name), set: (name, value) => locales.set(name, String(value)), has: (name) => lookup(name) !== undefined },
    collectionVariables: { get: (name) => collectionVars.get(name), set: (name, value) => collectionVars.set(name, String(value)) },
    environment: { get: (name) => env.get(name), set: (name, value) => env.set(name, String(value)) },
  };
  new Function("pm", lines.join("\n"))(pm);
}

const scripts = (owner, listen) => (owner.event || []).filter((e) => e.listen === listen).map((e) => e.script.exec);

for (const item of collection.item) {
  console.log(`→ ${item.name}`);
  for (const lines of scripts(collection, "prerequest").concat(scripts(item, "prerequest"))) run(lines, undefined);
  const request = item.request;
  const headers = Object.fromEntries((request.header || []).map((h) => [resolve(h.key), resolve(h.value)]));
  let body;
  if (request.body?.mode === "raw") body = resolve(request.body.raw);
  if (request.body?.mode === "urlencoded") {
    body = new URLSearchParams(request.body.urlencoded.map((p) => [resolve(p.key), resolve(p.value)])).toString();
  }
  const start = Date.now();
  const answer = await fetch(resolve(request.url.raw), { method: request.method, headers, body });
  const text = await answer.text();
  const res = response(answer.status, answer.headers, text, Date.now() - start);
  for (const lines of scripts(item, "test")) run(lines, res);
}

console.log(`${total} tests, ${failures} en échec`);
process.exit(failures ? 1 : 0);
