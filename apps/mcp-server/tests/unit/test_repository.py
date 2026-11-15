from uuid import UUID

from kb_mcp.models import Actor, DocStatus
from kb_mcp.repository import InMemoryRepository, can_view, can_write

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

ALICE_DOC = UUID("00000000-0000-0000-0000-000000000001")
BOB_DOC = UUID("00000000-0000-0000-0000-000000000003")
ALICE_PRIVATE = UUID("00000000-0000-0000-0000-000000000006")
SALES_DOC = UUID("00000000-0000-0000-0000-000000000007")


async def test_search_is_limited_to_visible_departments() -> None:
    repository = InMemoryRepository.with_demo_data()

    engineering = await repository.search(ALICE, "复盘", limit=20, offset=0)
    sales = await repository.search(ALICE, "客户", limit=20, offset=0)

    assert [doc.id for doc in engineering] == [ALICE_DOC]
    assert sales == []


async def test_org_document_is_visible_to_everyone() -> None:
    repository = InMemoryRepository.with_demo_data()

    alice = await repository.search(ALICE, "新人指南", limit=20, offset=0)
    admin = await repository.search(ADMIN, "新人指南", limit=20, offset=0)

    assert len(alice) == 1 and len(admin) == 1


async def test_private_document_is_owner_only() -> None:
    repository = InMemoryRepository.with_demo_data()

    assert await repository.get(ALICE, ALICE_PRIVATE) is not None
    assert await repository.get(BOB, ALICE_PRIVATE) is None
    assert await repository.get(ADMIN, ALICE_PRIVATE) is None


async def test_cross_department_document_is_hidden() -> None:
    repository = InMemoryRepository.with_demo_data()

    assert await repository.get(ALICE, SALES_DOC) is None


async def test_manager_can_write_same_department_document() -> None:
    repository = InMemoryRepository.with_demo_data()
    document = await repository.get(BOB, ALICE_DOC)
    assert document is not None

    assert can_write(BOB, document) is True
    assert can_write(ALICE, await repository.get(ALICE, BOB_DOC)) is False


async def test_admin_can_write_any_document_but_not_view_it() -> None:
    repository = InMemoryRepository.with_demo_data()
    document = await repository.get_unscoped(ALICE_DOC)
    assert document is not None

    assert can_view(ADMIN, document) is False
    assert can_write(ADMIN, document) is True


async def test_archive_is_idempotent() -> None:
    repository = InMemoryRepository.with_demo_data()

    first = await repository.archive(ALICE_DOC)
    second = await repository.archive(ALICE_DOC)

    assert first.status is DocStatus.ARCHIVED
    assert second.status is DocStatus.ARCHIVED
    assert second.version == first.version
