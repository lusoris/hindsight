"""Shared pieces for tests that stand in for ``ProcessedFact`` with a ``SimpleNamespace``.

The memories store reads every ``ProcessedFact`` attribute it writes, so a stand-in
has to carry the record-contract fields too. Taking them from the dataclass keeps the
stand-ins on the real defaults instead of a second copy of them.
"""

import dataclasses

from hindsight_api.engine.retain.types import ProcessedFact

_RECORD_CONTRACT_FIELDS = (
    "provenance_class",
    "write_authority",
    "observed_at",
    "fetched_at",
    "ttl_seconds",
    "scope",
    "reset_condition",
    "record_status",
)

RECORD_CONTRACT_DEFAULTS: dict[str, object] = {
    f.name: f.default for f in dataclasses.fields(ProcessedFact) if f.name in _RECORD_CONTRACT_FIELDS
}
assert set(RECORD_CONTRACT_DEFAULTS) == set(_RECORD_CONTRACT_FIELDS), "ProcessedFact lost a record-contract field"
