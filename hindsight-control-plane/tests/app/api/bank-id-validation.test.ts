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
 * A call counts only when its 400 reaches the client before anything else runs (see
 * `answersFirst`). The first version counted any call anywhere in the handler, so a validator
 * behind a condition, in a callback, or with its result dropped passed the walk while ".." still
 * reached the URL builder. The second counted any later `return <result>` and ignored what ran
 * before the call, so an inverted guard (`if (!error) return error;`) or a fetch ahead of the
 * validator passed, and it read only `function` declarations, so a `const GET = async () => ...`
 * handler was never walked.
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
  ts.SyntaxKind.TryStatement,
]);

/**
 * The calls a handler may make before its bank id is validated: reading the request, encoding a
 * path segment for the validator's own `suffix` argument, and answering a missing parameter.
 * Matched on the callee text with whitespace removed, so a new route that reads the request through
 * another receiver name needs an entry here; anything else that runs first (a `fetch`, an SDK call,
 * `dataplaneBankUrl`, a `new` expression) fails the walk.
 */
const BEFORE_VALIDATION = new Set([
  "JSON.parse",
  "NextResponse.json",
  "encodeURIComponent",
  "formData.get",
  "localizeApiErrorPayload",
  "missingQueryParam",
  "request.formData",
  "request.json",
  "request.json().catch",
  "request.nextUrl.searchParams.get",
  "searchParams.get",
]);

/** A route file's top-level function: `function name() {}` or `const name = () => {}`. */
type RouteFunction = ts.FunctionDeclaration | ts.ArrowFunction | ts.FunctionExpression;
type IdentifierCall = ts.CallExpression & { expression: ts.Identifier };

function* routeFiles(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) yield* routeFiles(path);
    else if (entry === "route.ts") yield path;
  }
}

/** The calls by plain identifier inside `fn`, nested callbacks included. */
function identifierCalls(fn: RouteFunction): IdentifierCall[] {
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

/** Whether `nodes`, outside the functions they define, call only what `BEFORE_VALIDATION` lists. */
function onlyRequestReads(nodes: readonly ts.Node[]): boolean {
  let clean = true;
  const visit = (node: ts.Node) => {
    if (ts.isFunctionLike(node)) return;
    if (ts.isNewExpression(node)) clean = false;
    if (ts.isCallExpression(node)) {
      clean &&= BEFORE_VALIDATION.has(node.expression.getText().replace(/\s+/g, ""));
    }
    ts.forEachChild(node, visit);
  };
  nodes.forEach(visit);
  return clean;
}

/**
 * Whether `call` is the first thing `fn` does on every path, apart from reading the request, and
 * whether its result reaches the client.
 *
 * - Only awaits, one variable declaration, a return, the function body or a try block may stand
 *   between the call and `fn`, so a call under an `if`, loop, callback or dead branch fails.
 * - The statements before it, and its own arguments, may only make `BEFORE_VALIDATION` calls, so a
 *   fetch that runs before the validator fails.
 * - The result is returned directly (`return relayBankGet(...)`), or the variable it initialises is
 *   returned by the very next statement, in one of the two guard forms the routes use (`if
 *   (bankIdError) return bankIdError;`, `if (bankId instanceof NextResponse) return bankId;`), so a
 *   dropped 400, an inverted guard, or a reassignment before the guard fails.
 */
function answersFirst(call: IdentifierCall, fn: RouteFunction): boolean {
  const earlier: ts.Node[] = [...call.arguments];
  let statement: ts.VariableStatement | undefined;
  let declared: string | undefined;
  let returned = false;
  let child: ts.Node = call;
  for (let node: ts.Node = call.parent; node !== fn; child = node, node = node.parent) {
    if (ts.isReturnStatement(node)) {
      returned = true;
    } else if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) {
      declared = node.name.text;
    } else if (ts.isVariableStatement(node)) {
      statement = node;
    } else if (ts.isBlock(node)) {
      const owner = node.parent;
      if (owner !== fn && !(ts.isTryStatement(owner) && owner.tryBlock === node)) return false;
      earlier.push(...node.statements.slice(0, node.statements.indexOf(child as ts.Statement)));
    } else if (!STRAIGHT_LINE.has(node.kind)) {
      return false;
    }
  }
  // An arrow function with an expression body returns that expression.
  const body = fn.body;
  if (body && child === body && !ts.isBlock(body)) returned = true;
  if (!onlyRequestReads(earlier)) return false;
  if (returned) return true;
  if (!statement || !declared || !ts.isBlock(statement.parent)) return false;
  const siblings = statement.parent.statements;
  const guard = siblings[siblings.indexOf(statement) + 1]?.getText().replace(/\s+/g, " ");
  return (
    guard === `if (${declared}) return ${declared};` ||
    guard === `if (${declared} instanceof NextResponse) return ${declared};`
  );
}

/** `start` and the same-file helpers it reaches through the calls `follow` accepts. */
function reachable(
  start: RouteFunction,
  local: Map<string, RouteFunction>,
  follow: (call: IdentifierCall, fn: RouteFunction) => boolean
): Set<RouteFunction> {
  const reached = new Set<RouteFunction>([start]);
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
 * The exported handlers of one route file that name a bank id without validating it first, plus an
 * `unsupported export` entry for each export the walk cannot see into.
 * `routePath` is the file's path under `src/app/api`, which carries the dynamic segments.
 */
function unvalidatedHandlers(source: string, routePath: string): string[] {
  const file = ts.createSourceFile(routePath, source, ts.ScriptTarget.Latest, true);
  const local = new Map<string, RouteFunction>();
  const handlers: string[] = [];
  const failing: string[] = [];
  for (const statement of file.statements) {
    const exported =
      ts.canHaveModifiers(statement) &&
      (ts.getModifiers(statement) ?? []).some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
    if (ts.isFunctionDeclaration(statement) && statement.name) {
      local.set(statement.name.text, statement);
      if (exported) handlers.push(statement.name.text);
    } else if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        const init = declaration.initializer;
        // Route segment config (`export const dynamic = "force-dynamic"`) is a literal, so it
        // cannot be a handler; any other exported value might be one the walk cannot read.
        const literal =
          init !== undefined &&
          (ts.isLiteralExpression(init) ||
            init.kind === ts.SyntaxKind.TrueKeyword ||
            init.kind === ts.SyntaxKind.FalseKeyword);
        if (
          ts.isIdentifier(declaration.name) &&
          init &&
          (ts.isArrowFunction(init) || ts.isFunctionExpression(init))
        ) {
          local.set(declaration.name.text, init);
          if (exported) handlers.push(declaration.name.text);
        } else if (exported && !literal) {
          failing.push(`unsupported export ${declaration.name.getText(file)}`);
        }
      }
    } else if (
      // A re-export (`export { handler as GET }`), a default expression or an exported class may
      // be a handler the walk cannot see into, so it fails rather than passing unread.
      (exported &&
        !ts.isInterfaceDeclaration(statement) &&
        !ts.isTypeAliasDeclaration(statement)) ||
      (ts.isExportDeclaration(statement) && !statement.isTypeOnly) ||
      ts.isExportAssignment(statement)
    ) {
      failing.push(`unsupported export ${statement.getText(file).slice(0, 40)}`);
    }
  }
  const pathScoped = BANK_SEGMENT.test(routePath);
  for (const name of handlers) {
    const handler = local.get(name)!;
    // Any same-file helper the handler calls (proxyAlias, proxyDirective, ...) can name a bank id.
    const reached = reachable(handler, local, () => true);
    const text = [...reached].map((fn) => fn.getText(file)).join("\n");
    const scoped = pathScoped || BANK_NAMES.test(text);
    // A helper's validator guards the handler only when the handler calls that helper first and
    // returns its result, so validation follows only those calls.
    const guarded = reachable(handler, local, answersFirst);
    const validated = [...guarded].some((fn) =>
      identifierCalls(fn).some(
        (call) => Object.hasOwn(VALIDATORS, call.expression.text) && answersFirst(call, fn)
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
      {
        label: "an inverted guard",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          if (!bankIdError) return bankIdError;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a guard on another condition",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          if (request.headers.has("x-check")) return bankIdError;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a result reassigned before its guard",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          let bankIdError = validateBankId(request, bankId);
          bankIdError = null;
          if (bankIdError) return bankIdError;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a fetch before the validator",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          await fetch(dataplaneBankUrl(bankId, "/warm"));
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) return bankIdError;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a helper that fetches before it validates",
        path: "banks/[bankId]/thing/[id]/route.ts",
        source: `async function proxyThing(request, { params }) {
          const { bankId, id } = await params;
          await fetch(dataplaneBankUrl(bankId, "/warm"));
          return proxyDataplane(request, { bankId, required: [bankId, id] });
        }
        export async function PATCH(request, context) {
          return proxyThing(request, context);
        }`,
      },
      {
        label: "an exported arrow handler",
        path: "banks/[bankId]/thing/route.ts",
        source: `export const GET = async (request, { params }) => {
          const { bankId } = await params;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        };`,
      },
      {
        label: "an arrow helper that reads a body bank_id",
        path: "thing/route.ts",
        source: `const proxyThing = async (request) => {
          const body = await request.json();
          return fetch(dataplaneBankUrl(body.bank_id, "/thing"));
        };
        export async function POST(request) {
          return proxyThing(request);
        }`,
      },
      {
        label: "a re-exported handler",
        path: "banks/[bankId]/thing/route.ts",
        source: `async function getThing(request, { params }) {
          const { bankId } = await params;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }
        export { getThing as GET };`,
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
        label: "a body read and a missing-id answer before the validator",
        path: "recall/route.ts",
        source: `export async function POST(request) {
          const body = await request.json();
          if (!body.bank_id) {
            return NextResponse.json(localizeApiErrorPayload(request, { error: "bank_id is required" }), {
              status: 400,
            });
          }
          const bankIdError = validateBankId(request, body.bank_id);
          if (bankIdError) return bankIdError;
          return sdk.recall({ path: { bank_id: body.bank_id } });
        }`,
      },
      {
        label: "a validator whose suffix encodes another segment",
        path: "banks/[bankId]/directives/[directiveId]/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId, directiveId } = await params;
          return proxyDataplane(request, {
            bankId,
            suffix: \`/directives/\${encodeURIComponent(directiveId)}\`,
          });
        }`,
      },
      {
        label: "an exported arrow handler that validates",
        path: "banks/[bankId]/thing/route.ts",
        source: `export const GET = async (request, { params }) => {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) return bankIdError;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        };`,
      },
      {
        label: "an arrow handler that returns a relay",
        path: "banks/[bankId]/thing/route.ts",
        source: `export const GET = (request, context) => relayBankGet(request, context, "/thing");`,
      },
      {
        label: "a route segment config export",
        path: "version/route.ts",
        source: `export const dynamic = "force-dynamic";
        export async function GET() {
          return fetch(DATAPLANE_URL + "/version");
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
