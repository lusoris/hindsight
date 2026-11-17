import { assertType, expectTypeOf, it } from "vitest";
import type { HindsightRetainClient } from "../../_shared/ts/hindsight-client-types.js";
import type { HindsightClient } from "./types.js";

it("accepts a structural retain client without an SDK dependency", () => {
  const client = {
    async retain(bankId, _content, options) {
      return {
        success: true,
        bank_id: bankId,
        items_count: 1,
        async: options?.async ?? false,
      };
    },
  } satisfies HindsightRetainClient;

  assertType<HindsightClient["retain"]>(client.retain);
  expectTypeOf<HindsightClient["retain"]>().toEqualTypeOf<HindsightRetainClient["retain"]>();

  // @ts-expect-error A structural retain client must return the full response.
  assertType<HindsightClient["retain"]>(async () => ({ success: true }));
});
