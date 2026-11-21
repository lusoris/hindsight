/**
 * Structural subset of `@vectorize-io/hindsight-client` accepted by these
 * integrations, so a real client instance type-checks without a dependency.
 * Each importer bundles these declarations into its own dist via tsup.
 * A change here ships in the next release of every importing package.
 */

/** Budget levels for recall/reflect operations. */
export type Budget = "low" | "mid" | "high";

/** Fact types for filtering recall results. */
export type FactType = "world" | "experience" | "observation";

export interface RecallResult {
  id: string;
  text: string;
  type?: string | null;
  entities?: string[] | null;
  context?: string | null;
  occurred_start?: string | null;
  occurred_end?: string | null;
  mentioned_at?: string | null;
  document_id?: string | null;
  metadata?: Record<string, string> | null;
  chunk_id?: string | null;
}

export interface EntityState {
  entity_id: string;
  canonical_name: string;
  observations: Array<{ text: string; mentioned_at?: string | null }>;
}

export interface ReflectFact {
  id?: string | null;
  text: string;
  type?: string | null;
  context?: string | null;
  occurred_start?: string | null;
  occurred_end?: string | null;
}

export interface RetainResponse {
  success: boolean;
  bank_id: string;
  items_count: number;
  async: boolean;
}

export interface HindsightRetainOptions {
  timestamp?: Date | string;
  context?: string;
  metadata?: Record<string, string>;
  documentId?: string;
  tags?: string[];
  async?: boolean;
}

export interface HindsightRetainClient {
  retain(
    bankId: string,
    content: string,
    options?: HindsightRetainOptions
  ): Promise<RetainResponse>;
}
