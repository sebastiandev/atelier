"""GET /api/providers — descriptor for each registered provider.

The frontend's new-agent dialog reads this to render the right form
fields per provider (primary selector + extra option enums). Both the
descriptor and the request validator are produced by the same ``Spec``
object, so the wire format and the server's validation cannot drift.
"""

from fastapi import APIRouter

from src.domain.agents import SPECS, ProviderDescriptor

router = APIRouter()


@router.get("/providers", response_model=list[ProviderDescriptor])
def list_providers() -> list[ProviderDescriptor]:
    return [spec.describe() for spec in SPECS.values()]
