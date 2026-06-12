import io
import logging
from pathlib import Path
from typing import List
from .base import OCRProcessor
from .utils import convert_to_images

logger = logging.getLogger(__name__)

try:
    import pytesseract

    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False


class TesseractOCRProcessor(OCRProcessor):
    """OCR processor using Tesseract (local)"""

    def __init__(self, language: str = "eng"):
        self.language = language

    async def extract_text(self, file_path: Path) -> str:
        """Extract text using Tesseract OCR"""
        if not HAS_TESSERACT:
            logger.error("pytesseract not installed")
            return f"Error: pytesseract not installed. Cannot process {file_path}"

        try:
            # Use utility to convert PDF/DICOM/Images to normalized JPEG bytes
            images = await convert_to_images(file_path)

            if not images:
                # Fallback to direct image read if it's an image
                if file_path.suffix.lower() in [
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                    ".tiff",
                    ".tif",
                ]:
                    with open(file_path, "rb") as f:
                        images = [f.read()]
                else:
                    raise ValueError(f"No images could be extracted from {file_path}")

            return await self.extract_text_from_images(images)
        except Exception as e:
            logger.error(f"Tesseract OCR failed: {e}")
            raise ValueError(f"Failed to extract text using Tesseract: {str(e)}")

    async def extract_text_from_images(self, images: List[bytes]) -> str:
        """Extract text from images using Tesseract OCR"""
        if not HAS_TESSERACT:
            raise ValueError("pytesseract not installed")

        try:
            from PIL import Image

            full_text = ""
            for img_bytes in images:
                img = Image.open(io.BytesIO(img_bytes))
                text = pytesseract.image_to_string(img, lang=self.language)
                full_text += text + "\n\n"

            return full_text.strip()
        except Exception as e:
            logger.error(f"Tesseract OCR failed: {e}")
            raise ValueError(
                f"Failed to extract text from images using Tesseract: {str(e)}"
            )

    async def extract_images(self, file_path: Path) -> List[bytes]:
        """Extract images from document using utility"""
        return await convert_to_images(file_path)
