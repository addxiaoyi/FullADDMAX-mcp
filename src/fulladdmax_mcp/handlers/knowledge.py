"""Obsidian vault read / write operations for the ``knowledge`` mega tool.

The ``knowledge`` tool exposes 5 operations:

* ``obsidian_list_notes``
* ``obsidian_read_note``
* ``obsidian_search_notes``
* ``obsidian_write_note``
* ``obsidian_append_note``
"""

from __future__ import annotations

from .. import server_internal
from ..dispatcher import OperationHandler, register_schema
from ..param_parser import FieldSpec


SCHEMAS: dict[str, dict[str, FieldSpec]] = {
    "obsidian_list_notes": {
        "vault_path": FieldSpec(required=True, type=str),
        "folder": FieldSpec(required=False, type=str, default=""),
        "limit": FieldSpec(required=False, type=int, default=500),
    },
    "obsidian_read_note": {
        "vault_path": FieldSpec(required=True, type=str),
        "path": FieldSpec(required=True, type=str),
    },
    "obsidian_search_notes": {
        "vault_path": FieldSpec(required=True, type=str),
        "keyword": FieldSpec(required=True, type=str),
        "folder": FieldSpec(required=False, type=str, default=""),
        "case_sensitive": FieldSpec(required=False, type=bool, default=False),
        "limit": FieldSpec(required=False, type=int, default=50),
    },
    "obsidian_write_note": {
        "vault_path": FieldSpec(required=True, type=str),
        "path": FieldSpec(required=True, type=str),
        "body": FieldSpec(required=True, type=str),
        "frontmatter_json": FieldSpec(required=False, type=str, default=""),
        "overwrite": FieldSpec(required=False, type=bool, default=False),
    },
    "obsidian_append_note": {
        "vault_path": FieldSpec(required=True, type=str),
        "path": FieldSpec(required=True, type=str),
        "content": FieldSpec(required=True, type=str),
    },
}


for _name, _schema in SCHEMAS.items():
    register_schema(_name, _schema)


async def _obsidian_list_notes(
    *,
    vault_path: str,
    folder: str = "",
    limit: int = 500,
    **_: object,
) -> str:
    return server_internal.obsidian_list_notes(
        vault_path=vault_path, folder=folder, limit=limit
    )


async def _obsidian_read_note(
    *, vault_path: str, path: str, **_: object
) -> str:
    return server_internal.obsidian_read_note(vault_path, path)


async def _obsidian_search_notes(
    *,
    vault_path: str,
    keyword: str,
    folder: str = "",
    case_sensitive: bool = False,
    limit: int = 50,
    **_: object,
) -> str:
    return server_internal.obsidian_search_notes(
        vault_path,
        keyword,
        folder=folder,
        case_sensitive=case_sensitive,
        limit=limit,
    )


async def _obsidian_write_note(
    *,
    vault_path: str,
    path: str,
    body: str,
    frontmatter_json: str = "",
    overwrite: bool = False,
    **_: object,
) -> str:
    return server_internal.obsidian_write_note(
        vault_path, path, body, frontmatter_json, overwrite=overwrite
    )


async def _obsidian_append_note(
    *,
    vault_path: str,
    path: str,
    content: str,
    **_: object,
) -> str:
    return server_internal.obsidian_append_note(vault_path, path, content)


HANDLERS: dict[str, OperationHandler] = {
    "obsidian_list_notes": _obsidian_list_notes,
    "obsidian_read_note": _obsidian_read_note,
    "obsidian_search_notes": _obsidian_search_notes,
    "obsidian_write_note": _obsidian_write_note,
    "obsidian_append_note": _obsidian_append_note,
}
