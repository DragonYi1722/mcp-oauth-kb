"""MCP tool registration."""

from __future__ import annotations

from mcp.server import MCPServer

from ..audit import AuditLogger
from ..repository import DocumentRepository
from .documents import register_document_tools
from .identity import register_identity_tools


def register_tools(mcp: MCPServer, repository: DocumentRepository, audit: AuditLogger) -> None:
    register_identity_tools(mcp, audit)
    register_document_tools(mcp, repository, audit)
