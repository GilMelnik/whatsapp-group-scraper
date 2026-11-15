"""One id per group member, constant across the chat history and across backups.

WhatsApp stores a sender under a phone id (``<digits>@s.whatsapp.net``, until 2024) or a
"lid" id (``<digits>@lid``, from 2025). The database links the two (``jid_map``: lid ->
phone) and records a member's change of number (``message_system_number_change``: old ->
new phone). Every raw id ("alias") linked that way belongs to one member, whose id is:

- the alias a members file already gives it (an alias keeps its member for ever);
- otherwise, for a member the file does not know yet, its phone id (the one the group saw
  first when the member changed number), or its lid id when the database knows no phone.

A members file (``{"members": [{"id", "aliases": [...]}]}``, ids only) is read when it
exists and written back extended: a new alias joins the member the database links it to.
When the database links two members that both exist already, both keep their ids and the
link is only counted (``MemberTable.conflicts``): the table never merges members on its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

LID_SERVER = "@lid"


def is_lid(alias: str) -> bool:
    return alias.endswith(LID_SERVER)


class _Components:
    """Union-find over aliases."""

    def __init__(self) -> None:
        self._parent: Dict[str, str] = {}

    def add(self, alias: str) -> None:
        self._parent.setdefault(alias, alias)

    def find(self, alias: str) -> str:
        self.add(alias)
        root = alias
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[alias] != root:
            self._parent[alias], alias = root, self._parent[alias]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)

    def groups(self) -> List[List[str]]:
        by_root: Dict[str, List[str]] = {}
        for alias in self._parent:
            by_root.setdefault(self.find(alias), []).append(alias)
        return [sorted(members) for _, members in sorted(by_root.items())]


class MemberTable:
    """Alias -> member id for one export (see the module docstring)."""

    def __init__(self, known: Optional[Dict[str, str]] = None) -> None:
        self.known: Dict[str, str] = dict(known or {})
        self.member_of: Dict[str, str] = {}
        self.conflicts = 0
        self.new_aliases = 0
        self.new_members = 0

    @classmethod
    def load(cls, path: Optional[Path]) -> "MemberTable":
        """The table of a members file; empty when ``path`` is ``None`` or does not exist."""
        if path is None or not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {
                alias: str(member["id"])
                for member in data["members"]
                for alias in [member["id"], *member["aliases"]]
            }
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(
                f"Members file {path} is not {{'members': [{{'id', 'aliases'}}]}}: {exc}"
            ) from exc
        return cls(known)

    def resolve(
        self,
        aliases: Iterable[str],
        links: Iterable[Tuple[str, str]],
        first_seen: Sequence[str],
    ) -> None:
        """Give every alias (and every alias linked to one) its member id.

        ``links`` are pairs of aliases of one member (lid/phone, old/new number);
        ``first_seen`` lists aliases in the order the group's history shows them.
        """
        components = _Components()
        for alias in aliases:
            if alias:
                components.add(alias)
        for a, b in links:
            if a and b:
                components.union(a, b)
        order = {alias: index for index, alias in enumerate(first_seen)}
        for group in components.groups():
            existing = sorted({self.known[a] for a in group if a in self.known})
            if not existing:
                member = self._derived_id(group, order)
                self.new_members += 1
            elif len(existing) == 1:
                member = existing[0]
            else:
                self.conflicts += 1
                member = self._preferred_existing(group, existing, order)
            for alias in group:
                if alias in self.known:
                    self.member_of[alias] = self.known[alias]
                else:
                    self.member_of[alias] = member
                    self.new_aliases += 1

    @staticmethod
    def _derived_id(group: List[str], order: Dict[str, int]) -> str:
        phones = [a for a in group if not is_lid(a)]
        candidates = phones or group
        return min(candidates, key=lambda a: (order.get(a, len(order)), a))

    def _preferred_existing(
        self, group: List[str], existing: List[str], order: Dict[str, int]
    ) -> str:
        """The member new aliases join when the database links several known members."""
        phones = [a for a in group if not is_lid(a) and a in self.known]
        if phones:
            first = min(phones, key=lambda a: (order.get(a, len(order)), a))
            return self.known[first]
        return existing[0]

    def member(self, alias: str) -> str:
        """The member id of a raw alias (an alias never resolved stays itself)."""
        return self.member_of.get(alias, alias)

    def members(self) -> List[Dict[str, object]]:
        """``[{"id", "aliases"}]`` of every member known to the file or to this export."""
        table: Dict[str, Set[str]] = {}
        for alias, member in {**self.known, **self.member_of}.items():
            table.setdefault(member, {member}).add(alias)
        return [
            {"id": member, "aliases": sorted(aliases)}
            for member, aliases in sorted(table.items())
        ]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"members": self.members()}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
