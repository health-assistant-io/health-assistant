from typing import Dict, Any
from .base import NLPExtractor

class SpaCyExtractor(NLPExtractor):
    """NLP extractor using spaCy with medical models"""
    
    def __init__(self, model: str = "en_core_sci_sm"):
        self.model = model
    
    async def extract_entities(self, text: str) -> Dict[str, Any]:
        """Extract medical entities from text"""
        return {
            "biomarkers": [],
            "dates": [],
            "medications": [],
            "procedures": [],
            "diagnoses": [],
            "quantities": []
        }