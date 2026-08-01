"""Provider-independent live travel inventory and cart domain."""

from .service import InventoryService, get_inventory_service

__all__ = ["InventoryService", "get_inventory_service"]
