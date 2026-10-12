"""Game integrations. To add a game: subclass ``Integration`` and register it here."""

from .assetto import AssettoIntegration
from .base import Integration, Outputs
from .f1 import F1Integration
from .forza import ForzaIntegration
from .kingdom_hearts import KingdomHeartsIntegration
from .outgauge import OutGaugeIntegration

REGISTRY: dict[str, type[Integration]] = {
    cls.id: cls for cls in (KingdomHeartsIntegration, ForzaIntegration, F1Integration, AssettoIntegration,
                                OutGaugeIntegration)
}

__all__ = ["REGISTRY", "Integration", "Outputs"]
