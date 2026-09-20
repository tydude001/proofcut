import json
import os
from pathlib import Path

import pytest

from proofcut.project import (
    LEGACY_MANIFEST_NAME,
    MANIFEST_NAME,
    SCHEMA_VERSION,
    LegacyManifestError,
    Project,
    ProjectConflictError,
    ProjectError,
)


def _v1_project(tmp_path: Path, name: str = "old") -> Project:
    """A project directory as a pre-cue-table proofcut left it: no `cues` key.

    Written through `create` then rewound, rather than assembled by hand, so
    the fixture is the real layout minus exactly the thing v2 added.
    """
    project = Project.create(tmp_path / name)
    project.write_manifest(
        {
            "schema_version": 1,
            "name": name,
            "timebase": 24.0,
            "clips": [{"clip_id": "a", "media": "media/a.wav"}],
        }
    )
    return project


def test_create_lays_out_the_directory(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "demo")

    assert project.manifest_path.exists()
    assert project.media_dir.is_dir()
    assert project.transcript_dir.is_dir()
    assert project.render_dir.is_dir()

    manifest = project.read_manifest()
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["name"] == "demo"
    assert manifest["clips"] == []
    assert manifest["cues"] == []


def test_create_refuses_to_clobber_an_existing_project(tmp_path: Path) -> None:
    Project.create(tmp_path / "demo")
    with pytest.raises(ProjectError, match="already exists"):
        Project.create(tmp_path / "demo")


def test_open_rejects_a_directory_with_no_manifest(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(ProjectError, match="no proofcut project"):
        Project.open(tmp_path / "empty")


def test_open_rejects_an_unknown_schema_version(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "demo")
    project.write_manifest({"schema_version": SCHEMA_VERSION + 1})

    with pytest.raises(ProjectError, match="schema_version"):
        Project.open(project.root)


def test_open_rejects_a_corrupt_manifest(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "demo")
    project.manifest_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(ProjectError, match="not valid JSON"):
        Project.open(project.root)


def _edited_project(tmp_path: Path, edits: int) -> Project:
    """A project with `edits` snapshots behind it.

    Each edit opens its own `Project`, because a snapshot fires at most once
    per instance — the way every op runs.
    """
    root = Project.create(tmp_path / "demo").root
    for n in range(edits):
        project = Project.open(root)
        manifest = project.read_manifest()
        manifest["name"] = f"edit-{n}"
        project.write_manifest(manifest)
    return Project.open(root)


def test_corrupt_manifest_refusal_names_the_newest_snapshot(tmp_path: Path) -> None:
    project = _edited_project(tmp_path, edits=2)
    newest = project.snapshots()[-1].manifest
    assert newest is not None
    project.manifest_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(ProjectError, match="not valid JSON") as refused:
        Project.open(project.root)

    assert str(newest) in str(refused.value)


def test_corrupt_manifest_refusal_skips_an_unreadable_snapshot(tmp_path: Path) -> None:
    project = _edited_project(tmp_path, edits=2)
    older, newest = (s.manifest for s in project.snapshots()[-2:])
    assert older is not None and newest is not None
    newest.write_text("{ also not json", encoding="utf-8")
    project.manifest_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(ProjectError) as refused:
        Project.open(project.root)

    assert str(older) in str(refused.value)
    assert str(newest) not in str(refused.value)


def test_corrupt_manifest_with_no_history_offers_no_recovery(tmp_path: Path) -> None:
    """Nothing to name, so nothing is claimed — not "snapshot: none"."""
    project = Project.create(tmp_path / "demo")
    project.manifest_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(ProjectError) as refused:
        Project.open(project.root)

    assert "snapshot" not in str(refused.value)


def test_corrupt_manifest_recovery_is_the_copy_it_says_it_is(tmp_path: Path) -> None:
    """The hint is advice, so follow it: the named file must open the project,
    and `undo` must in fact be unable to — the reason the hint names a copy."""
    from proofcut import ops

    project = _edited_project(tmp_path, edits=2)
    newest = project.snapshots()[-1].manifest
    assert newest is not None
    project.manifest_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(ProjectError, match="not valid JSON"):
        ops.undo(project.root)

    project.manifest_path.write_bytes(newest.read_bytes())
    assert Project.open(project.root).read_manifest()["schema_version"] == SCHEMA_VERSION


def test_open_refusal_names_migrate_when_there_is_a_path_forward(tmp_path: Path) -> None:
    """The refusal is a dead end unless it says what clears it."""
    project = _v1_project(tmp_path)

    with pytest.raises(ProjectError, match="proofcut migrate"):
        Project.open(project.root)


def test_open_refusal_does_not_offer_migrate_for_a_newer_project(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "demo")
    project.write_manifest({"schema_version": SCHEMA_VERSION + 1})

    with pytest.raises(ProjectError, match="no migration path"):
        Project.open(project.root)


def test_migrate_brings_a_v1_project_forward(tmp_path: Path) -> None:
    project = _v1_project(tmp_path)

    report = Project.migrate(project.root)

    assert report["schema_version"] == SCHEMA_VERSION
    # Stepwise, so a v1 project reaches the present through every step in
    # turn rather than through a v1 -> current shortcut nobody else takes.
    assert report["steps"] == ["1 -> 2", "2 -> 3", "3 -> 4"]
    assert report["migrated"] is True

    manifest = Project.open(project.root).read_manifest()
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["cues"] == []
    assert manifest["descriptions"] == []
    assert manifest["cards"] == []
    # v1's keys mean in v4 what they meant in v1; every step is additive only.
    assert manifest["name"] == "old"
    assert manifest["timebase"] == 24.0
    assert manifest["clips"] == [{"clip_id": "a", "media": "media/a.wav"}]


def test_migrate_copies_the_manifest_aside_before_writing(tmp_path: Path) -> None:
    project = _v1_project(tmp_path)

    backup = Path(Project.migrate(project.root)["backup"])

    assert backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8"))["schema_version"] == 1


def test_the_manifest_backup_is_invisible_to_undo(tmp_path: Path) -> None:
    """`proofcut-v3.json` lives in `cache/history/` beside the numbered snapshots
    and must not become an undo step: rolling the timeline back one edit must
    not roll the schema back with it. Measured as a delta rather than against
    an empty stack, because a manifest write is itself a snapshot now — the
    fixture's own `write_manifest` takes one — and `migrate` is the write that
    must not."""
    project = _v1_project(tmp_path)
    before = project.snapshots()

    backup = Path(Project.migrate(project.root)["backup"])

    assert backup.parent == project.history_dir
    assert [s.index for s in project.snapshots()] == [s.index for s in before]
    assert backup not in {s.manifest for s in project.snapshots()}


def test_migrate_plans_without_writing(tmp_path: Path) -> None:
    project = _v1_project(tmp_path)

    report = Project.migrate(project.root, plan=True)

    assert report["plan"] is True
    assert report["schema_version"] == 1
    assert report["steps"] == ["1 -> 2", "2 -> 3", "3 -> 4"]
    assert report["migrated"] is False
    assert report["backup"] is None
    assert project.read_manifest()["schema_version"] == 1
    assert "cues" not in project.read_manifest()
    assert "descriptions" not in project.read_manifest()
    assert "cards" not in project.read_manifest()


def test_migrate_is_a_no_op_on_a_current_project(tmp_path: Path) -> None:
    """Running it twice is how it will actually be used — over a directory of
    projects, some already forward. The second pass must not error or churn."""
    project = Project.create(tmp_path / "demo")

    report = Project.migrate(project.root)

    assert report["steps"] == []
    assert report["migrated"] is False
    assert report["backup"] is None
    assert not list(project.history_dir.glob("*.json"))


def test_migrate_refuses_a_newer_project(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "demo")
    project.write_manifest({"schema_version": SCHEMA_VERSION + 1})

    with pytest.raises(ProjectError, match="forward-only"):
        Project.migrate(project.root)


def test_migrate_refuses_a_non_integer_schema_version(tmp_path: Path) -> None:
    """`True` is an `int` subclass, so an unguarded check migrates it as v1."""
    project = Project.create(tmp_path / "demo")
    project.write_manifest({"schema_version": True})

    with pytest.raises(ProjectError, match="no migration step is registered"):
        Project.migrate(project.root)


def test_migrate_rejects_a_directory_with_no_manifest(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(ProjectError, match="no proofcut project"):
        Project.migrate(tmp_path / "empty")


# -- the pre-rename manifest (docs/plans/RENAME.md, decision 1) --------------


def _pre_rename(project: Project) -> Project:
    """The directory as a lucid-era install left it: the manifest under its
    old name. Renamed from a real `create` rather than written by hand, so the
    fixture differs from a current project by exactly the filename."""
    project.manifest_path.rename(project.root / LEGACY_MANIFEST_NAME)
    return project


def test_the_legacy_manifest_name_is_the_pre_rename_one() -> None:
    """Pinned as a literal: the whole point of the constant is that it names
    the file an older install wrote, so it must never follow a rename."""
    assert LEGACY_MANIFEST_NAME == "lucid.json"
    assert MANIFEST_NAME == "proofcut.json"


def test_open_refuses_a_pre_rename_project_naming_migrate(tmp_path: Path) -> None:
    project = _pre_rename(Project.create(tmp_path / "old"))

    with pytest.raises(LegacyManifestError, match="proofcut migrate") as refused:
        Project.open(project.root)

    # Not the "nothing here" refusal — there is a project here.
    assert "no proofcut project" not in str(refused.value)
    assert LEGACY_MANIFEST_NAME in str(refused.value)
    # And `open` renamed nothing on its way to refusing.
    assert (project.root / LEGACY_MANIFEST_NAME).exists()
    assert not project.manifest_path.exists()


def test_open_refuses_a_directory_holding_both_manifests(tmp_path: Path) -> None:
    """Never silently prefer one: nothing on disk says which is the project."""
    project = Project.create(tmp_path / "both")
    (project.root / LEGACY_MANIFEST_NAME).write_bytes(project.manifest_path.read_bytes())

    with pytest.raises(ProjectError, match="both") as refused:
        Project.open(project.root)
    assert not isinstance(refused.value, LegacyManifestError)

    with pytest.raises(ProjectError, match="both"):
        Project.migrate(project.root)
    with pytest.raises(ProjectError, match="both"):
        Project.migrate(project.root, plan=True)
    # Both files exactly as they were.
    assert (project.root / LEGACY_MANIFEST_NAME).exists()
    assert project.manifest_path.exists()


def test_create_refuses_a_directory_holding_a_pre_rename_manifest(tmp_path: Path) -> None:
    """Creating there would write the two-manifest directory `open` refuses."""
    project = _pre_rename(Project.create(tmp_path / "old"))

    with pytest.raises(ProjectError, match="proofcut migrate"):
        Project.create(project.root)
    assert not project.manifest_path.exists()


def test_migrate_renames_a_pre_rename_manifest_without_a_schema_step(tmp_path: Path) -> None:
    project = _pre_rename(Project.create(tmp_path / "old"))
    before = (project.root / LEGACY_MANIFEST_NAME).read_bytes()

    report = Project.migrate(project.root)

    # The filename step alone: not a `_MIGRATIONS` entry, and no bump.
    assert SCHEMA_VERSION == 4
    assert report["steps"] == ["lucid.json -> proofcut.json"]
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["manifest"] == MANIFEST_NAME
    assert report["migrated"] is True
    assert not (project.root / LEGACY_MANIFEST_NAME).exists()
    assert project.manifest_path.read_bytes() == before
    Project.open(project.root)  # opens now

    # Backed up under the name it had, invisible to undo, byte-identical.
    backup = Path(report["backup"])
    assert backup == project.history_dir / f"lucid-v{SCHEMA_VERSION}.json"
    assert backup.read_bytes() == before
    assert backup not in {s.manifest for s in project.snapshots()}

    # And a second run is the ordinary no-op.
    again = Project.migrate(project.root)
    assert again["steps"] == []
    assert again["migrated"] is False


def test_migrate_plans_the_filename_step_without_writing(tmp_path: Path) -> None:
    project = _pre_rename(Project.create(tmp_path / "old"))
    before = (project.root / LEGACY_MANIFEST_NAME).read_bytes()
    history_before = sorted(p.name for p in project.history_dir.iterdir())

    report = Project.migrate(project.root, plan=True)

    assert report["plan"] is True
    assert report["steps"] == ["lucid.json -> proofcut.json"]
    assert report["manifest"] == LEGACY_MANIFEST_NAME
    assert report["migrated"] is False
    assert report["backup"] is None
    assert (project.root / LEGACY_MANIFEST_NAME).read_bytes() == before
    assert not project.manifest_path.exists()
    assert sorted(p.name for p in project.history_dir.iterdir()) == history_before


def test_the_filename_step_runs_before_the_version_steps(tmp_path: Path) -> None:
    """A pre-rename manifest at an old schema: renamed first, then stepped
    forward — and the version write after the rename is `write_manifest`'s
    stale-read check passing across it, since the stamp is a digest of the
    bytes the rename moved unchanged."""
    project = _pre_rename(_v1_project(tmp_path))

    plan = Project.migrate(project.root, plan=True)
    assert plan["steps"] == ["lucid.json -> proofcut.json", "1 -> 2", "2 -> 3", "3 -> 4"]

    report = Project.migrate(project.root)

    assert report["steps"] == plan["steps"]
    assert report["migrated"] is True
    manifest = Project.open(project.root).read_manifest()
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["cards"] == []
    assert not (project.root / LEGACY_MANIFEST_NAME).exists()
    # One backup, of the file as found: old name, old version.
    backup = Path(report["backup"])
    assert backup.name == "lucid-v1.json"
    assert json.loads(backup.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not (project.history_dir / "proofcut-v1.json").exists()


def test_migrate_refuses_a_pre_rename_manifest_with_no_path_forward_before_renaming(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path / "old")
    project.write_manifest({"schema_version": SCHEMA_VERSION + 1})
    _pre_rename(project)

    with pytest.raises(ProjectError, match="forward-only"):
        Project.migrate(project.root)
    assert (project.root / LEGACY_MANIFEST_NAME).exists()
    assert not project.manifest_path.exists()


def test_migrate_refuses_a_second_writer_on_the_legacy_manifest_mid_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stale-read stamp across the rename: a write landing on `lucid.json`
    after `migrate` read it must be refused, not renamed into place under a
    manifest nobody re-read."""
    project = _pre_rename(_v1_project(tmp_path))
    legacy_path = project.root / LEGACY_MANIFEST_NAME
    real_backup = Project._backup_manifest

    def backup_then_second_writer(self: Project, version: object, source: Path | None = None) -> Path:
        dest = real_backup(self, version, source)
        manifest = json.loads(legacy_path.read_text(encoding="utf-8"))
        manifest["name"] = "written-by-second"
        legacy_path.write_text(json.dumps(manifest), encoding="utf-8")
        return dest

    monkeypatch.setattr(Project, "_backup_manifest", backup_then_second_writer)

    with pytest.raises(ProjectConflictError, match="changed on disk"):
        Project.migrate(project.root)
    assert json.loads(legacy_path.read_text(encoding="utf-8"))["name"] == "written-by-second"
    assert not project.manifest_path.exists()


def test_write_manifest_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "demo")
    project.write_manifest({"schema_version": SCHEMA_VERSION, "name": "demo", "clips": []})

    assert [p.name for p in project.root.glob("proofcut.json*")] == [MANIFEST_NAME]


# -- two writers on one project (TRIAL.md § Nothing in lucid notices two ----
# writers in one project) --------------------------------------------------


def test_write_manifest_refuses_a_write_the_file_has_moved_past(tmp_path: Path) -> None:
    """Two `Project` instances on the same root — a second `proofcut web`, an
    agent panel, a CLI command beside either. The second writer's write must
    not silently discard the first's, so the first (stale) instance's write
    is refused rather than clobbering."""
    root = tmp_path / "demo"
    first = Project.create(root)
    manifest = first.read_manifest()

    second = Project.open(root)
    second.write_manifest({**second.read_manifest(), "name": "written-by-second"})

    manifest["name"] = "written-by-first"
    with pytest.raises(ProjectConflictError, match="changed on disk"):
        first.write_manifest(manifest)

    # The second writer's edit survived, untouched by the refused write.
    assert Project.open(root).read_manifest()["name"] == "written-by-second"


def test_write_manifest_succeeds_after_re_reading_past_a_conflict(tmp_path: Path) -> None:
    """The recovery path: re-read (picking up the stamp the other writer
    left) and the same instance can write again."""
    root = tmp_path / "demo"
    first = Project.create(root)
    first.read_manifest()

    second = Project.open(root)
    second.write_manifest({**second.read_manifest(), "name": "written-by-second"})

    refreshed = first.read_manifest()
    refreshed["name"] = "written-by-first-after-reread"
    first.write_manifest(refreshed)

    assert Project.open(root).read_manifest()["name"] == "written-by-first-after-reread"


def test_write_manifest_does_not_refuse_an_instance_that_never_read(tmp_path: Path) -> None:
    """`Project.create`'s own first write, and any other write with nothing
    to compare against, must not be caught up in a check meant for a stale
    *read*."""
    root = tmp_path / "demo"
    Project.create(root)  # writes once, with no prior read on this instance

    fresh = Project(root)  # never called read_manifest at all
    fresh.write_manifest({"schema_version": SCHEMA_VERSION, "name": "demo", "clips": []})

    assert Project.open(root).read_manifest()["name"] == "demo"


def test_undo_refuses_when_the_manifest_moved_past_the_snapshot_it_read(tmp_path: Path) -> None:
    """`restore()` bypasses `write_manifest` (a raw `shutil.copy2`, since the
    undo step is not itself an edit to snapshot) and would otherwise be the
    one path around this refusal."""
    root = tmp_path / "demo"
    first = Project.create(root)
    manifest = first.read_manifest()
    manifest["name"] = "renamed"
    first.write_manifest(manifest)  # one snapshot to undo back to

    stale = Project.open(root)  # reads the post-rename state

    second = Project.open(root)
    second.write_manifest({**second.read_manifest(), "name": "written-by-second"})

    with pytest.raises(ProjectConflictError, match="changed on disk"):
        stale.restore()

    assert Project.open(root).read_manifest()["name"] == "written-by-second"


def test_a_second_write_inside_the_same_clock_tick_is_still_a_conflict(tmp_path: Path) -> None:
    """The stamp is a digest of the bytes, not the file's mtime. Windows
    stamps two writes inside one timer tick (~15ms) with the same mtime, and
    with an mtime stamp the refusal above passed on one CI run and failed on
    the next with no code change between them (ci run 34793271514). Pin the
    mtime back to what the stale instance saw, so a clock-based check would
    read "unchanged", and the refusal has to fire on the bytes alone."""
    root = tmp_path / "demo"
    first = Project.create(root)
    stale = Project.open(root)
    held = stale.read_manifest()
    seen = first.manifest_path.stat()

    second = Project.open(root)
    second.write_manifest({**second.read_manifest(), "name": "written-by-second"})
    os.utime(first.manifest_path, ns=(seen.st_atime_ns, seen.st_mtime_ns))
    assert first.manifest_path.stat().st_mtime_ns == seen.st_mtime_ns

    with pytest.raises(ProjectConflictError, match="changed on disk"):
        stale.write_manifest({**held, "name": "written-by-stale"})


# -- the Windows folder-path limit -------------------------------------------


def _windows(monkeypatch: pytest.MonkeyPatch, *, long_paths: int | None) -> None:
    """Stand in for Windows with `LongPathsEnabled` at `long_paths` (`None`:
    the key is missing). A fake `winreg`, so the registry read itself is under
    test rather than stubbed past — and the same fake on a real Windows runner,
    whose own setting is 1 and would otherwise decide the test."""
    import sys
    import types

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def open_key(hive: object, path: str) -> object:
        assert path == r"SYSTEM\CurrentControlSet\Control\FileSystem"
        if long_paths is None:
            raise FileNotFoundError(path)
        return _Key()

    fake = types.ModuleType("winreg")
    fake.HKEY_LOCAL_MACHINE = object()  # type: ignore[attr-defined]
    fake.OpenKey = open_key  # type: ignore[attr-defined]
    fake.QueryValueEx = lambda key, name: (long_paths, 4)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")


def _root_of_length(tmp_path: Path, length: int) -> Path:
    base = str(tmp_path.resolve() / "deep")
    assert len(base) < length - 2, "tmp_path is already longer than the root asked for"
    return Path(base + "-" + "d" * (length - len(base) - 1))


def test_create_refuses_a_root_too_deep_for_windows_with_long_paths_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The laptop's probe: with `LongPathsEnabled` 0 a 235-character root got as
    far as `cache` and died on `cache\\transcripts` with a traceback, leaving a
    half-made project. The refusal comes first, writes nothing, and names both
    fixes."""
    from proofcut.project import (
        PATH_HEADROOM,
        WINDOWS_DIR_LIMIT,
        PathTooLongError,
        max_root_length,
    )

    _windows(monkeypatch, long_paths=0)
    limit = WINDOWS_DIR_LIMIT - PATH_HEADROOM
    assert max_root_length() == limit
    root = _root_of_length(tmp_path, limit + 1)

    with pytest.raises(PathTooLongError) as refused:
        Project.create(root)
    assert (refused.value.length, refused.value.limit) == (limit + 1, limit)
    assert isinstance(refused.value, ProjectError)
    assert f"at most {limit} characters" in str(refused.value)
    assert "LongPathsEnabled 1" in str(refused.value)
    assert not root.exists()


def test_create_takes_a_root_at_exactly_the_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from proofcut.project import max_root_length

    _windows(monkeypatch, long_paths=0)
    root = _root_of_length(tmp_path, max_root_length())
    assert (Project.create(root).root / MANIFEST_NAME).exists()


def test_create_takes_a_deep_root_when_long_paths_are_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: the same root, one registry value apart."""
    from proofcut.project import WINDOWS_DIR_LIMIT, max_root_length

    _windows(monkeypatch, long_paths=1)
    assert max_root_length() is None
    root = _root_of_length(tmp_path, WINDOWS_DIR_LIMIT - 60)
    assert (Project.create(root).root / MANIFEST_NAME).exists()


def test_a_missing_long_paths_key_reads_as_the_stock_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from proofcut.project import windows_long_paths

    _windows(monkeypatch, long_paths=None)
    assert windows_long_paths() is False


def test_no_limit_is_asked_about_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from proofcut.project import max_root_length, windows_long_paths

    monkeypatch.setattr(sys, "platform", "linux")
    assert windows_long_paths() is None
    assert max_root_length() is None


def _too_long(where: str) -> OSError:
    error = OSError(2, "The filename or extension is too long", where)
    error.winerror = 206  # type: ignore[attr-defined]
    return error


def test_refusing_path_too_long_turns_206_into_the_family_clients_already_catch() -> None:
    """The MCP tools and the web UI's jobs flatten a `ProjectError` and let
    everything else trace, so this is what reaches all of their `except`s."""
    from proofcut.project import refusing_path_too_long

    where = "C:\\deep\\cache\\proxy\\a-long-clip.mp4"
    with pytest.raises(ProjectError) as refused, refusing_path_too_long():
        raise _too_long(where)
    assert str(refused.value).startswith(f"Windows refused a path as too long ({len(where)} characters: {where})")
    assert isinstance(refused.value.__cause__, OSError)


def test_refusing_path_too_long_passes_any_other_os_error_through() -> None:
    from proofcut.project import refusing_path_too_long

    with pytest.raises(PermissionError), refusing_path_too_long():
        raise PermissionError(13, "Access is denied")
