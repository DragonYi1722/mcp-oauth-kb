from kb_mcp.server import mcp


async def test_expected_tools_are_registered() -> None:
    names = {tool.name for tool in await mcp.list_tools()}
    assert {"echo", "whoami"} <= names
