"""Seed PostgreSQL with Keycloak users and demo documents.

Prerequisites:
  1. `docker compose -f infra/docker-compose.yml up -d`
  2. Keycloak has imported `infra/keycloak/realm-kb.json`
  3. The `kb` database schema exists (init.sql ran automatically)

Run:
    ./.venv/Scripts/python.exe scripts/bootstrap_seed.py

The script reads real `sub` values from the Keycloak Admin API, so document
ownership matches the tokens issued at login. Never hard-code a `sub`.
"""

from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg
import httpx

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
REALM = os.getenv("KEYCLOAK_REALM", "kb")
ADMIN_USER = os.getenv("KC_BOOTSTRAP_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("KC_BOOTSTRAP_ADMIN_PASSWORD", "admin")
DSN = os.getenv("KB_DATABASE_URL", "postgresql://kb:kb@localhost:5432/kb")

DEPARTMENTS = {
    "engineering": "11111111-1111-1111-1111-111111111111",
    "sales": "22222222-2222-2222-2222-222222222222",
    "it": "33333333-3333-3333-3333-333333333333",
}

SAMPLES = [
    # (title, content, owner username, department, visibility)
    (
        "Q2 复盘",
        "本季度完成了知识库迁移与权限模型评审。",
        "alice",
        "engineering",
        "department",
    ),
    (
        "值班手册",
        "线上故障先看监控，再查发布记录，最后联系值班经理。",
        "alice",
        "engineering",
        "department",
    ),
    (
        "发布流程",
        "所有发布必须经过代码评审、CI 与灰度验证。",
        "bob",
        "engineering",
        "department",
    ),
    (
        "架构决策记录",
        "MCP Server 只做资源服务器，授权由 Keycloak 负责。",
        "bob",
        "engineering",
        "department",
    ),
    ("新人指南", "入职第一周完成环境搭建与安全培训。", "alice", "engineering", "org"),
    (
        "Alice 的私人笔记",
        "这是只有 alice 能看到的草稿。",
        "alice",
        "engineering",
        "private",
    ),
    ("销售话术", "先理解客户痛点，再介绍产品能力。", "admin", "sales", "department"),
    ("客户清单", "客户信息属于销售部内部资料。", "admin", "sales", "department"),
    ("季度目标", "本季度重点是提升续约率。", "admin", "sales", "department"),
]


async def get_admin_token(client: httpx.AsyncClient) -> str:
    response = await client.post(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN_USER,
            "password": ADMIN_PASSWORD,
        },
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


async def get_user(
    client: httpx.AsyncClient, token: str, username: str
) -> dict[str, object]:
    response = await client.get(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/users",
        params={"username": username, "exact": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    users = response.json()
    if not users:
        raise SystemExit(
            f"Keycloak user {username!r} not found in realm {REALM!r}. "
            "Check that realm-kb.json was imported."
        )
    return dict(users[0])


async def main(*, reset: bool) -> None:
    # trust_env=False: httpx reads the Windows system proxy, which turns
    # http://localhost:8080 into a 502 Bad Gateway on machines that run a
    # local proxy (Clash, v2ray, corporate agents). These calls only ever
    # target localhost, so the proxy must never be used.
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        token = await get_admin_token(client)
        keycloak_users = {}
        for username in ("alice", "bob", "admin"):
            user = await get_user(client, token, username)
            keycloak_users[username] = {
                "sub": user["id"],
                "email": user.get("email"),
                "department": (user.get("attributes") or {}).get("department", ["it"])[
                    0
                ],
            }

    connection = await asyncpg.connect(DSN)
    try:
        if reset:
            # Re-importing the realm gives every user a new `sub`, so old rows
            # would point at identities that no longer exist.
            await connection.execute("DELETE FROM documents")
            await connection.execute("DELETE FROM users")
            print("cleared users and documents")

        for name, department_id in DEPARTMENTS.items():
            await connection.execute(
                """
                INSERT INTO departments (id, name)
                VALUES ($1, $2)
                ON CONFLICT (name) DO NOTHING
                """,
                department_id,
                name,
            )

        for username, info in keycloak_users.items():
            await connection.execute(
                """
                INSERT INTO users (id, username, email, department_id)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (id) DO UPDATE SET
                    username = EXCLUDED.username,
                    email = EXCLUDED.email,
                    department_id = EXCLUDED.department_id
                """,
                info["sub"],
                username,
                info["email"],
                DEPARTMENTS[info["department"]],
            )

        existing = await connection.fetchval("SELECT count(*) FROM documents")
        if existing:
            print(f"documents already seeded ({existing} rows); skipping samples")
            return

        for title, content, owner, department, visibility in SAMPLES:
            sub = keycloak_users[owner]["sub"]
            await connection.execute(
                """
                INSERT INTO documents
                    (title, content, owner_id, department_id, visibility)
                VALUES ($1, $2, $3, $4, $5::visibility)
                """,
                title,
                content,
                sub,
                DEPARTMENTS[department],
                visibility,
            )

        print(f"seeded {len(SAMPLES)} documents for {len(keycloak_users)} users")
    finally:
        await connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete existing users/documents first (use after re-importing the realm)",
    )
    asyncio.run(main(reset=parser.parse_args().reset))
