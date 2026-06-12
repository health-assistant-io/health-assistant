import base64
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)
from .base import OCRProcessor
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_core.language_models.chat_models import BaseChatModel


class LangChainOCRProcessor(OCRProcessor):
    """OCR processor using LangChain and Vision-capable LLMs"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        max_tokens: int = 16384,
        temperature: float = 0.0,
        timeout: int = 600,
        llm: Optional[BaseChatModel] = None,
    ):
        if llm:
            self.llm = llm
        else:
            self.llm = ChatOpenAI(
                api_key=api_key,
                base_url=api_base,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )

    async def extract_text(self, file_path: Path) -> str:
        """Extract text using LangChain and Vision-capable LLM"""
        try:
            if file_path.suffix.lower() in [".txt", ".csv", ".md"]:
                with open(file_path, "r", encoding="utf-8") as f:
                    return f.read()

            # Use utility to convert PDF/DICOM/Images to normalized JPEG bytes
            from .utils import convert_to_images

            images = await convert_to_images(file_path)

            if not images:
                # Fallback to raw read if utils returned nothing but it might be a direct image
                if file_path.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"]:
                    with open(file_path, "rb") as f:
                        images = [f.read()]
                else:
                    raise ValueError(f"No images could be extracted from {file_path}")

            return await self.extract_text_from_images(images)

        except Exception as e:
            raise ValueError(f"Failed to extract text: {str(e)}")

    async def extract_text_from_images(self, images: List[bytes]) -> str:
        """Extract text from images using LangChain and Vision (Page by Page with Context)"""
        try:
            full_text = []

            for i, image_data in enumerate(images):
                base64_image = base64.b64encode(image_data).decode("utf-8")

                # Context from previous pages to maintain consistency
                previous_context = ""
                if full_text:
                    context_str = "\n\n".join(full_text)
                    if len(context_str) > 3000:
                        context_str = "..." + context_str[-3000:]

                    previous_context = (
                        f"\n[REFERENCE: TEXT EXTRACTED FROM PREVIOUS PAGES]\n"
                        f"{context_str}\n"
                        f"[END OF REFERENCE]\n\n"
                    )

                prompt = (
                    f"{previous_context}"
                    f"TASK: Extract ALL text from the current page (Page {i + 1}) of this medical document.\n"
                    "FORMAT: Provide the output strictly in Markdown format.\n"
                    "TABLES: Identify all tabular data and extract it using standard Markdown table syntax. Preserve all headers, columns, and cell alignments accurately.\n"
                    "CONSISTENCY: Use the provided reference text to ensure consistent naming and table structures if a section continues from previous pages.\n"
                    "STRUCTURE: Maintain the original hierarchy, formatting, and all numeric values exactly as shown.\n"
                    "INSTRUCTIONS: Return ONLY the extracted Markdown content. No introduction, no conversational preamble, no code block markers (```)."
                )

                # Attempt with retries and fallback mechanisms
                response = None
                last_error = None

                # Strategies: 1. High Detail, 2. Low Detail, 3. Small Image
                strategies = [
                    {"detail": "high", "resize": False},
                    {"detail": "low", "resize": False},
                    {"detail": "low", "resize": True},
                ]

                for j, strategy in enumerate(strategies):
                    current_image_data = image_data
                    if strategy["resize"]:
                        # If we need to resize, we do it here
                        try:
                            from PIL import Image
                            import io

                            img = Image.open(io.BytesIO(image_data))
                            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                            buf = io.BytesIO()
                            img.save(buf, format="JPEG", quality=85)
                            current_image_data = buf.getvalue()
                            base64_image = base64.b64encode(current_image_data).decode(
                                "utf-8"
                            )
                        except Exception as res_err:
                            logger.warning(
                                f"Failed to resize image for retry: {res_err}"
                            )

                    content = [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                                "detail": strategy["detail"],
                            },
                        },
                    ]

                    try:
                        logger.info(
                            f"OCR attempt {j + 1} for page {i + 1} (strategy: {strategy}, size: {len(current_image_data)} bytes)"
                        )
                        response = await self.llm.ainvoke(
                            [HumanMessage(content=content)]
                        )
                        break  # Success!
                    except Exception as e:
                        last_error = e
                        err_msg = str(e).lower()
                        logger.warning(
                            f"OCR attempt {j + 1} failed for page {i + 1}: {e}"
                        )

                        # If it's a 4xx error (except 429), don't bother retrying with other strategies if it's auth/not found
                        if "401" in err_msg or "403" in err_msg or "404" in err_msg:
                            break

                        # Continue to next strategy for server errors or timeouts
                        await asyncio.sleep(1 * (j + 1))

                if not response:
                    raise ValueError(
                        f"Permanent OCR failure for page {i + 1} after all strategies. Last error: {last_error}"
                    )

                # Cleanup potential code block wrappers
                text_content = str(response.content).strip()
                if text_content.startswith("```"):
                    # Remove markdown markers
                    lines = text_content.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    text_content = "\n".join(lines).strip()

                full_text.append(text_content)

            return "\n\n".join(full_text)
        except Exception as e:
            logger.error(f"OCR method failed: {e}")
            raise ValueError(f"OCR processing failed: {str(e)}")

    async def extract_images(self, file_path: Path) -> List[bytes]:
        """Extract images from document using utility"""
        from .utils import convert_to_images

        return await convert_to_images(file_path)

    async def extract_text_with_prompt(
        self,
        file_path: Path,
        prompt: str,
    ) -> str:
        """Extract text with custom prompt"""
        try:
            if file_path.suffix.lower() in [".txt", ".csv", ".md"]:
                with open(file_path, "r", encoding="utf-8") as f:
                    doc_text = f.read()
                content = [
                    {"type": "text", "text": f"{prompt}\n\nDocument Text:\n{doc_text}"}
                ]
            else:
                from .utils import convert_to_images

                images = await convert_to_images(file_path)

                if not images:
                    if file_path.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"]:
                        with open(file_path, "rb") as f:
                            images = [f.read()]
                    else:
                        raise ValueError(
                            f"No images could be extracted from {file_path}"
                        )

                content = [{"type": "text", "text": prompt}]
                for image_data in images:
                    base64_image = base64.b64encode(image_data).decode("utf-8")
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                                "detail": "high",
                            },
                        }
                    )

            message = HumanMessage(content=content)
            response = await self.llm.ainvoke([message])
            return str(response.content)

        except Exception as e:
            raise ValueError(f"Failed to extract text with prompt: {str(e)}")

    async def extract_structured_data(
        self,
        file_path: Path,
        schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Extract structured data from document"""
        try:
            import json
            from langchain_core.output_parsers import JsonOutputParser

            parser = JsonOutputParser()
            schema_str = json.dumps(schema, indent=2)
            prompt = (
                f"Extract data from this medical document following this exact schema:\n{schema_str}\n\n"
                "INSTRUCTIONS:\n"
                "- Map all values correctly based on the visual context.\n"
                "- If a field is not found, use null.\n"
                "- Return ONLY the raw JSON object. No preamble, no ```json code blocks."
            )

            if file_path.suffix.lower() in [".txt", ".csv", ".md"]:
                with open(file_path, "r", encoding="utf-8") as f:
                    doc_text = f.read()
                content = [
                    {"type": "text", "text": f"{prompt}\n\nDocument Text:\n{doc_text}"}
                ]
            else:
                from .utils import convert_to_images

                images = await convert_to_images(file_path)

                if not images:
                    if file_path.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"]:
                        with open(file_path, "rb") as f:
                            images = [f.read()]
                    else:
                        raise ValueError(
                            f"No images could be extracted from {file_path}"
                        )

                content = [{"type": "text", "text": prompt}]
                for image_data in images:
                    base64_image = base64.b64encode(image_data).decode("utf-8")
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                                "detail": "high",
                            },
                        }
                    )

            message = HumanMessage(content=content)
            # Chain with parser to ensure clean JSON output
            chain = self.llm | parser
            return await chain.ainvoke([message])

        except Exception as e:
            raise ValueError(f"Failed to extract structured data: {str(e)}")

    def _get_content_type(self, file_path: Path) -> str:
        """Get content type based on file extension"""
        suffix = file_path.suffix.lower()
        content_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".pdf": "application/pdf",
        }
        return content_types.get(suffix, "application/octet-stream")
