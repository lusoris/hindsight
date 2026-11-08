import { proxyDataplane, type ApiFailure, type DataplaneCall } from "@/lib/dataplane-proxy";

const MISSING_PAYLOAD: ApiFailure = {
  error: "bank_id is required",
  errorKey: "api.errors.validation.bankIdRequired",
};

type HandlerContext = { params: Promise<{ bankId: string; alias: string }> };

/**
 * Resolves the ids and the dataplane path once for PATCH and DELETE, which differ only in the
 * call options. Only the bank id is required: the alias always comes from the route segment.
 */
async function proxyAlias(request: Request, { params }: HandlerContext, call: DataplaneCall) {
  const { bankId, alias } = await params;
  return proxyDataplane(request, {
    required: [bankId],
    missing: MISSING_PAYLOAD,
    bankId,
    suffix: `/aliases/${encodeURIComponent(alias)}`,
    ...call,
  });
}

/** Forwards a primary-alias update (shown in place of the bank id) to the dataplane. */
export async function PATCH(request: Request, context: HandlerContext) {
  return proxyAlias(request, context, {
    method: "PATCH",
    failure: { error: "Failed to update bank alias", errorKey: "api.errors.bankAliases.update" },
    action: "setting primary bank alias",
    forwardJsonBody: true,
  });
}

/** Removes one alias from the bank on the dataplane. */
export async function DELETE(request: Request, context: HandlerContext) {
  return proxyAlias(request, context, {
    method: "DELETE",
    failure: { error: "Failed to delete bank alias", errorKey: "api.errors.bankAliases.delete" },
    action: "deleting bank alias",
  });
}
