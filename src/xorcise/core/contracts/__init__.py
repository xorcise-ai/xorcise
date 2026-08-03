"""xorcise.core.contracts — wire DTOs shared across process boundaries.

LAYER: LEAF. Imports NOTHING internal (stdlib + pydantic only). In-process ABCs
('ports') do NOT live here — they live beside their owner.
"""
