from sqlalchemy import Column, ForeignKey, Table, UUID
from app.models.base import Base

# Association table for Many-to-Many relationship between Examinations and Doctors
examination_doctors = Table(
    "examination_doctors",
    Base.metadata,
    Column(
        "examination_id",
        UUID(as_uuid=True),
        ForeignKey("examinations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "doctor_id",
        UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

# Association table for Many-to-Many relationship between Organizations and Doctors
organization_doctors = Table(
    "organization_doctors",
    Base.metadata,
    Column(
        "organization_id",
        UUID(as_uuid=True),
        ForeignKey("fhir_organizations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "doctor_id",
        UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
