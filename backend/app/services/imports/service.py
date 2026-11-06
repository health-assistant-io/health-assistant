"""The ImportService composition root — one restore engine, ten concerns (mixin
composition keeps the public class face; method bodies live in the section modules
verbatim)."""

from uuid import UUID

from app.core.database import AsyncSession
from app.services.imports.csv_ocr import CsvOcrImportMixin
from app.services.imports.documents import DocumentsMixin
from app.services.imports.g8 import G8UpsertsMixin
from app.services.imports.jobs import JobLifecycleMixin
from app.services.imports.manifest import ManifestMixin
from app.services.imports.orchestrator import OrchestratorMixin
from app.services.imports.restore_fhir import RestoreFhirMixin
from app.services.imports.sidecar import SidecarMixin
from app.services.imports.taxonomy import TaxonomyAnatomyMixin
from app.services.imports.verbs import VerbRoutingMixin


class ImportService(
    JobLifecycleMixin,
    ManifestMixin,
    RestoreFhirMixin,
    VerbRoutingMixin,
    G8UpsertsMixin,
    SidecarMixin,
    TaxonomyAnatomyMixin,
    DocumentsMixin,
    OrchestratorMixin,
    CsvOcrImportMixin,
):
    def __init__(self, db: AsyncSession):
        self.db = db
        self._job_id: UUID | None = None
