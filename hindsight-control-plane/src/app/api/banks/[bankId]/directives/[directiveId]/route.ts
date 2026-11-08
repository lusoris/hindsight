import { proxyDataplane, type ApiFailure, type DataplaneCall } from "@/lib/dataplane-proxy";

const MISSING_PAYLOAD: ApiFailure = {
  error: "bank_id and directive_id are required",
  errorKey: "api.errors.validation.bankAndDirectiveIdRequired",
};

type HandlerContext = { params: Promise<{ bankId: string; directiveId: string }> };

/**
 * Resolves the ids and the dataplane path once for GET, PATCH and DELETE, which differ
 * only in the call options.
 */
async function proxyDirective(request: Request, { params }: HandlerContext, call: DataplaneCall) {
  const { bankId, directiveId } = await params;
  return proxyDataplane(request, {
    required: [bankId, directiveId],
    missing: MISSING_PAYLOAD,
    bankId,
    suffix: `/directives/${encodeURIComponent(directiveId)}`,
    ...call,
  });
}

/** Proxies one directive from the dataplane. */
export async function GET(request: Request, context: HandlerContext) {
  return proxyDirective(request, context, {
    method: "GET",
    failure: { error: "Failed to get directive", errorKey: "api.errors.directives.fetch" },
    action: "getting directive",
  });
}

/** Forwards a directive update to the dataplane. */
export async function PATCH(request: Request, context: HandlerContext) {
  return proxyDirective(request, context, {
    method: "PATCH",
    failure: { error: "Failed to update directive", errorKey: "api.errors.directives.update" },
    action: "updating directive",
    forwardJsonBody: true,
    upstreamTextAsError: true,
  });
}

/** Deletes one directive on the dataplane. */
export async function DELETE(request: Request, context: HandlerContext) {
  return proxyDirective(request, context, {
    method: "DELETE",
    failure: { error: "Failed to delete directive", errorKey: "api.errors.directives.delete" },
    action: "deleting directive",
    upstreamTextAsError: true,
    successBody: { success: true },
  });
}
