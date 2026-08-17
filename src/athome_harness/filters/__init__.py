"""Filter-map schema, validation, and query encoding (M2).

Public surface for the milestone:

* ``map_schema`` - versioned schema, conditions metadata, validation.
* ``encoder``   - SearchPlan + validated map -> POST parameter pairs.
"""

from __future__ import annotations

from athome_harness.filters import encoder, map_schema

__all__ = ["encoder", "map_schema"]
