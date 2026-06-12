from abc import ABC, abstractmethod
from pathlib import Path
from typing import List


class OCRProcessor(ABC):
    """Base class for OCR processors"""

    @abstractmethod
    async def extract_text(self, file_path: Path) -> str:
        """Extract text from a document file"""
        pass

    @abstractmethod
    async def extract_text_from_images(self, images: List[bytes]) -> str:
        """Extract text from a list of image bytes (e.g. from PDF pages)"""
        pass

    @abstractmethod
    async def extract_images(self, file_path: Path) -> List[bytes]:
        """Extract images from a document (for multi-page files)"""
        pass
