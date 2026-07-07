import io
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    from pdf2image import convert_from_path

    HAS_PDF2IMAGE = True
except ImportError:
    HAS_PDF2IMAGE = False

try:
    import pydicom
    import numpy as np

    HAS_DICOM = True
except ImportError:
    HAS_DICOM = False


async def convert_to_images(file_path: Path) -> List[bytes]:
    """Convert a document file to a list of image bytes (JPEG)"""
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        return []

    suffix = file_path.suffix.lower()

    # Try to use PIL for all images to ensure they are valid and normalized
    if suffix in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"]:
        if not HAS_PIL:
            logger.warning(f"PIL not installed, falling back to raw bytes for {suffix}")
            with open(file_path, "rb") as f:
                return [f.read()]
        try:
            with Image.open(file_path) as img:
                # Convert to RGB (removes alpha channel, handles grayscale)
                if img.mode != "RGB":
                    img = img.convert("RGB")

                # Resize if too large
                max_size = 2048
                if max(img.width, img.height) > max_size:
                    img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

                buf = io.BytesIO()
                # Use high quality (90+) for better OCR of small medical text
                img.save(buf, format="JPEG", quality=90, optimize=True)
                return [buf.getvalue()]
        except Exception as e:
            logger.error(f"Error processing image {file_path}: {e}")
            # Final fallback to raw bytes
            with open(file_path, "rb") as f:
                return [f.read()]

    if suffix == ".pdf":
        return await _convert_pdf_to_images(file_path)

    if suffix == ".dcm":
        return await _convert_dicom_to_images(file_path)

    return []


async def _convert_pdf_to_images(file_path: Path) -> List[bytes]:
    """Convert PDF pages to JPEG images"""
    if not HAS_PDF2IMAGE:
        logger.warning("pdf2image not installed, cannot convert PDF to images")
        return []

    try:
        # Using a DPI of 150 for a balance between quality and payload size
        images = convert_from_path(file_path, dpi=150)
        image_bytes_list = []

        for img in images:
            # Ensure RGB mode (remove alpha channel, handle CMYK/grayscale)
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Resize if too large
            max_size = 2048
            if max(img.width, img.height) > max_size:
                img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90, optimize=True)
            image_bytes_list.append(buf.getvalue())

        return image_bytes_list
    except Exception as e:
        logger.error(f"Error converting PDF to images: {e}")
        return []


async def _convert_dicom_to_images(file_path: Path) -> List[bytes]:
    """Convert DICOM to JPEG images with proper medical windowing. Handles multi-frame DICOMs."""
    if not HAS_DICOM or not HAS_PIL:
        logger.warning("pydicom, numpy or PIL not installed, cannot convert DICOM")
        return []

    try:
        ds = pydicom.dcmread(str(file_path))
        pixel_array = ds.pixel_array

        # 1. Determine frames
        frames = []
        if hasattr(ds, "NumberOfFrames") and int(ds.NumberOfFrames) > 1:
            # It's a multi-frame DICOM
            for i in range(int(ds.NumberOfFrames)):
                frames.append(pixel_array[i])
        else:
            # Single frame
            frames.append(pixel_array)

        from pydicom.pixel_data_handlers.util import apply_voi_lut

        processed_images = []

        for frame in frames:
            # 2. Apply VOI LUT (Window Center/Width) if available
            try:
                # This handles Rescale Slope/Intercept and Windowing
                frame = apply_voi_lut(frame, ds)
            except Exception as e:
                logger.debug(f"VOI LUT application failed for a frame: {e}")

            # 3. Normalize to 8-bit (0-255)
            p_min, p_max = np.min(frame), np.max(frame)
            if p_max > p_min:
                frame = ((frame - p_min) / (p_max - p_min)) * 255.0
            else:
                frame = np.zeros_like(frame)

            frame = frame.astype(np.uint8)

            # 4. Handle shape issues (e.g., singleton dimensions)
            frame = np.squeeze(frame)

            if frame.ndim == 1:
                continue

            # Ensure it's 2D for Pillow if it's grayscale
            if frame.ndim == 3 and frame.shape[-1] not in [3, 4]:
                if frame.shape[-1] == 1:
                    frame = frame[:, :, 0]
                elif frame.shape[0] == 1:
                    frame = frame[0]

            # 5. Convert to PIL Image
            img = Image.fromarray(frame)
            if img.mode != "RGB":
                img = img.convert("RGB")

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=95)
            processed_images.append(buf.getvalue())

        return processed_images
    except Exception as e:
        logger.error(f"Error converting DICOM to images: {e}", exc_info=True)
        return []
