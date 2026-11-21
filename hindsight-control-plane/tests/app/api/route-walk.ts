import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";
import * as bankSdkRoute from "@/lib/bank-sdk-route";
import * as dataplaneProxy from "@/lib/dataplane-proxy";
import * as knowledgeBaseProxy from "@/lib/knowledge-base-proxy";
import * as routeValidation from "@/lib/route-validation";

/**
 * Every route handler that takes a bank id must refuse "." and ".." before it builds a dataplane
 * URL or SDK call: a URL parser resolves them out of `/v1/default/banks/<id>`, so the request
 * would reach another endpoint. The handlers that missed it were found one review comment at a
 * time, so this walks every `src/app/api/**\/route.ts` and fails on a handler that does not call one
 * of the validators below first, unless `NO_BANK_ID` lists it as taking no bank id.
 *
 * A call counts only when its 400 reaches the client before anything else runs (see
 * `answersFirst`). The first version counted any call anywhere in the handler, so a validator
 * behind a condition, in a callback, or with its result dropped passed the walk while ".." still
 * reached the URL builder. The second counted any later `return <result>` and ignored what ran
 * before the call, so an inverted guard (`if (!error) return error;`) or a fetch ahead of the
 * validator passed, and it read only `function` declarations, so a `const GET = async () => ...`
 * handler was never walked. The third checked only the statements before the call, not the code
 * beside it, so a fetch in another declaration of the validator's `const`, or in the `catch` or
 * `finally` of a `try` around it, passed. The fourth checked a `catch` and `finally` only for the
 * calls they make, so a `catch` that fell through to a fetch after the `try`, or a `finally` that
 * returned its own response over the 400, passed; a `try` is now accepted only in the shape the
 * routes use. The fifth skipped the functions defined in the code that runs first, so a fetch in a
 * callback that a request read runs (`request.json().catch(() => fetch(...))`) passed. The sixth
 * skipped every function in the validator's own arguments, so such a callback passed there too
 * (`respondWithBankSdk(request, context, await request.json().catch(() => fetch(...)), call)`); only
 * the functions a validator is handed, and runs after it validates, are skipped now. The seventh
 * checked the other declarations beside a guarded result only for their calls, so one that cleared
 * the result without a call (`let error = validateBankId(...), cleared = (error = null);`) passed;
 * a guarded result must now be the only declaration in its statement. The eighth chose the
 * handlers to check by the bank names in their text, so one that kept its bank id under another
 * name (`const bank = requireQueryBankId(request)`, as the knowledge-base node routes do) or reached
 * it through a method of a same-file object was never checked; every handler outside `NO_BANK_ID`
 * is checked now. It also let a success answer run before the validator (see `isErrorAnswer`),
 * accepted an `instanceof` guard on an async validator's unawaited promise (see `VALIDATORS`),
 * credited a local function named like a validator (see `misboundValidators`), and matched a guard
 * on its source text, so a braced one failed (see `isGuard`). The ninth skipped a function's
 * parameters, so a default that fetched before the body ran passed, and followed a helper call to
 * the top-level function of its name, so a local function of that name answering 200 passed (see
 * `misboundHelpers`). The tenth let a `catch` before the validator answer with any named status,
 * as the retain route relayed the status of the error it caught, so `{ status: code }` after
 * `const code = 200` in a `catch` passed; a status must now be a literal of 400 or more, and the
 * retain route reads its body in a `try` of its own and relays an error's status only after its
 * check.
 *
 * The walk guards against a handler that forgets or misplaces its validator. It does not check
 * which value a validator is given, or code that reassigns a bank id after its guard.
 */
type Validator = {
  fn: unknown;
  /** The module a route imports it from; any other binding of its name fails the walk. */
  module: string;
  /** The guard a route checks a stored result with; without one the result must be returned. */
  guard?: GuardForm;
};
type GuardForm = "truthy" | "instanceof";

/**
 * The shared validators. `validateBankId` returns a 400 or null and `requireQueryBankId` the bank
 * id or a 400, so each has its own guard (`if (bankIdError) return bankIdError;`, `if (bankId
 * instanceof NextResponse) return bankId;`). The others are async and answer the whole request, so
 * a route returns their result: an unawaited promise is never a `NextResponse`, so an `instanceof`
 * guard on one falls through and the 400 is lost. Each one's own test under `tests/lib/` shows it
 * refusing "." and ".." before it fetches.
 */
export const VALIDATORS = new Map<string, Validator>([
  [
    "validateBankId",
    { fn: routeValidation.validateBankId, module: "@/lib/route-validation", guard: "truthy" },
  ],
  [
    "requireQueryBankId",
    {
      fn: routeValidation.requireQueryBankId,
      module: "@/lib/route-validation",
      guard: "instanceof",
    },
  ],
  ["respondWithBankSdk", { fn: bankSdkRoute.respondWithBankSdk, module: "@/lib/bank-sdk-route" }],
  ["proxyDataplane", { fn: dataplaneProxy.proxyDataplane, module: "@/lib/dataplane-proxy" }],
  ["relayBankGet", { fn: dataplaneProxy.relayBankGet, module: "@/lib/dataplane-proxy" }],
  ["relayBankPost", { fn: dataplaneProxy.relayBankPost, module: "@/lib/dataplane-proxy" }],
  [
    "createKnowledgeBaseNode",
    { fn: knowledgeBaseProxy.createKnowledgeBaseNode, module: "@/lib/knowledge-base-proxy" },
  ],
]);

/**
 * The handlers that take no bank id (route file and export), with what each does instead. Every
 * other handler is checked. A listed handler that names a bank id (a `[bankId]`/`[agentId]` route
 * segment, or one of `BANK_NAMES` in its body or in a same-file helper it calls) fails, and so does
 * an entry no route has, so the list cannot hide a bank route or outlive one.
 */
export const NO_BANK_ID = new Map([
  ["auth/login/route.ts POST", "checks the access key and sets the session cookie"],
  ["auth/logout/route.ts POST", "clears the session cookie"],
  ["banks/route.ts GET", "lists the banks"],
  ["chunks/route.ts GET", "reads one chunk by its chunk id"],
  ["files/download/route.ts GET", "proxies only /v1/default/files/download/ paths"],
  ["health/route.ts GET", "checks the dataplane health"],
  ["list/route.ts DELETE", "always answers 501"],
  ["version/route.ts GET", "reads the dataplane version"],
]);

export const API_DIR = join(__dirname, "../../../src/app/api");
// `\b` treats `_` as a word character, so `bank_id` does not match inside `target_bank_id`.
const BANK_NAMES = /\b(bankId|bank_id|agentId|agent_id|targetBankId|target_bank_id)\b/;
const BANK_SEGMENT = /\[(bankId|agentId)\]/;

/**
 * Nodes a call may sit in and still run whenever the statement around it runs. Their other
 * children (another declaration in the same list) are checked as code that runs before the answer.
 * A `try` is accepted separately, in one shape only (see `answersFirst`).
 */
const STRAIGHT_LINE = new Set([
  ts.SyntaxKind.AwaitExpression,
  ts.SyntaxKind.ParenthesizedExpression,
  ts.SyntaxKind.VariableDeclarationList,
]);

/**
 * The calls a handler may make before its validator's answer reaches the client: reading the
 * request, encoding a path segment for the validator's own `suffix` argument, and answering a
 * missing parameter or, in a `catch` around a read, a failure (logging it and building a 500). Matched on the callee text with whitespace removed (`new X` for a constructor), so a new
 * route that reads the request through another receiver name needs an entry here; anything else
 * that runs first (a `fetch`, an SDK call, `dataplaneBankUrl`, `new URL`) fails the walk. Building
 * a response is allowed only as an error answer (see `isErrorAnswer`).
 */
const BEFORE_ANSWER = new Set([
  "JSON.parse",
  "NextResponse.json",
  "String",
  "console.error",
  "encodeURIComponent",
  "formData.get",
  "localizeApiErrorPayload",
  "missingQueryParam",
  "new NextResponse",
  "request.formData",
  "request.json",
  "request.json().catch",
  "request.nextUrl.searchParams.get",
  "searchParams.get",
]);

/** A route file's top-level function: `function name() {}` or `const name = () => {}`. */
type RouteFunction = ts.FunctionDeclaration | ts.ArrowFunction | ts.FunctionExpression;
type IdentifierCall = ts.CallExpression & { expression: ts.Identifier };

export function* routeFiles(dir: string): Generator<string> {
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

/**
 * Whether `nodes` call only what `BEFORE_ANSWER` lists, the bodies of the functions they define
 * included: a callback handed to a request read runs before the answer (`request.json().catch(cb)`
 * runs `cb` on a malformed body). The functions in `later` are not read, as they run only after the
 * answer (see `answersFirst`).
 */
function onlyRequestReads(nodes: readonly ts.Node[], later: ReadonlySet<ts.Node>): boolean {
  let clean = true;
  const visit = (node: ts.Node) => {
    if (later.has(node)) return;
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
      const callee = node.expression.getText().replace(/\s+/g, "");
      clean &&= BEFORE_ANSWER.has(ts.isNewExpression(node) ? `new ${callee}` : callee);
    }
    ts.forEachChild(node, visit);
  };
  nodes.forEach(visit);
  return clean;
}

/**
 * Whether `answer`, returned before a validator's answer, refuses the request: `missingQueryParam`
 * (always a 400), or `NextResponse.json(body, { status })` / `new NextResponse(body, { status })`
 * with a literal status of 400 or more (the `Response` constructor throws on one above 599) and no
 * spread that could replace it. A success answer that runs first (`if (body.dry_run) return
 * NextResponse.json({ ok: true })`) would serve the request with its bank id never checked. A named
 * status fails even in a `catch`: the walk does not track values, so the name could hold a success
 * status (see the tenth round in the file header).
 */
function isErrorAnswer(answer: ts.Expression | undefined): boolean {
  if (!answer || !(ts.isCallExpression(answer) || ts.isNewExpression(answer))) return false;
  const callee = answer.expression.getText().replace(/\s+/g, "");
  if (ts.isCallExpression(answer) && callee === "missingQueryParam") return true;
  const builds = ts.isCallExpression(answer)
    ? callee === "NextResponse.json"
    : callee === "NextResponse";
  const init = answer.arguments?.[1];
  if (!builds || !init || !ts.isObjectLiteralExpression(init)) return false;
  if (init.properties.some(ts.isSpreadAssignment)) return false;
  const status = init.properties.find(
    (p): p is ts.PropertyAssignment => ts.isPropertyAssignment(p) && p.name.getText() === "status"
  )?.initializer;
  return status !== undefined && ts.isNumericLiteral(status) && Number(status.text) >= 400;
}

/** Whether every return in `nodes`, outside the functions they define, is an error answer. */
function onlyErrorAnswers(nodes: readonly ts.Node[]): boolean {
  let clean = true;
  const visit = (node: ts.Node) => {
    if (ts.isFunctionLike(node)) return;
    if (ts.isReturnStatement(node)) clean &&= isErrorAnswer(node.expression);
    ts.forEachChild(node, visit);
  };
  nodes.forEach(visit);
  return clean;
}

/**
 * Whether `next` is the guard `form` names for the variable `declared`, matched on its syntax:
 * `if (declared) return declared;` (truthy) or `if (declared instanceof NextResponse) return
 * declared;` (instanceof), the return braced or not, alone in its braces.
 */
function isGuard(next: ts.Statement | undefined, declared: string, form: GuardForm): boolean {
  if (!next || !ts.isIfStatement(next)) return false;
  const isDeclared = (node: ts.Node) => ts.isIdentifier(node) && node.text === declared;
  const test = next.expression;
  const tested =
    form === "truthy"
      ? isDeclared(test)
      : ts.isBinaryExpression(test) &&
        test.operatorToken.kind === ts.SyntaxKind.InstanceOfKeyword &&
        isDeclared(test.left) &&
        ts.isIdentifier(test.right) &&
        test.right.text === "NextResponse";
  const then = next.thenStatement;
  const answer = ts.isBlock(then) && then.statements.length === 1 ? then.statements[0] : then;
  return (
    tested &&
    ts.isReturnStatement(answer) &&
    answer.expression !== undefined &&
    isDeclared(answer.expression)
  );
}

/**
 * Whether `call` is the first thing `fn` does on every path, apart from reading the request, and
 * whether its result reaches the client.
 *
 * - Only awaits, one variable declaration, a return, the function body or a try block may stand
 *   between the call and `fn`, so a call under an `if`, loop, callback or dead branch fails.
 * - A `try` around the call has no `finally` and its `catch` ends in a return, the shape every
 *   real route uses. A `finally` can replace the 400 with its own return, and a `catch` that falls
 *   through runs the code after the `try` with the validator skipped (an earlier read threw).
 * - Everything else that can run before its 400 reaches the client may only make `BEFORE_ANSWER`
 *   calls, so a fetch that runs first fails: its own arguments, the parameters of `fn` (a default
 *   runs before the body when the caller omits its argument), the statements before it in each
 *   enclosing block, and every other child of the nodes in between (another declaration in the same
 *   `const`, before or after it; the `catch` of a `try` around it, which runs when an earlier read
 *   throws), the callbacks these define included. The one exception is a function the validator is
 *   handed, as an argument or as a property of an object-literal argument: each validator runs it
 *   only after it validates (`respondWithBankSdk`'s `call`, `relayBankPost`'s `prepare`,
 *   `relayBankGet`'s `suffix`). A function anywhere else in an argument may run while the arguments
 *   are evaluated, before the validator starts.
 * - Every return in that code is an error answer (see `isErrorAnswer`).
 * - The result is returned directly (`return relayBankGet(...)`), or the variable it initialises,
 *   the only declaration in its statement, is returned by the very next statement, in the guard
 *   form `VALIDATORS` gives that validator (see `isGuard`), so a dropped 400, an inverted guard, a
 *   guard of the other validator's form, or a reassignment before the guard fails, another
 *   declaration beside it included. A same-file helper has no guard form, so its result is returned.
 */
function answersFirst(call: IdentifierCall, fn: RouteFunction): boolean {
  const beforeAnswer: ts.Node[] = [...fn.parameters];
  let statement: ts.VariableStatement | undefined;
  let declared: string | undefined;
  let returned = false;
  let child: ts.Node = call;
  for (let node: ts.Node = call.parent; node !== fn; child = node, node = node.parent) {
    if (ts.isBlock(node)) {
      const owner = node.parent;
      if (owner !== fn && !(ts.isTryStatement(owner) && owner.tryBlock === node)) return false;
      // The statements after the call run once its answer is out (the guard is checked below).
      beforeAnswer.push(
        ...node.statements.slice(0, node.statements.indexOf(child as ts.Statement))
      );
      continue;
    }
    if (ts.isReturnStatement(node)) {
      returned = true;
    } else if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) {
      declared = node.name.text;
    } else if (ts.isVariableStatement(node)) {
      statement = node;
    } else if (ts.isTryStatement(node)) {
      const last = node.catchClause?.block.statements.at(-1);
      if (node.finallyBlock || !last || !ts.isReturnStatement(last)) return false;
    } else if (!STRAIGHT_LINE.has(node.kind)) {
      return false;
    }
    ts.forEachChild(node, (other) => {
      if (other !== child) beforeAnswer.push(other);
    });
  }
  // An arrow function with an expression body returns that expression.
  const body = fn.body;
  if (body && child === body && !ts.isBlock(body)) returned = true;
  const handed = call.arguments
    .flatMap((arg) =>
      ts.isObjectLiteralExpression(arg)
        ? arg.properties.flatMap((p) => (ts.isPropertyAssignment(p) ? [p.initializer] : []))
        : [arg]
    )
    .filter((node) => ts.isArrowFunction(node) || ts.isFunctionExpression(node));
  if (!onlyRequestReads([...call.arguments, ...beforeAnswer], new Set(handed))) return false;
  if (!onlyErrorAnswers(beforeAnswer)) return false;
  if (returned) return true;
  const form = VALIDATORS.get(call.expression.text)?.guard;
  if (!form || !statement || !declared || !ts.isBlock(statement.parent)) return false;
  // The other declarations were checked above only for their calls, and one can clear the result
  // without a call (`let error = validateBankId(...), cleared = (error = null);`). Every real route
  // declares a validator result on its own, so that is the only shape accepted.
  if (statement.declarationList.declarations.length !== 1) return false;
  const siblings = statement.parent.statements;
  return isGuard(siblings[siblings.indexOf(statement) + 1], declared, form);
}

/**
 * `start` and the same-file helpers it reaches through the calls `follow` accepts. A call is
 * followed by its name to the top-level function of that name, which is its binding only while
 * nothing else binds the name: `misboundHelpers` fails a file where something does.
 */
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
 * An entry for each place a validator's name is bound to something other than its own unrenamed
 * import (a local function or variable, a parameter, a renamed import, an import from another
 * module), or used other than as a callee. The walk credits a validator by its name, so a local
 * `validateBankId` that checks nothing would otherwise count as validation. A call with no import
 * at all fails the type check, so the import itself is not required.
 */
function misboundValidators(file: ts.SourceFile): string[] {
  const failing: string[] = [];
  const visit = (node: ts.Node) => {
    const validator = ts.isIdentifier(node) ? VALIDATORS.get(node.text) : undefined;
    if (validator) {
      const parent = node.parent;
      const declaration = ts.isImportSpecifier(parent) ? parent.parent.parent.parent : undefined;
      const imported =
        ts.isImportSpecifier(parent) &&
        parent.name === node &&
        !parent.propertyName &&
        declaration !== undefined &&
        ts.isImportDeclaration(declaration) &&
        ts.isStringLiteral(declaration.moduleSpecifier) &&
        declaration.moduleSpecifier.text === validator.module;
      const called = ts.isCallExpression(parent) && parent.expression === node;
      if (!imported && !called)
        failing.push(`validator ${node.getText()} bound outside its import`);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return failing;
}

/**
 * An entry for each place the name of a top-level function in `declared` (name to its declaring
 * identifier) is bound again (a local function or variable, a parameter, an earlier top-level
 * declaration, an assignment, a property name) or used other than as a callee. `reachable`
 * credits a call with the top-level function its name names, so a local `proxyThing` answering
 * 200 would otherwise pass on the validator of the top-level one. Like `misboundValidators`, this
 * lists the uses that are safe, not the ones that are not.
 */
function misboundHelpers(
  file: ts.SourceFile,
  declared: ReadonlyMap<string, ts.Identifier>
): string[] {
  const failing: string[] = [];
  const visit = (node: ts.Node) => {
    const own = ts.isIdentifier(node) ? declared.get(node.text) : undefined;
    if (own && own !== node) {
      const parent = node.parent;
      const called = ts.isCallExpression(parent) && parent.expression === node;
      // `export { proxyThing as GET }` already fails as an unsupported export.
      const reexported = ts.isExportSpecifier(parent);
      if (!called && !reexported) {
        failing.push(`helper ${own.text} bound outside its declaration`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return failing;
}

/**
 * The exported handlers of one route file that do not validate a bank id first and are not in
 * `NO_BANK_ID`, the listed ones that name a bank id anyway, an entry for each misbound validator
 * name (see `misboundValidators`) and misbound helper name (see `misboundHelpers`), and an
 * `unsupported export` entry for each export the walk cannot see into. `routePath` is the file's
 * path under `src/app/api`, which carries the dynamic segments, with either separator (`relative()`
 * gives `\` on Windows); each handler's `NO_BANK_ID` key, written with `/`, is added to `seen`.
 */
export function unvalidatedHandlers(
  source: string,
  routePath: string,
  seen?: Set<string>
): string[] {
  const route = routePath.replaceAll("\\", "/");
  const file = ts.createSourceFile(route, source, ts.ScriptTarget.Latest, true);
  const local = new Map<string, RouteFunction>();
  const declared = new Map<string, ts.Identifier>();
  const handlers: string[] = [];
  const failing = misboundValidators(file);
  for (const statement of file.statements) {
    const exported =
      ts.canHaveModifiers(statement) &&
      (ts.getModifiers(statement) ?? []).some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
    if (ts.isFunctionDeclaration(statement) && statement.name) {
      local.set(statement.name.text, statement);
      declared.set(statement.name.text, statement.name);
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
          declared.set(declaration.name.text, declaration.name);
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
  failing.push(...misboundHelpers(file, declared));
  const pathScoped = BANK_SEGMENT.test(route);
  for (const name of handlers) {
    const handler = local.get(name)!;
    const key = `${route} ${name}`;
    seen?.add(key);
    if (NO_BANK_ID.has(key)) {
      // Any same-file helper the handler calls (proxyAlias, proxyDirective, ...) can name a bank id.
      // Choosing the handlers to check by these names missed a bank id kept under another name, so
      // they only check the list now.
      const reached = reachable(handler, local, () => true);
      const text = [...reached].map((fn) => fn.getText(file)).join("\n");
      if (pathScoped || BANK_NAMES.test(text)) failing.push(`${name} listed in NO_BANK_ID`);
      continue;
    }
    // A helper's validator guards the handler only when the handler calls that helper first and
    // returns its result, so validation follows only those calls.
    const guarded = reachable(handler, local, answersFirst);
    const validated = [...guarded].some((fn) =>
      identifierCalls(fn).some(
        (call) => VALIDATORS.has(call.expression.text) && answersFirst(call, fn)
      )
    );
    if (!validated) {
      failing.push(name);
    }
  }
  return failing;
}
