from typing import List, Dict, Any
from statistics import mean, stdev


class AnomalyDetector:
    """Detects anomalies in health data trends"""

    def __init__(self, threshold_std: float = 2.0):
        self.threshold_std = threshold_std

    def detect_biomarker_anomalies(
        self, historical_values: List[Dict[str, Any]], new_value: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Detect anomalies in biomarker trends"""
        anomalies = []

        if len(historical_values) < 3:
            return anomalies

        # Extract values
        values = [v["value"] for v in historical_values]
        new_val = new_value["value"]

        # Calculate statistics
        avg = mean(values)
        std = stdev(values) if len(values) > 1 else 0

        # Check if new value is anomalous
        if std > 0:
            z_score = (new_val - avg) / std
            if abs(z_score) > self.threshold_std:
                anomalies.append(
                    {
                        "type": "statistical_anomaly",
                        "message": f"Value {new_val} is {abs(z_score):.2f} standard deviations from mean",
                        "severity": "warning" if abs(z_score) < 3 else "critical",
                    }
                )

        # Check for trend anomalies
        if len(values) >= 5:
            recent_values = values[-5:]
            recent_avg = mean(recent_values)
            if recent_avg > avg + (std * 1.5):
                anomalies.append(
                    {
                        "type": "upward_trend",
                        "message": "Biomarker showing upward trend over last 5 measurements",
                        "severity": "info",
                    }
                )
            elif recent_avg < avg - (std * 1.5):
                anomalies.append(
                    {
                        "type": "downward_trend",
                        "message": "Biomarker showing downward trend over last 5 measurements",
                        "severity": "info",
                    }
                )

        return anomalies

    def detect_reference_range_violations(
        self, value: float, unit: str, reference_ranges: Dict[str, Dict[str, float]]
    ) -> List[Dict[str, Any]]:
        """Check if value is outside reference range"""
        violations = []

        for biomarker, ranges in reference_ranges.items():
            if ranges.get("unit") != unit:
                continue

            min_val = ranges.get("min")
            max_val = ranges.get("max")

            if min_val is not None and value < min_val:
                violations.append(
                    {
                        "type": "below_reference",
                        "biomarker": biomarker,
                        "value": value,
                        "min_reference": min_val,
                        "severity": "warning" if value < min_val * 0.9 else "info",
                    }
                )

            if max_val is not None and value > max_val:
                violations.append(
                    {
                        "type": "above_reference",
                        "biomarker": biomarker,
                        "value": value,
                        "max_reference": max_val,
                        "severity": "warning" if value > max_val * 1.1 else "info",
                    }
                )

        return violations
