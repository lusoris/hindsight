import { proxyDataplane, type MentalModelRouteContext } from "@/lib/dataplane-proxy";

/** Runs a dry-run mental model refresh on the dataplane without persisting changes. */
export async function POST(request: Request, { params }: MentalModelRouteContext) {
  const { bankId, mentalModelId } = await params;
  return proxyDataplane(request, {
    action: "dry-running mental model refresh",
    method: "POST",
    suffix: `/mental-models/${encodeURIComponent(mentalModelId)}/dry-run-refresh`,
    bankId,
    required: [bankId, mentalModelId],
    missing: {
      error: "bank_id and mental_model_id are required",
      errorKey: "api.errors.validation.bankAndMentalModelIdRequired",
    },
    failure: {
      error: "Failed to dry-run mental model refresh",
      errorKey: "api.errors.mentalModels.dryRunRefresh",
    },
    upstreamTextAsError: true,
  });
}
