import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";
import * as bankSdkRoute from "@/lib/bank-sdk-route";
import * as dataplaneProxy from "@/lib/dataplane-proxy";
import * as routeValidation from "@/lib/route-validation";

/**
 * Every route handler that takes a bank id must refuse "." and ".." before it builds a dataplane
 * URL or SDK call: a URL parser resolves them out of `/v1/default/banks/<id>`, so the request
 * would reach another endpoint. The handlers that missed it were found one review comment at a
 * time, so this walks every `src/app/api/**\/route.ts` and fails on a handler that names a bank
 * id (a `[bankId]`/`[agentId]` route segment, or `bankId`/`bank_id`/`agentId`/`agent_id` in its
 * body or in a helper of the same file it calls) without calling one of the validators below.
 */
const VALIDATORS = {
  validateBankId: routeValidation.validateBankId,
  requireQueryBankId: routeValidation.requireQueryBankId,
  respondWithBankSdk: bankSdkRoute.respondWithBankSdk,
  proxyDataplane: dataplaneProxy.proxyDataplane,
  relayBankGet: dataplaneProxy.relayBankGet,
  relayBankPost: dataplaneProxy.relayBankPost,
} as const;

const API_DIR = join(__dirname, "../../../src/app/api");
const BANK_NAMES = /\b(bankId|bank_id|agentId|agent_id)\b/;
const BANK_SEGMENT = /\[(bankId|agentId)\]/;

function* routeFiles(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) yield* routeFiles(path);
    else if (entry === "route.ts") yield path;
  }
}

/** Names of the functions `node` calls by plain identifier. */
function calledNames(node: ts.Node): Set<string> {
  const names = new Set<string>();
  const visit = (child: ts.Node) => {
    if (ts.isCallExpression(child) && ts.isIdentifier(child.expression)) {
      names.add(child.expression.text);
    }
    ts.forEachChild(child, visit);
  };
  visit(node);
  return names;
}

/**
 * The exported handlers of one route file that name a bank id without calling a validator.
 * `routePath` is the file's path under `src/app/api`, which carries the dynamic segments.
 */
function unvalidatedHandlers(source: string, routePath: string): string[] {
  const file = ts.createSourceFile(routePath, source, ts.ScriptTarget.Latest, true);
  const local = new Map<string, ts.FunctionDeclaration>();
  for (const statement of file.statements) {
    if (ts.isFunctionDeclaration(statement) && statement.name) {
      local.set(statement.name.text, statement);
    }
  }
  const pathScoped = BANK_SEGMENT.test(routePath);
  const failing: string[] = [];
  for (const [name, declaration] of local) {
    const exported = declaration.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
    if (!exported) continue;
    // Follow calls into helpers of the same file (proxyAlias, proxyDirective, ...).
    const reached = new Set<ts.FunctionDeclaration>([declaration]);
    const calls = new Set<string>();
    const queue = [declaration];
    while (queue.length > 0) {
      for (const called of calledNames(queue.pop()!)) {
        calls.add(called);
        const helper = local.get(called);
        if (helper && !reached.has(helper)) {
          reached.add(helper);
          queue.push(helper);
        }
      }
    }
    const text = [...reached].map((fn) => fn.getText(file)).join("\n");
    const scoped = pathScoped || BANK_NAMES.test(text);
    if (scoped && !Object.keys(VALIDATORS).some((validator) => calls.has(validator))) {
      failing.push(name);
    }
  }
  return failing;
}

describe("bank id validation in every route handler", () => {
  it("names only validators the shared modules still export", () => {
    for (const validator of Object.values(VALIDATORS)) {
      expect(validator).toBeTypeOf("function");
    }
  });

  it("finds no route handler that skips bank id validation", () => {
    const failing: string[] = [];
    let handlers = 0;
    for (const path of routeFiles(API_DIR)) {
      const routePath = relative(API_DIR, path);
      const source = readFileSync(path, "utf8");
      handlers += (source.match(/^export async function /gm) ?? []).length;
      for (const name of unvalidatedHandlers(source, routePath)) {
        failing.push(`${routePath} ${name}`);
      }
    }
    // Guards against a walk that silently finds nothing (moved directory, changed export form).
    expect(handlers).toBeGreaterThan(50);
    expect(failing).toEqual([]);
  });

  describe("planted cases", () => {
    it.each([
      {
        label: "a [bankId] route that reads the param and fetches",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a body bank_id route",
        path: "recall/route.ts",
        source: `export async function POST(request) {
          const body = await request.json();
          const bankId = body.bank_id || "default";
          return sdk.recall({ path: { bank_id: bankId } });
        }`,
      },
      {
        label: "a [bankId] route whose same-file helper validates nothing",
        path: "banks/[bankId]/thing/[id]/route.ts",
        source: `async function proxyThing(request, context) {
          return fetch(DATAPLANE_URL);
        }
        export async function DELETE(request, context) {
          return proxyThing(request, context);
        }`,
      },
    ])("flags $label", ({ path, source }) => {
      expect(unvalidatedHandlers(source, path)).toHaveLength(1);
    });

    it.each([
      {
        label: "a handler that calls validateBankId",
        path: "stats/[agentId]/route.ts",
        source: `export async function GET(request, { params }) {
          const { agentId } = await params;
          const bankIdError = validateBankId(request, agentId);
          if (bankIdError) return bankIdError;
          return sdk.getAgentStats({ path: { bank_id: agentId } });
        }`,
      },
      {
        label: "a handler whose same-file helper calls proxyDataplane",
        path: "banks/[bankId]/thing/[id]/route.ts",
        source: `async function proxyThing(request, { params }) {
          const { bankId, id } = await params;
          return proxyDataplane(request, { bankId, required: [bankId, id] });
        }
        export async function PATCH(request, context) {
          return proxyThing(request, context);
        }`,
      },
      {
        label: "a handler with no bank id",
        path: "version/route.ts",
        source: `export async function GET() {
          return fetch(DATAPLANE_URL + "/version");
        }`,
      },
    ])("passes $label", ({ path, source }) => {
      expect(unvalidatedHandlers(source, path)).toEqual([]);
    });
  });
});
