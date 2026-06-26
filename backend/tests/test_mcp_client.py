"""Tests for the MCP Client integration.

These tests are organized so that the pure-logic ones (security, tool
adapter, namespacing, masking, encryption) run without a database or
fastmcp installed. The end-to-end test that exercises the connection
manager with a real in-memory FastMCP server is guarded by
``pytest.importorskip('fastmcp')``.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# These imports don't require fastmcp and don't touch the DB.
from integrations.sdk.secrets import (
    SecretCipher,
    decrypt_fields,
    encrypt_fields,
    mask_fields,
)
from integrations.mcp_client.security import (
    build_ssl_context,
    get_allowed_commands,
    validate_http_url,
    validate_stdio_command,
)
from integrations.mcp_client.tool_adapter import (
    MAX_DESCRIPTION_LEN,
    filter_and_adapt_tools,
    namespaced_name,
    sanitize_instance_slug,
    _extract_text_result,
)


# --------------------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _mcp_settings(monkeypatch):
    """Provide deterministic MCP settings + a Fernet key for all tests."""
    key = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa="  # valid Fernet key
    monkeypatch.setenv("INTEGRATION_SECRET_KEY", key)
    monkeypatch.setenv("MCP_STDIO_ALLOWED_COMMANDS", "npx,uvx,python,python3,node")
    monkeypatch.setenv("MCP_MAX_SERVERS_PER_USER", "5")
    monkeypatch.setenv("MCP_MAX_TOTAL_STDIO", "20")
    monkeypatch.setenv("MCP_REQUEST_TIMEOUT", "30")
    monkeypatch.setenv("MCP_TOOL_RESULT_MAX_BYTES", "65536")
    monkeypatch.setenv("INTEGRATION_MAX_TOOLS_PER_SESSION", "20")
    monkeypatch.setenv("MCP_PER_INSTANCE_CONCURRENCY", "4")
    monkeypatch.setenv("MCP_ALLOW_INSECURE_HTTP", "False")
    # Clear the cached settings so the new env vars take effect.
    from app.core.config import get_settings
    get_settings.cache_clear()


# --------------------------------------------------------------------------- security

class TestStdioCommandValidation:
    def test_allowlisted_bare_command_accepted(self):
        ok, reason = validate_stdio_command("npx", ["-y", "server"], None)
        assert ok, reason

    def test_disallowed_command_rejected(self):
        ok, reason = validate_stdio_command("rm", ["-rf", "/"], None)
        assert not ok
        assert "allowlist" in reason.lower()

    def test_absolute_path_rejected(self):
        ok, reason = validate_stdio_command("/bin/sh", ["-c", "x"], None)
        assert not ok
        assert "absolute" in reason.lower() or "traversal" in reason.lower()

    def test_shell_metachar_rejected(self):
        ok, reason = validate_stdio_command("npx; rm -rf /", [], None)
        assert not ok

    def test_non_string_arg_rejected(self):
        ok, reason = validate_stdio_command("npx", [123], None)
        assert not ok

    def test_blocked_cwd_rejected(self, tmp_path):
        ok, reason = validate_stdio_command("npx", [], cwd="/etc")
        assert not ok
        assert "restricted" in reason.lower() or "cwd" in reason.lower()

    def test_unknown_command_empty_rejected(self):
        ok, reason = validate_stdio_command("", [], None)
        assert not ok


class TestHttpUrlValidation:
    def test_https_accepted(self):
        assert validate_http_url("https://example.com/mcp")[0]

    def test_http_rejected_by_default(self):
        ok, reason = validate_http_url("http://example.com/mcp")
        assert not ok
        assert "insecure" in reason.lower()

    def test_http_allowed_when_insecure_flag(self):
        assert validate_http_url("http://localhost:9000", allow_insecure=True)[0]

    def test_non_http_scheme_rejected(self):
        assert not validate_http_url("ftp://x")[0]

    def test_empty_rejected(self):
        assert not validate_http_url("")[0]


class TestAllowedCommands:
    def test_parses_comma_separated(self):
        cmds = get_allowed_commands()
        assert "npx" in cmds
        assert "node" in cmds
        assert "" not in cmds


class TestSslContext:
    def test_default_verify_context(self):
        import ssl

        ctx = build_ssl_context(True, None)
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_unverified_context(self):
        import ssl

        ctx = build_ssl_context(False, None)
        assert ctx.verify_mode == ssl.CERT_NONE

    def test_missing_ca_bundle_raises(self, tmp_path):
        with pytest.raises(ValueError):
            build_ssl_context(True, str(tmp_path / "nope.pem"))


# --------------------------------------------------------------------------- cipher

class TestSecretCipher:
    def test_roundtrip_string(self):
        c = SecretCipher("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")
        enc = c.encrypt_value("hunter2")
        assert "_encrypted" in enc
        assert c.decrypt_value(enc) == "hunter2"

    def test_roundtrip_dict(self):
        c = SecretCipher("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")
        enc = c.encrypt_value({"K": "V"})
        out = c.decrypt_value(enc)
        assert out == {"K": "V"}

    def test_encrypt_fields_only_tags_secret_fields(self):
        config = {
            "env": {"TOKEN": "x"},
            "headers": {"X-Custom": "y"},
            "auth_token": "tok",
            "url": "https://x.com",  # NOT secret
            "command": "npx",        # NOT secret
        }
        enc = encrypt_fields(config, ["env", "headers", "auth_token"])
        assert "_encrypted" in enc["env"]
        assert "_encrypted" in enc["headers"]
        assert "_encrypted" in enc["auth_token"]
        assert enc["url"] == "https://x.com"
        assert enc["command"] == "npx"
        # Round-trip
        dec = decrypt_fields(enc, ["env", "headers", "auth_token"])
        assert dec == config

    def test_encrypt_fields_noop_on_empty_list(self):
        # No fields -> no cipher constructed -> no key required.
        out = encrypt_fields({"url": "x"}, [])
        assert out == {"url": "x"}

    def test_decrypt_passthrough_for_plaintext(self):
        c = SecretCipher("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")
        # A plain (non-encrypted) value is returned unchanged so legacy
        # configs that were saved before encryption don't break.
        assert c.decrypt_value("plain") == "plain"
        assert c.decrypt_value({"k": "v"}) == {"k": "v"}

    def test_missing_key_raises(self, monkeypatch):
        with pytest.raises(RuntimeError, match="INTEGRATION_SECRET_KEY is not configured"):
            SecretCipher(None)


class TestMaskFields:
    def test_secrets_masked(self):
        out = mask_fields({"env": {"K": "V"}, "auth_token": "tok", "url": "u"}, ["env", "auth_token"])
        assert out["env"] == "***"
        assert out["auth_token"] == "***"
        assert out["url"] == "u"

    def test_empty_config(self):
        assert mask_fields({}, ["env"]) == {}

    def test_no_secrets(self):
        out = mask_fields({"url": "u", "verify_ssl": True}, [])
        assert out == {"url": "u", "verify_ssl": True}

    def test_empty_secret_left_unchanged(self):
        # Empty secret fields are not masked (so the UI shows an empty input,
        # not '***').
        out = mask_fields({"env": {}, "url": "u"}, ["env"])
        assert out["env"] == {}
        assert out["url"] == "u"


class TestConfigFlowSdkHooks:
    """Verify the MCP config flow declares capabilities via SDK hooks."""

    def test_get_secret_fields(self):
        from integrations.mcp_client.config_flow import McpClientConfigFlow

        cf = McpClientConfigFlow()
        assert set(cf.get_secret_fields()) == {"env", "headers", "auth_token"}

    def test_max_instances_per_user_from_settings(self):
        from integrations.mcp_client.config_flow import McpClientConfigFlow

        cf = McpClientConfigFlow()
        assert cf.max_instances_per_user == 5  # MCP_MAX_SERVERS_PER_USER default

    def test_prepare_for_storage_encrypts_secrets(self):
        from integrations.mcp_client.config_flow import McpClientConfigFlow
        import asyncio

        cf = McpClientConfigFlow()
        enc = asyncio.run(cf.prepare_for_storage({
            "env": {"K": "V"},
            "auth_token": "tok",
            "url": "https://x.com",
        }))
        assert "_encrypted" in enc["env"]
        assert "_encrypted" in enc["auth_token"]
        assert enc["url"] == "https://x.com"

    def test_prepare_for_read_masks_secrets(self):
        from integrations.mcp_client.config_flow import McpClientConfigFlow

        cf = McpClientConfigFlow()
        out = cf.prepare_for_read({"env": {"K": "V"}, "auth_token": "tok", "url": "u"})
        assert out["env"] == "***"
        assert out["auth_token"] == "***"
        assert out["url"] == "u"

    def test_default_config_flow_has_no_secrets_and_no_cap(self):
        """Existing integrations (dev_dummy, webhook, etc.) are unaffected."""
        from integrations.sdk import BaseConfigFlow

        class DummyCF(BaseConfigFlow):
            domain = "dummy"
            async def get_schema(self): return {}
            async def validate_input(self, i): return i

        cf = DummyCF()
        assert cf.get_secret_fields() == []
        assert cf.max_instances_per_user is None
        # prepare_for_storage is a no-op (no secret fields -> no cipher)
        import asyncio
        out = asyncio.run(cf.prepare_for_storage({"url": "x"}))
        assert out == {"url": "x"}


# --------------------------------------------------------------------------- tool adapter

class TestNamespacing:
    def test_basic_namespace(self):
        assert namespaced_name("github", "get_repo") == "mcp__github__get_repo"

    def test_double_underscore_rejected(self):
        with pytest.raises(ValueError):
            namespaced_name("x", "weird__name")

    def test_invalid_identifier_rejected(self):
        with pytest.raises(ValueError):
            namespaced_name("x", "123name")
        with pytest.raises(ValueError):
            namespaced_name("x", "has space")

    def test_long_name_truncated(self):
        long_slug = "a" * 200
        name = namespaced_name(long_slug, "tool")
        assert len(name) <= 64
        assert name.endswith("__tool")


class TestSanitizeSlug:
    def test_basic(self):
        assert sanitize_instance_slug("GitHub MCP", uuid.uuid4()) == "github-mcp"

    def test_falls_back_to_id_prefix(self):
        iid = uuid.UUID("12345678-1234-1234-1234-123456789012")
        assert sanitize_instance_slug("", iid) == "12345678"

    def test_strips_special_chars(self):
        assert sanitize_instance_slug("My Server!@#", uuid.uuid4()) == "my-server"


class TestExtractResult:
    def _result(self, data=None, content=None, is_error=False):
        r = MagicMock()
        r.data = data
        r.content = content or []
        r.is_error = is_error
        r.isError = is_error
        return r

    def _text_block(self, text):
        b = MagicMock()
        b.text = text
        return b

    def test_text_content(self):
        result = self._result(content=[self._text_block("hello")])
        assert _extract_text_result(result, 1024) == "hello"

    def test_data_dict(self):
        result = self._result(data={"k": "v"})
        out = _extract_text_result(result, 1024)
        assert '"k": "v"' in out

    def test_truncation(self):
        result = self._result(content=[self._text_block("x" * 5000)])
        out = _extract_text_result(result, 1024)
        assert len(out.encode("utf-8")) <= 1100  # 1024 + marker
        assert "truncated" in out

    def test_error_marker(self):
        result = self._result(is_error=True)
        out = _extract_text_result(result, 1024)
        assert out.startswith("[MCP tool error]")


class TestDescriptionTruncation:
    def test_long_description_truncated_in_adapter(self):
        fastmcp = pytest.importorskip("fastmcp")
        from integrations.mcp_client.tool_adapter import adapt_tool, MAX_DESCRIPTION_LEN

        long_desc = "x" * (MAX_DESCRIPTION_LEN + 100)
        mcp_tool = MagicMock()
        mcp_tool.name = "tool_with_long_desc"
        mcp_tool.description = long_desc
        mcp_tool.inputSchema = {"type": "object", "properties": {}}
        mcp_tool.tags = None
        mcp_tool.annotations = None

        integration = MagicMock()
        integration.id = uuid.uuid4()
        integration.instance_name = "test"
        integration.user_config = {}

        cm = MagicMock()
        tool = adapt_tool(integration, mcp_tool, "test", cm)
        assert tool is not None
        assert len(tool.description) <= MAX_DESCRIPTION_LEN + len("…[truncated]")
        assert "truncated" in tool.description


# --------------------------------------------------------------------------- adapter end-to-end

class TestAdapterEndToEnd:
    """Exercise filter_and_adapt_tools against a real in-memory FastMCP server."""

    def test_discover_and_call(self):
        fastmcp = pytest.importorskip("fastmcp")
        from fastmcp import FastMCP, Client
        from integrations.mcp_client.connection_manager import (
            _Connection,
            mcp_connection_manager,
        )

        mcp = FastMCP("EchoServer")

        @mcp.tool
        def echo(text: str) -> str:
            """Echo the given text back."""
            return text

        @mcp.tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        async def run():
            integration = MagicMock()
            integration.id = uuid.uuid4()
            integration.instance_name = "EchoServer"
            integration.user_config = {"tool_result_max_bytes": 1024}

            # Manually inject a live in-memory client.
            client = Client(mcp)
            conn = _Connection(client, "http", 4)
            await client.__aenter__()
            conn.entered = True
            mcp_connection_manager._connections[integration.id] = conn
            try:
                tools = await mcp_connection_manager.list_tools(integration)
                assert {t.name for t in tools} == {"echo", "add"}

                lc_tools = filter_and_adapt_tools(integration, tools, mcp_connection_manager)
                assert {t.name for t in lc_tools} == {
                    "mcp__echoserver__echo",
                    "mcp__echoserver__add",
                }

                echo_tool = next(t for t in lc_tools if "echo" in t.name)
                out = await echo_tool.ainvoke({"text": "hello mcp"})
                assert out == "hello mcp"

                add_tool = next(t for t in lc_tools if "add" in t.name)
                out = await add_tool.ainvoke({"a": 3, "b": 4})
                assert out == "7"
            finally:
                await mcp_connection_manager.disconnect(integration.id)

        asyncio.run(run())

    def test_disabled_tools_filtered(self):
        fastmcp = pytest.importorskip("fastmcp")
        from fastmcp import FastMCP, Client
        from integrations.mcp_client.connection_manager import (
            _Connection,
            mcp_connection_manager,
        )

        mcp = FastMCP("S")

        @mcp.tool
        def keep() -> str:
            """kept"""
            return "k"

        @mcp.tool
        def drop() -> str:
            """dropped"""
            return "d"

        async def run():
            integration = MagicMock()
            integration.id = uuid.uuid4()
            integration.instance_name = "S"
            integration.user_config = {"disabled_tools": ["drop"], "tool_result_max_bytes": 1024}

            client = Client(mcp)
            conn = _Connection(client, "http", 4)
            await client.__aenter__()
            conn.entered = True
            mcp_connection_manager._connections[integration.id] = conn
            try:
                tools = await mcp_connection_manager.list_tools(integration)
                lc_tools = filter_and_adapt_tools(integration, tools, mcp_connection_manager)
                names = {t.name for t in lc_tools}
                assert "mcp__s__keep" in names
                assert "mcp__s__drop" not in names
            finally:
                await mcp_connection_manager.disconnect(integration.id)

        asyncio.run(run())


# --------------------------------------------------------------------------- aggregator

class TestAggregator:
    """Test the generic integration_tool_aggregator with mocked DB + registry."""

    def _make_integration(self, domain="mcp_client", name="MCP", config=None):
        integration = MagicMock()
        integration.id = uuid.uuid4()
        integration.provider = domain
        integration.instance_name = name
        integration.user_config = config or {}
        return integration

    def _patch_registry(self, monkeypatch, providers):
        """providers: dict {domain: provider_mock}."""
        from app.core import integration_registry as reg_mod
        fake_registry = MagicMock()
        fake_registry.get_provider.side_effect = lambda d: providers.get(d)
        monkeypatch.setattr(reg_mod, "integration_registry", fake_registry)
        # Also patch the aggregator's own import site.
        from app.services import integration_tool_aggregator as agg_mod
        monkeypatch.setattr(agg_mod, "integration_registry", fake_registry)

    def test_no_instances_returns_empty(self, monkeypatch):
        from app.services import integration_tool_aggregator

        db = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result)

        out = asyncio.run(
            integration_tool_aggregator.aggregate(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        )
        assert out == []

    def test_skips_non_tool_integrations(self, monkeypatch):
        """Integrations whose provider doesn't support tools are ignored."""
        from app.services import integration_tool_aggregator

        mcp_inst = self._make_integration("mcp_client", "MCP")
        webhook_inst = self._make_integration("webhook", "WH")

        db = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [webhook_inst, mcp_inst]
        db.execute = AsyncMock(return_value=result)

        mcp_provider = MagicMock()
        mcp_provider.supports_tools.return_value = True
        async def fake_get_tools(integration):
            t = MagicMock(); t.name = "mcp__mcp__tool"
            return [t]
        mcp_provider.get_tools = fake_get_tools

        webhook_provider = MagicMock()
        webhook_provider.supports_tools.return_value = False  # not a tool provider

        self._patch_registry(monkeypatch, {
            "mcp_client": mcp_provider,
            "webhook": webhook_provider,
        })

        out = asyncio.run(
            integration_tool_aggregator.aggregate(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        )
        assert len(out) == 1
        # webhook was skipped (supports_tools False), mcp was picked up
        webhook_provider.supports_tools.assert_called_once()

    def test_skips_failing_instance(self, monkeypatch):
        from app.services import integration_tool_aggregator

        good = self._make_integration("mcp_client", "Good")
        bad = self._make_integration("mcp_client", "Bad")

        db = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [bad, good]
        db.execute = AsyncMock(return_value=result)

        provider = MagicMock()
        provider.supports_tools.return_value = True
        async def fake_get_tools(integration):
            if integration is bad:
                raise RuntimeError("connection refused")
            t = MagicMock(); t.name = "mcp__good__tool"
            return [t]
        provider.get_tools = fake_get_tools

        self._patch_registry(monkeypatch, {"mcp_client": provider})

        out = asyncio.run(
            integration_tool_aggregator.aggregate(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        )
        # Only the good instance's tool should appear.
        assert len(out) == 1

    def test_caps_total_tools(self, monkeypatch):
        from app.services import integration_tool_aggregator

        monkeypatch.setattr(integration_tool_aggregator.settings, "INTEGRATION_MAX_TOOLS_PER_SESSION", 1)

        inst = self._make_integration("mcp_client", "Cap")

        db = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [inst]
        db.execute = AsyncMock(return_value=result)

        provider = MagicMock()
        provider.supports_tools.return_value = True
        async def fake_get_tools(integration):
            t1 = MagicMock(); t1.name = "a"
            t2 = MagicMock(); t2.name = "b"
            return [t1, t2]
        provider.get_tools = fake_get_tools

        self._patch_registry(monkeypatch, {"mcp_client": provider})

        out = asyncio.run(
            integration_tool_aggregator.aggregate(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        )
        assert len(out) == 1  # capped at 1

    def test_domain_agnostic_picks_up_hypothetical_integration(self, monkeypatch):
        """A non-MCP integration that implements supports_tools is picked up."""
        from app.services import integration_tool_aggregator

        # Hypothetical future 'web_search' integration
        ws_inst = self._make_integration("web_search", "WebSearch")

        db = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [ws_inst]
        db.execute = AsyncMock(return_value=result)

        ws_provider = MagicMock()
        ws_provider.supports_tools.return_value = True
        async def fake_get_tools(integration):
            t = MagicMock(); t.name = "web_search"
            return [t]
        ws_provider.get_tools = fake_get_tools

        self._patch_registry(monkeypatch, {"web_search": ws_provider})

        out = asyncio.run(
            integration_tool_aggregator.aggregate(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        )
        assert len(out) == 1
        assert out[0].name == "web_search"


# --------------------------------------------------------------------------- config flow

class TestConfigFlow:
    def test_validates_http_url(self):
        from integrations.mcp_client.config_flow import McpClientConfigFlow

        cf = McpClientConfigFlow()
        out = asyncio.run(
            cf.validate_input({
                "instance_name": "X",
                "transport": "http",
                "url": "https://example.com/mcp",
            })
        )
        assert out["transport"] == "http"

    def test_rejects_insecure_http(self):
        from integrations.mcp_client.config_flow import McpClientConfigFlow

        cf = McpClientConfigFlow()
        with pytest.raises(ValueError):
            asyncio.run(
                cf.validate_input({
                    "instance_name": "X",
                    "transport": "http",
                    "url": "http://example.com/mcp",
                })
            )

    def test_validates_stdio_command(self):
        from integrations.mcp_client.config_flow import McpClientConfigFlow

        cf = McpClientConfigFlow()
        out = asyncio.run(
            cf.validate_input({
                "instance_name": "X",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "server"],
            })
        )
        assert out["command"] == "npx"

    def test_rejects_disallowed_stdio(self):
        from integrations.mcp_client.config_flow import McpClientConfigFlow

        cf = McpClientConfigFlow()
        with pytest.raises(ValueError):
            asyncio.run(
                cf.validate_input({
                    "instance_name": "X",
                    "transport": "stdio",
                    "command": "rm",
                    "args": ["-rf", "/"],
                })
            )

    def test_rejects_missing_instance_name(self):
        from integrations.mcp_client.config_flow import McpClientConfigFlow

        cf = McpClientConfigFlow()
        with pytest.raises(ValueError):
            asyncio.run(cf.validate_input({"transport": "http", "url": "https://x.com"}))


# --------------------------------------------------------------------------- provider

class TestProvider:
    def test_pull_data_noop(self):
        from integrations.mcp_client.provider import McpClientProvider

        provider = McpClientProvider()
        out = asyncio.run(provider.pull_data(MagicMock()))
        assert out == []

    def test_supports_tools_true(self):
        from integrations.mcp_client.provider import McpClientProvider

        provider = McpClientProvider()
        assert provider.supports_tools() is True

    def test_get_tools_returns_empty_on_failure(self):
        """get_tools swallows errors and returns [] (SDK contract)."""
        from integrations.mcp_client.provider import McpClientProvider

        provider = McpClientProvider()
        integration = MagicMock()
        integration.id = uuid.uuid4()
        integration.instance_name = "Bad"

        # Patch the connection manager to raise.
        import integrations.mcp_client.connection_manager as cm_mod
        original = cm_mod.mcp_connection_manager
        fake_cm = MagicMock()
        async def boom(integration):
            raise RuntimeError("connection refused")
        fake_cm.list_tools = boom
        cm_mod.mcp_connection_manager = fake_cm
        try:
            out = asyncio.run(provider.get_tools(integration))
            assert out == []
        finally:
            cm_mod.mcp_connection_manager = original

    def test_list_tools_action_returns_structured_results(self):
        """The list_tools custom action returns display blocks, not just a message."""
        from integrations.mcp_client.provider import McpClientProvider

        provider = McpClientProvider()
        integration = MagicMock()
        integration.id = uuid.uuid4()
        integration.instance_name = "TestServer"

        import integrations.mcp_client.connection_manager as cm_mod
        original = cm_mod.mcp_connection_manager
        fake_cm = MagicMock()
        async def fake_list_tools(integration):
            t1 = MagicMock(); t1.name = "echo"; t1.description = "Echo text"
            t2 = MagicMock(); t2.name = "add"; t2.description = "Add numbers"
            return [t1, t2]
        fake_cm.list_tools = fake_list_tools
        cm_mod.mcp_connection_manager = fake_cm
        try:
            out = asyncio.run(provider.execute_custom_action(integration, "list_tools"))
            assert "results" in out
            assert out["message"] == "Discovered 2 tool(s)."
            # First block is kv (summary), second is table (tools)
            types = [b["type"] for b in out["results"]]
            assert "kv" in types
            assert "table" in types
            # The table should have the tool names
            table = next(b for b in out["results"] if b["type"] == "table")
            assert table["columns"] == ["Name", "Description"]
            assert table["rows"][0][0] == "echo"
        finally:
            cm_mod.mcp_connection_manager = original

    def test_test_connection_action_returns_kv_block(self):
        """The test_connection custom action returns a kv block with status."""
        from integrations.mcp_client.provider import McpClientProvider

        provider = McpClientProvider()
        integration = MagicMock()
        integration.id = uuid.uuid4()
        integration.instance_name = "TestServer"

        import integrations.mcp_client.connection_manager as cm_mod
        original = cm_mod.mcp_connection_manager
        fake_cm = MagicMock()
        async def fake_health(integration):
            return {"status": "connected", "transport": "stdio", "tools": 3}
        fake_cm.health = fake_health
        cm_mod.mcp_connection_manager = fake_cm
        try:
            out = asyncio.run(provider.execute_custom_action(integration, "test_connection"))
            assert "results" in out
            assert out["results"][0]["type"] == "kv"
            assert out["results"][0]["items"]["Status"] == "Connected"
            assert out["results"][0]["items"]["Transport"] == "stdio"
        finally:
            cm_mod.mcp_connection_manager = original

    def test_custom_actions(self):
        from integrations.mcp_client.provider import McpClientProvider

        provider = McpClientProvider()
        actions = provider.get_custom_actions()
        ids = {a["id"] for a in actions}
        assert ids == {"test_connection", "list_tools", "restart_connection"}

    def test_api_proxy_refuses_tool_invocation(self):
        from integrations.mcp_client.provider import McpClientProvider

        provider = McpClientProvider()
        # Only GET /status is allowed.
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.handle_api_request(MagicMock(), "tools", "POST", MagicMock()))
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.handle_api_request(MagicMock(), "call/my_tool", "POST", MagicMock()))


class TestDisplayBlocks:
    """Verify the SDK display block builders (integrations/sdk/display.py)."""

    def test_kv_block(self):
        from integrations.sdk import kv_block
        b = kv_block("Conn", {"Status": "Connected", "Tools": 3})
        assert b["type"] == "kv"
        assert b["title"] == "Conn"
        assert b["items"] == {"Status": "Connected", "Tools": 3}

    def test_list_block(self):
        from integrations.sdk import list_block
        b = list_block("Tools", ["echo", "add"])
        assert b["type"] == "list"
        assert b["items"] == ["echo", "add"]

    def test_table_block(self):
        from integrations.sdk import table_block
        b = table_block("T", ["Name", "Desc"], [["echo", "Echos"], ["add", "Adds"]])
        assert b["type"] == "table"
        assert b["columns"] == ["Name", "Desc"]
        assert b["rows"] == [["echo", "Echos"], ["add", "Adds"]]

    def test_json_block(self):
        from integrations.sdk import json_block
        b = json_block("Raw", {"k": "v"})
        assert b["type"] == "json"
        assert b["data"] == {"k": "v"}

    def test_text_block(self):
        from integrations.sdk import text_block
        b = text_block("Note", "hello world")
        assert b["type"] == "text"
        assert b["content"] == "hello world"

    def test_code_block_with_language(self):
        from integrations.sdk import code_block
        b = code_block("Cmd", "curl x", "bash")
        assert b["type"] == "code"
        assert b["language"] == "bash"

    def test_code_block_without_language(self):
        from integrations.sdk import code_block
        b = code_block("Cmd", "curl x")
        assert "language" not in b

    def test_action_result_with_message_and_results(self):
        from integrations.sdk import action_result, list_block
        r = action_result("Discovered 2 tools", [list_block("Tools", ["a", "b"])])
        assert r["message"] == "Discovered 2 tools"
        assert len(r["results"]) == 1

    def test_action_result_message_only(self):
        from integrations.sdk import action_result
        r = action_result("just a toast")
        assert r == {"message": "just a toast"}
        assert "results" not in r

    def test_action_result_extra_fields(self):
        from integrations.sdk import action_result, list_block
        r = action_result("msg", [list_block("T", ["x"])], tools=["x"])
        assert r["tools"] == ["x"]  # backwards-compat extra field
