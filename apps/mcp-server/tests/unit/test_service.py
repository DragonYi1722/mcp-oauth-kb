from uuid import UUID

import pytest

from kb_mcp import service
from kb_mcp.errors import KbError
from kb_mcp.models import Actor, Visibility
from kb_mcp.repository import InMemoryRepository

ALICE = Actor(
    sub="dev-alice",
    username="alice",
    department="engineering",
    roles=("employee",),
    scopes=("kb.read", "kb.write"),
)
BOB = Actor(
    sub="dev-bob",
    username="bob",
    department="engineering",
    roles=("employee", "manager"),
    scopes=("kb.read", "kb.write"),
)
ADMIN = Actor(
    sub="dev-admin",
    username="admin",
    department="it",
    roles=("employee", "admin"),
    scopes=("kb.read", "kb.write", "kb.admin"),
)

ALICE_DOC = "00000000-0000-0000-0000-000000000001"
BOB_DOC = "00000000-0000-0000-0000-000000000003"
SALES_DOC = "00000000-0000-0000-0000-000000000007"


@pytest.fixture
def repository() -> InMemoryRepository:
    return InMemoryRepository.with_demo_data()


async def test_search_paginates(repository: InMemoryRepository) -> None:
    first = await service.search(ALICE, repository, query="。", limit=2, cursor=None)
    assert len(first["items"]) == 2
    assert first["next_cursor"]

    second = await service.search(
        ALICE, repository, query="。", limit=2, cursor=str(first["next_cursor"])
    )
    first_ids = {item["id"] for item in first["items"]}
    second_ids = {item["id"] for item in second["items"]}
    assert first_ids.isdisjoint(second_ids)


async def test_invalid_cursor_is_rejected(repository: InMemoryRepository) -> None:
    with pytest.raises(KbError) as exc:
        await service.search(ALICE, repository, query="复盘", limit=10, cursor="not-a-cursor")
    assert exc.value.code == "VALIDATION_ERROR"


async def test_create_uses_token_subject_as_owner(
    repository: InMemoryRepository,
) -> None:
    created = await service.create(
        ALICE,
        repository,
        title="新文档",
        content="内容",
        visibility=Visibility.DEPARTMENT,
    )
    document = created["document"]
    assert document["owner"] == "alice"
    assert document["department"] == "engineering"

    fetched = await repository.get(ALICE, UUID(str(document["id"])))
    assert fetched is not None


async def test_employee_cannot_update_another_users_document(
    repository: InMemoryRepository,
) -> None:
    with pytest.raises(KbError) as exc:
        await service.update(
            ALICE,
            repository,
            document_id=BOB_DOC,
            expected_version=1,
            title="hijack",
            content=None,
            visibility=None,
        )
    assert exc.value.code == "NOT_FOUND"


async def test_manager_can_update_same_department_document(
    repository: InMemoryRepository,
) -> None:
    result = await service.update(
        BOB,
        repository,
        document_id=ALICE_DOC,
        expected_version=1,
        title="经理修订",
        content=None,
        visibility=None,
    )
    assert result["document"]["title"] == "经理修订"
    assert result["document"]["version"] == 2


async def test_version_conflict_is_reported(repository: InMemoryRepository) -> None:
    with pytest.raises(KbError) as exc:
        await service.update(
            ALICE,
            repository,
            document_id=ALICE_DOC,
            expected_version=99,
            title="过期更新",
            content=None,
            visibility=None,
        )
    assert exc.value.code == "CONFLICT"


async def test_archive_requires_admin_role(repository: InMemoryRepository) -> None:
    with pytest.raises(KbError) as exc:
        await service.archive(ALICE, repository, document_id=ALICE_DOC)
    assert exc.value.code == "FORBIDDEN_ROLE"


async def test_admin_can_archive_any_document(repository: InMemoryRepository) -> None:
    result = await service.archive(ADMIN, repository, document_id=SALES_DOC)
    assert result["archived"] is True
    assert result["document"]["status"] == "archived"


async def test_cross_department_read_returns_404(
    repository: InMemoryRepository,
) -> None:
    with pytest.raises(KbError) as exc:
        await service.get(ALICE, repository, document_id=SALES_DOC)
    assert exc.value.code == "NOT_FOUND"
    assert exc.value.http_status == 404
