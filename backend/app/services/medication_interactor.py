from typing import List, Dict, Any
import requests


class MedicationInteractor:
    """Checks for medication interactions using RxNorm"""

    RXNORM_API = "https://rxnav.nlm.nih.gov/REST"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    async def check_interactions(self, medications: List[str]) -> List[Dict[str, Any]]:
        """Check for interactions between medications"""
        interactions = []

        if len(medications) < 2:
            return interactions

        # Get RxCUI codes for medications
        rxcui_codes = []
        for med in medications:
            rxcui = await self._get_rxcui(med)
            if rxcui:
                rxcui_codes.append(rxcui)

        # Check interactions
        for i, code1 in enumerate(rxcui_codes):
            for code2 in rxcui_codes[i + 1 :]:
                interaction = await self._check_interaction(code1, code2)
                if interaction:
                    interactions.append(interaction)

        return interactions

    async def _get_rxcui(self, medication: str) -> str:
        """Get RxCUI code for medication name"""
        try:
            response = self.session.get(
                f"{self.RXNORM_API}/rxcui.json", params={"name": medication}
            )
            data = response.json()
            return data.get("idGroup", {}).get("rxnormId", "")
        except Exception as e:
            import logging

            logging.getLogger(__name__).error(
                f"Error getting RxCUI for {medication}: {e}"
            )
            return ""

    async def _check_interaction(self, code1: str, code2: str) -> Dict[str, Any]:
        """Check interaction between two medications"""
        try:
            response = self.session.get(
                f"{self.RXNORM_API}/interaction/list.json",
                params={"rxcui1": code1, "rxcui2": code2},
            )
            data = response.json()

            if "interactionPair" in data:
                return {
                    "medication1": code1,
                    "medication2": code2,
                    "severity": data["interactionPair"][0]["interactionSeverity"],
                    "description": data["interactionPair"][0]["description"],
                }
        except Exception as e:
            import logging

            logging.getLogger(__name__).error(
                f"Error checking interaction between {code1} and {code2}: {e}"
            )

        return {}

    async def get_medication_info(self, medication: str) -> Dict[str, Any]:
        """Get detailed information about a medication"""
        rxcui = await self._get_rxcui(medication)
        if not rxcui:
            return {"error": "Medication not found"}

        try:
            response = self.session.get(f"{self.RXNORM_API}/rxcui/{rxcui}/status.json")
            return response.json()
        except Exception:
            return {"error": "Failed to fetch medication info"}
