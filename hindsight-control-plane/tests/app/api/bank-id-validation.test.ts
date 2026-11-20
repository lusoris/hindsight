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
 * id (a `[bankId]`/`[agentId]` route segment, or one of `BANK_NAMES` in its body or in a helper of
 * the same file it calls) without calling one of the validators below.
 *
 * A call counts only when its 400 reaches the client: it runs on every path through its function
 * and its result is returned (see `returnsUnconditionally`). The first version counted any call
 * anywhere in the handler, so a validator behind a condition, in a callback, or with its result
 * dropped passed the walk while ".." still reached the URL builder.
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
// `\b` treats `_` as a word character, so `bank_id` does not match inside `target_bank_id`.
const BANK_NAMES = /\b(bankId|bank_id|agentId|agent_id|targetBankId|target_bank_id)\b/;
const BANK_SEGMENT = /\[(bankId|agentId)\]/;

/** Nodes a call may sit in and still run whenever the statement around it runs. */
const STRAIGHT_LINE = new Set([
  ts.SyntaxKind.AwaitExpression,
  ts.SyntaxKind.ParenthesizedExpression,
  ts.SyntaxKind.VariableDeclarationList,
  ts.SyntaxKind.VariableStatement,
  ts.SyntaxKind.TryStatement,
]);

type IdentifierCall = ts.CallExpression & { expression: ts.Identifier };

function* routeFiles(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) yield* routeFiles(path);
    else if (entry === "route.ts") yield path;
  }
}

/** The calls by plain identifier inside `fn`, nested callbacks included. */
function identifierCalls(fn: ts.FunctionDeclaration): IdentifierCall[] {
  const calls: IdentifierCall[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) {
      calls.push(node as IdentifierCall);
    }
    ts.forEachChild(node, visit);
  };
  ts.forEachChild(fn, visit);
  return calls;
}

/** Whether `fn` has `return <name>` outside its nested functions. */
function returnsIdentifier(fn: ts.FunctionDeclaration, name: string): boolean {
  let found = false;
  const visit = (node: ts.Node) => {
    if (ts.isFunctionLike(node)) return;
    if (ts.isReturnStatement(node) && node.expression && ts.isIdentifier(node.expression)) {
      found ||= node.expression.text === name;
    }
    ts.forEachChild(node, visit);
  };
  ts.forEachChild(fn, visit);
  return found;
}

/**
 * Whether `call` runs on every path through `fn` and hands its result back. Only awaits, one
 * variable declaration, a return, the function body or a try block may stand between them, so a
 * call under an `if`, loop, callback or dead branch fails. The result must be returned directly
 * (`return relayBankGet(...)`) or through the variable it initialises (`const bankIdError =
 * validateBankId(...)` and a later `return bankIdError`), so a dropped 400 fails too.
 */
function returnsUnconditionally(call: IdentifierCall, fn: ts.FunctionDeclaration): boolean {
  let declared: string | undefined;
  let returned = false;
  for (let node: ts.Node = call.parent; node !== fn; node = node.parent) {
    if (ts.isReturnStatement(node)) {
      returned = true;
    } else if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) {
      declared = node.name.text;
    } else if (ts.isBlock(node)) {
      const owner = node.parent;
      if (owner !== fn && !(ts.isTryStatement(owner) && owner.tryBlock === node)) return false;
    } else if (!STRAIGHT_LINE.has(node.kind)) {
      return false;
    }
  }
  if (returned) return true;
  return declared !== undefined && returnsIdentifier(fn, declared);
}

/** `start` and the same-file helpers it reaches through the calls `follow` accepts. */
function reachable(
  start: ts.FunctionDeclaration,
  local: Map<string, ts.FunctionDeclaration>,
  follow: (call: IdentifierCall, fn: ts.FunctionDeclaration) => boolean
): Set<ts.FunctionDeclaration> {
  const reached = new Set<ts.FunctionDeclaration>([start]);
  const queue = [start];
  while (queue.length > 0) {
    const fn = queue.pop()!;
    for (const call of identifierCalls(fn)) {
      const helper = local.get(call.expression.text);
      if (helper && !reached.has(helper) && follow(call, fn)) {
        reached.add(helper);
        queue.push(helper);
      }
    }
  }
  return reached;
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
    // Any same-file helper the handler calls (proxyAlias, proxyDirective, ...) can name a bank id.
    const reached = reachable(declaration, local, () => true);
    const text = [...reached].map((fn) => fn.getText(file)).join("\n");
    const scoped = pathScoped || BANK_NAMES.test(text);
    // A helper's validator guards the handler only when the handler returns that helper's result
    // on every path, so validation follows only those calls.
    const guarded = reachable(declaration, local, returnsUnconditionally);
    const validated = [...guarded].some((fn) =>
      identifierCalls(fn).some(
        (call) => Object.hasOwn(VALIDATORS, call.expression.text) && returnsUnconditionally(call, fn)
      )
    );
    if (scoped && !validated) {
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
      {
        label: "a validator in a dead branch",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          if (false) validateBankId(request, bankId);
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a validator behind a condition",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          if (request.headers.get("x-check")) {
            const bankIdError = validateBankId(request, bankId);
            if (bankIdError) return bankIdError;
          }
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a validator whose 400 is dropped",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          validateBankId(request, bankId);
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a validator result stored and never returned",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a validator inside a callback",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          const check = () => {
            const bankIdError = validateBankId(request, bankId);
            if (bankIdError) return bankIdError;
          };
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a validating helper the handler calls only behind a condition",
        path: "banks/[bankId]/thing/[id]/route.ts",
        source: `async function proxyThing(request, { params }) {
          const { bankId, id } = await params;
          return proxyDataplane(request, { bankId, required: [bankId, id] });
        }
        export async function PATCH(request, context) {
          if (request.headers.get("x-proxy")) return proxyThing(request, context);
          return fetch(DATAPLANE_URL);
        }`,
      },
      {
        label: "a body target_bank_id route",
        path: "clone/route.ts",
        source: `export async function POST(request) {
          const { target_bank_id } = await request.json();
          return fetch(dataplaneBankUrl(target_bank_id, "/clone"));
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
        label: "a validator inside a try block",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          try {
            const { bankId } = await params;
            const bankIdError = validateBankId(request, bankId);
            if (bankIdError) return bankIdError;
            return fetch(dataplaneBankUrl(bankId, "/thing"));
          } catch (error) {
            return new Response(null, { status: 500 });
          }
        }`,
      },
      {
        label: "a query bank id returned when it is a response",
        path: "documents/route.ts",
        source: `export async function GET(request) {
          const bankId = requireQueryBankId(request);
          if (bankId instanceof NextResponse) return bankId;
          return sdk.listDocuments({ path: { bank_id: bankId } });
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
