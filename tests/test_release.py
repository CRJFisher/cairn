"""Doc 16: nothing in the artifact is shaped like its author, and the manifest is real.

Three claims, each of which an inspection by a person reliably misses and a machine with a
different username finds on its first run:

- **No author-shaped literal reaches the tree.** Not in the source, not in a generated
  fixture, not in a recorded one. An absolute path to somebody's home directory is the
  whole class, and it arrives through generated files and recordings rather than through
  code anybody wrote by hand.
- **The directory is self-contained.** Nothing in it imports or path-references anything
  outside it, which is what makes the checkout both the package and the skill and makes
  moving it the whole of packaging.
- **The manifest states the floor.** One Python version, in the one place a reader, an
  installer and the type checker each look, and an executable that actually resolves.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import sys
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from cairn import unsupported
from cairn.__main__ import first_screen
from cairn.__main__ import main as cairn_main
from cairn.layout import RUNS_DIRECTORY, VIEW_BASE_ENV
from cairn.record.vocabulary import VERDICT_GREEN_WITH_EXCLUSIONS
from cairn.skill.vocabulary import CAPABILITY_ORDER, TASK_BY_CAPABILITY
from cairn.topology import WORKTREES_SUFFIX
from cairn.verify import EXCLUSION_CAUSES
from cairn.workflow.pin import PINNED_DIRECTORY
from cairn.workflow.schema import ENGINE_VERSION
from scripts.worked_example import RECORDING
from tests.engine import SKIP_ENV

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
README = PACKAGE_ROOT / "README.md"
MANIFEST = PACKAGE_ROOT / "pyproject.toml"
WORKFLOW = PACKAGE_ROOT / ".github" / "workflows" / "ci.yml"

# The floor, stated once here and required of every file that states it.
MINIMUM_PYTHON = (3, 11)
MINIMUM_PYTHON_TEXT = "3.11"

# A home directory, written absolutely. The user segment is what makes it author-shaped, so
# the shape is what is matched and the segment is what is judged.
HOME_PATH = re.compile(
    r"""(?:^|[\s"'`(\[=:,])(?P<root>/Users|/home)/(?P<user>[A-Za-z0-9._-]+)""",
)
# The same literal in its Windows and superuser spellings, where there is no segment to
# judge: nothing here has any business naming either.
FORBIDDEN_LITERALS = ("C:\\Users\\", "/root/", "~/workspace", "$HOME/workspace")

# The user segments that are deliberately not anybody's. `me` is the corpus of invocations'
# stand-in for the person asking; `p` is a transcript path in a provider fixture. Adding to
# this list is the deliberate act the guard exists to require.
PLACEHOLDER_USERS = frozenset({"me", "p"})

# Build products and caches: not the artifact, and not what is being guarded.
UNSCANNED_SUFFIXES = (".pyc", ".svg", ".png", ".ico")


def tracked_files() -> list[Path]:
    """Every file the repository ships, which is the thing being published, but this one.

    Asked of git rather than walked, because what is published is what is tracked: a
    scratch file in the working tree is not part of the artifact and a guard that failed on
    one would be a guard people learn to ignore. This file is left out because it is where
    the forbidden shapes are spelled, and where the guard is shown rejecting them.
    """
    listed = subprocess.run(
        ("git", "ls-files", "-z"),
        cwd=PACKAGE_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    guard = Path(__file__).resolve()
    return [
        PACKAGE_ROOT / name
        for name in listed.stdout.split("\0")
        if name and PACKAGE_ROOT / name != guard
    ]


def readable(path: Path) -> str | None:
    """The file's text, or None where it is not text at all."""
    if path.suffix in UNSCANNED_SUFFIXES:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


class NothingInTheArtifactIsShapedLikeItsAuthor(unittest.TestCase):
    """Task 7's guard, over the source and over every generated and recorded fixture."""

    def setUp(self) -> None:
        self.files = tracked_files()
        self.assertGreater(len(self.files), 50, "the file listing did not come from git")

    def test_no_file_names_a_home_directory_that_belongs_to_somebody(self) -> None:
        found: list[str] = []
        for path in self.files:
            text = readable(path)
            if text is None:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                for match in HOME_PATH.finditer(line):
                    if match.group("user") in PLACEHOLDER_USERS:
                        continue
                    where = path.relative_to(PACKAGE_ROOT)
                    found.append(
                        f"{where}:{number}: {match.group('root')}/{match.group('user')}"
                    )
        self.assertEqual(
            found,
            [],
            "an absolute home directory reached the artifact; re-derive the path, or add "
            "the segment to PLACEHOLDER_USERS if it is nobody's",
        )

    def test_no_file_names_a_workspace_root_or_a_superuser_home(self) -> None:
        found: list[str] = []
        for path in self.files:
            text = readable(path)
            if text is None:
                continue
            for literal in FORBIDDEN_LITERALS:
                if literal in text:
                    found.append(f"{path.relative_to(PACKAGE_ROOT)}: {literal}")
        self.assertEqual(found, [])

    def test_no_file_names_this_machine(self) -> None:
        """The shape rule catches the class; this catches the instance.

        A home directory somewhere the shape rule does not know about — and the checkout's
        own location, which is how a generated fixture usually leaks one — is caught here
        by comparing against the machine the suite is running on. It finds a different
        literal on every machine, which is exactly the point.
        """
        machine = {str(Path.home()), str(PACKAGE_ROOT)}
        found: list[str] = []
        for path in self.files:
            text = readable(path)
            if text is None:
                continue
            for literal in machine:
                if literal in text:
                    found.append(f"{path.relative_to(PACKAGE_ROOT)}: {literal}")
        self.assertEqual(found, [])

    def test_the_guard_would_fail_on_a_home_directory(self) -> None:
        """A guard nobody has seen reject anything is a guard nobody can trust."""
        self.assertIsNotNone(HOME_PATH.search('    "cwd": "/Users/someone/src/product",'))
        self.assertIsNotNone(HOME_PATH.search("PYTHONPATH=/home/someone/cairn"))
        # And would not fail on the two it must not: a placeholder, and a path that merely
        # carries the word.
        match = HOME_PATH.search("run offline-export against /Users/me/src/product")
        assert match is not None
        self.assertIn(match.group("user"), PLACEHOLDER_USERS)
        self.assertIsNone(HOME_PATH.search("dir=/a/very/long/path/home/dags"))


class TheDirectoryIsSelfContained(unittest.TestCase):
    """Task 14: extraction is a move, asserted rather than trusted."""

    def test_the_package_imports_nothing_but_the_standard_library_and_itself(self) -> None:
        outside: list[str] = []
        for path in sorted((PACKAGE_ROOT / "cairn").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    # A relative import has no module to judge and cannot leave the package.
                    names = [node.module] if node.level == 0 and node.module else []
                else:
                    continue
                for name in names:
                    root = name.split(".")[0]
                    if root == "cairn" or root in sys.stdlib_module_names:
                        continue
                    outside.append(f"{path.relative_to(PACKAGE_ROOT)}: {name}")
        self.assertEqual(outside, [], "the package took a dependency outside itself")

    def test_no_document_links_outside_the_repository(self) -> None:
        link = re.compile(r"\]\(([^)\s#]+)")
        escapes: list[str] = []
        for path in tracked_files():
            if path.suffix != ".md":
                continue
            text = readable(path)
            if text is None:
                continue
            for target in link.findall(text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                resolved = (path.parent / target).resolve()
                if resolved != PACKAGE_ROOT and PACKAGE_ROOT not in resolved.parents:
                    escapes.append(f"{path.relative_to(PACKAGE_ROOT)} -> {target}")
        self.assertEqual(escapes, [])

    def test_the_worked_example_reads_a_fixture_in_this_directory(self) -> None:
        """The one command a reader is told to run has to work from a fresh checkout."""
        self.assertTrue((RECORDING / "status.jsonl").is_file())
        self.assertTrue((RECORDING / "reports").is_dir())
        self.assertIn(PACKAGE_ROOT, RECORDING.parents)


class AnUnsupportedPlatformIsToldSoBeforeItFails(unittest.TestCase):
    """Task 9: the unsupported case is an early, actionable message rather than a
    `ModuleNotFoundError` on the first command somebody runs."""

    def test_the_platforms_cairn_is_measured_on_are_not_refused(self) -> None:
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform):
                self.assertIsNone(unsupported(platform))

    def test_windows_is_refused_with_what_to_do_instead(self) -> None:
        for platform in ("win32", "cygwin"):
            with self.subTest(platform=platform):
                refusal = unsupported(platform)
                assert refusal is not None
                self.assertIn(platform, refusal)
                self.assertIn("WSL", refusal)

    def test_the_refusal_is_reached_before_the_import_that_would_fail(self) -> None:
        """`cairn.locks` imports `fcntl`, which does not exist on Windows. The guard has to
        be in the package's own `__init__`, because that is the only module every other one
        is imported through."""
        initial = (PACKAGE_ROOT / "cairn" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("raise ImportError", initial)
        self.assertIn("import fcntl", (PACKAGE_ROOT / "cairn" / "locks.py").read_text("utf-8"))


class TheManifestStatesTheFloorAndTheWayIn(unittest.TestCase):
    """Task 15 and the audit's addition: one interpreter floor, stated everywhere once."""

    def setUp(self) -> None:
        self.manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
        self.project = self.manifest["project"]

    def test_the_manifest_carries_install_metadata_and_a_build_backend(self) -> None:
        for field in ("name", "version", "description", "readme", "license"):
            with self.subTest(field=field):
                self.assertTrue(self.project.get(field), f"{field} is not stated")
        self.assertIn("build-backend", self.manifest["build-system"])

    def test_the_package_declares_no_dependency(self) -> None:
        """A step runs Cairn from a snapshot directory on `PYTHONPATH`, where a dependency
        tree would have nowhere to be installed."""
        self.assertEqual(self.project["dependencies"], [])

    def test_one_interpreter_floor_is_stated_in_every_place_that_states_one(self) -> None:
        self.assertEqual(self.project["requires-python"], f">={MINIMUM_PYTHON_TEXT}")
        self.assertEqual(
            self.manifest["tool"]["pyright"]["pythonVersion"], MINIMUM_PYTHON_TEXT
        )
        self.assertIn(
            f"Python {MINIMUM_PYTHON_TEXT}",
            README.read_text(encoding="utf-8"),
            "the README does not name the interpreter the manifest requires",
        )

    def test_the_suite_runs_on_an_interpreter_that_meets_the_floor(self) -> None:
        """A floor nothing is checked against is a number rather than a claim."""
        self.assertGreaterEqual(sys.version_info[:2], MINIMUM_PYTHON)

    def test_the_executable_the_manifest_declares_resolves(self) -> None:
        module, _, attribute = self.project["scripts"]["cairn"].partition(":")
        imported = __import__(module, fromlist=[attribute])
        self.assertTrue(callable(getattr(imported, attribute)))

    def test_the_type_checker_is_strict(self) -> None:
        self.assertEqual(self.manifest["tool"]["pyright"]["typeCheckingMode"], "strict")


class TheQualityGateIsCheckedIn(unittest.TestCase):
    """The audit's addition: both halves of the gate run somewhere that is not a laptop."""

    def setUp(self) -> None:
        self.assertTrue(WORKFLOW.is_file(), f"{WORKFLOW} is not checked in")
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_it_runs_the_suite_the_readme_names(self) -> None:
        """One command, so a reader reproducing the build runs what the build runs. The
        workflow adds the leak check around it and nothing else."""
        discovery = "-m unittest discover -s tests -t ."
        self.assertIn(discovery, self.workflow)
        self.assertIn(f"python3 {discovery}", README.read_text(encoding="utf-8"))

    def test_it_runs_the_type_checker_at_the_version_the_manifest_pins(self) -> None:
        """Two statements of one version drift. The manifest is where a reader installs it
        from, the workflow is where the build does, and this is what keeps them one."""
        manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
        pinned = manifest["dependency-groups"]["dev"]
        self.assertEqual(len(pinned), 1, "the development dependencies are no longer one")
        self.assertIn(pinned[0], self.workflow)

    def test_it_installs_the_pinned_engine_rather_than_skipping_the_engine_tests(
        self,
    ) -> None:
        """A build that set the skip would be green on the suites that cover the failures
        which otherwise report success."""
        self.assertIn(f"--version v{ENGINE_VERSION}", self.workflow)
        self.assertNotIn("CAIRN_SKIP_ENGINE_TESTS", self.workflow)

    def test_it_fails_on_a_leaked_resource(self) -> None:
        self.assertIn("ResourceWarning", self.workflow)


class TheReadmeStatesWhatItMayNotSoften(unittest.TestCase):
    """Doc 16's exit criteria, held to the words rather than to the gist.

    Each of these is a sentence a later edit could round off into something friendlier and
    untrue, which is the failure mode a security section has.
    """

    def setUp(self) -> None:
        self.readme = README.read_text(encoding="utf-8")

    def test_the_trust_boundary_is_stated_unsoftened(self) -> None:
        self.assertIn("The trust boundary is the user account, not the server.", self.readme)

    def test_the_licensing_boundary_names_the_licence_and_the_reason(self) -> None:
        self.assertIn("GPL-3.0-or-later", self.readme)
        self.assertIn("never imports its Go API", self.readme)

    def test_the_posture_says_what_no_posture_closes(self) -> None:
        for claim in ("binds loopback", "cross-origin", "0600"):
            with self.subTest(claim=claim):
                self.assertIn(claim, self.readme)

    def test_the_install_path_is_one_command_against_the_pin(self) -> None:
        self.assertIn("installer.sh", self.readme)
        self.assertIn(f"--version v{ENGINE_VERSION}", self.readme)
        self.assertIn("checksums.txt", self.readme)

    def test_the_install_path_owns_the_engines_base_configuration(self) -> None:
        self.assertIn("cairn supervise base-config --disable", self.readme)

    def test_the_worked_example_is_the_failing_one(self) -> None:
        """The example's quoted output has to be output, not a description of one. The
        verdict and the cause are both checked against the frozen vocabularies, so a
        renamed word fails here rather than leaving the one page a cold reader has
        quoting something the command no longer prints."""
        self.assertIn("python3 -m scripts.worked_example", self.readme)
        self.assertIn(VERDICT_GREEN_WITH_EXCLUSIONS, self.readme)
        quoted = re.findall(r"^\s*cause: (\S+)$", self.readme, re.MULTILINE)
        self.assertTrue(quoted, "the example quotes no exclusion cause")
        for cause in quoted:
            with self.subTest(cause=cause):
                self.assertIn(cause, EXCLUSION_CAUSES)

    def test_the_engine_test_policy_is_stated_once(self) -> None:
        self.assertIn(SKIP_ENV, self.readme)

    def test_it_names_the_skill_registration_and_a_check_that_starts_nothing(self) -> None:
        self.assertIn("~/.claude/skills/cairn", self.readme)
        self.assertIn("python3 -m cairn supervise base-config", self.readme)

    def test_a_cold_machine_is_told_what_it_needs_before_anything_else(self) -> None:
        """Plan 29 A, item 1: the floor, in the document a cold reader has. A person on an
        unsupported platform finding out from a traceback is the failure this closes."""
        for named in ("Python 3.11", "git", "Claude Code CLI", "macOS and Linux", "WSL"):
            with self.subTest(named=named):
                self.assertIn(named, self.readme)

    def test_the_checks_verify_the_engine_the_provider_and_discovery(self) -> None:
        """Plan 29 A, item 4: a check that spends nothing and mutates nothing, over all
        three things a first run needs — the engine, the agent installation a session runs
        as, and the skill being findable by the name a person types."""
        for check in ("dagu version", "claude --version", "~/.claude/skills/cairn/SKILL.md"):
            with self.subTest(check=check):
                self.assertIn(check, self.readme)
        self.assertIn("None of these launches a server, a scheduler or a run", self.readme)

    def test_the_path_ends_in_authoring_and_explaining_a_plan(self) -> None:
        """Plan 29 A, item 5: the walk ends with a plan authored and explained, which is the
        shape of every offer — Cairn says what it would do and waits."""
        self.assertIn("Author and explain a minimal plan, before anything is offered", self.readme)
        for command in (
            "python3 -m cairn plan report fixtures/plans/single-step/graph.json",
            "python3 -m cairn workflow author fixtures/plans/single-step/graph.json",
            "python3 -m cairn explain workflow --plan single-step",
        ):
            with self.subTest(command=command):
                self.assertIn(command, self.readme)

    def test_the_plan_the_readme_walks_is_in_this_directory(self) -> None:
        """The one page a cold reader has may name no plan the checkout does not ship."""
        self.assertTrue((PACKAGE_ROOT / "fixtures" / "plans" / "single-step" / "graph.json").is_file())


class TheCommandLinesFirstScreenLeadsBackToTheSkill(unittest.TestCase):
    """Plan 29 A: going looking for a command line is how somebody arrives at the wrong door.

    The screen is held to leading with the way back rather than to its wording: the six
    tasks, the name that opens the skill, what has to be installed, and one example that
    spends nothing — all of it above the subcommands an emitted workflow invokes.
    """

    def setUp(self) -> None:
        self.screen = first_screen()

    def test_it_says_the_command_line_is_internal_and_names_what_is_not(self) -> None:
        self.assertIn("is internal", " ".join(self.screen.split()))
        self.assertIn("/cairn", self.screen)

    def test_it_leads_with_all_six_tasks_a_person_asks_for(self) -> None:
        for capability in CAPABILITY_ORDER:
            with self.subTest(capability=capability):
                self.assertIn(TASK_BY_CAPABILITY[capability], self.screen)

    def test_it_names_the_prerequisites_and_one_example(self) -> None:
        self.assertIn("Python 3.11", self.screen)
        self.assertIn(ENGINE_VERSION, self.screen)
        self.assertIn("python3 -m scripts.worked_example", self.screen)

    def test_the_plumbing_comes_after_all_of_it(self) -> None:
        plumbing = self.screen.index("runtime plumbing")
        for leading in ("/cairn", TASK_BY_CAPABILITY[CAPABILITY_ORDER[0]], "worked_example"):
            with self.subTest(leading=leading):
                self.assertLess(self.screen.index(leading), plumbing)

    def test_the_whole_help_screen_carries_it_before_the_subcommands(self) -> None:
        spoken = io.StringIO()
        with redirect_stdout(spoken), self.assertRaises(SystemExit):
            cairn_main(["--help"])
        said = spoken.getvalue()
        self.assertLess(said.index("/cairn"), said.index("positional arguments"))


class TheReadmeIsStillWhatTheReaderIsShown(unittest.TestCase):
    """The rest of doc 16's README criteria, unchanged by plan 29's additions."""

    def setUp(self) -> None:
        self.readme = README.read_text(encoding="utf-8")

    def test_every_path_cairn_touches_is_listed_with_a_resolution_rule(self) -> None:
        """Task 1: the enumeration, in the one document a cold reader has. Each row names
        where the path comes from, because "it is configurable" is how an author-shaped
        default survives an inspection."""
        self.assertIn("### Every path, and where it comes from", self.readme)
        for path in (WORKTREES_SUFFIX, RUNS_DIRECTORY, PINNED_DIRECTORY, VIEW_BASE_ENV):
            with self.subTest(path=path):
                self.assertIn(path, self.readme)
        self.assertIn("per-machine build\nproduct", self.readme)


if __name__ == "__main__":
    unittest.main()
