"""
Data Collectors for Rental Portals
"""
from .nobroker import NoBrokerCollector
from .housing import HousingCollector
from .ninety_nine_acres import NinetyNineAcresCollector

__all__ = [
    "NoBrokerCollector",
    "HousingCollector",
    "NinetyNineAcresCollector"
]
