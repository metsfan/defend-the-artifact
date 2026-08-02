import os
import subprocess
import unreal

# Apply the standard Artifact-dino setup to every dino not yet committed to git.
REPO       = r"F:\ARKDevkit\Projects\ShooterGame\Mods\DefendTheArtifact"
MOUNT      = "/DefendTheArtifact/"
CONTROLLER = "/DefendTheArtifact/Dino/Dino_AIController_BP_Artifact"
DMGTYPE    = "/DefendTheArtifact/Dino/DmgType_Melee_DmgMetal_ArtifactDino"
TAG        = "ArtifactDino"
TEAM_NAME  = "Carnivores_High"

APPLY = True    # False = dry run: reports everything, including the original
                # AttackInfos[0] damage types, without writing

DINO_FOLDER = "/DefendTheArtifact/Dino"
SELECT = "untagged"   # "untagged"  -- any Pawn in DINO_FOLDER without TAG
                      # "untracked" -- git-untracked only (the old behaviour)
                      # "either"    -- union of the two

# Rather than flattening every attack to DMGTYPE, derive a per-original subclass:
# copy the vanilla damage type into DMG_FOLDER and reparent it to DMGTYPE, so it
# keeps its own overrides (bleed, torpor, harvest) but gains metal damage.
DERIVE_DMGTYPES = True
DMG_FOLDER = "/DefendTheArtifact/Dino"
NEW_SUFFIX = "_Artifact"   # you have both _Artifact and _Metal in use; new ones
                           # get this. Existing ones are reused, never duplicated.
REUSE_SUFFIXES = ("", "_Artifact", "_ArtifactDino", "_Metal")

ALL_INDICES = False   # True = every AttackInfos entry, not just [0]

# Assets to process regardless of git state -- use for dinos already committed.
EXTRA = ()
# e.g. EXTRA = ("/DefendTheArtifact/Dino/Artifact_SpiderL_Character_BP_Medium",)

TAMED_PROPS  = ("bCanBeTamed", "can_be_tamed", "CanBeTamed")
AICTRL_PROPS = ("AIControllerClass", "ai_controller_class")
MELEE_PROPS  = ("MeleeDamageType", "melee_damage_type")
INFOS_PROPS  = ("AttackInfos", "attack_infos")
TEAMNM_PROPS = ("TargetingTeamNameOverride", "targeting_team_name_override")

eal = unreal.EditorAssetLibrary


def class_of(path, label):
    asset = eal.load_asset(path)
    if asset is None:
        raise Exception("Could not load %s (%s)" % (path, label))
    cls = asset.generated_class() if isinstance(asset, unreal.Blueprint) else asset
    if cls is None:
        raise Exception("%s has no generated class -- needs a compile?" % path)
    return cls


def first_prop(obj, names):
    """UE accepts native or snake_case depending on build; find the one that reads."""
    for n in names:
        try:
            return n, obj.get_editor_property(n)
        except Exception:
            continue
    return None, None


def name_of(v):
    if v is None:
        return "None"
    try:
        return v.get_name()
    except Exception:
        return str(v)


def parent_name_of(asset_path):
    """Blueprint.ParentClass isn't reflected to Python here -- read the asset
    registry tag instead. Only used for reporting."""
    try:
        raw = eal.find_asset_data(asset_path).get_tag_value("ParentClass")
        if raw:
            return (raw.split("'")[-2] if "'" in raw else raw).rsplit(".", 1)[-1]
    except Exception:
        pass
    return "<unknown>"


_dmg_cache = {}
_dmg_created, _dmg_reused, _dmg_planned = [], [], []


def artifact_dmgtype(orig_class):
    """Vanilla damage type -> mod-local subclass of DMGTYPE. Reuses an existing
    asset under any known suffix; only creates when nothing matches."""
    if not DERIVE_DMGTYPES or orig_class is None:
        return dmg_class, "base"

    orig_path = orig_class.get_path_name().split(".")[0]
    name = orig_class.get_name()
    if name.endswith("_C"):
        name = name[:-2]

    if orig_path.startswith(MOUNT):
        return orig_class, "already mod-local"

    # A NoMetal type explicitly disables metal damage as its own override, so a
    # reparented copy would keep that and defeat the point -- use the base.
    if "nometal" in name.lower():
        return dmg_class, "NoMetal -> base"

    if name in _dmg_cache:
        return _dmg_cache[name], "cached"

    for suf in REUSE_SUFFIXES:
        cand = "%s/%s%s" % (DMG_FOLDER, name, suf)
        if eal.does_asset_exist(cand):
            cls = class_of(cand, "derived damage type")
            _dmg_cache[name] = cls
            _dmg_reused.append((name, cand))
            return cls, "reused %s" % cand.rsplit("/", 1)[-1]

    dest = "%s/%s%s" % (DMG_FOLDER, name, NEW_SUFFIX)
    if not APPLY:
        # Dry run can't cache a real class, so dedupe by name here -- otherwise
        # one shared damage type is reported once per dino that uses it.
        if name not in [n for n, _, _ in _dmg_planned]:
            _dmg_planned.append((name, dest, parent_name_of(orig_path)))
        return None, "WOULD CREATE %s (from %s, parent %s)" % (
            dest.rsplit("/", 1)[-1], name, parent_name_of(orig_path))

    old_parent = parent_name_of(orig_path)
    if eal.duplicate_asset(orig_path, dest) is None:
        raise Exception("duplicate_asset failed: %s -> %s" % (orig_path, dest))

    bp = eal.load_asset(dest)
    bel = unreal.BlueprintEditorLibrary
    if not hasattr(bel, "reparent_blueprint"):
        raise Exception("BlueprintEditorLibrary.reparent_blueprint unavailable -- "
                        "%s was duplicated but NOT reparented" % dest)
    bel.reparent_blueprint(bp, dmg_class)
    if hasattr(bel, "compile_blueprint"):
        bel.compile_blueprint(bp)
    eal.save_asset(dest, only_if_is_dirty=False)

    cls = class_of(dest, "derived damage type")   # re-fetch: recompile swaps it
    _dmg_cache[name] = cls
    _dmg_created.append((name, dest, old_parent))
    return cls, "created %s (was parented to %s)" % (dest.rsplit("/", 1)[-1], old_parent)


def set_dmgtype(holder, prop_names, label, actions, problems):
    """Read current damage type, resolve its Artifact equivalent, write it back.
    Returns (original_name, applied) -- applied is False in dry run."""
    prop, cur = first_prop(holder, prop_names)
    if prop is None:
        problems.append("no %s property" % label)
        return None, False

    target, how = artifact_dmgtype(cur)
    if target is not None and cur == target:
        actions.append("%s already %s" % (label, name_of(cur)))
        return name_of(cur), False
    if target is None:            # dry run, asset doesn't exist yet
        actions.append("%s %s -> %s" % (label, name_of(cur), how))
        return name_of(cur), False

    try:
        if APPLY:
            holder.set_editor_property(prop, target)
        actions.append("%s %s -> %s [%s]" % (label, name_of(cur), target.get_name(), how))
        return name_of(cur), True
    except Exception as e:
        problems.append("%s set: %s" % (label, type(e).__name__))
        return name_of(cur), False


def untagged_dinos():
    """Every Pawn blueprint in DINO_FOLDER that does not carry TAG."""
    out = []
    for p in eal.list_assets(DINO_FOLDER, recursive=True, include_folder=False):
        obj = p.split(".")[0]
        asset = eal.load_asset(obj)
        if not isinstance(asset, unreal.Blueprint):
            continue
        gen = asset.generated_class()
        if gen is None:
            continue
        cdo = unreal.get_default_object(gen)
        if not isinstance(cdo, unreal.Pawn):
            continue
        try:
            tags = [str(t) for t in cdo.get_editor_property("tags")]
        except Exception:
            continue
        if TAG not in tags:
            out.append(obj)
    return out


def git_new_assets():
    """Untracked only ('??') -- files git has no record of at all. A staged add
    ('A ') is already in the index and is deliberately excluded. -uall so a
    brand-new folder lists its files instead of collapsing to one entry."""
    out = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=REPO,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise Exception("git failed: %s" % (out.stderr.strip() or out.returncode))

    paths = []
    for line in out.stdout.splitlines():
        if len(line) < 4:
            continue
        code, rel = line[:2], line[3:].strip()
        if code != "??":
            continue
        rel = rel.strip('"')
        if not rel.lower().endswith(".uasset"):
            continue
        parts = rel.replace("\\", "/").split("/")
        if parts[0] != "Content":
            continue
        paths.append(MOUNT + "/".join(parts[1:])[:-len(".uasset")])
    return paths


controller_class = class_of(CONTROLLER, "AI controller")
dmg_class = class_of(DMGTYPE, "damage type")
if not isinstance(unreal.get_default_object(controller_class), unreal.AIController):
    raise Exception("%s is not an AIController subclass" % CONTROLLER)

untagged = untagged_dinos() if SELECT in ("untagged", "either") else []
try:
    untracked = git_new_assets()
except Exception as e:
    unreal.log_warning("git lookup failed (%s) -- advisory note below is unavailable" % e)
    untracked = []

if SELECT == "untracked":
    candidates = untracked
elif SELECT == "untagged":
    candidates = untagged
else:
    candidates = untagged + [p for p in untracked if p not in untagged]

unreal.log("=" * 72)
unreal.log("SELECT = '%s' -- %d dino(s) selected" % (SELECT, len(candidates)))
for p in candidates:
    unreal.log("   %s" % p)

# A dino duplicated from an already-tagged Artifact dino inherits the tag, so
# the tag test can miss something genuinely new. Surface that rather than let
# it pass silently.
def _is_pawn_bp(path):
    asset = eal.load_asset(path)
    if not isinstance(asset, unreal.Blueprint) or asset.generated_class() is None:
        return False
    return isinstance(unreal.get_default_object(asset.generated_class()), unreal.Pawn)


# Only Pawns are meaningful here -- an untracked DmgType asset is not a dino
# the tag test "missed", it was never a candidate.
missed = [p for p in untracked if p not in candidates and _is_pawn_bp(p)]
if missed:
    unreal.log("")
    unreal.log_warning("NOT SELECTED but untracked in git (%d) -- already tagged,"
                       " so the tag test skips them:" % len(missed))
    for p in missed:
        unreal.log_warning("   %s" % p)
    unreal.log_warning("   Use SELECT = 'either' to include them.")
if EXTRA:
    unreal.log("plus %d from EXTRA" % len(EXTRA))
    for p in EXTRA:
        unreal.log("   %s" % p)
targets = list(candidates) + [p for p in EXTRA if p not in candidates]
unreal.log("=" * 72)

done, skipped, failed, originals = [], [], [], []

for path in targets:
    asset = eal.load_asset(path)
    if asset is None:
        failed.append((path, "load returned None"))
        continue
    if not isinstance(asset, unreal.Blueprint):
        skipped.append((path, "not a Blueprint"))
        continue
    gen = asset.generated_class()
    if gen is None:
        failed.append((path, "no generated class"))
        continue
    cdo = unreal.get_default_object(gen)
    if not isinstance(cdo, unreal.Pawn):
        skipped.append((path, "not a Pawn"))
        continue

    actions, problems = [], []

    # --- actor tag (idempotent: never stack a duplicate) ---
    try:
        tags = cdo.get_editor_property("tags")
        existing = [str(t) for t in tags]
        if TAG in existing:
            actions.append("tag already present")
        else:
            if APPLY:
                cdo.set_editor_property("tags", [unreal.Name(t) for t in existing]
                                        + [unreal.Name(TAG)])
            actions.append("tag += %s (had %s)" % (TAG, existing or "none"))
    except Exception as e:
        problems.append("tags: %s" % type(e).__name__)

    # --- AI controller ---
    prop, prev = first_prop(cdo, AICTRL_PROPS)
    if prop is None:
        problems.append("no AIControllerClass property")
    elif prev == controller_class:
        actions.append("AIController already set")
    else:
        try:
            if APPLY:
                cdo.set_editor_property(prop, controller_class)
            actions.append("AIController %s -> %s" % (name_of(prev), controller_class.get_name()))
        except Exception as e:
            problems.append("AIController set: %s" % type(e).__name__)

    # --- bCanBeTamed ---
    prop, prev = first_prop(cdo, TAMED_PROPS)
    if prop is None:
        problems.append("no bCanBeTamed property")
    elif bool(prev) is False:
        actions.append("bCanBeTamed already False")
    else:
        try:
            if APPLY:
                cdo.set_editor_property(prop, False)
            actions.append("bCanBeTamed True -> False")
        except Exception as e:
            problems.append("bCanBeTamed set: %s" % type(e).__name__)

    # --- TargetingTeamNameOverride ---
    prop, prev = first_prop(cdo, TEAMNM_PROPS)
    if prop is None:
        problems.append("no TargetingTeamNameOverride property")
    elif str(prev) == TEAM_NAME:
        actions.append("TargetingTeamNameOverride already %s" % TEAM_NAME)
    else:
        try:
            if APPLY:
                # FName vs FString varies; plain str usually converts, Name is the
                # fallback rather than letting a type mismatch look like success.
                try:
                    cdo.set_editor_property(prop, TEAM_NAME)
                except Exception:
                    cdo.set_editor_property(prop, unreal.Name(TEAM_NAME))
            actions.append("TargetingTeamNameOverride '%s' -> '%s'"
                           % (prev if str(prev) else "<empty>", TEAM_NAME))
        except Exception as e:
            problems.append("TargetingTeamNameOverride set: %s" % type(e).__name__)

    # --- top-level MeleeDamageType ---
    set_dmgtype(cdo, MELEE_PROPS, "MeleeDamageType", actions, problems)

    # --- AttackInfos[0].MeleeDamageType (originals logged for later analysis) ---
    prop, infos = first_prop(cdo, INFOS_PROPS)
    if prop is None:
        problems.append("no AttackInfos property")
    elif infos is None or len(infos) == 0:
        actions.append("AttackInfos empty -- nothing to change")
    else:
        try:
            # Record every index for analysis, even those left alone.
            for i, entry in enumerate(infos):
                sub, cur = first_prop(entry, MELEE_PROPS)
                originals.append((path, i, name_of(cur) if sub else "<unreadable>"))

            wanted = range(len(infos)) if ALL_INDICES else [0]
            dirty = False
            for i in wanted:
                entry = infos[i]
                orig, applied = set_dmgtype(entry, MELEE_PROPS,
                                            "AttackInfos[%d]" % i, actions, problems)
                if applied:
                    # Element access hands back a copy -- write it back, then
                    # assign the whole array, or the change is lost.
                    infos[i] = entry
                    dirty = True
            if dirty and APPLY:
                cdo.set_editor_property(prop, infos)
        except Exception as e:
            problems.append("AttackInfos: %s: %s" % (type(e).__name__, e))

    if APPLY and not problems:
        eal.save_asset(path, only_if_is_dirty=False)

    done.append((path, actions, problems))
    if problems:
        failed.append((path, "; ".join(problems)))

unreal.log("")
for path, actions, problems in done:
    unreal.log("%s" % path)
    for a in actions:
        unreal.log("      %s" % a)
    for p in problems:
        unreal.log_warning("      PROBLEM: %s" % p)

unreal.log("")
unreal.log("=" * 72)
unreal.log("ORIGINAL AttackInfos MeleeDamageType (index 0 is the one changed)")
for path, i, orig in originals:
    unreal.log("   [%d] %-46s %s" % (i, path.rsplit("/", 1)[-1], orig))

unreal.log("")
unreal.log("Grouped by original damage type -- shared types are candidates for a")
unreal.log("single standard replacement; unique ones may need a specialization.")
groups = {}
for path, i, orig in originals:
    if i == 0:
        groups.setdefault(orig, []).append(path.rsplit("/", 1)[-1])
for orig in sorted(groups):
    unreal.log("   %-44s %d: %s" % (orig, len(groups[orig]), ", ".join(groups[orig])))

unreal.log("")
unreal.log("=" * 72)
unreal.log("DERIVED DAMAGE TYPES")
if _dmg_reused:
    unreal.log("  REUSED existing (%d):" % len(_dmg_reused))
    for name, cand in _dmg_reused:
        unreal.log("     %-46s -> %s" % (name, cand.rsplit("/", 1)[-1]))
if _dmg_planned:
    unreal.log("  WOULD CREATE (%d) -- check the old parent on each:" % len(_dmg_planned))
    for name, dest, old_parent in _dmg_planned:
        unreal.log("     %-46s -> %s" % (name, dest.rsplit("/", 1)[-1]))
        unreal.log("        was parented to %s -- values inherited from that class" % old_parent)
        unreal.log("        are LOST on reparent; only this asset's own overrides survive.")
if _dmg_created:
    unreal.log("  CREATED (%d):" % len(_dmg_created))
    for name, dest, old_parent in _dmg_created:
        unreal.log("     %-46s -> %s   (was parented to %s)"
                   % (name, dest.rsplit("/", 1)[-1], old_parent))
if not (_dmg_reused or _dmg_planned or _dmg_created):
    unreal.log("  none")

unreal.log("")
if skipped:
    unreal.log("SKIPPED %d:" % len(skipped))
    for p, why in skipped:
        unreal.log("   %s  <-- %s" % (p, why))
if failed:
    unreal.log_warning("PROBLEMS %d:" % len(failed))
    for p, why in failed:
        unreal.log_warning("   %s  <-- %s" % (p, why))

unreal.log("=" * 72)
if APPLY:
    unreal.log("APPLIED to %d asset(s)." % len([d for d in done if not d[2]]))
else:
    unreal.log("DRY RUN -- nothing written. Set APPLY = True to write.")
unreal.log("=" * 72)
