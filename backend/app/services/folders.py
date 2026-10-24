"""User folders. They live in our database and never move mail in Gmail."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.constants import MAX_FOLDER_DEPTH
from app.models import Folder, FolderItem, Message


class FolderError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def depth_of(db: Session, folder_id: str | None) -> int:
    depth = 0
    seen = set()
    current = folder_id
    while current:
        if current in seen:
            raise FolderError("folder_cycle", "A folder cannot contain itself.")
        seen.add(current)
        folder = db.get(Folder, current)
        if folder is None:
            break
        depth += 1
        current = folder.parent_id
    return depth


def subtree_height(db: Session, folder_id: str) -> int:
    children = db.query(Folder).filter(Folder.parent_id == folder_id).all()
    if not children:
        return 1
    return 1 + max(subtree_height(db, child.id) for child in children)


def assert_can_place(db: Session, folder_id: str | None, parent_id: str | None) -> None:
    if parent_id and folder_id and parent_id == folder_id:
        raise FolderError("folder_cycle", "A folder cannot be moved inside itself.")
    if parent_id and folder_id and _is_ancestor(db, ancestor_id=folder_id, node_id=parent_id):
        raise FolderError("folder_cycle", "A folder cannot be moved inside its own subfolder.")
    parent_depth = depth_of(db, parent_id)
    extra = subtree_height(db, folder_id) if folder_id else 1
    if parent_depth + extra > MAX_FOLDER_DEPTH:
        raise FolderError(
            "folder_depth",
            f"Folders can only be nested {MAX_FOLDER_DEPTH} levels deep.",
        )


def _is_ancestor(db: Session, ancestor_id: str, node_id: str | None) -> bool:
    seen = set()
    current = node_id
    while current:
        if current in seen:
            return True
        if current == ancestor_id:
            return True
        seen.add(current)
        folder = db.get(Folder, current)
        if folder is None:
            return False
        current = folder.parent_id
    return False


def create_folder(db: Session, user_id: str, name: str, parent_id: str | None = None, **extra) -> Folder:
    if parent_id:
        parent = db.get(Folder, parent_id)
        if parent is None or parent.user_id != user_id:
            raise FolderError("folder_missing", "That parent folder does not exist.")
        if parent.is_smart:
            raise FolderError("smart_folder", "Mail and folders cannot be filed into a smart folder.")
    assert_can_place(db, None, parent_id)
    siblings = db.query(Folder).filter(Folder.user_id == user_id, Folder.parent_id == parent_id).count()
    folder = Folder(user_id=user_id, parent_id=parent_id, name=name.strip(), position=siblings, **extra)
    db.add(folder)
    db.flush()
    return folder


def move_folder(db: Session, folder: Folder, parent_id: str | None) -> None:
    if parent_id:
        parent = db.get(Folder, parent_id)
        if parent is None or parent.user_id != folder.user_id:
            raise FolderError("folder_missing", "That parent folder does not exist.")
    assert_can_place(db, folder.id, parent_id)
    folder.parent_id = parent_id


def delete_folder(db: Session, folder: Folder, subfolders: str = "promote") -> None:
    children = db.query(Folder).filter(Folder.parent_id == folder.id).all()
    if subfolders == "delete":
        for child in children:
            delete_folder(db, child, "delete")
    else:
        for child in children:
            child.parent_id = folder.parent_id
    db.query(FolderItem).filter(FolderItem.folder_id == folder.id).delete()
    db.delete(folder)


def file_thread(db: Session, folder: Folder, account_id: str, thread_id: str) -> FolderItem:
    if folder.is_smart:
        raise FolderError("smart_folder", "A smart folder updates itself. File mail into a normal folder.")
    existing = (
        db.query(FolderItem)
        .filter(
            FolderItem.folder_id == folder.id,
            FolderItem.account_id == account_id,
            FolderItem.thread_id == thread_id,
        )
        .one_or_none()
    )
    if existing:
        return existing
    item = FolderItem(folder_id=folder.id, account_id=account_id, thread_id=thread_id)
    db.add(item)
    db.flush()
    return item


def unread_map(db: Session, user_id: str) -> dict[str, int]:
    folders = db.query(Folder).filter(Folder.user_id == user_id, Folder.is_smart.is_(False)).all()
    direct = {folder.id: 0 for folder in folders}
    items = db.query(FolderItem).filter(FolderItem.folder_id.in_(direct.keys()) if direct else False).all() if direct else []
    for item in items:
        count = (
            db.query(Message)
            .filter(
                Message.account_id == item.account_id,
                Message.thread_id == item.thread_id,
                Message.is_read.is_(False),
                Message.is_trashed.is_(False),
            )
            .count()
        )
        direct[item.folder_id] = direct.get(item.folder_id, 0) + count
    by_parent: dict[str | None, list[Folder]] = {}
    for folder in folders:
        by_parent.setdefault(folder.parent_id, []).append(folder)

    rolled: dict[str, int] = {}

    def roll(folder_id: str) -> int:
        total = direct.get(folder_id, 0)
        for child in by_parent.get(folder_id, []):
            total += roll(child.id)
        rolled[folder_id] = total
        return total

    for folder in folders:
        if folder.parent_id is None or folder.parent_id not in direct:
            roll(folder.id)
    return rolled
