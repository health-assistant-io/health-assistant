import asyncio
from app.processors.ocr import get_ocr_processor
from pathlib import Path
from app.core.config import settings


async def test():
    ocr_processor = get_ocr_processor(
        provider=settings.OCR_PROVIDER,
        api_key=settings.OPENAI_API_KEY,
        api_base=settings.OPENAI_API_BASE,
        model=settings.OPENAI_MODEL,
    )

    from app.core.constants import CATEGORY_NAMES

    category_options = ", ".join(CATEGORY_NAMES)

    schema = {
        "document_category": f"string (One of: {category_options})",
        "document_sub_category": "string",
        "patient_info": {"name": "string", "dob": "string", "gender": "string"},
        "biomarkers": [
            {
                "name": "string",
                "value": "number",
                "unit": "string",
                "reference_range": "string",
            }
        ],
        "diagnoses": ["string"],
        "medications": ["string"],
        "impressions_or_findings": "string",
    }

    # create a mock medical file
    with open("mock_eye_exam.txt", "w") as f:
        f.write("""
        medical-eyes-results.txt
        HbA1c 6.4 % not specified
        Intraocular Pressure (Right Eye) 16 mmHg 10-21 mmHg
        Intraocular Pressure (Left Eye) 15 mmHg 10-21 mmHg
        Diagnoses
        Age-Related Nuclear Cataract, Right Eye (H25.11)
        Age-Related Nuclear Cataract, Left Eye (H25.12)
        Type 2 Diabetes Mellitus without Ophthalmic Complications (E11.9)
        Presbyopia (H52.4)
        Glaucoma Suspect

        Medications
        Lisinopril 10mg
        Metformin
        Tropicamide 1% (administered)
        Phenylephrine 2.5% (administered)
        """)

    result = await ocr_processor.extract_structured_data(
        Path("mock_eye_exam.txt"), schema
    )
    print("RESULT:", result)


if __name__ == "__main__":
    asyncio.run(test())
