"""Tests for tenant_service.create_tenant slug generation.

The ``tenants.slug`` column is NOT NULL + UNIQUE. ``create_tenant`` must
auto-generate a slug from the name (when none is supplied) and guarantee
uniqueness by appending a suffix on collision. This was a latent bug —
the function built the model with no slug at all — surfaced when the
first-run setup wizard started calling it.
"""
import pytest

from app.services.tenant_service import create_tenant
from app.utils.slug import slugify


def test_slugify_basic():
    assert slugify("My Organization") == "my-organization"


def test_slugify_collapses_non_alnum():
    assert slugify("  Home1 / Test !! ") == "home1-test"


def test_slugify_empty_falls_back():
    assert slugify("") == "tenant"
    assert slugify("   ") == "tenant"
    assert slugify(None) == "tenant"


def test_slugify_custom_fallback():
    assert slugify("", fallback="system-tenant") == "system-tenant"


@pytest.mark.asyncio
async def test_create_tenant_generates_non_null_slug():
    """A tenant created with only a name gets a slugified, non-null slug."""
    tenant = await create_tenant(name="Acme Clinic")
    try:
        assert tenant is not None
        assert tenant.slug == "acme-clinic"
        assert tenant.name == "Acme Clinic"
    finally:
        from app.services.tenant_service import delete_tenant
        await delete_tenant(tenant.id)


@pytest.mark.asyncio
async def test_create_tenant_disambiguates_duplicate_slugs():
    """Two tenants with the same name get distinct slugs (suffix appended)."""
    t1 = await create_tenant(name="Dup Org")
    t2 = await create_tenant(name="Dup Org")
    try:
        assert t1.slug == "dup-org"
        assert t2.slug.startswith("dup-org-")
        assert t2.slug != t1.slug
    finally:
        from app.services.tenant_service import delete_tenant
        await delete_tenant(t1.id)
        await delete_tenant(t2.id)
