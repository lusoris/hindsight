/**
 * Minimal Hindsight client surface used by this integration.
 *
 * It is a structural subset of `@vectorize-io/hindsight-client` so that
 * consumers can pass a real client instance without this package taking a hard
 * dependency on it. Only `recall` and `retain` are required.
 */

import type {
  Budget,
  FactType,
  RecallResult,
  HindsightRetainClient,
} from "../../_shared/ts/hindsight-client-types.js";
export type {
  Budget,
  FactType,
  RecallResult,
  RetainResponse,
} from "../../_shared/ts/hindsight-client-types.js";

export interface RecallResponse {
  results: RecallResult[];
  trace?: Record<string, unknown> | null;
  entities?: Record<string, unknown> | null;
  chunks?: Record<string, unknown> | null;
}

/**
 * Hindsight client interface - matches `@vectorize-io/hindsight-client`.
 */
export interface HindsightClient extends HindsightRetainClient {
  recall(
    bankId: string,
    query: string,
    options?: {
      types?: FactType[];
      maxTokens?: number;
      budget?: Budget;
      includeEntities?: boolean;
      includeChunks?: boolean;
    }
  ): Promise<RecallResponse>;
}
