import pytest
from mcp.server.mcpserver.exceptions import ToolError

from kb_mcp.server import mcp


async def test_whoami_reports_dev_identity() -> None:
    result = await mcp.call_tool("whoami", {})
    assert result.structured_content is not None
    assert result.structured_content["username"] == "alice"
    assert result.structured_content["dev_mode"] is True
    assert "kb.read" in result.structured_content["scopes"]


async def test_search_tool_returns_visible_documents() -> None:
    result = await mcp.call_tool("search_documents", {"query": "复盘"})
    assert result.structured_content is not None
    items = result.structured_content["items"]
    assert len(items) == 1
    assert items[0]["title"] == "Q2 复盘"


async def test_archive_tool_denies_missing_scope() -> None:
    with pytest.raises(ToolError, match="FORBIDDEN_SCOPE"):
        await mcp.call_tool(
            "archive_document",
            {
                "document_id": "00000000-0000-0000-0000-000000000001",
                "reason": "cleanup",
            },
        )
