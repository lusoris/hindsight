import { readFileSync } from "node:fs";
import { relative } from "node:path";
import { describe, expect, it } from "vitest";
import { API_DIR, NO_BANK_ID, VALIDATORS, routeFiles, unvalidatedHandlers } from "./route-walk";

/**
 * Runs the route walk (`route-walk.ts`, which says what it checks and why) over every real route
 * handler, then over planted handlers that each break one of its rules. The walk sits in its own
 * module so each file stays one concern: the walk there, the cases here.
 */
describe("bank id validation in every route handler", () => {
  it("names only validators the shared modules still export", () => {
    for (const [name, validator] of VALIDATORS) {
      expect(validator.fn, name).toBeTypeOf("function");
    }
  });

  it("finds no route handler that skips bank id validation", () => {
    const failing: string[] = [];
    const seen = new Set<string>();
    for (const path of routeFiles(API_DIR)) {
      const routePath = relative(API_DIR, path);
      for (const name of unvalidatedHandlers(readFileSync(path, "utf8"), routePath, seen)) {
        failing.push(`${routePath} ${name}`);
      }
    }
    // Guards against a walk that silently finds nothing (moved directory, a route file it skips).
    // `seen` holds a key for each handler the walk checked, whatever form its export takes.
    expect(seen.size).toBeGreaterThan(50);
    expect(failing).toEqual([]);
    // An entry for a handler that no longer exists would let a new one of that name skip the check.
    expect([...NO_BANK_ID.keys()].filter((key) => !seen.has(key))).toEqual([]);
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
        label: "a result cleared by another declaration in its statement",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          let bankIdError = validateBankId(request, bankId),
            cleared = (bankIdError = null);
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
        label: "a URL built before the validator",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          const url = new URL(\`/v1/default/banks/\${bankId}/thing\`, "http://dataplane");
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) return bankIdError;
          return fetch(url);
        }`,
      },
      {
        label: "a fetch declared before the validator in its const",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          const warm = await fetch(dataplaneBankUrl(bankId, "/warm")),
            bankIdError = validateBankId(request, bankId);
          if (bankIdError) return bankIdError;
          return warm;
        }`,
      },
      {
        label: "a fetch declared after the validator in its const",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId),
            warm = await fetch(dataplaneBankUrl(bankId, "/warm"));
          if (bankIdError) return bankIdError;
          return warm;
        }`,
      },
      {
        label: "a fetch in a finally around the validator",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          try {
            const bankIdError = validateBankId(request, bankId);
            if (bankIdError) return bankIdError;
            return fetch(dataplaneBankUrl(bankId, "/thing"));
          } catch (error) {
            return NextResponse.json({ error: String(error) }, { status: 500 });
          } finally {
            await fetch(dataplaneBankUrl(bankId, "/warm"));
          }
        }`,
      },
      {
        label: "a catch that fetches when the body read throws",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function POST(request, { params }) {
          const { bankId } = await params;
          try {
            const body = await request.json();
            const bankIdError = validateBankId(request, bankId);
            if (bankIdError) return bankIdError;
            return fetch(dataplaneBankUrl(bankId, "/thing"), { body });
          } catch {
            return fetch(dataplaneBankUrl(bankId, "/fallback"));
          }
        }`,
      },
      {
        label: "a catch that falls through to a fetch after the try",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function POST(request, { params }) {
          const { bankId } = await params;
          try {
            const body = await request.json();
            const bankIdError = validateBankId(request, bankId);
            if (bankIdError) return bankIdError;
          } catch {
            console.error("body read failed");
          }
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a finally that returns over the validator's 400",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          try {
            const bankIdError = validateBankId(request, bankId);
            if (bankIdError) return bankIdError;
            return fetch(dataplaneBankUrl(bankId, "/thing"));
          } catch (error) {
            return NextResponse.json({ error: String(error) }, { status: 500 });
          } finally {
            return NextResponse.json({});
          }
        }`,
      },
      {
        // The error-answer check passes this return, so only the rule against a finally catches it.
        label: "a finally that answers 500 over the validator's 400",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          try {
            const bankIdError = validateBankId(request, bankId);
            if (bankIdError) return bankIdError;
            return fetch(dataplaneBankUrl(bankId, "/thing"));
          } catch (error) {
            return NextResponse.json({ error: String(error) }, { status: 500 });
          } finally {
            return NextResponse.json({ error: "Failed" }, { status: 500 });
          }
        }`,
      },
      {
        label: "a fetch in a callback a body read runs first",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function POST(request, { params }) {
          const { bankId } = await params;
          const body = await request.json().catch(() => fetch(dataplaneBankUrl(bankId, "/warm")));
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) return bankIdError;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a fetch in a callback a validator argument runs first",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function POST(request, context) {
          return respondWithBankSdk(
            request,
            context,
            await request.json().catch(() => fetch(dataplaneBankUrl("default", "/warm"))),
            (bankId) => sdk.runThing({ path: { bank_id: bankId } })
          );
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
        label: "a helper whose parameter default fetches before it validates",
        path: "banks/[bankId]/thing/[id]/route.ts",
        source: `async function proxyThing(request, { params }, warm = fetch(dataplaneBankUrl("default", "/warm"))) {
          const { bankId, id } = await params;
          return proxyDataplane(request, { bankId, required: [bankId, id] });
        }
        export async function PATCH(request, context) {
          return proxyThing(request, context);
        }`,
      },
      {
        label: "a handler whose parameter default fetches before it validates",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }, warm = fetch(dataplaneBankUrl("default", "/warm"))) {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) return bankIdError;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a local function shadowing a validating helper",
        path: "banks/[bankId]/thing/[id]/route.ts",
        source: `async function proxyThing(request, { params }) {
          const { bankId, id } = await params;
          return proxyDataplane(request, { bankId, required: [bankId, id] });
        }
        export async function PATCH(request, context) {
          const proxyThing = async () => NextResponse.json({ ok: true });
          return proxyThing(request, context);
        }`,
      },
      {
        label: "a parameter shadowing a validating helper",
        path: "banks/[bankId]/thing/[id]/route.ts",
        source: `async function proxyThing(request, { params }) {
          const { bankId, id } = await params;
          return proxyDataplane(request, { bankId, required: [bankId, id] });
        }
        export async function PATCH(request, context, proxyThing = async () => NextResponse.json({ ok: true })) {
          return proxyThing(request, context);
        }`,
      },
      {
        label: "a validating helper reassigned before the handler calls it",
        path: "banks/[bankId]/thing/[id]/route.ts",
        source: `let proxyThing = async (request, { params }) => {
          const { bankId, id } = await params;
          return proxyDataplane(request, { bankId, required: [bankId, id] });
        };
        export async function PATCH(request, context) {
          proxyThing = async () => NextResponse.json({ ok: true });
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
      {
        label: "a success answer before the validator",
        path: "recall/route.ts",
        source: `export async function POST(request) {
          const body = await request.json();
          if (body.dry_run) return NextResponse.json({ ok: true });
          const bankIdError = validateBankId(request, body.bank_id);
          if (bankIdError) return bankIdError;
          return sdk.recall({ path: { bank_id: body.bank_id } });
        }`,
      },
      {
        label: "a catch that answers success when the body read throws",
        path: "recall/route.ts",
        source: `export async function POST(request) {
          try {
            const body = await request.json();
            const bankIdError = validateBankId(request, body.bank_id);
            if (bankIdError) return bankIdError;
            return sdk.recall({ path: { bank_id: body.bank_id } });
          } catch {
            return NextResponse.json({ ok: true }, { status: 200 });
          }
        }`,
      },
      {
        label: "an answer before the validator whose status is a name outside a catch",
        path: "recall/route.ts",
        source: `export async function POST(request) {
          const body = await request.json();
          const code = 200;
          if (body.dry_run) return NextResponse.json({ ok: true }, { status: code });
          const bankIdError = validateBankId(request, body.bank_id);
          if (bankIdError) return bankIdError;
          return sdk.recall({ path: { bank_id: body.bank_id } });
        }`,
      },
      {
        label: "an error status a spread can replace",
        path: "recall/route.ts",
        source: `export async function POST(request) {
          const body = await request.json();
          if (body.dry_run) return NextResponse.json({ ok: true }, { status: 400, ...body.init });
          const bankIdError = validateBankId(request, body.bank_id);
          if (bankIdError) return bankIdError;
          return sdk.recall({ path: { bank_id: body.bank_id } });
        }`,
      },
      {
        label: "an async validator's unawaited result guarded as a response",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function POST(request, context) {
          const answer = respondWithBankSdk(request, context, "Failed to run thing", (bankId) =>
            sdk.runThing({ path: { bank_id: bankId } })
          );
          if (answer instanceof NextResponse) return answer;
          return fetch(DATAPLANE_URL);
        }`,
      },
      {
        label: "a query bank id under the truthy guard",
        path: "documents/route.ts",
        source: `export async function GET(request) {
          const bankId = requireQueryBankId(request);
          if (bankId) return bankId;
          return sdk.listDocuments({ path: { bank_id: bankId } });
        }`,
      },
      {
        label: "a guard that returns another answer",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) return NextResponse.json({ ok: true });
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a query bank id guarded against another class",
        path: "documents/route.ts",
        source: `export async function GET(request) {
          const bankId = requireQueryBankId(request);
          if (bankId instanceof URL) return bankId;
          return sdk.listDocuments({ path: { bank_id: bankId } });
        }`,
      },
      {
        label: "a query bank id compared to NextResponse",
        path: "documents/route.ts",
        source: `export async function GET(request) {
          const bankId = requireQueryBankId(request);
          if (bankId === NextResponse) return bankId;
          return sdk.listDocuments({ path: { bank_id: bankId } });
        }`,
      },
      {
        label: "a braced guard that fetches before it returns",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) {
            await fetch(dataplaneBankUrl(bankId, "/warm"));
            return bankIdError;
          }
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a handler that delegates to a method of a same-file object",
        path: "thing/route.ts",
        source: `const actions = {
          async run(request) {
            const body = await request.json();
            return fetch(dataplaneBankUrl(body.bank_id, "/thing"));
          },
        };
        export async function POST(request) {
          return actions.run(request);
        }`,
      },
      {
        label: "a handler that keeps its query bank id under another name",
        path: "knowledge-base/nodes/[nodeId]/route.ts",
        source: `export async function DELETE(request, { params }) {
          const { nodeId } = await params;
          const bank = bankFromQuery(request);
          return fetch(dataplaneBankUrl(bank, \`/knowledge-base/nodes/\${nodeId}\`));
        }`,
      },
      {
        label: "a NO_BANK_ID handler that reads a bank id",
        path: "version/route.ts",
        source: `export async function GET(request) {
          const bankId = request.nextUrl.searchParams.get("bank_id");
          return fetch(dataplaneBankUrl(bankId, "/version"));
        }`,
      },
      {
        label: "a validator name bound to a local function",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const validateBankId = (request, bankId) => null;
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) return bankIdError;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a validator imported from another module",
        path: "banks/[bankId]/thing/route.ts",
        source: `import { validateBankId } from "@/lib/other-validation";
        export async function GET(request, { params }) {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) return bankIdError;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "another export imported under a validator's name",
        path: "banks/[bankId]/thing/route.ts",
        source: `import { missingQueryParam as validateBankId } from "@/lib/route-validation";
        export async function GET(request, { params }) {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) return bankIdError;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
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
        label: "a validator handed the SDK call it runs after it validates",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function POST(request, context) {
          return respondWithBankSdk(request, context, "Failed to run thing", (bankId) =>
            sdk.runThing({ path: { bank_id: bankId } })
          );
        }`,
      },
      {
        label: "a relay handed a suffix it builds after it validates",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, context) {
          return relayBankGet(request, context, {
            action: "fetching thing",
            failure: { error: "Failed to fetch thing", errorKey: "api.errors.thing" },
            suffix: (q) => \`/thing?\${forwardParams(q, { single: ["limit"] }).toString()}\`,
          });
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
            console.error("thing failed", error);
            return NextResponse.json({ error: String(error) }, { status: 500 });
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
      {
        label: "a braced guard",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }) {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) {
            return bankIdError;
          }
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a catch that relays the caught error's status",
        path: "memories/retain/route.ts",
        source: `export async function POST(request) {
          try {
            const body = await request.json();
            const bankIdError = validateBankId(request, body.bank_id);
            if (bankIdError) return bankIdError;
            return sdk.retainMemories({ path: { bank_id: body.bank_id } });
          } catch (error) {
            const statusCode = error.statusCode;
            if (statusCode && typeof statusCode === "number") {
              return NextResponse.json({ error: String(error) }, { status: statusCode });
            }
            return NextResponse.json({ error: String(error) }, { status: 500 });
          }
        }`,
      },
      {
        label: "a handler with its validator's own import",
        path: "banks/[bankId]/thing/route.ts",
        source: `import { NextResponse } from "next/server";
        import { validateBankId } from "@/lib/route-validation";
        export async function GET(request, { params }) {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) return bankIdError;
          return fetch(dataplaneBankUrl(bankId, "/thing"));
        }`,
      },
      {
        label: "a parameter default that only reads the request",
        path: "banks/[bankId]/thing/route.ts",
        source: `export async function GET(request, { params }, limit = request.nextUrl.searchParams.get("limit")) {
          const { bankId } = await params;
          const bankIdError = validateBankId(request, bankId);
          if (bankIdError) return bankIdError;
          return fetch(dataplaneBankUrl(bankId, "/thing?limit=" + limit));
        }`,
      },
      {
        label: "a listed handler under a Windows route path",
        path: "list\\route.ts",
        source: `export async function DELETE() {
          return NextResponse.json({ error: "Not implemented" }, { status: 501 });
        }`,
      },
      {
        label: "a knowledge-base node created through its shared helper",
        path: "knowledge-base/folders/route.ts",
        source: `import { createKnowledgeBaseNode } from "@/lib/knowledge-base-proxy";
        export async function POST(request) {
          return createKnowledgeBaseNode(request, "folders", { error: "Failed", errorKey: "api.errors.kb" });
        }`,
      },
    ])("passes $label", ({ path, source }) => {
      expect(unvalidatedHandlers(source, path)).toEqual([]);
    });
  });
});
