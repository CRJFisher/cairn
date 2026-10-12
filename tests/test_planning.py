"""The planning folder answers "what is outstanding" from its index alone.

Three claims, each of which drifts the first time a document is added or closed by hand:

- **Every live document states its state in one place.** Line 3, in a fixed vocabulary, so a
  reader never has to find the state in prose. The folder is the kind, so a document in
  `ideas/` is an idea and a document elsewhere is not.
- **The index is the whole of the folder.** Every document is a row, every row is a document,
  and a live row's state is the state its document states.
- **A number names one document.** Numbers are global across the folders, so a link by number
  never has two targets.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PLANNING = PACKAGE_ROOT / "planning"
INDEX = PLANNING / "README.md"

LIVE_FOLDERS = ("fixes", "features", "ideas")
ARCHIVE_FOLDER = "archive"
STATES = ("idea", "proposed", "building", "built")
IDEA_FOLDER = "ideas"

STATUS_LINE = re.compile(r"^\*\*Status: (?P<state>[a-z]+)\.\*\*")
NUMBERED = re.compile(r"^(?P<number>\d{2})-[a-z0-9-]+\.md$")
# A table row whose second cell links to a document in this folder. The live table carries a
# state column the archive table does not, which is how the two are told apart.
ROW = re.compile(r"^\|\s*(?P<number>\d+|—)\s*\|\s*\[[^\]]+\]\((?P<path>[^)]+)\)\s*\|(?P<rest>.*)$")


def documents(folder: str) -> list[Path]:
    return sorted((PLANNING / folder).glob("*.md"))


def stated_state(path: Path) -> str | None:
    lines = path.read_text("utf-8").splitlines()
    status = STATUS_LINE.match(lines[2]) if len(lines) >= 3 else None
    return None if status is None else status["state"]


def numbers() -> list[int]:
    found: list[int] = []
    for folder in (*LIVE_FOLDERS, ARCHIVE_FOLDER):
        for path in documents(folder):
            named = NUMBERED.match(path.name)
            if named is not None:
                found.append(int(named["number"]))
    return found


def index_rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in INDEX.read_text("utf-8").splitlines():
        found = ROW.match(line)
        if found is None:
            continue
        cells = [cell.strip() for cell in found["rest"].split("|")]
        rows[found["path"]] = cells
    return rows


class EveryLiveDocumentStatesItsState(unittest.TestCase):
    def test_line_three_is_the_status_in_the_fixed_vocabulary(self) -> None:
        for folder in LIVE_FOLDERS:
            for path in documents(folder):
                with self.subTest(document=path.name):
                    lines = path.read_text("utf-8").splitlines()
                    self.assertTrue(lines[0].startswith("# "))
                    self.assertEqual(lines[1], "")
                    self.assertIn(stated_state(path), STATES)

    def test_the_folder_is_the_kind(self) -> None:
        for folder in LIVE_FOLDERS:
            for path in documents(folder):
                with self.subTest(document=path.name):
                    self.assertEqual(stated_state(path) == "idea", folder == IDEA_FOLDER)

    def test_nothing_lives_beside_the_folders_but_the_index(self) -> None:
        loose = sorted(path.name for path in PLANNING.iterdir() if path.is_file())
        self.assertEqual(loose, [INDEX.name])
        folders = sorted(path.name for path in PLANNING.iterdir() if path.is_dir())
        self.assertEqual(folders, sorted((*LIVE_FOLDERS, ARCHIVE_FOLDER)))


class TheIndexIsTheWholeFolder(unittest.TestCase):
    def test_every_document_is_a_row_and_every_row_a_document(self) -> None:
        on_disk = {
            path.relative_to(PLANNING).as_posix()
            for folder in (*LIVE_FOLDERS, ARCHIVE_FOLDER)
            for path in documents(folder)
        }
        self.assertEqual(set(index_rows()), on_disk)

    def test_a_live_row_states_the_state_its_document_states(self) -> None:
        rows = index_rows()
        for folder in LIVE_FOLDERS:
            for path in documents(folder):
                with self.subTest(document=path.name):
                    state = rows[path.relative_to(PLANNING).as_posix()][1]
                    self.assertEqual(state, stated_state(path))

    def test_the_next_number_the_index_names_is_the_next_free_one(self) -> None:
        stated = re.search(r"The next document is (\d+)\.", INDEX.read_text("utf-8"))
        assert stated is not None
        self.assertEqual(int(stated[1]), max(numbers()) + 1)


class ANumberNamesOneDocument(unittest.TestCase):
    def test_no_number_is_used_twice_across_the_folders(self) -> None:
        taken = numbers()
        self.assertEqual(len(taken), len(set(taken)))

    def test_every_live_document_is_numbered(self) -> None:
        for folder in LIVE_FOLDERS:
            for path in documents(folder):
                with self.subTest(document=path.name):
                    self.assertIsNotNone(NUMBERED.match(path.name))


if __name__ == "__main__":
    unittest.main()
