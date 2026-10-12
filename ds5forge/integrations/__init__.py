"""Game integrations. To add a game: subclass ``Integration`` and register it here."""

from .assetto import AssettoIntegration
from .base import Integration, Outputs
from .f1 import F1Integration
from .forza import ForzaIntegration
from .outgauge import OutGaugeIntegration

REGISTRY: dict[str, type[Integration]] = {
    cls.id: cls for cls in (ForzaIntegration, F1Integration, AssettoIntegration, OutGaugeIntegration)
}

__all__ = ["REGISTRY", "Integration", "Outputs"]
