# ruff: noqa: F401 -- the original module prelude, kept verbatim;
# imports here are the package's shared namespace (F401 is the point).

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerAllowedState, BiomarkerDefinition
from app.models.document_model import DocumentModel
from app.models.enums import BiomarkerValueType
from app.models.examination_model import ExaminationModel

# FHIR models
from app.models.fhir import DiagnosticReport, Medication, Observation
from app.models.user_model import UserModel
from app.schemas.biomarker import is_safe_slug
from app.services.biomarkers.unit_conversion import units_are_compatible

logger = logging.getLogger(__name__)

# Strict whitelist for the INTERVAL '{bucket}' SQL interpolation.
# Values not in this set fall back to "1 hour". The list mirrors the
# PERIOD_MAPPING defaults + the auto-calculated buckets.
_ALLOWED_TELEMETRY_BUCKETS = frozenset(
    {
        "1 minute",
        "5 minutes",
        "15 minutes",
        "30 minutes",
        "1 hour",
        "6 hours",
        "12 hours",
        "1 day",
        "1 week",
        "1 month",
    }
)
