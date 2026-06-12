from typing import Dict
from dataclasses import dataclass


@dataclass
class UnitDefinition:
    base_unit: str
    to_base: float
    from_base: float


class UnitConverter:
    """Converts between different unit systems"""

    # Base units: kg, mmol/L, mmHg, cm
    UNIT_CONVERSIONS: Dict[str, Dict[str, UnitDefinition]] = {
        "weight": {
            "kg": UnitDefinition("kg", 1.0, 1.0),
            "lbs": UnitDefinition("kg", 0.453592, 2.20462),
            "st": UnitDefinition("kg", 6.35029, 0.157473),
        },
        "height": {
            "cm": UnitDefinition("cm", 1.0, 1.0),
            "m": UnitDefinition("cm", 100.0, 0.01),
            "in": UnitDefinition("cm", 2.54, 0.393701),
            "ft": UnitDefinition("cm", 30.48, 0.0328084),
        },
        "glucose": {
            "mmol/L": UnitDefinition("mmol/L", 1.0, 1.0),
            "mg/dL": UnitDefinition("mmol/L", 0.05551, 18.0182),
        },
        "blood_pressure": {"mmHg": UnitDefinition("mmHg", 1.0, 1.0)},
    }

    def convert(
        self, value: float, from_unit: str, to_unit: str, category: str
    ) -> float:
        """Convert value from one unit to another"""
        if from_unit == to_unit:
            return value

        conversions = self.UNIT_CONVERSIONS.get(category)
        if not conversions:
            raise ValueError(f"Unknown unit category: {category}")

        from_def = conversions.get(from_unit)
        to_def = conversions.get(to_unit)

        if not from_def or not to_def:
            raise ValueError(f"Unsupported units: {from_unit}, {to_unit}")

        # Convert to base unit, then to target unit
        base_value = value * from_def.to_base
        return base_value * to_def.from_base

    def convert_to_user_unit(
        self, value: float, from_unit: str, user_unit: str, category: str
    ) -> float:
        """Convert value to user's preferred unit"""
        return self.convert(value, from_unit, user_unit, category)

    def convert_to_base_unit(
        self, value: float, from_unit: str, category: str
    ) -> float:
        """Convert value to base unit for storage"""
        conversions = self.UNIT_CONVERSIONS.get(category)
        if not conversions:
            raise ValueError(f"Unknown unit category: {category}")

        unit_def = conversions.get(from_unit)
        if not unit_def:
            raise ValueError(f"Unsupported unit: {from_unit}")

        return value * unit_def.to_base

    def get_supported_units(self, category: str) -> list:
        """Get list of supported units for a category"""
        conversions = self.UNIT_CONVERSIONS.get(category)
        if not conversions:
            return []
        return list(conversions.keys())
