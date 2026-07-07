from PIL import Image, ImageEnhance
import os
from typing import Optional, Tuple, List


import numpy as np


def find_coeffs(pa, pb):
    """
    Find coefficients for perspective transform.
    pa: 4 points in target (output) rectangle [(0,0), (W,0), (W,H), (0,H)]
    pb: 4 points in source (input) quadrilateral
    """
    matrix = []
    for p1, p2 in zip(pa, pb):
        matrix.append([p1[0], p1[1], 1, 0, 0, 0, -p2[0] * p1[0], -p2[0] * p1[1]])
        matrix.append([0, 0, 0, p1[0], p1[1], 1, -p2[1] * p1[0], -p2[1] * p1[1]])

    A = np.array(matrix)
    B = np.array(pb).reshape(8)

    res = np.linalg.solve(A, B)
    return res.tolist()


def edit_image(
    input_path: str,
    output_path: str,
    crop: Optional[Tuple[int, int, int, int]] = None,
    perspective_points: Optional[List[List[int]]] = None,
    brightness: float = 1.0,
    contrast: float = 1.0,
    sharpness: float = 1.0,
    rotation: int = 0,
) -> str:
    """
    Edit an image and save it to output_path.
    """
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"Editing image {input_path} -> {output_path}")
    logger.info(
        f"Params: crop={crop}, perspective={perspective_points}, rot={rotation}, b={brightness}, c={contrast}"
    )

    img = Image.open(input_path)

    # Check if we have anything to do
    has_changes = (
        rotation != 0
        or crop
        or perspective_points
        or brightness != 1.0
        or contrast != 1.0
        or sharpness != 1.0
    )

    if not has_changes:
        logger.info("No changes requested, copying original")
        img.save(output_path)
        return output_path

    # Apply rotation first
    if rotation != 0:
        pil_angle = (360 - rotation) % 360
        logger.info(f"Applying rotation: {rotation} deg (PIL angle: {pil_angle})")
        img = img.rotate(pil_angle, expand=True)

    # Apply perspective transform
    if perspective_points and len(perspective_points) == 4:
        try:
            logger.info("Applying perspective transform")
            pb = [tuple(p) for p in perspective_points]
            pts = [np.array(p) for p in pb]
            width = int(
                max(np.linalg.norm(pts[0] - pts[1]), np.linalg.norm(pts[3] - pts[2]))
            )
            height = int(
                max(np.linalg.norm(pts[0] - pts[3]), np.linalg.norm(pts[1] - pts[2]))
            )

            if width > 0 and height > 0:
                coeffs = find_coeffs(
                    [(0, 0), (width, 0), (width, height), (0, height)], pb
                )
                img = img.transform(
                    (width, height),
                    Image.Transform.PERSPECTIVE,
                    coeffs,
                    Image.Resampling.BICUBIC,
                )
            else:
                logger.warning(f"Invalid dimensions for perspective: {width}x{height}")
        except Exception as e:
            logger.error(f"Perspective transform failed: {e}")
            if crop:
                img = img.crop(crop)

    # Apply rectangular crop
    elif crop:
        logger.info(f"Applying rectangular crop: {crop}")
        # Ensure crop coordinates are within image bounds and valid
        left, top, right, bottom = crop
        w, h = img.size
        left = max(0, min(w, left))
        top = max(0, min(h, top))
        right = max(left + 1, min(w, right))
        bottom = max(top + 1, min(h, bottom))
        logger.info(f"Clipped crop: ({left}, {top}, {right}, {bottom})")
        img = img.crop((left, top, right, bottom))

    # Convert to RGB
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # Apply enhancements
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if sharpness != 1.0:
        img = ImageEnhance.Sharpness(img).enhance(sharpness)

    # Final dimension check
    if img.size[0] == 0 or img.size[1] == 0:
        logger.error(f"Zero dimension after processing: {img.size}")
        img = Image.open(input_path)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, quality=95, subsampling=0)
    logger.info(f"Saved edited image to {output_path}")
    return output_path
