import bpy, traceback, math, re, json
from mathutils import Vector, Matrix
from bpy.types import Context, Object, Operator
from bpy.props import IntProperty, StringProperty, BoolProperty, EnumProperty, FloatProperty

from ..utils.utils_object import is_armature, get_armature_meshes
from ..utils.utils_bone import bonename_direction_map
from ..utils.utils_contextmanagers import unhide_all_objects
from ..utils.utils_humanoidmapper2 import (
    hm2_validate_mapping,
    collect_chain,
    find_intermediate_bones_in_chain,
    create_twist_bones,
    add_twist_driver,
    ensure_hm2_shapes,
    ensure_bone_collection,
    detect_hm2_rig,
    compute_fpa_kept_bones,
    cull_mesh_to_bones,
    get_hm2_shape,
    HM2_CONTROLLER_PREFIXES,
)

def _try_mirror_bone_name(bone_name: str) -> str | None:
    infix_pairs = [
        ('.L.', '.R.'), ('.R.', '.L.'),
        ('_L_', '_R_'), ('_R_', '_L_'),
    ]
    for infix, replacement in infix_pairs:
        if infix in bone_name:
            return bone_name.replace(infix, replacement, 1)

    for suffix, replacement in bonename_direction_map.items():
        if bone_name.endswith(suffix):
            return bone_name[: -len(suffix)] + replacement
        if bone_name.startswith(suffix):
            return replacement + bone_name[len(suffix):]

    return None


class HM2_OT_AddFinger(Operator):
    bl_idname = "kitsunetools.hm2_add_finger"
    bl_label = "Add Finger"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return is_armature(context.active_object)

    def execute(self, context: Context) -> set:
        context.active_object.kitsunetools.hm2.hm2_fingers.add()
        return {'FINISHED'}


class HM2_OT_RemoveFinger(Operator):
    bl_idname = "kitsunetools.hm2_remove_finger"
    bl_label = "Remove Finger"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    index: IntProperty()

    @classmethod
    def poll(cls, context: Context) -> bool:
        return is_armature(context.active_object)

    def execute(self, context: Context) -> set:
        hm2 = context.active_object.kitsunetools.hm2
        if 0 <= self.index < len(hm2.hm2_fingers):
            hm2.hm2_fingers.remove(self.index)
        return {'FINISHED'}


class HM2_OT_MirrorFingers(Operator):
    bl_idname = "kitsunetools.hm2_mirror_fingers"
    bl_label = "Mirror Fingers L ↔ R"
    bl_description = "Duplicate finger entries from one side to the other"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return is_armature(context.active_object)

    def execute(self, context: Context) -> set:
        hm2 = context.active_object.kitsunetools.hm2
        fingers = hm2.hm2_fingers
        existing = [(f.finger_type, f.side) for f in fingers]
        added = 0

        bones = context.active_object.data.bones

        for f in list(fingers):
            opposite = 'R' if f.side == 'L' else 'L'
            if (f.finger_type, opposite) not in existing:
                new_f = fingers.add()
                new_f.finger_type = f.finger_type
                new_f.side = opposite
                new_f.joint_count = f.joint_count
                new_f.generate_ik = f.generate_ik

                if f.source_bone:
                    mirrored = _try_mirror_bone_name(f.source_bone)
                    if mirrored and mirrored in bones:
                        new_f.source_bone = mirrored

                existing.append((f.finger_type, opposite))
                added += 1

        self.report({'INFO'}, f"Mirrored {added} finger(s)")
        return {'FINISHED'}


_MIRROR_PAIRS_BY_SCOPE = {
    'EYES': [
        ('hm2_map_eye_l', 'hm2_map_eye_r'),
    ],
    'ARMS': [
        ('hm2_map_scapula_l',  'hm2_map_scapula_r'),
        ('hm2_map_shoulder_l', 'hm2_map_shoulder_r'),
        ('hm2_map_elbow_l',    'hm2_map_elbow_r'),
        ('hm2_map_hand_l',     'hm2_map_hand_r'),
    ],
    'LEGS': [
        ('hm2_map_hip_l',   'hm2_map_hip_r'),
        ('hm2_map_knee_l',  'hm2_map_knee_r'),
        ('hm2_map_ankle_l', 'hm2_map_ankle_r'),
        ('hm2_map_toe_l',   'hm2_map_toe_r'),
    ],
}


class HM2_OT_MirrorBodyMapping(Operator):
    bl_idname = "kitsunetools.hm2_mirror_body"
    bl_label = "Mirror Body Mapping"
    bl_description = "Fill empty L or R bone slots by mirroring the filled opposite side"
    bl_options = {'REGISTER', 'UNDO'}

    scope: EnumProperty(
        name="Scope",
        items=[
            ('EYES', 'Eyes', ''),
            ('ARMS', 'Arms', ''),
            ('LEGS', 'Legs', ''),
        ],
        default='ARMS',
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context: Context) -> bool:
        return is_armature(context.active_object)

    def execute(self, context: Context) -> set:
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2
        bones = arm.data.bones
        mirrored = 0
        missing = []

        pairs = _MIRROR_PAIRS_BY_SCOPE.get(self.scope, [])

        for prop_l, prop_r in pairs:
            val_l = getattr(hm2, prop_l, "").strip()
            val_r = getattr(hm2, prop_r, "").strip()

            if val_l and val_r:
                continue

            if val_l and not val_r:
                mirrored_name = _try_mirror_bone_name(val_l)
                if mirrored_name and mirrored_name in bones:
                    setattr(hm2, prop_r, mirrored_name)
                    mirrored += 1
                else:
                    missing.append(f"{prop_r}: could not mirror '{val_l}' → '{mirrored_name or '?'}'")
            elif val_r and not val_l:
                mirrored_name = _try_mirror_bone_name(val_r)
                if mirrored_name and mirrored_name in bones:
                    setattr(hm2, prop_l, mirrored_name)
                    mirrored += 1
                else:
                    missing.append(f"{prop_l}: could not mirror '{val_r}' → '{mirrored_name or '?'}'")

        for msg in missing:
            self.report({'WARNING'}, msg)

        if mirrored:
            self.report({'INFO'}, f"Mirrored {mirrored} bone mapping(s)")
        elif not missing:
            self.report({'INFO'}, "Nothing to mirror - all pairs already filled or empty")

        return {'FINISHED'}


class HM2_OT_CopyMappingToSelected(Operator):
    bl_idname = "kitsunetools.hm2_copy_mapping"
    bl_label = "Copy HM2 Mapping"
    bl_description = "Copy HM2 mapping from active armature to other selected armatures"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return (
            context.mode == 'OBJECT'
            and is_armature(context.active_object)
            and any(o != context.active_object and is_armature(o) for o in context.selected_objects)
        )

    def execute(self, context: Context) -> set:
        src = context.active_object.kitsunetools.hm2
        targets = [o for o in context.selected_objects if o != context.active_object and is_armature(o)]

        str_props = [a for a in dir(src) if a.startswith('hm2_') and isinstance(getattr(src, a), str)]
        int_props = [a for a in dir(src) if a.startswith('hm2_') and isinstance(getattr(src, a), int)]
        bool_props = [a for a in dir(src) if a.startswith('hm2_') and isinstance(getattr(src, a), bool)]

        for ob in targets:
            dst = ob.kitsunetools.hm2
            for prop in str_props + int_props + bool_props:
                try:
                    setattr(dst, prop, getattr(src, prop))
                except Exception:
                    pass
            dst.hm2_fingers.clear()
            for f in src.hm2_fingers:
                new_f = dst.hm2_fingers.add()
                new_f.source_bone = f.source_bone
                new_f.finger_type = f.finger_type
                new_f.side = f.side
                new_f.joint_count = f.joint_count
                new_f.generate_ik = f.generate_ik

        self.report({'INFO'}, f"Copied HM2 mapping to {len(targets)} object(s)")
        return {'FINISHED'}


class HM2_OT_JsonFormatHelp(Operator):
    bl_idname = "kitsunetools.hm2_json_format_help"
    bl_label = "Export Config JSON Format"
    bl_options = {'REGISTER', 'INTERNAL'}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=420)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Export Config JSON Format", icon='INFO')
        layout.separator()

        layout.label(text='Top level: a JSON object keyed by bone type.')
        layout.separator()

        layout.label(text='Single bones  (no direction substitution):')
        layout.label(text='  Keys: Root, Chest, Neck, Head', icon='DOT')
        layout.label(text='  "Root": { "name": "Hips", "exportrot": "0 0 0", "exportloc": "0 0 0" }', icon='BLANK1')
        layout.separator()

        layout.label(text='Bilateral bones  ({dir} replaced by dir_l / dir_r):')
        layout.label(text='  Keys: Scapula, Shoulder, Elbow, Hand, Hip, Knee, Ankle, Toe, Eye', icon='DOT')
        layout.label(text='  "Shoulder": { "name": "{dir}UpperArm", "dir_l": "Left", "dir_r": "Right",', icon='BLANK1')
        layout.label(text='               "exportrot": "0 0 0", "exportloc": "0 0 0" }', icon='BLANK1')
        layout.separator()

        layout.label(text='Spine  ({*} replaced by count, optional starting_count):')
        layout.label(text='  Key: Spine', icon='DOT')
        layout.label(text='  "Spine": { "name": "Spine{*}", "starting_count": 1,', icon='BLANK1')
        layout.label(text='             "exportrot": "0 0 0", "exportloc": "0 0 0" }', icon='BLANK1')
        layout.separator()

        layout.label(text='Fingers  (bilateral + {*} for joint index):')
        layout.label(text='  Keys: Thumb, Index, Middle, Ring, Pinky', icon='DOT')
        layout.label(text='  Thumb starts at joint 0, others at joint 1.', icon='BLANK1')
        layout.label(text='  "Index": { "name": "{dir}IndexProximal{*}", "dir_l": "Left", "dir_r": "Right",', icon='BLANK1')
        layout.label(text='             "starting_count": 1, "exportrot": "0 0 0" }', icon='BLANK1')
        layout.separator()

        layout.label(text='exportrot / exportloc: shared "X Y Z" floats used when no per-side keys are set.')
        layout.label(text='exportrot_l / exportrot_r: per-side rotation overrides; if only one side is given,', icon='BLANK1')
        layout.label(text='  it is used for both sides.  exportloc_l / exportloc_r work the same way.', icon='BLANK1')
        layout.label(text='Twist bones inherit the same offsets as their parent joint automatically.')

    def execute(self, context):
        return {'FINISHED'}


class HM2_OT_ValidateMapping(Operator):
    bl_idname = "kitsunetools.hm2_validate"
    bl_label = "Validate HM2 Mapping"
    bl_description = "Check bone assignments for errors without modifying the armature"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return is_armature(context.active_object)

    def execute(self, context: Context) -> set:
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2
        errors = hm2_validate_mapping(arm, hm2)
        if errors:
            for e in errors:
                self.report({'ERROR'}, e)
            self.report({'WARNING'}, f"{len(errors)} validation error(s) found")
            return {'CANCELLED'}
        self.report({'INFO'}, "Validation passed - no issues found")
        return {'FINISHED'}


class HM2_OT_Process(Operator):
    bl_idname = "kitsunetools.hm2_process"
    bl_label = "Run HM2 Setup"
    bl_description = "Rename bones, generate twist bones, build IK rig, assign custom shapes, and organize collections"
    bl_options = {'REGISTER', 'UNDO'}

    reapply_config: BoolProperty(
        name="Re-apply JSON config",
        description="Apply the JSON export config file again during this update",
        default=True,
    )

    @classmethod
    def poll(cls, context: Context) -> bool:
        if not (context.mode == 'OBJECT' and is_armature(context.active_object)):
            return False
        return not context.active_object.kitsunetools.hm2.hm2_is_puppet

    def invoke(self, context: Context, event) -> set:
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2
        if self._is_hm2_applied(arm) and hm2.hm2_json_filepath.strip():
            return context.window_manager.invoke_props_dialog(self, title="Re-apply HM2 Setup")
        return self.execute(context)

    def draw(self, context: Context) -> None:
        self.layout.prop(self, "reapply_config")

    def execute(self, context: Context) -> set:
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2

        errors = hm2_validate_mapping(arm, hm2)
        if errors:
            for e in errors:
                self.report({'ERROR'}, e)
            return {'CANCELLED'}

        bpy.ops.ed.undo_push(message="Before HM2 Process")
        try:
            was_applied = self._is_hm2_applied(arm)
            # Snapshot VS config and twist-child parenting before cleanup wipes them.
            vs_snapshot = self._snapshot_vs_config(arm) if was_applied else {}
            twist_children_snapshot = self._snapshot_twist_children(arm) if was_applied else {}
            if was_applied:
                self._cleanup_for_reapply(arm)
            self._run(context, arm, hm2)
            # Always restore the VS snapshot so manually-edited fields on bones not
            # covered by the JSON are preserved. When reapply_config=True,
            # _apply_export_config already ran inside _run and its values take
            # precedence because _restore_vs_config only writes fields present in
            # the snapshot — any bone the JSON touched was freshly written first,
            # then the snapshot overwrites with the pre-rebuild value. To let the
            # JSON win we restore BEFORE the config reapply, but since _run calls
            # _apply_export_config at the end, we instead restore here and let
            # the JSON re-run overwrite its own bones.
            # Correct order: restore snapshot (catches all bones), then re-apply
            # JSON (overwrites only its bones). Both only run when there is data.
            if vs_snapshot:
                self._restore_vs_config(arm, vs_snapshot)
            if hm2.hm2_json_filepath.strip() and self.reapply_config:
                self._apply_export_config(arm, hm2)
            if twist_children_snapshot:
                self._restore_twist_children(arm, twist_children_snapshot)
        except Exception as e:
            traceback.print_exc()
            self.report({'ERROR'}, f"HM2 failed and was reverted: {e}")
            # Roll the half-built rig back to the "Before HM2 Process" snapshot.
            # The undo is deferred to after this operator returns: driving undo
            # while still inside execute() restores a memfile underneath our live
            # bpy references (arm / edit_bones / hm2), which only partially
            # reverts and can corrupt the depsgraph.
            self._schedule_revert()
            return {'CANCELLED'}

        # Auto-process every puppet in the master's list.
        puppet_warnings: list[str] = []
        for entry in hm2.hm2_puppets:
            puppet_obj = entry.armature
            if not puppet_obj or not is_armature(puppet_obj):
                continue
            self._twist_bone_names = {}
            self._rename_map = {}
            try:
                for w in self._run_puppet(context, arm, puppet_obj, entry.mode):
                    puppet_warnings.append(f"[{puppet_obj.name}] {w}")
            except Exception as e:
                traceback.print_exc()
                puppet_warnings.append(f"[{puppet_obj.name}] Failed: {e}")
        bpy.context.view_layer.objects.active = arm
        for w in puppet_warnings:
            self.report({'WARNING'}, w)

        self.report({'INFO'}, "HM2 processing complete")
        return {'FINISHED'}

    @staticmethod
    def _schedule_revert() -> None:
        """Run a single undo on the next timer tick, once this operator has
        fully returned and the context is stable again."""
        def _revert():
            try:
                bpy.ops.ed.undo()
            except Exception:
                traceback.print_exc()
            return None  # one-shot
        bpy.app.timers.register(_revert, first_interval=0.0)

    @staticmethod
    def _is_hm2_applied(arm: Object) -> bool:
        bones = arm.data.bones
        return any(m in bones for m in ('CTRL_Ground', 'IK_Hand_L', 'IK_Hand_R', 'IK_Ankle_L', 'IK_Ankle_R'))

    _VS_FIELDS = (
        'export_name',
        'ignore_rotation_offset',
        'export_rotation_offset_x', 'export_rotation_offset_y', 'export_rotation_offset_z',
        'ignore_location_offset',
        'export_location_offset_x', 'export_location_offset_y', 'export_location_offset_z',
    )

    @classmethod
    def _snapshot_vs_config(cls, arm: Object) -> dict:
        """Capture every bone's `vs` export config, keyed by bone name."""
        snapshot = {}
        for bone in arm.data.bones:
            vs = getattr(bone, 'vs', None)
            if vs is None:
                continue
            data = {f: getattr(vs, f) for f in cls._VS_FIELDS if hasattr(vs, f)}
            if data:
                snapshot[bone.name] = data
        return snapshot

    @classmethod
    def _restore_vs_config(cls, arm: Object, snapshot: dict) -> None:
        """Re-apply a snapshot from `_snapshot_vs_config` for bones that still exist."""
        bones = arm.data.bones
        for name, data in snapshot.items():
            bone = bones.get(name)
            if bone is None:
                continue
            vs = getattr(bone, 'vs', None)
            if vs is None:
                continue
            for field, value in data.items():
                try:
                    setattr(vs, field, value)
                except Exception:
                    pass

    @classmethod
    def _snapshot_twist_children(cls, arm: Object) -> dict:
        """Record which user bones are parented to a twist bone, and which
        positional slot (index) within that joint's twist list they reference.

        Stored as {child_bone_name: (joint_name, twist_index)} so that after
        twist bones are deleted and recreated the child can be re-parented to
        the new bone at the same slot.

        A twist bone is identified by the cleanup regex used in
        _cleanup_for_reapply: name matches r'\\.\\d{3}$' and parent is in
        _TWIST_PARENTS. We build the joint→[twist_names_in_order] map from
        the armature's current state to resolve indices."""
        twist_children: dict[str, tuple[str, int]] = {}

        # Build joint → ordered twist bone list from current bones.
        # Twist bones sort by their suffix number to get stable ordering.
        joint_twists: dict[str, list[str]] = {}
        for bone in arm.data.bones:
            if (re.search(r'\.\d{3}$', bone.name)
                    and bone.parent
                    and bone.parent.name in cls._TWIST_PARENTS):
                joint = bone.parent.name
                joint_twists.setdefault(joint, []).append(bone.name)
        for names in joint_twists.values():
            names.sort()  # .001 < .002 < .003 — stable positional index

        # Build reverse map: twist_bone_name → (joint, index)
        twist_to_slot: dict[str, tuple[str, int]] = {}
        for joint, names in joint_twists.items():
            for idx, name in enumerate(names):
                twist_to_slot[name] = (joint, idx)

        # Find bones whose parent is a twist bone (not themselves twist bones).
        twist_set = set(twist_to_slot)
        for bone in arm.data.bones:
            if bone.name in twist_set:
                continue  # skip the twist bones themselves
            if bone.parent and bone.parent.name in twist_set:
                joint, idx = twist_to_slot[bone.parent.name]
                twist_children[bone.name] = (joint, idx)

        return twist_children

    def _restore_twist_children(self, arm: Object, snapshot: dict) -> None:
        """Re-parent bones that were previously children of twist bones, using
        the joint name and positional index to find the new twist bone name."""
        if not snapshot:
            return

        # Build joint → new twist bone names from self._twist_bone_names
        # (populated by _create_all_twist_bones in _run).
        joint_twists = getattr(self, '_twist_bone_names', {})
        if not joint_twists:
            return

        # child_name → new parent name
        reparent: dict[str, str] = {}
        for child_name, (joint, idx) in snapshot.items():
            names = joint_twists.get(joint, [])
            if idx < len(names):
                reparent[child_name] = names[idx]
            elif names:
                # Twist count decreased — use the last available bone.
                reparent[child_name] = names[-1]

        if not reparent:
            return

        bpy.ops.object.mode_set(mode='EDIT')
        eb = arm.data.edit_bones
        for child_name, new_parent_name in reparent.items():
            child_eb = eb.get(child_name)
            new_parent_eb = eb.get(new_parent_name)
            if child_eb and new_parent_eb:
                child_eb.parent = new_parent_eb
        bpy.ops.object.mode_set(mode='OBJECT')

    _ADDED_PREFIXES = ('CTRL_', 'IK_', 'MCH_', 'VIS_', 'FK_')
    _TWIST_PARENTS = frozenset({
        'L_Shoulder', 'R_Shoulder', 'L_Elbow', 'R_Elbow',
        'L_Hip', 'R_Hip', 'L_Knee', 'R_Knee',
    })

    # Fixed HM2-managed deform bones (excludes spine - computed dynamically).
    _HM2_CORE_BONES = frozenset({
        'M_Root', 'M_Chest', 'M_Neck', 'M_Head',
        'L_Scapula', 'R_Scapula',
        'L_Shoulder', 'R_Shoulder', 'L_Elbow', 'R_Elbow', 'L_Hand', 'R_Hand',
        'L_Hip', 'R_Hip', 'L_Knee', 'R_Knee', 'L_Ankle', 'R_Ankle',
        'L_Toe', 'R_Toe',
        'L_Eye', 'R_Eye',
    })

    @classmethod
    def _hm2_managed_bone_names(cls, arm_obj: 'Object', hm2) -> frozenset:
        """Return the set of HM2-managed deform bone names: core, spine, twist, fingers.
        Used to limit .vs sync to bones HM2 controls, skipping hair/cloth/misc bones."""
        names: set[str] = set(cls._HM2_CORE_BONES)

        # Spine bones
        count = getattr(hm2, 'hm2_spine_count', 1)
        if count == 1:
            names.add('M_Spine')
        else:
            for i in range(count):
                names.add(f'M_Spine{i + 1}')

        # Twist bones (identified by parent being in _TWIST_PARENTS + .NNN suffix)
        for bone in arm_obj.data.bones:
            if (re.search(r'\.\d{3}$', bone.name)
                    and bone.parent
                    and bone.parent.name in cls._TWIST_PARENTS):
                names.add(bone.name)

        # Finger bones
        _ftm = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                'RING': 'Ring', 'PINKY': 'Pinky'}
        for finger in hm2.hm2_fingers:
            base      = _ftm.get(finger.finger_type, finger.finger_type)
            side      = finger.side
            start_idx = 0 if finger.finger_type == 'THUMB' else 1
            for i in range(finger.joint_count):
                names.add(f'{side}_{base}Finger{start_idx + i}')

        return frozenset(names)

    @classmethod
    def _make_proc(cls) -> object:
        """Return a plain-Python proxy with all HM2 instance methods bound to it.

        Blender's bpy_struct.__new__ rejects direct instantiation of registered
        operator classes (TypeError: expected a single argument), so other operators
        that need to call HM2 processing methods use this factory instead of
        HM2_OT_Process().
        """
        import types as _types, inspect as _inspect
        proc = _types.SimpleNamespace(
            _twist_bone_names={},
            _rename_map={},
            _computed_pole_angles={},
        )
        for name, val in cls.__dict__.items():
            if name.startswith('__'):
                continue
            if isinstance(val, staticmethod):
                setattr(proc, name, val.__func__)
            elif isinstance(val, classmethod):
                setattr(proc, name, val.__func__.__get__(cls))
            elif _inspect.isfunction(val):
                setattr(proc, name, val.__get__(proc))
            else:
                try:
                    setattr(proc, name, val)
                except Exception:
                    pass
        return proc

    def _cleanup_for_reapply(self, arm: Object) -> None:
        bpy.ops.object.mode_set(mode='POSE')
        bpy.ops.pose.select_all(action='SELECT')
        bpy.ops.pose.rot_clear()
        bpy.ops.pose.loc_clear()
        bpy.ops.pose.scale_clear()

        bpy.ops.object.mode_set(mode='EDIT')
        eb = arm.data.edit_bones
        to_delete = [
            bone for bone in eb
            if any(bone.name.startswith(p) for p in self._ADDED_PREFIXES)
            or (re.search(r'\.\d{3}$', bone.name)
                and bone.parent and bone.parent.name in self._TWIST_PARENTS)
        ]
        for bone in to_delete:
            eb.remove(bone)

        bpy.ops.object.mode_set(mode='POSE')
        for pb in arm.pose.bones:
            for c in list(pb.constraints):
                pb.constraints.remove(c)
            pb.custom_shape = None
            pb.color.palette = 'DEFAULT'

        if arm.animation_data:
            to_remove = [fc for fc in arm.animation_data.drivers
                         if fc.data_path.startswith('pose.bones[')]
            for fc in to_remove:
                arm.animation_data.drivers.remove(fc)

        bpy.ops.object.mode_set(mode='OBJECT')

    def _run(self, context: Context, arm: Object, hm2) -> None:
        self._twist_bone_names = {}
        self._computed_pole_angles = {}

        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        arm.location = (0.0, 0.0, 0.0)
        arm.rotation_euler = (0.0, 0.0, 0.0)
        arm.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        arm.show_in_front = True

        bpy.ops.object.mode_set(mode='EDIT')
        arm.data.use_mirror_x = False
        for eb in arm.data.edit_bones:
            eb.use_connect = False

        self._rename_core_bones(arm, hm2)
        if hm2.hm2_first_person_mode:
            self._ensure_first_person_root(arm, hm2)
        self._setup_spine(arm, hm2)
        self._rename_fingers(arm, hm2)
        self._connect_chains(arm, hm2)
        self._remove_intermediates(arm, hm2)

        bpy.ops.object.mode_set(mode='EDIT')
        self._align_bone_rolls(arm, hm2)
        self._realign_finger_bones(arm, hm2)
        self._create_all_twist_bones(arm, hm2)
        self._create_ik_bones(arm, hm2)

        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')

        self._unlock_all_bones(arm)
        if hm2.hm2_generate_ik:
            self._setup_ik_constraints(arm, hm2)
            self._setup_eye_constraints(arm)
        self._setup_fk_controllers(arm)
        self._setup_twist_drivers(arm, hm2)

        if hm2.hm2_generate_shapes:
            shapes = ensure_hm2_shapes(context)
            self._assign_custom_shapes(arm, hm2, shapes)
        self._assign_bone_colors(arm, hm2)

        bpy.ops.object.mode_set(mode='OBJECT')
        self._organize_collections(arm, hm2)


        # NOTE: export config is no longer applied here: execute() handles it
        # after restoring the VS snapshot, so the JSON always wins over the
        # snapshot for bones it covers, without losing manually-edited fields
        # on bones the JSON doesn't mention.
        #if hm2.hm2_json_filepath.strip() and self.reapply_config:
        #    self._apply_export_config(arm, hm2)

    def _rename_core_bones(self, arm: Object, hm2) -> None:
        eb = arm.data.edit_bones
        prefix = "__HM2__"

        # Build source->target map
        rename_pairs = []
        for src_attr, target_name in [
            ('hm2_map_root',       'M_Root'),
            ('hm2_map_chest',      'M_Chest'),
            ('hm2_map_neck',       'M_Neck'),
            ('hm2_map_head',       'M_Head'),
            ('hm2_map_eye_l',      'L_Eye'),
            ('hm2_map_eye_r',      'R_Eye'),
            ('hm2_map_scapula_l',  'L_Scapula'),
            ('hm2_map_scapula_r',  'R_Scapula'),
            ('hm2_map_shoulder_l', 'L_Shoulder'),
            ('hm2_map_shoulder_r', 'R_Shoulder'),
            ('hm2_map_elbow_l',    'L_Elbow'),
            ('hm2_map_elbow_r',    'R_Elbow'),
            ('hm2_map_hand_l',     'L_Hand'),
            ('hm2_map_hand_r',     'R_Hand'),
            ('hm2_map_hip_l',      'L_Hip'),
            ('hm2_map_hip_r',      'R_Hip'),
            ('hm2_map_knee_l',     'L_Knee'),
            ('hm2_map_knee_r',     'R_Knee'),
            ('hm2_map_ankle_l',    'L_Ankle'),
            ('hm2_map_ankle_r',    'R_Ankle'),
            ('hm2_map_toe_l',      'L_Toe'),
            ('hm2_map_toe_r',      'R_Toe'),
        ]:
            src_name = getattr(hm2, src_attr, "")
            if src_name and src_name in eb:
                rename_pairs.append((src_name, target_name))

        self._rename_map = {src: tgt for src, tgt in rename_pairs}

        # Pass 1: prefix all source bones to avoid collisions
        for src_name, _ in rename_pairs:
            if src_name in eb:
                eb[src_name].name = f"{prefix}{src_name}"

        # Pass 2: rename to final names and update hm2 props
        for src_name, target_name in rename_pairs:
            temp = f"{prefix}{src_name}"
            if temp in eb:
                eb[temp].name = target_name

        # Persist original source names so puppet auto-mapping can find them later.
        # Stored as {hm2_name: original_source_name} on the armature data.
        try:
            _sm = json.loads(arm.data.get("_hm2_src_map", "{}") or "{}")
        except Exception:
            _sm = {}
        for src_name, target_name in rename_pairs:
            _sm[target_name] = src_name
        arm.data["_hm2_src_map"] = json.dumps(_sm)

        # Update hm2 props to reflect new names
        for src_attr, target_name in [
            ('hm2_map_root', 'M_Root'), ('hm2_map_chest', 'M_Chest'),
            ('hm2_map_neck', 'M_Neck'), ('hm2_map_head', 'M_Head'),
            ('hm2_map_eye_l', 'L_Eye'), ('hm2_map_eye_r', 'R_Eye'),
            ('hm2_map_scapula_l', 'L_Scapula'), ('hm2_map_scapula_r', 'R_Scapula'),
            ('hm2_map_shoulder_l', 'L_Shoulder'), ('hm2_map_shoulder_r', 'R_Shoulder'),
            ('hm2_map_elbow_l', 'L_Elbow'), ('hm2_map_elbow_r', 'R_Elbow'),
            ('hm2_map_hand_l', 'L_Hand'), ('hm2_map_hand_r', 'R_Hand'),
            ('hm2_map_hip_l', 'L_Hip'), ('hm2_map_hip_r', 'R_Hip'),
            ('hm2_map_knee_l', 'L_Knee'), ('hm2_map_knee_r', 'R_Knee'),
            ('hm2_map_ankle_l', 'L_Ankle'), ('hm2_map_ankle_r', 'R_Ankle'),
            ('hm2_map_toe_l', 'L_Toe'), ('hm2_map_toe_r', 'R_Toe'),
        ]:
            if getattr(hm2, src_attr, ""):
                setattr(hm2, src_attr, target_name)

    def _setup_spine(self, arm: Object, hm2) -> None:
        eb = arm.data.edit_bones
        if 'M_Root' not in eb or 'M_Chest' not in eb:
            return

        chain = collect_chain(arm.data.edit_bones, 'M_Root', 'M_Chest')
        if len(chain) < 2:
            return

        intermediates = chain[1:-1]
        count = hm2.hm2_spine_count

        def spine_name(i: int) -> str:
            return 'M_Spine' if count == 1 else f'M_Spine{i + 1}'

        if len(intermediates) == count:
            for i, bone in enumerate(intermediates):
                eb[bone.name].name = spine_name(i)
        elif len(intermediates) > count:
            for i in range(count):
                eb[intermediates[i].name].name = spine_name(i)
            for bone in intermediates[count:]:
                edit_bone = eb.get(bone.name)
                if edit_bone:
                    edit_bone.parent = eb.get(spine_name(count - 1)) or eb['M_Root']
            bpy.ops.object.mode_set(mode='OBJECT')
            default_coll = ensure_bone_collection(arm, 'Default')
            inter_coll = ensure_bone_collection(arm, 'Intermediate', default_coll)
            inter_coll.is_visible = False
            for bone in intermediates[count:]:
                b = arm.data.bones.get(bone.name)
                if b:
                    for c in list(b.collections):
                        c.unassign(b)
                    inter_coll.assign(b)
            bpy.ops.object.mode_set(mode='EDIT')
        else:
            for i, bone in enumerate(intermediates):
                eb[bone.name].name = spine_name(i)
            chest_eb = eb.get('M_Chest')
            last_parent = eb.get(spine_name(len(intermediates) - 1)) if intermediates else eb.get('M_Root')
            for i in range(len(intermediates), count):
                new_name = spine_name(i)
                new_bone = eb.new(new_name)
                prev = eb.get(spine_name(i - 1)) if i > 0 else eb.get('M_Root')
                n_remaining = count - i
                new_bone.head = prev.head.lerp(chest_eb.head, 1.0 / (n_remaining + 1)) if prev else Vector((0, 0, 0))
                next_head = chest_eb.head if i == count - 1 else prev.head.lerp(chest_eb.head, 2.0 / (n_remaining + 1))
                new_bone.tail = next_head
                new_bone.parent = prev
                new_bone.use_connect = False
                last_parent = new_bone
            if chest_eb and last_parent:
                chest_eb.parent = last_parent

    def _ensure_first_person_root(self, arm: Object, hm2) -> None:
        """First person mode: guarantee an 'M_Root' control and parent the arms to it.

        Must run in EDIT mode, after _rename_core_bones. If no Root was mapped, a new
        M_Root is created at the average of the shoulder (or scapula, when scapula bones
        are mapped) head positions. The top-of-arm bones (scapula if present, else
        shoulder) are then parented to M_Root so the whole arm assembly follows it -
        there is no spine/chest to hang them from in an arms-only rig."""
        eb = arm.data.edit_bones

        # Top-of-arm bone per side: scapula when present, else shoulder.
        top_names = []
        for side in ('L', 'R'):
            top = eb.get(f'{side}_Scapula') or eb.get(f'{side}_Shoulder')
            if top:
                top_names.append(top.name)
        if not top_names:
            return

        # Averaging anchors: scapula heads if any scapula is mapped, else shoulder heads.
        use_scap = any(eb.get(f'{s}_Scapula') for s in ('L', 'R'))
        anchor_names = []
        for side in ('L', 'R'):
            b = eb.get(f'{side}_Scapula') if use_scap else eb.get(f'{side}_Shoulder')
            if b:
                anchor_names.append(b.name)
        if not anchor_names:
            anchor_names = top_names

        heads = [eb[n].head.copy() for n in anchor_names]
        center = sum(heads, Vector()) / len(heads)

        root_eb = eb.get('M_Root')
        if root_eb is None:
            # Length from L/R separation (or the anchor bone length for a single arm).
            span = (heads[0] - heads[-1]).length if len(heads) >= 2 else eb[anchor_names[0]].length
            length = max(span * 0.5, 1e-3)
            root_eb = eb.new('M_Root')
            root_eb.head = center
            root_eb.tail = center + Vector((0.0, 0.0, length))
            root_eb.use_connect = False

        # Parent the arm roots to M_Root so they move with the first-person root.
        for n in top_names:
            b = eb.get(n)
            if b and b is not root_eb:
                b.use_connect = False
                b.parent = root_eb

    def _rename_fingers(self, arm: Object, hm2) -> None:
        eb = arm.data.edit_bones
        type_name_map = {
            'THUMB': 'Thumb',
            'INDEX': 'Index',
            'MIDDLE': 'Middle',
            'RING': 'Ring',
            'PINKY': 'Pinky',
        }

        for item in hm2.hm2_fingers:
            if not item.source_bone or item.source_bone not in eb:
                continue

            bone = eb[item.source_bone]
            chain = []
            current = bone
            while current:
                chain.append(current)
                current = current.children[0] if len(current.children) == 1 else None

            base = type_name_map.get(item.finger_type, item.finger_type)
            side = item.side
            start_idx = 0 if item.finger_type == 'THUMB' else 1
            cap = min(item.joint_count, len(chain))

            for i in range(cap):
                chain[i].name = f"{side}_{base}Finger{i + start_idx}"

            # Keep the finger entry pointing at the renamed first joint so a
            # re-apply still resolves source_bone (mirrors _rename_core_bones,
            # which writes the new names back into the body mapping props).
            if cap > 0:
                new_first = f"{side}_{base}Finger{start_idx}"
                # Persist original source → HM2 name for puppet auto-mapping.
                try:
                    _sm = json.loads(arm.data.get("_hm2_src_map", "{}") or "{}")
                except Exception:
                    _sm = {}
                _sm[new_first] = item.source_bone
                arm.data["_hm2_src_map"] = json.dumps(_sm)
                item.source_bone = new_first

            hand_name = f"{side}_Hand"
            hand_eb = eb.get(hand_name)
            if hand_eb and chain[0].parent != hand_eb:
                chain[0].parent = hand_eb
                chain[0].use_connect = False

    def _connect_chains(self, arm: Object, hm2) -> None:
        eb = arm.data.edit_bones

        pairs = []

        # Limbs - both sides
        for side in ('L', 'R'):
            pairs += [
                (f'{side}_Scapula',  f'{side}_Shoulder'),
                (f'{side}_Shoulder', f'{side}_Elbow'),
                (f'{side}_Elbow',    f'{side}_Hand'),
                (f'{side}_Hip',      f'{side}_Knee'),
                (f'{side}_Knee',     f'{side}_Ankle'),
                (f'{side}_Ankle',    f'{side}_Toe'),
            ]

        # Spine chain - built from count
        count = hm2.hm2_spine_count
        if count == 1:
            pairs += [('M_Root', 'M_Spine'), ('M_Spine', 'M_Chest')]
        else:
            pairs.append(('M_Root', 'M_Spine1'))
            for i in range(1, count):
                pairs.append((f'M_Spine{i}', f'M_Spine{i + 1}'))
            pairs.append((f'M_Spine{count}', 'M_Chest'))

        pairs += [('M_Chest', 'M_Neck'), ('M_Neck', 'M_Head')]

        for parent_name, child_name in pairs:
            parent = eb.get(parent_name)
            child = eb.get(child_name)
            if parent and child:
                # Only move tail if the child is actually further away than a minimum length,
                # to avoid zero-length bones if two mapped bones share the same position.
                new_tail = child.head.copy()
                if (new_tail - parent.head).length > 1e-4:
                    parent.tail = new_tail

    def _remove_intermediates(self, arm: Object, hm2) -> None:
        pairs = [
            ('L_Shoulder', 'L_Elbow'), ('L_Elbow', 'L_Hand'),
            ('R_Shoulder', 'R_Elbow'), ('R_Elbow', 'R_Hand'),
            ('L_Hip', 'L_Knee'), ('L_Knee', 'L_Ankle'),
            ('R_Hip', 'R_Knee'), ('R_Knee', 'R_Ankle'),
        ]

        eb = arm.data.edit_bones
        all_intermediates = []

        for start_name, end_name in pairs:
            start = eb.get(start_name)
            end = eb.get(end_name)
            if not (start and end):
                continue
            intermediates = find_intermediate_bones_in_chain(start, end)
            for bone in intermediates:
                bone.parent = start
            all_intermediates.extend([b.name for b in intermediates])

        bpy.ops.object.mode_set(mode='OBJECT')
        if all_intermediates:
            default_coll = ensure_bone_collection(arm, 'Default')
            inter_coll = ensure_bone_collection(arm, 'Intermediate', default_coll)
            inter_coll.is_visible = False
            for name in all_intermediates:
                bone = arm.data.bones.get(name)
                if bone:
                    for c in list(bone.collections):
                        c.unassign(bone)
                    inter_coll.assign(bone)

    def _align_bone_rolls(self, arm: Object, hm2) -> None:
        if getattr(hm2, 'hm2_legacy_roll', False):
            self._align_bone_rolls_legacy(arm, hm2)
        else:
            self._align_bone_rolls_world(arm, hm2)

    def _align_bone_rolls_world(self, arm: Object, hm2) -> None:
        """Recompute every roll from fixed world axes so the result is identical
        regardless of the source rest pose (A-pose, T-pose, anything in between).

        Assumes the character faces -Y (Blender's standard front view). Each roll
        depends only on its own bone direction and a constant world reference, so
        it is fully deterministic and L/R symmetric (bone_X_R == -bone_X_L)."""
        eb = arm.data.edit_bones

        FORWARD = Vector((0.0, -1.0, 0.0))  # character faces -Y
        BACK    = Vector((0.0,  1.0, 0.0))
        UP      = Vector((0.0,  0.0, 1.0))

        def align_axis(name: str, z_ref: Vector, fallback: Vector) -> None:
            b = eb.get(name)
            if not b:
                return
            bone_y = b.tail - b.head
            if bone_y.length < 1e-6:
                return
            bone_y.normalize()
            # Avoid a degenerate align_roll when the reference is parallel to the bone.
            ref = z_ref if abs(z_ref.normalized().dot(bone_y)) < 0.999 else fallback
            b.align_roll(ref)

        def align_limb_pair(upper_l: str, mid_l: str, upper_r: str, mid_r: str,
                            bend_l: Vector) -> None:
            # bend_l is the world direction the joint flexes toward on the LEFT side.
            # Negating it for the right keeps bone_X_R == -bone_X_L (X-mirror symmetry).
            for name, bend in ((upper_l, bend_l), (mid_l, bend_l),
                               (upper_r, -bend_l), (mid_r, -bend_l)):
                b = eb.get(name)
                if not b:
                    continue
                bone_y = b.tail - b.head
                if bone_y.length < 1e-6:
                    continue
                z_dir = bend.cross(bone_y.normalized())
                if z_dir.length < 1e-5:
                    continue
                b.align_roll(z_dir)

        # Arms flex forward at the elbow; legs flex backward at the knee.
        align_limb_pair('L_Shoulder', 'L_Elbow', 'R_Shoulder', 'R_Elbow', FORWARD)
        align_limb_pair('L_Hip',      'L_Knee',  'R_Hip',      'R_Knee',  BACK)

        # Hands and scapulae sit roughly horizontal -> Z up. Opposite bone
        # directions across the body make these auto-mirror.
        for name in ('L_Hand', 'R_Hand', 'L_Scapula', 'R_Scapula'):
            align_axis(name, UP, FORWARD)

        # Spine column + head/neck point roughly up -> Z faces forward (-Y).
        count = hm2.hm2_spine_count
        spine_names = ['M_Spine'] if count == 1 else [f'M_Spine{i + 1}' for i in range(count)]
        for name in ['M_Root', *spine_names, 'M_Chest', 'M_Neck', 'M_Head']:
            align_axis(name, FORWARD, UP)

        # Fingers: keep each finger's segments consistent with its first joint.
        _ftype_map = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                      'RING': 'Ring', 'PINKY': 'Pinky'}
        for finger in hm2.hm2_fingers:
            base      = _ftype_map.get(finger.finger_type, finger.finger_type)
            start_idx = 0 if finger.finger_type == 'THUMB' else 1
            first_eb  = eb.get(f"{finger.side}_{base}Finger{start_idx}")
            if not first_eb:
                continue
            for i in range(1, finger.joint_count):
                joint_eb = eb.get(f"{finger.side}_{base}Finger{start_idx + i}")
                if joint_eb:
                    joint_eb.roll = first_eb.roll

    def _align_bone_rolls_legacy(self, arm: Object, hm2) -> None:
        eb = arm.data.edit_bones

        def _get_bend(upper_n: str, mid_n: str) -> Vector | None:
            upper_eb = eb.get(upper_n)
            mid_eb   = eb.get(mid_n)
            if not (upper_eb and mid_eb):
                return None
            limb_vec = mid_eb.tail - upper_eb.head
            if limb_vec.length < 1e-5:
                return None
            limb_n = limb_vec.normalized()
            t      = (mid_eb.head - upper_eb.head).dot(limb_n)
            proj   = upper_eb.head + limb_n * t
            bend   = mid_eb.head - proj
            if bend.length < 1e-4:
                return None
            return bend.normalized()

        def _apply_limb_roll(upper_n: str, bend: Vector) -> None:
            upper_eb = eb.get(upper_n)
            if not upper_eb:
                return
            bone_y = (upper_eb.tail - upper_eb.head).normalized()
            z_dir  = bend.cross(bone_y)
            if z_dir.length < 1e-5:
                return
            upper_eb.align_roll(z_dir)

        def _canonicalize_limb_pair(upper_l: str, mid_l: str,
                                     upper_r: str, mid_r: str) -> None:
            bend_l = _get_bend(upper_l, mid_l)
            bend_r = _get_bend(upper_r, mid_r)

            if bend_l is None and bend_r is None:
                # Straight limb on both sides : fall back to current bone X axes.
                ul_eb = eb.get(upper_l)
                ur_eb = eb.get(upper_r)
                if not (ul_eb and ur_eb):
                    return
                x_l      = ul_eb.x_axis.copy()
                x_r      = ur_eb.x_axis.copy()
                mirror_r = Vector((-x_r.x, x_r.y, x_r.z))
                avg      = x_l + mirror_r
                if avg.length < 1e-5:
                    return
                bend_l = avg.normalized()
            elif bend_l is None:
                bend_l = Vector((-bend_r.x, bend_r.y, bend_r.z))
            elif bend_r is not None:
                mirror_r = Vector((-bend_r.x, bend_r.y, bend_r.z))
                avg = bend_l + mirror_r
                if avg.length > 1e-5:
                    bend_l = avg.normalized()

            # Negate for R: ensures bone_X_R = -bone_X_L (Blender X-mirror symmetry).
            bend_r_canon = -bend_l

            _apply_limb_roll(upper_l, bend_l)
            _apply_limb_roll(upper_r, bend_r_canon)
            _apply_limb_roll(mid_l, bend_l)
            _apply_limb_roll(mid_r, bend_r_canon)

        _canonicalize_limb_pair('L_Shoulder', 'L_Elbow', 'R_Shoulder', 'R_Elbow')
        _canonicalize_limb_pair('L_Hip',      'L_Knee',  'R_Hip',      'R_Knee')

        _ftype_map = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                      'RING': 'Ring', 'PINKY': 'Pinky'}
        for finger in hm2.hm2_fingers:
            base      = _ftype_map.get(finger.finger_type, finger.finger_type)
            start_idx = 0 if finger.finger_type == 'THUMB' else 1
            first_eb  = eb.get(f"{finger.side}_{base}Finger{start_idx}")
            if not first_eb:
                continue
            for i in range(1, finger.joint_count):
                joint_eb = eb.get(f"{finger.side}_{base}Finger{start_idx + i}")
                if joint_eb:
                    joint_eb.roll = first_eb.roll

        count = hm2.hm2_spine_count
        spine_names = ['M_Spine'] if count == 1 else [f'M_Spine{i + 1}' for i in range(count)]

        root_eb = eb.get('M_Root')
        if root_eb:
            current_roll = root_eb.roll
            for name in spine_names:
                b = eb.get(name)
                if b:
                    b.roll = current_roll
                    current_roll = b.roll

        top_spine = eb.get(spine_names[-1]) if spine_names else None
        if top_spine:
            for name in ('M_Chest', 'M_Neck', 'M_Head'):
                b = eb.get(name)
                if b:
                    b.roll = top_spine.roll

        root_eb = eb.get('M_Root')
        if root_eb:
            root_eb.align_roll(Vector((0, -1, 0)))

        for name in ('L_Scapula', 'R_Scapula'):
            b = eb.get(name)
            if b:
                b.align_roll(Vector((0, 0, 1)))

    def _realign_finger_bones(self, arm: Object, hm2) -> None:
        """Snap every finger joint's tail onto the next joint's head so the
        chain is contiguous with no kinks. The outermost tip joint is left
        untouched (nothing points past it), and bone roll is preserved - only
        head/tail positions change. Runs in edit mode."""
        eb = arm.data.edit_bones
        _ftype_map = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                      'RING': 'Ring', 'PINKY': 'Pinky'}
        for finger in hm2.hm2_fingers:
            base      = _ftype_map.get(finger.finger_type, finger.finger_type)
            side      = finger.side
            start_idx = 0 if finger.finger_type == 'THUMB' else 1
            # Ordered joint chain; the last entry is the outermost tip (skipped).
            joints = [eb.get(f"{side}_{base}Finger{start_idx + i}")
                      for i in range(finger.joint_count)]
            for i in range(len(joints) - 1):
                cur, nxt = joints[i], joints[i + 1]
                if not (cur and nxt):
                    continue
                new_tail = nxt.head.copy()
                # Skip if it would collapse the bone to zero length.
                if (new_tail - cur.head).length <= 1e-4:
                    continue
                saved_roll = cur.roll
                cur.tail = new_tail
                cur.roll = saved_roll  # realign position only, keep roll

    def _create_all_twist_bones(self, arm: Object, hm2) -> None:
        self._twist_bone_names = {}
        twist_joints = [
            ('L_Shoulder', hm2.hm2_twist_shoulder),
            ('R_Shoulder', hm2.hm2_twist_shoulder),
            ('L_Elbow',    hm2.hm2_twist_elbow),
            ('R_Elbow',    hm2.hm2_twist_elbow),
            ('L_Hip',      hm2.hm2_twist_hip),
            ('R_Hip',      hm2.hm2_twist_hip),
            ('L_Knee',     hm2.hm2_twist_knee),
            ('R_Knee',     hm2.hm2_twist_knee),
        ]
        for bone_name, count in twist_joints:
            names = create_twist_bones(arm, bone_name, count)
            if names:
                self._twist_bone_names[bone_name] = names

        self._reparent_children_to_twist(arm, hm2)

    def _reparent_children_to_twist(self, arm: Object, hm2) -> None:
        """Re-parent user bones that hang off a twisting joint onto whichever
        twist segment sits closest to them.

        For every joint that received twist bones, each of its direct children
        that is a user bone (not an HM2-managed deform bone, twist bone, or
        HM2-added control bone) is projected onto the joint's head->tail axis.
        The projection parameter selects the twist segment covering that spot,
        so accessory bones (cloth, muscle, jiggle, ...) follow the twist that
        matches their position instead of the whole joint. Runs in edit mode.
        """
        eb = arm.data.edit_bones
        managed = self._hm2_managed_bone_names(arm, hm2)
        # arm.data.bones is stale in edit mode, so the just-created twist bones
        # are not yet caught by _hm2_managed_bone_names' regex - exclude them
        # explicitly using the names we just recorded.
        all_twist = {n for names in self._twist_bone_names.values() for n in names}

        def is_user_bone(name: str) -> bool:
            if name in managed or name in all_twist:
                return False
            return not any(name.startswith(p) for p in self._ADDED_PREFIXES)

        for joint_name, twist_names in self._twist_bone_names.items():
            joint = eb.get(joint_name)
            if not joint or not twist_names:
                continue

            axis = joint.tail - joint.head
            len_sq = axis.length_squared
            count = len(twist_names)

            # Snapshot children first: reassigning .parent mutates the collection.
            children = [c for c in joint.children if is_user_bone(c.name)]
            for child in children:
                if len_sq > 0.0:
                    t = (child.head - joint.head).dot(axis) / len_sq
                else:
                    t = 0.0
                idx = min(count - 1, max(0, int(t * count)))
                new_parent = eb.get(twist_names[idx])
                if new_parent and child.parent is not new_parent:
                    child.parent = new_parent
                    child.use_connect = False

    def _create_ik_bones(self, arm: Object, hm2) -> None:
        eb = arm.data.edit_bones

        def make_ik_bone(name: str, head: Vector, tail: Vector | None = None,
                         parent_name: str | None = None):
            if name in eb:
                return eb[name]
            b = eb.new(name)
            b.head = head.copy()
            b.tail = (head + Vector((0, 0, 0.1))) if tail is None else tail.copy()
            b.use_connect = False
            b.use_deform = False
            if parent_name:
                p = eb.get(parent_name)
                if p:
                    b.parent = p
            return b

        def limb_pole_position(upper_name: str, mid_name: str, dist: float) -> tuple | None:
            upper = eb.get(upper_name)
            mid   = eb.get(mid_name)
            if not (upper and mid):
                return None
            limb_start = upper.head
            limb_end   = mid.tail  # = next joint's head after _connect_chains
            limb_vec   = limb_end - limb_start
            if limb_vec.length < 1e-5:
                return None
            limb_n = limb_vec.normalized()
            t = (mid.head - limb_start).dot(limb_n)
            projected = limb_start + limb_n * t
            bend_dir  = mid.head - projected
            if bend_dir.length < 1e-4:
                # Perfectly straight limb - pick a reasonable perpendicular
                perp = limb_n.cross(Vector((0, 0, 1)))
                if perp.length < 1e-5:
                    perp = Vector((1, 0, 0))
                bend_dir = perp
            bend_dir = bend_dir.normalized()
            return mid.head + bend_dir * dist, bend_dir

        def compute_pole_angle(ik_root_name: str, ik_target_pos: Vector, pole_pos: Vector,
                               rest_bend_dir: Vector | None = None) -> float:
            root = eb.get(ik_root_name)
            if not root:
                return 0.0

            ik_axis = (ik_target_pos - root.head)
            if ik_axis.length < 1e-5:
                return 0.0
            ik_axis = ik_axis.normalized()

            pole_vec  = pole_pos - root.head
            pole_proj = pole_vec - pole_vec.dot(ik_axis) * ik_axis
            if pole_proj.length < 1e-5:
                return 0.0
            pole_proj = pole_proj.normalized()

            x_axis = root.x_axis.normalized()
            x_proj  = x_axis - x_axis.dot(ik_axis) * ik_axis
            if x_proj.length < 1e-5:
                return 0.0
            x_proj = x_proj.normalized()

            angle = math.atan2(x_proj.cross(pole_proj).dot(ik_axis),
                               x_proj.dot(pole_proj))

            # ±π check: if computed angle would bend the chain opposite to rest pose, flip it.
            if rest_bend_dir is not None and rest_bend_dir.length > 1e-4:
                c, s = math.cos(angle), math.sin(angle)
                expected_bend = x_proj * c + ik_axis.cross(x_proj) * s
                if expected_bend.dot(rest_bend_dir) < 0:
                    angle += math.pi
                    if angle > math.pi:
                        angle -= 2 * math.pi

            return angle

        _al = eb.get('L_Ankle')
        _ar = eb.get('R_Ankle')
        _rg = eb.get('M_Root')
        if _al and _ar:
            _gx = (_al.head.x + _ar.head.x) / 2.0
            _gy = (_al.head.y + _ar.head.y) / 2.0
        elif _al or _ar:
            _a  = _al or _ar
            _gx, _gy = _a.head.x, _a.head.y
        elif _rg:
            _gx, _gy = _rg.head.x, _rg.head.y
        else:
            _gx, _gy = 0.0, 0.0
        _gz = 0.0
        _gpos    = Vector((_gx, _gy, _gz))
        _g_len   = (_rg.length if _rg else 0.1) * 2.0
        make_ik_bone('CTRL_Ground', _gpos, tail=_gpos + Vector((0, _g_len, 0)))

        for shoulder_n, elbow_n, hand_n, ik_hand, ik_elbow in [
            ('L_Shoulder', 'L_Elbow', 'L_Hand', 'IK_Hand_L', 'IK_Elbow_L'),
            ('R_Shoulder', 'R_Elbow', 'R_Hand', 'IK_Hand_R', 'IK_Elbow_R'),
        ]:
            hand     = eb.get(hand_n)
            shoulder = eb.get(shoulder_n)
            if hand:
                ib = make_ik_bone(ik_hand, hand.head, tail=hand.tail,
                                  parent_name='CTRL_Ground')
                if ib:
                    ib.roll = hand.roll
            arm_len = (shoulder.head - hand.head).length if (shoulder and hand) else 0.3
            pole_dist = max(arm_len * 0.5, 1e-4)
            result = limb_pole_position(shoulder_n, elbow_n, dist=pole_dist)
            if result is not None:
                pole_pos, bend_dir = result
                bone_len = max(arm_len * 0.12, 1e-4)
                make_ik_bone(ik_elbow, pole_pos, tail=pole_pos + bend_dir * bone_len,
                             parent_name='CTRL_Ground')
                self._computed_pole_angles[ik_elbow] = compute_pole_angle(
                    shoulder_n, hand.head if hand else pole_pos, pole_pos, bend_dir)

        for hip_n, knee_n, ankle_n, ik_ankle, ik_knee, side in [
            ('L_Hip', 'L_Knee', 'L_Ankle', 'IK_Ankle_L', 'IK_Knee_L', 'L'),
            ('R_Hip', 'R_Knee', 'R_Ankle', 'IK_Ankle_R', 'IK_Knee_R', 'R'),
        ]:
            ankle  = eb.get(ankle_n)
            hip    = eb.get(hip_n)
            toe_eb = eb.get(f'{side}_Toe')

            if ankle:
                ctrl_toe_name = f'CTRL_Toe_{side}'
                ib = make_ik_bone(ik_ankle, ankle.head, tail=ankle.tail,
                                  parent_name='CTRL_Ground')
                if ib:
                    ib.roll = ankle.roll

                if toe_eb:
                    roll_mch = f'MCH_FootRoll_{side}'
                    tgt_mch  = f'MCH_IK_Ankle_{side}'

                    mch_fwd = (toe_eb.tail - toe_eb.head).normalized()
                    mr = make_ik_bone(roll_mch, toe_eb.head.copy(),
                                      tail=toe_eb.head + mch_fwd * ankle.length * 0.3,
                                      parent_name=ik_ankle)
                    if mr:
                        mr.roll = toe_eb.roll

                    # The actual leg-IK target, carried by the roll pivot.
                    tm = make_ik_bone(tgt_mch, ankle.head, tail=ankle.tail,
                                      parent_name=roll_mch)
                    if tm:
                        tm.roll = ankle.roll

                    # Toe wiggle control, sibling of the roll pivot under the master.
                    ct = make_ik_bone(ctrl_toe_name, toe_eb.head, tail=toe_eb.tail,
                                      parent_name=ik_ankle)
                    if ct:
                        ct.roll = toe_eb.roll
            leg_len = (hip.head - ankle.head).length if (hip and ankle) else 0.3
            pole_dist = max(leg_len * 0.5, 1e-4)
            result = limb_pole_position(hip_n, knee_n, dist=pole_dist)
            if result is not None:
                pole_pos, bend_dir = result
                bone_len = max(leg_len * 0.12, 1e-4)
                make_ik_bone(ik_knee, pole_pos, tail=pole_pos + bend_dir * bone_len,
                             parent_name='CTRL_Ground')
                self._computed_pole_angles[ik_knee] = compute_pole_angle(
                    hip_n, ankle.head if ankle else pole_pos, pole_pos, bend_dir)

        for _en in ('L_Eye', 'R_Eye'):
            _eeb = eb.get(_en)
            if _eeb:
                _eeb.align_roll(Vector((0, 0, 1)))

        head_bone = eb.get('M_Head')
        if head_bone and (hm2.hm2_map_eye_l or hm2.hm2_map_eye_r):
            eye_l = eb.get('L_Eye')
            eye_r = eb.get('R_Eye')
            if eye_l or eye_r:
                if eye_l and eye_r:
                    eye_mid = (eye_l.head + eye_r.head) / 2.0
                    lr_vec  = eye_r.head - eye_l.head
                else:
                    e       = eye_l or eye_r
                    eye_mid = e.head.copy()
                    lr_vec  = Vector((1, 0, 0))

                if lr_vec.length > 1e-5:
                    lr_vec.normalize()
                    fwd = Vector((0, 0, 1)).cross(lr_vec)
                    if fwd.length < 1e-5:
                        fwd = Vector((0, -1, 0))
                    fwd.normalize()
                else:
                    fwd = Vector((0, -1, 0))

                dist     = head_bone.length * 2.0
                eye_head = eye_mid + fwd * dist
                eye_tail = eye_head + fwd * head_bone.length * 0.5
                make_ik_bone('IK_EyeTarget', eye_head, tail=eye_tail, parent_name='M_Head')

                _bone_len = head_bone.length * 0.3
                for _eye_eb, _name in ((eye_l, 'IK_EyeTarget_L'), (eye_r, 'IK_EyeTarget_R')):
                    if _eye_eb is None:
                        continue
                    _lh = Vector((_eye_eb.head.x, eye_head.y, eye_head.z))
                    _lt = _lh + fwd * _bone_len
                    make_ik_bone(_name, _lh, tail=_lt, parent_name='IK_EyeTarget')

        _ftype_map = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                      'RING': 'Ring', 'PINKY': 'Pinky'}
        for finger in hm2.hm2_fingers:
            if not finger.generate_ik or not finger.source_bone:
                continue
            base      = _ftype_map.get(finger.finger_type, finger.finger_type)
            side      = finger.side
            start_idx = 0 if finger.finger_type == 'THUMB' else 1

            # Collect the existing (deform) finger segments that are present.
            orgs = []
            for i in range(finger.joint_count):
                o = eb.get(f"{side}_{base}Finger{start_idx + i}")
                if o is None:
                    break
                orgs.append(o)
            if not orgs:
                continue
            n           = len(orgs)
            hand_eb     = orgs[0].parent
            hand_name   = hand_eb.name if hand_eb else None

            # Deform bones must be free so COPY_TRANSFORMS can place them in pose.
            for o in orgs:
                o.use_connect = False

            # Master: spans the whole finger, parented to the hand.
            master_name = f"CTRL_{base}MasterFinger_{side}"
            mb = make_ik_bone(master_name, orgs[0].head, tail=orgs[-1].tail,
                              parent_name=hand_name)
            if mb:
                mb.roll = orgs[0].roll

            # FK controls (one per segment) + a tip control.
            fk_names = []
            for i, o in enumerate(orgs):
                fkn = f"FK_{base}Finger{start_idx + i}_{side}"
                fb  = make_ik_bone(fkn, o.head, tail=o.tail)
                if fb:
                    fb.roll = o.roll
                fk_names.append(fkn)
            tip_name = f"FK_{base}FingerTip_{side}"
            _tip_dir = (orgs[-1].tail - orgs[-1].head)
            _tip_dir = _tip_dir.normalized() if _tip_dir.length > 1e-6 else Vector((0, 1, 0))
            tb = make_ik_bone(tip_name, orgs[-1].tail.copy(),
                              tail=orgs[-1].tail + _tip_dir * max(orgs[-1].length * 0.5, 1e-4))
            if tb:
                tb.roll = orgs[-1].roll
            fk_names.append(tip_name)  # fk list has n + 1 entries

            # MCH bend (short stubs) and MCH stretch (copies of org).
            bend_names, stretch_names = [], []
            for i, o in enumerate(orgs):
                bdir = (o.tail - o.head)
                bdir = bdir.normalized() if bdir.length > 1e-6 else Vector((0, 1, 0))
                bn = f"MCH_Bend_{base}Finger{start_idx + i}_{side}"
                bb = make_ik_bone(bn, o.head, tail=o.head + bdir * max(o.length * 0.3, 1e-4))
                if bb:
                    bb.roll = o.roll
                bend_names.append(bn)

                sn = f"MCH_Stretch_{base}Finger{start_idx + i}_{side}"
                sb = make_ik_bone(sn, o.head, tail=o.tail)
                if sb:
                    sb.roll = o.roll
                stretch_names.append(sn)

            # Parenting (mirrors Rigify super_finger):
            #   fk[i].parent = bend[i]; tip.parent = last real fk
            for i in range(n):
                eb[fk_names[i]].parent = eb[bend_names[i]]
            eb[fk_names[n]].parent = eb[fk_names[n - 1]]
            #   bend[0].parent = hand; bend[i>0].parent = fk[i-1]
            eb[bend_names[0]].parent = hand_eb
            for i in range(1, n):
                eb[bend_names[i]].parent = eb[fk_names[i - 1]]
            #   stretch[0].parent = hand; stretch[i>0].parent = fk[i]
            eb[stretch_names[0]].parent = hand_eb
            for i in range(1, n):
                eb[stretch_names[i]].parent = eb[fk_names[i]]

        for joint_n, pole_n, line_n in [
            ('L_Elbow', 'IK_Elbow_L', 'VIS_PoleLine_Elbow_L'),
            ('R_Elbow', 'IK_Elbow_R', 'VIS_PoleLine_Elbow_R'),
            ('L_Knee',  'IK_Knee_L',  'VIS_PoleLine_Knee_L'),
            ('R_Knee',  'IK_Knee_R',  'VIS_PoleLine_Knee_R'),
        ]:
            joint_eb = eb.get(joint_n)
            pole_eb  = eb.get(pole_n)
            if joint_eb and pole_eb:
                lb                      = eb.new(line_n)
                lb.head                 = joint_eb.head.copy()
                lb.tail                 = pole_eb.head.copy()
                lb.use_connect          = False
                lb.use_deform           = False
                lb.use_inherit_rotation = False  # keep world-aligned so Stretch To works
                lb.inherit_scale        = 'NONE'
                lb.parent               = joint_eb

    def _setup_ik_constraints(self, arm: Object, hm2) -> None:
        pb       = arm.pose.bones
        computed = getattr(self, '_computed_pole_angles', {})

        def add_ik(chain_tip: str, target: str, pole: str | None, chain_count: int,
                   pole_key: str | None = None):
            bone = pb.get(chain_tip)
            if not bone:
                return
            for c in bone.constraints:
                if c.type == 'IK':
                    bone.constraints.remove(c)
            ik = bone.constraints.new('IK')
            ik.target     = arm
            ik.subtarget  = target
            ik.chain_count = chain_count
            if pole and pb.get(pole):
                ik.pole_target    = arm
                ik.pole_subtarget = pole
                ik.pole_angle = computed.get(pole_key or pole,
                                             hm2.hm2_ik_pole_angle_arm if 'Elbow' in (pole or '')
                                             else hm2.hm2_ik_pole_angle_leg)

        add_ik('L_Elbow', 'IK_Hand_L',  'IK_Elbow_L', 2, 'IK_Elbow_L')
        add_ik('R_Elbow', 'IK_Hand_R',  'IK_Elbow_R', 2, 'IK_Elbow_R')
        for side in ('L', 'R'):
            # Prefer the roll-driven MCH target so the foot can stand on its toe;
            # fall back to the bare foot master when there is no toe.
            ankle_tgt = f'MCH_IK_Ankle_{side}' if pb.get(f'MCH_IK_Ankle_{side}') \
                else f'IK_Ankle_{side}'
            add_ik(f'{side}_Knee', ankle_tgt, f'IK_Knee_{side}', 2, f'IK_Knee_{side}')

        _ftype_map2 = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                       'RING': 'Ring', 'PINKY': 'Pinky'}

        def _clear_constraints(bone, ctypes=None):
            for c in list(bone.constraints):
                if ctypes is None or c.type in ctypes:
                    bone.constraints.remove(c)

        for finger in hm2.hm2_fingers:
            if not finger.generate_ik:
                continue
            base      = _ftype_map2.get(finger.finger_type, finger.finger_type)
            side      = finger.side
            start_idx = 0 if finger.finger_type == 'THUMB' else 1
            master_name = f"CTRL_{base}MasterFinger_{side}"
            if not pb.get(master_name):
                continue

            orgs = []
            for i in range(finger.joint_count):
                o = pb.get(f"{side}_{base}Finger{start_idx + i}")
                if o is None:
                    break
                orgs.append(o)
            n = len(orgs)
            if n == 0:
                continue

            # Bend MCH: bone 0 copies the master; the rest curl from master scale.y.
            for i in range(n):
                bend = pb.get(f"MCH_Bend_{base}Finger{start_idx + i}_{side}")
                if not bend:
                    continue
                _clear_constraints(bend)
                bend.driver_remove('rotation_euler')
                if i == 0:
                    cl = bend.constraints.new('COPY_LOCATION')
                    cl.target = arm
                    cl.subtarget = master_name
                    cr = bend.constraints.new('COPY_ROTATION')
                    cr.target = arm
                    cr.subtarget = master_name
                    cr.owner_space = 'LOCAL'
                    cr.target_space = 'LOCAL'
                else:
                    # Master Y-scale drives the curl: rot.x = (1 - scale_y) * pi.
                    bend.rotation_mode = 'XYZ'
                    fcurve = bend.driver_add('rotation_euler', 0)
                    drv = fcurve.driver
                    drv.type = 'SCRIPTED'
                    drv.expression = '(1 - sy) * pi'
                    var = drv.variables.new()
                    var.name = 'sy'
                    var.type = 'TRANSFORMS'
                    t = var.targets[0]
                    t.id = arm
                    t.bone_target = master_name
                    t.transform_type = 'SCALE_Y'
                    t.transform_space = 'LOCAL_SPACE'

            for i in range(n):
                stretch = pb.get(f"MCH_Stretch_{base}Finger{start_idx + i}_{side}")
                if not stretch:
                    continue
                _clear_constraints(stretch)
                fk_cur  = f"FK_{base}Finger{start_idx + i}_{side}"
                fk_next = f"FK_{base}Finger{start_idx + i + 1}_{side}" if i < n - 1 \
                    else f"FK_{base}FingerTip_{side}"
                cl = stretch.constraints.new('COPY_LOCATION')
                cl.target = arm
                cl.subtarget = fk_cur
                cs = stretch.constraints.new('COPY_SCALE')
                cs.target = arm
                cs.subtarget = fk_cur
                st = stretch.constraints.new('STRETCH_TO')
                st.target = arm
                st.subtarget = fk_next
                st.volume = 'NO_VOLUME'
                st.keep_axis = 'SWING_Y'

            for i in range(n):
                stretch_name = f"MCH_Stretch_{base}Finger{start_idx + i}_{side}"
                if not pb.get(stretch_name):
                    continue
                _clear_constraints(orgs[i], {'COPY_TRANSFORMS', 'COPY_ROTATION'})
                ct = orgs[i].constraints.new('COPY_TRANSFORMS')
                ct.target = arm
                ct.subtarget = stretch_name

        for line_n, target_n in [
            ('VIS_PoleLine_Elbow_L', 'IK_Elbow_L'),
            ('VIS_PoleLine_Elbow_R', 'IK_Elbow_R'),
            ('VIS_PoleLine_Knee_L',  'IK_Knee_L'),
            ('VIS_PoleLine_Knee_R',  'IK_Knee_R'),
        ]:
            b = pb.get(line_n)
            if b and pb.get(target_n):
                st             = b.constraints.new('STRETCH_TO')
                st.target      = arm
                st.subtarget   = target_n
                st.rest_length = 0.0  # 0 = use bone's edit-mode rest length
                st.volume      = 'NO_VOLUME'
                b.lock_location   = [True, True, True]
                b.lock_rotation   = [True, True, True]
                b.lock_rotation_w = True
                b.lock_scale      = [True, True, True]

    def _setup_eye_constraints(self, arm: Object) -> None:
        pb = arm.pose.bones
        if not pb.get('IK_EyeTarget'):
            return
        for eye_name, subtarget in (('L_Eye', 'IK_EyeTarget_L'), ('R_Eye', 'IK_EyeTarget_R')):
            bone = pb.get(eye_name)
            if not bone:
                continue
            if not pb.get(subtarget):
                subtarget = 'IK_EyeTarget'
            for c in list(bone.constraints):
                if c.type == 'TRACK_TO':
                    bone.constraints.remove(c)
            tt = bone.constraints.new('TRACK_TO')
            tt.target     = arm
            tt.subtarget  = subtarget
            tt.track_axis = 'TRACK_Y'
            tt.up_axis    = 'UP_Z'

    def _setup_fk_controllers(self, arm: Object) -> None:
        pb = arm.pose.bones

        root_pb = pb.get('M_Root')
        if root_pb and pb.get('CTRL_Ground'):
            co = root_pb.constraints.new('CHILD_OF')
            co.target    = arm
            co.subtarget = 'CTRL_Ground'
            co.inverse_matrix = arm.pose.bones['CTRL_Ground'].matrix.inverted()

        def copy_rot(owner_name: str, target_name: str, local: bool = False) -> None:
            bone = pb.get(owner_name)
            if not (bone and pb.get(target_name)):
                return
            for c in list(bone.constraints):
                if c.type == 'COPY_ROTATION' and getattr(c, 'subtarget', '') == target_name:
                    bone.constraints.remove(c)
            cr = bone.constraints.new('COPY_ROTATION')
            cr.target       = arm
            cr.subtarget    = target_name
            cr.mix_mode     = 'REPLACE'
            space = 'LOCAL' if local else 'WORLD'
            cr.owner_space  = space
            cr.target_space = space

        copy_rot('L_Hand', 'IK_Hand_L', local=False)
        copy_rot('R_Hand', 'IK_Hand_R', local=False)
        for side in ('L', 'R'):
            ankle_tgt = f'MCH_IK_Ankle_{side}' if pb.get(f'MCH_IK_Ankle_{side}') \
                else f'IK_Ankle_{side}'
            copy_rot(f'{side}_Ankle', ankle_tgt, local=False)

        for side in ('L', 'R'):
            roll_pb = pb.get(f'MCH_FootRoll_{side}')
            toe_ctrl = f'CTRL_Toe_{side}'
            if roll_pb and pb.get(toe_ctrl):
                for c in list(roll_pb.constraints):
                    if c.type == 'COPY_ROTATION' and getattr(c, 'subtarget', '') == toe_ctrl:
                        roll_pb.constraints.remove(c)
                cr = roll_pb.constraints.new('COPY_ROTATION')
                cr.target       = arm
                cr.subtarget    = toe_ctrl
                cr.mix_mode     = 'REPLACE'
                cr.owner_space  = 'LOCAL'
                cr.target_space = 'LOCAL'

        for toe_name, toe_ctrl in [
            ('L_Toe', 'CTRL_Toe_L'),
            ('R_Toe', 'CTRL_Toe_R'),
        ]:
            tb = pb.get(toe_name)
            if tb and pb.get(toe_ctrl):
                cr = tb.constraints.new('COPY_ROTATION')
                cr.target       = arm
                cr.subtarget    = toe_ctrl
                cr.mix_mode     = 'ADD'
                cr.invert_x     = True
                cr.invert_y     = True
                cr.invert_z     = True
                cr.owner_space  = 'LOCAL'
                cr.target_space = 'LOCAL'

    def _setup_twist_drivers(self, arm: Object, hm2) -> None:
        pb = arm.pose.bones

        def setup_joint_twists(joint_name: str, target_prop: str, mode_prop: str):
            target = getattr(hm2, target_prop, "")
            mode = getattr(hm2, mode_prop, "FOLLOW")
            invert = (mode == 'AGAINST')
            names = self._twist_bone_names.get(joint_name, [])
            n = len(names)
            if n == 0:
                return
            rename_map = getattr(self, '_rename_map', {})
            if target and target in rename_map:
                target = rename_map[target]
            if target and not arm.data.bones.get(target):
                target = ""
            resolved = target or joint_name
            for idx, twist_name in enumerate(names):
                twist_pb = pb.get(twist_name)
                if not twist_pb:
                    continue
                if invert:
                    influence = (n - idx) / n
                else:
                    influence = (idx + 1) / n
                add_twist_driver(arm, twist_pb, resolved, influence, invert)

        setup_joint_twists('L_Shoulder', 'hm2_twist_shoulder_target_l', 'hm2_twist_shoulder_mode_l')
        setup_joint_twists('R_Shoulder', 'hm2_twist_shoulder_target_r', 'hm2_twist_shoulder_mode_r')
        setup_joint_twists('L_Elbow', 'hm2_twist_elbow_target_l', 'hm2_twist_elbow_mode_l')
        setup_joint_twists('R_Elbow', 'hm2_twist_elbow_target_r', 'hm2_twist_elbow_mode_r')
        setup_joint_twists('L_Hip', 'hm2_twist_hip_target_l', 'hm2_twist_hip_mode_l')
        setup_joint_twists('R_Hip', 'hm2_twist_hip_target_r', 'hm2_twist_hip_mode_r')
        setup_joint_twists('L_Knee', 'hm2_twist_knee_target_l', 'hm2_twist_knee_mode_l')
        setup_joint_twists('R_Knee', 'hm2_twist_knee_target_r', 'hm2_twist_knee_mode_r')

        self._setup_twist_vs(arm)

    def _setup_twist_vs(self, arm: Object) -> None:
        """Set rotation_copy_target on each twist bone's .vs to its parent joint name.
        Uses direct dict assignment to avoid triggering the VS update callback during setup."""
        pb = arm.pose.bones
        for joint_name, names in self._twist_bone_names.items():
            for twist_name in names:
                twist_pb = pb.get(twist_name)
                if not twist_pb:
                    continue
                try:
                    twist_pb.bone.vs['rotation_copy_target'] = joint_name
                except Exception:
                    pass

    def _assign_custom_shapes(self, arm: Object, hm2, shapes: dict) -> None:
        pb = arm.pose.bones
        _arm_world_3x3 = arm.matrix_world.to_3x3()

        def _orient_scoop_front(bone) -> None:
            """Lock a shoulder-scoop widget (scapula/shoulder) to a consistent world
            orientation regardless of the bone's roll. The widget's +Y stays along
            the bone, but it is rotated about that axis so the scoop opening (+Z)
            faces world front (-Y, Blender's standard armature-front). This keeps
            shoulder shapes displaying the same way across every model, where
            differing bone rolls would otherwise rotate them arbitrarily."""
            bone_world = _arm_world_3x3 @ bone.bone.matrix_local.to_3x3()
            by = (bone_world @ Vector((0.0, 1.0, 0.0)))
            if by.length < 1e-5:
                return
            by.normalize()
            front = Vector((0.0, -1.0, 0.0))
            # Component of world front perpendicular to the bone axis.
            z = front - by * front.dot(by)
            if z.length < 1e-5:
                z = Vector((0.0, 0.0, 1.0))  # bone ~parallel to front; fall back to up
            z.normalize()
            x = by.cross(z).normalized()
            # Columns map widget-local X/Y/Z → world right/along-bone/front.
            world_basis = Matrix((x, by, z)).transposed()
            local_rot = bone_world.inverted() @ world_basis
            bone.custom_shape_rotation_euler = local_rot.to_euler()

        hip_pb      = pb.get('L_Hip') or pb.get('R_Hip')
        ankle_pb    = pb.get('L_Ankle') or pb.get('R_Ankle')
        shoulder_pb = pb.get('L_Shoulder') or pb.get('R_Shoulder')
        hand_pb     = pb.get('L_Hand') or pb.get('R_Hand')
        root_ref    = pb.get('M_Root')
        head_ref    = pb.get('M_Head')

        # Separate limb refs so arm shapes scale to arm proportions and
        # leg shapes to leg proportions : Rigify sizes each region independently.
        _leg_ref = max((hip_pb.head - ankle_pb.head).length * 0.05, 1e-5) \
            if (hip_pb and ankle_pb) else None
        _arm_ref = max((shoulder_pb.head - hand_pb.head).length * 0.07, 1e-5) \
            if (shoulder_pb and hand_pb) else None
        _body_ref = max((head_ref.head - root_ref.head).length * 0.04, 1e-5) \
            if (head_ref and root_ref) else None
        ref = _leg_ref or _arm_ref or _body_ref or 0.035

        def assign(bone_name: str, shape_key: str, sz: float) -> None:
            """Assign a custom shape at an explicit world-space size (use_bone_size=False)."""
            bone = pb.get(bone_name)
            if bone and shape_key in shapes:
                bone.custom_shape               = shapes[shape_key]
                bone.custom_shape_scale_xyz     = (sz, sz, sz)
                bone.use_custom_shape_bone_size = False

        _hip_l_pb, _hip_r_pb = pb.get('L_Hip'), pb.get('R_Hip')
        _sho_l_pb, _sho_r_pb = pb.get('L_Shoulder'), pb.get('R_Shoulder')
        _hip_width = (_hip_l_pb.head - _hip_r_pb.head).length if (_hip_l_pb and _hip_r_pb) else 0.0
        _sho_width = (_sho_l_pb.head - _sho_r_pb.head).length if (_sho_l_pb and _sho_r_pb) else 0.0
        _root_z  = pb['M_Root'].head.z  if pb.get('M_Root')  else 0.0
        _chest_z = pb['M_Chest'].head.z if pb.get('M_Chest') else 1.0
        _vert_span = max(_chest_z - _root_z, 1e-5)

        _root_pb = pb.get('M_Root')
        if _root_pb and 'box' in shapes:
            _bl = _root_pb.bone.length
            _box_sz = _vert_span * 0.6 if _vert_span > 1e-5 else _hip_width if _hip_width > 0 else _bl * 1.8
            _root_pb.custom_shape               = shapes['box']
            _root_pb.custom_shape_scale_xyz     = (_box_sz, _box_sz, _box_sz)
            _root_pb.use_custom_shape_bone_size = False
            _root_pb.custom_shape_translation   = Vector((0, _bl * 0.5, 0))

        for _sbn in ('M_Spine', 'M_Spine1', 'M_Spine2', 'M_Spine3',
                     'M_Spine4', 'M_Spine5', 'M_Spine6', 'M_Spine7', 'M_Spine8'):
            _spb = pb.get(_sbn)
            if not (_spb and 'circle' in shapes):
                continue
            _bl = _spb.bone.length
            _spb.custom_shape             = shapes['circle']
            _spb.custom_shape_translation = Vector((0, _bl * 0.5, 0))
            # FK spine: scale with own bone length (Rigify radius=1.0 convention).
            _spb.custom_shape_scale_xyz     = (1.0, 1.0, 1.0)
            _spb.use_custom_shape_bone_size = True

        _chest_pb = pb.get('M_Chest')
        if _chest_pb and 'shoulder' in shapes:
            _bl = _chest_pb.bone.length
            _sz = _sho_width if _sho_width > 0 else _bl * 1.8
            _chest_pb.custom_shape                 = shapes['shoulder']
            _chest_pb.custom_shape_scale_xyz       = (_sz, _sz, _sz)
            _chest_pb.use_custom_shape_bone_size   = False
            _bw = _arm_world_3x3 @ _chest_pb.bone.matrix_local.to_3x3()
            _world_basis = Matrix((Vector((1.0, 0.0, 0.0)),
                                   Vector((0.0, 1.0, 0.0)),
                                   Vector((0.0, 0.0, 1.0)))).transposed()
            _chest_pb.custom_shape_rotation_euler  = (_bw.inverted() @ _world_basis).to_euler()
            _by = (_bw @ Vector((0.0, 1.0, 0.0)))
            _by = _by.normalized() if _by.length > 1e-5 else Vector((0.0, 0.0, 1.0))
            _world_off = _by * (_bl * 0.8) + Vector((0.0, -1.0, 0.0)) * (_sz * 0.5)
            _chest_pb.custom_shape_translation     = _bw.inverted() @ _world_off

        # Neck/head: Rigify uses create_circle_widget with bone-relative radii.
        # We keep absolute sizing here keyed to body ref.
        _br = _body_ref or ref
        for _bn, _mult, _ht in (('M_Neck', 1.0, 0.5), ('M_Head', 2.5, 1.0)):
            _b = pb.get(_bn)
            if _b and 'circle' in shapes:
                _sz = _br * _mult
                _b.custom_shape               = shapes['circle']
                _b.custom_shape_scale_xyz     = (_sz, _sz, _sz)
                _b.use_custom_shape_bone_size = False
                _b.custom_shape_translation   = Vector((0, _b.bone.length * _ht, 0))

        # Scale to roughly match the character's stance width.
        _ground_sz = max(_hip_width, _sho_width) * 0.9 if (_hip_width or _sho_width) else ref * 6.0
        assign('CTRL_Ground', 'master', _ground_sz)

        _ar = _arm_ref or ref
        _lr = _leg_ref or ref
        for side in ('L', 'R'):
            assign(f'CTRL_Toe_{side}', 'circle',   _lr * 0.7)
            assign(f'{side}_Scapula', 'shoulder',  _ar * 1.0)
            _scap_pb = pb.get(f'{side}_Scapula')
            if _scap_pb and 'shoulder' in shapes:
                _orient_scoop_front(_scap_pb)

        # Rigify sizes these via set_bone_widget_transform to the IK output bone
        # (wrist/ankle), so the widget naturally fits the limb endpoint.
        # We approximate that by using limb-specific refs.
        for side in ('L', 'R'):
            assign(f'IK_Hand_{side}',  'hand',   _ar * 1.2)
            assign(f'IK_Elbow_{side}', 'sphere', _ar * 0.8)
            assign(f'IK_Knee_{side}',  'sphere', _lr * 0.8)
            assign(f'{side}_Eye',      'circle', _ar * 0.7)
            hb = pb.get(f'IK_Hand_{side}')
            if hb and 'hand' in shapes:
                hb.custom_shape_rotation_euler = (0.0, math.radians(90), 0.0)

            ab  = pb.get(f'IK_Ankle_{side}')
            ank = pb.get(f'{side}_Ankle')
            toe = pb.get(f'{side}_Toe')
            if ab and 'foot' in shapes:
                # Foot length from ankle joint to toe (fall back to a leg-ref guess).
                if ank and toe:
                    foot_len = (toe.bone.head_local - ank.bone.head_local).length
                else:
                    foot_len = _lr * 4.0
                len_sz = max(foot_len * 1.3 / 1.75, _lr * 2.5)
                wid_sz = len_sz * 0.6
                ab.custom_shape               = shapes['foot']
                ab.use_custom_shape_bone_size = False
                ab.custom_shape_scale_xyz     = (wid_sz, len_sz, len_sz)

                bone_world = _arm_world_3x3 @ ab.bone.matrix_local.to_3x3()
                if ank and toe:
                    fwd = _arm_world_3x3 @ (toe.bone.head_local - ank.bone.head_local)
                else:
                    fwd = bone_world @ Vector((0.0, 1.0, 0.0))
                fwd.z = 0.0
                if fwd.length < 1e-5:
                    fwd = Vector((0.0, -1.0, 0.0))
                fwd.normalize()
                up    = Vector((0.0, 0.0, 1.0))
                right = fwd.cross(up).normalized()
                # Columns map widget-local X/Y/Z → world right/forward/up.
                world_basis = Matrix((right, fwd, up)).transposed()
                # custom_shape_rotation is applied in bone-local space.
                local_rot = bone_world.inverted() @ world_basis
                ab.custom_shape_rotation_euler = local_rot.to_euler()

                # Drop the print to the floor, centered between heel and toe.
                if ank and toe:
                    mid = (ank.bone.head_local + toe.bone.head_local) * 0.5
                else:
                    mid = ab.bone.head_local.copy()
                target_world = arm.matrix_world @ Vector((mid.x, mid.y, 0.0))
                head_world   = arm.matrix_world @ ab.bone.head_local
                # Translation is in raw bone-local units (bone-size scaling is off).
                ab.custom_shape_translation = bone_world.inverted() @ (target_world - head_world)

        _eye_l_pb = pb.get('L_Eye')
        _eye_r_pb = pb.get('R_Eye')
        if _eye_l_pb and _eye_r_pb:
            _eye_sep = (_eye_l_pb.bone.head_local - _eye_r_pb.bone.head_local).length
            _goggle_sz = _eye_sep if _eye_sep > 1e-5 else _br * 1.5
        else:
            _goggle_sz = _br * 1.5
        assign('IK_EyeTarget',   'goggle', _goggle_sz)
        assign('IK_EyeTarget_L', 'sphere', _br * 0.6)
        assign('IK_EyeTarget_R', 'sphere', _br * 0.6)

        _arm_twist_joints = {'L_Shoulder', 'R_Shoulder', 'L_Elbow', 'R_Elbow'}
        for joint_name, names in self._twist_bone_names.items():
            _tsz = (_ar if joint_name in _arm_twist_joints else _lr) * 0.9
            for name in names:
                bone = pb.get(name)
                if not (bone and 'twist' in shapes):
                    continue
                bone.custom_shape               = shapes['twist']
                bone.custom_shape_scale_xyz     = (_tsz, _tsz, _tsz)
                bone.use_custom_shape_bone_size = False
                bone.custom_shape_translation   = Vector((0.0, bone.bone.length * 0.5, 0.0))

        _ftm = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                'RING': 'Ring', 'PINKY': 'Pinky'}
        for finger in hm2.hm2_fingers:
            if not finger.generate_ik:
                continue
            base      = _ftm.get(finger.finger_type, finger.finger_type)
            side      = finger.side
            start_idx = 0 if finger.finger_type == 'THUMB' else 1

            mpb = pb.get(f"CTRL_{base}MasterFinger_{side}")
            if mpb and 'finger_master' in shapes:
                mpb.custom_shape               = shapes['finger_master']
                mpb.custom_shape_scale_xyz     = (1.0, 1.0, 1.0)
                mpb.use_custom_shape_bone_size = True

            if 'circle' not in shapes:
                continue
            fk_bones = [f"FK_{base}Finger{start_idx + i}_{side}"
                        for i in range(finger.joint_count)]
            fk_bones.append(f"FK_{base}FingerTip_{side}")
            for idx, fkn in enumerate(fk_bones):
                cpb = pb.get(fkn)
                if not cpb:
                    continue
                is_tip = (idx == len(fk_bones) - 1)
                cpb.custom_shape               = shapes['circle']
                cpb.custom_shape_scale_xyz     = (0.6, 0.6, 0.6)
                cpb.use_custom_shape_bone_size = True
                # Ring sits mid-bone for joints, at the head for the tip.
                cpb.custom_shape_translation   = Vector((0.0, 0.0 if is_tip else 0.5, 0.0))

        if 'line' in shapes:
            for line_n in ('VIS_PoleLine_Elbow_L', 'VIS_PoleLine_Elbow_R',
                           'VIS_PoleLine_Knee_L',  'VIS_PoleLine_Knee_R'):
                b = pb.get(line_n)
                if not b:
                    continue
                b.custom_shape               = shapes['line']
                b.custom_shape_scale_xyz     = (1.0, 1.0, 1.0)
                b.use_custom_shape_bone_size = True

    def _assign_bone_colors(self, arm: Object, hm2) -> None:
        pb = arm.pose.bones

        def color(bone_name: str, palette: str) -> None:
            b = pb.get(bone_name)
            if b:
                b.color.palette = palette

        for n in ('CTRL_Ground', 'M_Root'):
            color(n, 'THEME07')
        for n in ('M_Chest', 'M_Spine', 'M_Spine1', 'M_Spine2', 'M_Spine3',
                  'M_Spine4', 'M_Spine5', 'M_Spine6', 'M_Spine7', 'M_Spine8',
                  'CTRL_Toe_L', 'CTRL_Toe_R'):
            color(n, 'THEME05')
        for n in ('IK_Hand_L', 'IK_Hand_R', 'IK_Ankle_L', 'IK_Ankle_R'):
            color(n, 'THEME04')
        for n in ('IK_EyeTarget', 'IK_EyeTarget_L', 'IK_EyeTarget_R'):
            color(n, 'THEME08')
        for n in ('IK_Elbow_L', 'IK_Elbow_R', 'IK_Knee_L', 'IK_Knee_R'):
            color(n, 'THEME06')
        for n in ('VIS_PoleLine_Elbow_L', 'VIS_PoleLine_Elbow_R',
                  'VIS_PoleLine_Knee_L',  'VIS_PoleLine_Knee_R'):
            color(n, 'THEME06')
        for names in self._twist_bone_names.values():
            for n in names:
                color(n, 'THEME09')
        _ftmc = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                 'RING': 'Ring', 'PINKY': 'Pinky'}
        for finger in hm2.hm2_fingers:
            if not finger.generate_ik:
                continue
            base      = _ftmc.get(finger.finger_type, finger.finger_type)
            side      = finger.side
            start_idx = 0 if finger.finger_type == 'THUMB' else 1
            color(f"CTRL_{base}MasterFinger_{side}", 'THEME05')
            for i in range(finger.joint_count):
                color(f"FK_{base}Finger{start_idx + i}_{side}", 'THEME05')
            color(f"FK_{base}FingerTip_{side}", 'THEME05')

    def _apply_export_config(self, arm: Object, hm2) -> None:
        import json as _json, os

        path = bpy.path.abspath(hm2.hm2_json_filepath.strip())
        if not path or not os.path.isfile(path):
            print(f"HM2: export config not found: {path}")
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                config = _json.load(f)
        except Exception as e:
            print(f"HM2: failed to load export config JSON: {e}")
            return

        pb = arm.pose.bones

        _SINGLE = {
            'Root': 'M_Root', 'Chest': 'M_Chest',
            'Neck': 'M_Neck', 'Head':  'M_Head',
        }
        _BILATERAL = {
            'Scapula':  ('L_Scapula',  'R_Scapula'),
            'Shoulder': ('L_Shoulder', 'R_Shoulder'),
            'Elbow':    ('L_Elbow',    'R_Elbow'),
            'Hand':     ('L_Hand',     'R_Hand'),
            'Hip':      ('L_Hip',      'R_Hip'),
            'Knee':     ('L_Knee',     'R_Knee'),
            'Ankle':    ('L_Ankle',    'R_Ankle'),
            'Toe':      ('L_Toe',      'R_Toe'),
            'Eye':      ('L_Eye',      'R_Eye'),
        }
        _FINGER = {  # json key -> (bone base name, first joint index)
            'Thumb':  ('Thumb',  0),
            'Index':  ('Index',  1),
            'Middle': ('Middle', 1),
            'Ring':   ('Ring',   1),
            'Pinky':  ('Pinky',  1),
        }

        def _set_vs(pose_bone, export_name: str, rot_str: str, loc_str: str) -> None:
            vs = getattr(pose_bone.bone, 'vs', None)
            if vs is None:
                return
            try:
                setattr(vs, 'export_name', export_name)
            except Exception:
                pass
            if rot_str:
                try:
                    x, y, z = (float(v) for v in rot_str.split())
                    setattr(vs, 'ignore_rotation_offset', False)
                    setattr(vs, 'export_rotation_offset_x', x)
                    setattr(vs, 'export_rotation_offset_y', y)
                    setattr(vs, 'export_rotation_offset_z', z)
                except Exception:
                    pass
            if loc_str:
                try:
                    x, y, z = (float(v) for v in loc_str.split())
                    setattr(vs, 'ignore_location_offset', False)
                    setattr(vs, 'export_location_offset_x', x)
                    setattr(vs, 'export_location_offset_y', y)
                    setattr(vs, 'export_location_offset_z', z)
                except Exception:
                    pass

        def _apply(bone_name: str, export_name: str, entry: dict,
                   rot_str: str | None = None, loc_str: str | None = None) -> None:
            b = pb.get(bone_name)
            if not b:
                return
            rot = rot_str if rot_str is not None else entry.get('exportrot', '')
            loc = loc_str if loc_str is not None else entry.get('exportloc', '')
            _set_vs(b, export_name, rot, loc)
            # Twist bones get the same offsets and a generated export name
            for idx, twist_name in enumerate(self._twist_bone_names.get(bone_name, [])):
                tb = pb.get(twist_name)
                if tb:
                    _set_vs(tb, f"{export_name} twist {idx + 1}", rot, loc)

        for bone_type, entry in config.items():
            if not isinstance(entry, dict):
                continue
            name_pat = entry.get('name', '')

            if bone_type in _SINGLE:
                _apply(_SINGLE[bone_type], name_pat, entry)

            elif bone_type in _BILATERAL:
                dir_l = entry.get('dir_l', 'L_')
                dir_r = entry.get('dir_r', 'R_')
                l_name, r_name = _BILATERAL[bone_type]

                rot_l = entry.get('exportrot_l', '') or entry.get('exportrot', '')
                rot_r = entry.get('exportrot_r', '') or entry.get('exportrot', '')
                loc_l = entry.get('exportloc_l', '') or entry.get('exportloc', '')
                loc_r = entry.get('exportloc_r', '') or entry.get('exportloc', '')

                if rot_l and not rot_r:
                    rot_r = rot_l
                elif rot_r and not rot_l:
                    rot_l = rot_r
                if loc_l and not loc_r:
                    loc_r = loc_l
                elif loc_r and not loc_l:
                    loc_l = loc_r

                _apply(l_name, name_pat.replace('{dir}', dir_l), entry, rot_str=rot_l, loc_str=loc_l)
                _apply(r_name, name_pat.replace('{dir}', dir_r), entry, rot_str=rot_r, loc_str=loc_r)

            elif bone_type == 'Spine':
                start = entry.get('starting_count', 1)
                ignore_zero = entry.get('ignore_zero', False)
                if hm2.hm2_spine_count == 1:
                    spine_bones = ['M_Spine']
                else:
                    spine_bones = [f'M_Spine{i + 1}' for i in range(hm2.hm2_spine_count)]
                for i, sbn in enumerate(spine_bones):
                    num = start + i
                    num_str = '' if (ignore_zero and num == 0) else str(num)
                    _apply(sbn, name_pat.replace('{*}', num_str), entry)

            elif bone_type in _FINGER:
                base, joint_start = _FINGER[bone_type]
                dir_l = entry.get('dir_l', 'L_')
                dir_r = entry.get('dir_r', 'R_')
                start = entry.get('starting_count', 1)
                ignore_zero = entry.get('ignore_zero', False)

                rot_l = entry.get('exportrot_l', '') or entry.get('exportrot', '')
                rot_r = entry.get('exportrot_r', '') or entry.get('exportrot', '')
                loc_l = entry.get('exportloc_l', '') or entry.get('exportloc', '')
                loc_r = entry.get('exportloc_r', '') or entry.get('exportloc', '')
                if rot_l and not rot_r:
                    rot_r = rot_l
                elif rot_r and not rot_l:
                    rot_l = rot_r
                if loc_l and not loc_r:
                    loc_r = loc_l
                elif loc_r and not loc_l:
                    loc_l = loc_r

                for side, dir_str, rot_str, loc_str in (('L', dir_l, rot_l, loc_l), ('R', dir_r, rot_r, loc_r)):
                    for i in range(10):
                        joint_name = f"{side}_{base}Finger{joint_start + i}"
                        if not pb.get(joint_name):
                            break
                        num = start + i
                        num_str = '' if (ignore_zero and num == 0) else str(num)
                        _apply(joint_name,
                            name_pat.replace('{dir}', dir_str).replace('{*}', num_str),
                            entry, rot_str=rot_str, loc_str=loc_str)

    def _unlock_all_bones(self, arm: Object) -> None:
        for pb in arm.pose.bones:
            pb.lock_location = [False, False, False]
            pb.lock_rotation = [False, False, False]
            pb.lock_rotation_w = False
            pb.lock_scale = [False, False, False]

    def _organize_collections(self, arm: Object, hm2) -> None:
        default_coll = ensure_bone_collection(arm, 'Default')
        twist_coll   = ensure_bone_collection(arm, 'Twist')
        if twist_coll.parent is not None:
            twist_coll.parent = None
        finger_coll  = ensure_bone_collection(arm, 'Fingers',   default_coll)
        face_coll    = ensure_bone_collection(arm, 'Face',      default_coll)
        misc_coll    = ensure_bone_collection(arm, 'Misc')
        hair_coll    = ensure_bone_collection(arm, 'Hair',      misc_coll)
        mech_coll    = ensure_bone_collection(arm, 'Mechanism', misc_coll)
        mech_coll.is_visible = False
        ctrl_coll    = ensure_bone_collection(arm, 'Controllers')
        spine_coll   = ensure_bone_collection(arm, 'Spine',     ctrl_coll)

        hm2_bone_names = set()
        for attr in dir(hm2):
            if attr.startswith('hm2_map_'):
                val = getattr(hm2, attr, '')
                if val:
                    hm2_bone_names.add(val)

        twist_names = set()
        for names in self._twist_bone_names.values():
            twist_names.update(names)

        for bone in arm.data.bones:
            name = bone.name
            target = None

            if name in twist_names:
                target = twist_coll
            elif name.startswith('VIS_'):
                target = ctrl_coll
            elif name.startswith('MCH_'):
                target = mech_coll
            elif name in ('IK_EyeTarget_L', 'IK_EyeTarget_R'):
                target = mech_coll
            elif name.startswith('FK_') or name.startswith('CTRL_') or name.startswith('IK_'):
                target = ctrl_coll
            elif name in ('M_Root', 'M_Neck', 'M_Chest', 'M_Head') or name.startswith('M_Spine') or name == 'M_Spine':
                target = spine_coll
            elif 'Finger' in name:
                target = finger_coll
            elif 'Scapula' in name:
                target = ctrl_coll
            elif 'Eye' in name:
                target = face_coll
            elif name in hm2_bone_names:
                target = default_coll
            else:
                if 'hair' in name.lower() or 'bangs' in name.lower():
                    target = hair_coll
                else:
                    target = misc_coll

            if target is not None:
                for c in list(bone.collections):
                    c.unassign(bone)
                target.assign(bone)

        default_coll.is_visible = False
        face_coll.is_visible    = False
        misc_coll.is_visible    = False
        hair_coll.is_visible    = False
        mech_coll.is_visible    = False
        twist_coll.is_visible   = True
        ctrl_coll.is_visible    = True
        spine_coll.is_visible   = True
        finger_coll.is_visible  = True

    # ------------------------------------------------------------------
    # Puppet helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_rest_pose_match(master_obj: Object, puppet_obj: Object,
                                threshold: float = 0.05) -> list[str]:
        """Return error strings for bones whose rest rotation differs > threshold radians.
        Uses master's hm2_map_* source names to look up bones in both armatures."""
        master_arm = master_obj.data
        master_hm2 = master_obj.kitsunetools.hm2
        puppet_arm = puppet_obj.data

        mapping = [
            ('hm2_map_root',       'M_Root'),
            ('hm2_map_chest',      'M_Chest'),
            ('hm2_map_neck',       'M_Neck'),
            ('hm2_map_head',       'M_Head'),
            ('hm2_map_scapula_l',  'L_Scapula'),
            ('hm2_map_scapula_r',  'R_Scapula'),
            ('hm2_map_shoulder_l', 'L_Shoulder'),
            ('hm2_map_shoulder_r', 'R_Shoulder'),
            ('hm2_map_elbow_l',    'L_Elbow'),
            ('hm2_map_elbow_r',    'R_Elbow'),
            ('hm2_map_hand_l',     'L_Hand'),
            ('hm2_map_hand_r',     'R_Hand'),
            ('hm2_map_hip_l',      'L_Hip'),
            ('hm2_map_hip_r',      'R_Hip'),
            ('hm2_map_knee_l',     'L_Knee'),
            ('hm2_map_knee_r',     'R_Knee'),
            ('hm2_map_ankle_l',    'L_Ankle'),
            ('hm2_map_ankle_r',    'R_Ankle'),
            ('hm2_map_toe_l',      'L_Toe'),
            ('hm2_map_toe_r',      'R_Toe'),
        ]

        errors = []
        for master_src_prop, master_bone_name in mapping:
            # Master and puppet share the same source bone names — use master's mapping
            # to look up the corresponding bone in both armatures.
            puppet_src = getattr(master_hm2, master_src_prop, '').strip()
            if not puppet_src:
                continue
            master_bone = master_arm.bones.get(master_bone_name)
            puppet_bone = puppet_arm.bones.get(puppet_src)
            if not (master_bone and puppet_bone):
                continue
            q_m = master_bone.matrix_local.to_3x3().to_quaternion()
            q_p = puppet_bone.matrix_local.to_3x3().to_quaternion()
            angle = q_m.rotation_difference(q_p).angle
            if angle > threshold:
                errors.append(
                    f"{master_bone_name} ↔ {puppet_src}: {math.degrees(angle):.1f}°"
                )
        return errors

    @staticmethod
    def _auto_map_puppet_bones(master_obj: Object, puppet_obj: Object) -> list[str]:
        """Populate puppet hm2_map_* and hm2_fingers using the original source bone names
        stored in master's _hm2_src_map during its own rename phase.
        Returns warnings for missing or structurally mismatched bones."""
        master_hm2 = master_obj.kitsunetools.hm2
        puppet_hm2 = puppet_obj.kitsunetools.hm2
        puppet_arm  = puppet_obj.data
        warnings: list[str] = []

        # {hm2_bone_name: original_source_bone_name} — saved by _rename_core_bones/_rename_fingers
        try:
            src_map: dict[str, str] = json.loads(
                master_obj.data.get("_hm2_src_map", "{}") or "{}")
        except Exception:
            src_map = {}

        CORE_PROPS = [
            ('hm2_map_root',       'M_Root',      True),
            ('hm2_map_chest',      'M_Chest',     True),
            ('hm2_map_neck',       'M_Neck',      True),
            ('hm2_map_head',       'M_Head',      True),
            ('hm2_map_scapula_l',  'L_Scapula',   False),
            ('hm2_map_scapula_r',  'R_Scapula',   False),
            ('hm2_map_shoulder_l', 'L_Shoulder',  True),
            ('hm2_map_shoulder_r', 'R_Shoulder',  True),
            ('hm2_map_elbow_l',    'L_Elbow',     True),
            ('hm2_map_elbow_r',    'R_Elbow',     True),
            ('hm2_map_hand_l',     'L_Hand',      True),
            ('hm2_map_hand_r',     'R_Hand',      True),
            ('hm2_map_hip_l',      'L_Hip',       True),
            ('hm2_map_hip_r',      'R_Hip',       True),
            ('hm2_map_knee_l',     'L_Knee',      True),
            ('hm2_map_knee_r',     'R_Knee',      True),
            ('hm2_map_ankle_l',    'L_Ankle',     True),
            ('hm2_map_ankle_r',    'R_Ankle',     True),
            ('hm2_map_toe_l',      'L_Toe',       False),
            ('hm2_map_toe_r',      'R_Toe',       False),
            ('hm2_map_eye_l',      'L_Eye',       False),
            ('hm2_map_eye_r',      'R_Eye',       False),
        ]

        for prop, target_name, required in CORE_PROPS:
            # Original name stored at rename time; fall back to current master prop
            # (handles case where master was never renamed, e.g. names already matched).
            orig_src = src_map.get(target_name, '').strip() \
                       or getattr(master_hm2, prop, '').strip()
            setattr(puppet_hm2, prop, orig_src)
            if orig_src and not puppet_arm.bones.get(orig_src):
                label = "Required" if required else "Optional"
                warnings.append(f"{label} bone '{orig_src}' not found in puppet")

        # Spine count + structural check.
        puppet_hm2.hm2_spine_count = master_hm2.hm2_spine_count
        root_src  = puppet_hm2.hm2_map_root.strip()
        chest_src = puppet_hm2.hm2_map_chest.strip()
        if root_src and chest_src:
            root_b  = puppet_arm.bones.get(root_src)
            chest_b = puppet_arm.bones.get(chest_src)
            if root_b and chest_b:
                spine_actual = 0
                b = chest_b
                seen: set[str] = set()
                while b and b.name not in seen:
                    seen.add(b.name)
                    if not b.parent or b.parent.name == root_src:
                        break
                    b = b.parent
                    spine_actual += 1
                if spine_actual < master_hm2.hm2_spine_count:
                    warnings.append(
                        f"Spine: puppet has {spine_actual} bone(s) between root and chest, "
                        f"master expects {master_hm2.hm2_spine_count} (missing bones will be created)"
                    )

        # Fingers — look up each finger's original source bone via src_map.
        puppet_hm2.hm2_fingers.clear()
        _fdisp = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                  'RING': 'Ring', 'PINKY': 'Pinky'}
        for mf in master_hm2.hm2_fingers:
            orig_src = src_map.get(mf.source_bone, '').strip() or mf.source_bone
            nf = puppet_hm2.hm2_fingers.add()
            nf.source_bone = orig_src
            nf.finger_type = mf.finger_type
            nf.side        = mf.side
            nf.joint_count = mf.joint_count
            nf.generate_ik = mf.generate_ik

            if not puppet_arm.bones.get(orig_src):
                label = f"{mf.side} {_fdisp.get(mf.finger_type, mf.finger_type)}"
                warnings.append(
                    f"Optional bone '{orig_src}' ({label} finger) not found in puppet")
                continue

            cur = puppet_arm.bones.get(orig_src)
            actual = 0
            while cur:
                actual += 1
                if actual >= mf.joint_count:
                    break
                children = list(cur.children)
                cur = children[0] if len(children) == 1 else None

            if actual < mf.joint_count:
                label = f"{mf.side} {_fdisp.get(mf.finger_type, mf.finger_type)}"
                warnings.append(
                    f"{label} finger: puppet has {actual} joint(s), master expects {mf.joint_count}")
                nf.joint_count = actual

        return warnings

    def _run_puppet(self, context: Context, master_obj: Object,
                    puppet_obj: Object, mode: str = 'MIMIC') -> list[str]:
        """Process puppet_obj against master_obj.

        mode='MIMIC': puppet follows master via COPY_TRANSFORMS on all deform bones.
        mode='SELF':  puppet gets its own IK controllers (like a standalone master);
                      only VS export config is synced from master.

        In both modes the HM2 base deform skeleton (core, spine, limbs, fingers, twist)
        is renamed and structured to match master, and .vs export data is copied from master.

        Returns list of non-fatal warnings from auto bone-mapping.
        """
        master_hm2 = master_obj.kitsunetools.hm2
        puppet_hm2 = puppet_obj.kitsunetools.hm2

        # Auto-populate puppet mapping from master (same source bone names).
        warnings = self._auto_map_puppet_bones(master_obj, puppet_obj)

        bpy.context.view_layer.objects.active = puppet_obj
        bpy.ops.object.mode_set(mode='OBJECT')

        # Re-apply cleanup: removes prior constraints, IK/FK/MCH bones, and twist bones.
        # Also run cleanup if the puppet was previously processed as a standalone HM2 rig
        # (hm2_is_puppet=False but IK/ctrl bones already present) to avoid duplicates.
        if puppet_hm2.hm2_is_puppet or self._is_hm2_applied(puppet_obj):
            self._cleanup_for_reapply(puppet_obj)

        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

        # Sync core settings from master.
        puppet_hm2.hm2_legacy_roll = master_hm2.hm2_legacy_roll
        puppet_hm2.hm2_first_person_mode = master_hm2.hm2_first_person_mode
        for attr in ('hm2_twist_shoulder', 'hm2_twist_elbow',
                     'hm2_twist_hip', 'hm2_twist_knee'):
            setattr(puppet_hm2, attr, getattr(master_hm2, attr))

        if mode == 'SELF':
            # Sync IK/shape/twist settings needed for full rig setup.
            for attr in (
                'hm2_generate_ik', 'hm2_generate_shapes',
                'hm2_ik_pole_angle_arm', 'hm2_ik_pole_angle_leg',
                'hm2_twist_shoulder_target_l', 'hm2_twist_shoulder_target_r',
                'hm2_twist_shoulder_mode_l',   'hm2_twist_shoulder_mode_r',
                'hm2_twist_elbow_target_l',    'hm2_twist_elbow_target_r',
                'hm2_twist_elbow_mode_l',      'hm2_twist_elbow_mode_r',
                'hm2_twist_hip_target_l',      'hm2_twist_hip_target_r',
                'hm2_twist_hip_mode_l',        'hm2_twist_hip_mode_r',
                'hm2_twist_knee_target_l',     'hm2_twist_knee_target_r',
                'hm2_twist_knee_mode_l',       'hm2_twist_knee_mode_r',
            ):
                setattr(puppet_hm2, attr, getattr(master_hm2, attr))

        # Sync bone head/tail/roll from master BEFORE rename, using original name map.
        # In SELF mode, skip bones whose rest-pose direction differs > 10 degrees from
        # master so the puppet keeps its own geometry for differently-posed bones.
        # _sync_puppet_bone_positions leaves puppet in EDIT mode.
        sync_skip = math.pi / 18 if mode == 'SELF' else None
        self._sync_puppet_bone_positions(master_obj, puppet_obj, skip_threshold=sync_skip)

        puppet_obj.data.use_mirror_x = False
        for eb in puppet_obj.data.edit_bones:
            eb.use_connect = False

        self._rename_core_bones(puppet_obj, puppet_hm2)
        if puppet_hm2.hm2_first_person_mode:
            self._ensure_first_person_root(puppet_obj, puppet_hm2)
        self._setup_spine(puppet_obj, puppet_hm2)
        self._rename_fingers(puppet_obj, puppet_hm2)
        self._connect_chains(puppet_obj, puppet_hm2)
        self._remove_intermediates(puppet_obj, puppet_hm2)

        bpy.ops.object.mode_set(mode='EDIT')
        self._create_all_twist_bones(puppet_obj, puppet_hm2)

        if mode == 'SELF':
            self._computed_pole_angles = {}
            self._create_ik_bones(puppet_obj, puppet_hm2)

        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')
        self._unlock_all_bones(puppet_obj)

        if mode == 'SELF':
            if puppet_hm2.hm2_generate_ik:
                self._setup_ik_constraints(puppet_obj, puppet_hm2)
                self._setup_eye_constraints(puppet_obj)
            self._setup_fk_controllers(puppet_obj)
            self._setup_twist_drivers(puppet_obj, puppet_hm2)  # also calls _setup_twist_vs
            if puppet_hm2.hm2_generate_shapes:
                shapes = ensure_hm2_shapes(context)
                self._assign_custom_shapes(puppet_obj, puppet_hm2, shapes)
            self._assign_bone_colors(puppet_obj, puppet_hm2)
        else:
            # MIMIC: set rotation_copy_target on twist bones for VS auto-propagation.
            self._setup_twist_vs(puppet_obj)

        bpy.ops.object.mode_set(mode='OBJECT')

        if mode == 'SELF':
            self._organize_collections(puppet_obj, puppet_hm2)
        else:
            self._organize_puppet_collections(puppet_obj)

        # VS export config sync from master (both modes).
        puppet_hm2.hm2_json_filepath = master_hm2.hm2_json_filepath
        self._copy_vs_to_puppet(master_obj, puppet_obj)

        # MIMIC mode: follow master deform bones via COPY_TRANSFORMS.
        if mode == 'MIMIC':
            self._add_puppet_constraints(master_obj, puppet_obj)

        # Parent puppet object to master so Object-mode moves are shared.
        if puppet_obj.parent != master_obj:
            saved_world = puppet_obj.matrix_world.copy()
            puppet_obj.parent = master_obj
            puppet_obj.matrix_parent_inverse = master_obj.matrix_world.inverted()
            puppet_obj.matrix_world = saved_world

        puppet_hm2.hm2_is_puppet = True
        puppet_hm2.hm2_puppet_master = master_obj

        return warnings

    @staticmethod
    def _copy_vs_to_puppet(master_obj: Object, puppet_obj: Object) -> None:
        """Copy .vs export fields from master to matching puppet bones.

        Only HM2-managed bones (core, spine, twist, fingers) are synced - misc
        hair/cloth/physics bones are left untouched.

        This is preferred over re-parsing the JSON because it captures any manual
        per-bone edits made in the panel after the config was last applied."""
        master_hm2 = master_obj.kitsunetools.hm2
        managed = HM2_OT_Process._hm2_managed_bone_names(master_obj, master_hm2)

        # Snapshot only HM2-managed bones from master, then restore onto puppet.
        snapshot = {
            name: data
            for name, data in HM2_OT_Process._snapshot_vs_config(master_obj).items()
            if name in managed
        }
        HM2_OT_Process._restore_vs_config(puppet_obj, snapshot)

        # Copy fields with update callbacks via direct dict assignment to avoid
        # triggering VS callbacks during setup:
        #   rotation_copy_target  - would fire _sync_rotation_from_target
        #   location_offset_in_armature_space / export_location_offset_arm_* -
        #     would fire _sync_local_to_arm / _sync_arm_to_local
        _CALLBACK_FIELDS = (
            'rotation_copy_target',
            'location_offset_in_armature_space',
            'export_location_offset_arm_x',
            'export_location_offset_arm_y',
            'export_location_offset_arm_z',
        )
        for bone in master_obj.data.bones:
            if bone.name not in managed:
                continue
            vs = getattr(bone, 'vs', None)
            if vs is None:
                continue
            puppet_bone = puppet_obj.data.bones.get(bone.name)
            if puppet_bone is None:
                continue
            pvs = getattr(puppet_bone, 'vs', None)
            if pvs is None:
                continue
            for field in _CALLBACK_FIELDS:
                val = getattr(vs, field, None)
                if val is None:
                    continue
                # Skip default/empty values to avoid spurious writes.
                if isinstance(val, str) and not val:
                    continue
                try:
                    puppet_bone.vs[field] = val
                except Exception:
                    pass

    def _sync_puppet_bone_positions(self, master_obj: Object, puppet_obj: Object,
                                     skip_threshold: float | None = None) -> None:
        """Pre-rename sync: copy head/tail/roll from each master HM2 deform bone to the
        corresponding puppet source bone using the stored original name map.
        Must be called while in OBJECT mode. Leaves puppet in EDIT mode.

        skip_threshold: if set, bones whose direction vector (in world space) differs from
        master's by more than this angle (radians) are left untouched. Used in SELF mode to
        preserve the puppet's own rest pose for bones that differ significantly from master."""
        # {hm2_bone_name: original_src_bone_name} -> invert to puppet_bone -> master_bone
        try:
            src_map: dict[str, str] = json.loads(
                master_obj.data.get("_hm2_src_map", "{}") or "{}")
        except Exception:
            src_map = {}
        puppet_to_master = {orig: hm2 for hm2, orig in src_map.items()}

        # Read master edit bones (roll only available in edit mode).
        bpy.context.view_layer.objects.active = master_obj
        bpy.ops.object.mode_set(mode='EDIT')
        master_edit: dict[str, tuple] = {
            eb.name: (eb.head.copy(), eb.tail.copy(), eb.roll)
            for eb in master_obj.data.edit_bones
        }
        bpy.ops.object.mode_set(mode='OBJECT')

        bpy.context.view_layer.objects.active = puppet_obj
        bpy.ops.object.mode_set(mode='EDIT')

        mw        = master_obj.matrix_world
        mw_rot    = mw.to_3x3()
        mw_inv    = puppet_obj.matrix_world.inverted()
        pw_rot    = puppet_obj.matrix_world.to_3x3()
        for eb in puppet_obj.data.edit_bones:
            master_name = puppet_to_master.get(eb.name)
            if master_name is None:
                continue
            md = master_edit.get(master_name)
            if md is None:
                continue
            m_head, m_tail, m_roll = md

            if skip_threshold is not None:
                m_dir = mw_rot @ (m_tail - m_head)
                p_dir = pw_rot @ (eb.tail - eb.head)
                if m_dir.length > 1e-6 and p_dir.length > 1e-6:
                    m_dir.normalize()
                    p_dir.normalize()
                    dot = max(-1.0, min(1.0, m_dir.dot(p_dir)))
                    if math.acos(dot) > skip_threshold:
                        continue

            eb.head = mw_inv @ (mw @ m_head)
            eb.tail = mw_inv @ (mw @ m_tail)
            eb.roll = m_roll
        # Leave in EDIT mode — caller continues the rename pipeline.

    def _hm2_deform_names(self, hm2) -> set[str]:
        """Return the explicit set of HM2 deform bone names for a given hm2 config."""
        names: set[str] = {
            'M_Root', 'M_Chest', 'M_Neck', 'M_Head',
            'L_Eye', 'R_Eye',
            'L_Scapula', 'R_Scapula',
            'L_Shoulder', 'R_Shoulder',
            'L_Elbow', 'R_Elbow',
            'L_Hand', 'R_Hand',
            'L_Hip', 'R_Hip',
            'L_Knee', 'R_Knee',
            'L_Ankle', 'R_Ankle',
            'L_Toe', 'R_Toe',
        }
        count = hm2.hm2_spine_count
        names.add('M_Spine') if count == 1 else names.update(
            f'M_Spine{i}' for i in range(1, count + 1))
        _fbase = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                  'RING': 'Ring', 'PINKY': 'Pinky'}
        for f in hm2.hm2_fingers:
            start = 0 if f.finger_type == 'THUMB' else 1
            for i in range(start, start + f.joint_count):
                names.add(f'{f.side}_{_fbase.get(f.finger_type, f.finger_type)}Finger{i}')
        for twist_list in self._twist_bone_names.values():
            names.update(twist_list)
        return names

    def _add_puppet_constraints(self, master_obj: Object, puppet_obj: Object) -> None:
        """Add COPY_TRANSFORMS on HM2 deform bones only — core, spine, fingers, twist."""
        puppet_hm2 = puppet_obj.kitsunetools.hm2
        allowed = self._hm2_deform_names(puppet_hm2)
        master_bones = {b.name for b in master_obj.data.bones}

        bpy.context.view_layer.objects.active = puppet_obj
        bpy.ops.object.mode_set(mode='POSE')
        for pb in puppet_obj.pose.bones:
            if pb.name not in allowed or pb.name not in master_bones:
                continue
            ct = pb.constraints.new('COPY_TRANSFORMS')
            ct.target = master_obj
            ct.subtarget = pb.name
            ct.target_space = 'POSE'
            ct.owner_space = 'POSE'
        bpy.ops.object.mode_set(mode='OBJECT')

    def _organize_puppet_collections(self, puppet_obj: Object) -> None:
        """Assign bones to the same collection structure as the master.
        HM2 deform bones → Twist / Spine / Fingers / Face / Default.
        Non-HM2 bones (hair, cloth, physics) → Hair or Misc."""
        hm2 = puppet_obj.kitsunetools.hm2
        allowed = self._hm2_deform_names(hm2)

        default_coll = ensure_bone_collection(puppet_obj, 'Default')
        twist_coll   = ensure_bone_collection(puppet_obj, 'Twist')
        finger_coll  = ensure_bone_collection(puppet_obj, 'Fingers', default_coll)
        face_coll    = ensure_bone_collection(puppet_obj, 'Face',    default_coll)
        spine_coll   = ensure_bone_collection(puppet_obj, 'Spine',   default_coll)
        misc_coll    = ensure_bone_collection(puppet_obj, 'Misc')
        hair_coll    = ensure_bone_collection(puppet_obj, 'Hair',    misc_coll)

        twist_names = {n for names in self._twist_bone_names.values() for n in names}

        for bone in puppet_obj.data.bones:
            name = bone.name
            if name in allowed:
                if name in twist_names:
                    target = twist_coll
                elif name in ('M_Root', 'M_Neck', 'M_Chest', 'M_Head') \
                        or name.startswith('M_Spine') or name == 'M_Spine':
                    target = spine_coll
                elif 'Finger' in name:
                    target = finger_coll
                elif 'Eye' in name:
                    target = face_coll
                else:
                    target = default_coll
            else:
                if 'hair' in name.lower() or 'bangs' in name.lower():
                    target = hair_coll
                else:
                    target = misc_coll

            for c in list(bone.collections):
                c.unassign(bone)
            target.assign(bone)

        default_coll.is_visible = False
        face_coll.is_visible    = False
        misc_coll.is_visible    = False
        hair_coll.is_visible    = False
        twist_coll.is_visible   = True
        spine_coll.is_visible   = True
        finger_coll.is_visible  = True


class HM2_OT_AddPuppet(Operator):
    bl_idname = "kitsunetools.hm2_add_puppet"
    bl_label = "Add Selected as Puppet"
    bl_description = "Add other selected armatures to the puppet list"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.mode == 'OBJECT' and is_armature(context.active_object)

    def execute(self, context: Context) -> set:
        master_obj = context.active_object
        master_hm2 = master_obj.kitsunetools.hm2

        candidates = [
            o for o in context.selected_objects
            if o != master_obj and is_armature(o)
        ]
        if not candidates:
            self.report({'WARNING'}, "Select one or more armatures in addition to the master")
            return {'CANCELLED'}

        existing = {e.armature for e in master_hm2.hm2_puppets if e.armature}
        added = 0
        for obj in candidates:
            if obj in existing:
                self.report({'WARNING'}, f"'{obj.name}' is already in the puppet list")
                continue
            if obj.kitsunetools.hm2.hm2_is_puppet:
                self.report({'WARNING'}, f"'{obj.name}' is already a puppet of another master")
                continue
            entry = master_hm2.hm2_puppets.add()
            entry.armature = obj
            master_hm2.hm2_puppets_index = len(master_hm2.hm2_puppets) - 1
            added += 1

        if added:
            self.report({'INFO'}, f"Added {added} armature(s) to puppet list")
        return {'FINISHED'}


class HM2_OT_RemovePuppet(Operator):
    bl_idname = "kitsunetools.hm2_remove_puppet"
    bl_label = "Remove Puppet Entry"
    bl_description = "Remove the selected entry from the puppet list"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: Context) -> bool:
        if not (context.mode == 'OBJECT' and is_armature(context.active_object)):
            return False
        hm2 = context.active_object.kitsunetools.hm2
        return 0 <= hm2.hm2_puppets_index < len(hm2.hm2_puppets)

    def execute(self, context: Context) -> set:
        master_hm2 = context.active_object.kitsunetools.hm2
        idx = master_hm2.hm2_puppets_index
        entry = master_hm2.hm2_puppets[idx]
        puppet_obj = entry.armature

        # Light disconnect: clear puppet markers without touching constraints.
        if puppet_obj and is_armature(puppet_obj):
            puppet_hm2 = puppet_obj.kitsunetools.hm2
            if puppet_hm2.hm2_is_puppet and puppet_hm2.hm2_puppet_master == context.active_object:
                puppet_hm2.hm2_is_puppet = False
                puppet_hm2.hm2_puppet_master = None

        master_hm2.hm2_puppets.remove(idx)
        master_hm2.hm2_puppets_index = max(0, min(idx, len(master_hm2.hm2_puppets) - 1))
        return {'FINISHED'}


class HM2_OT_ProcessPuppet(Operator):
    bl_idname = "kitsunetools.hm2_process_puppet"
    bl_label = "Process as Puppet"
    bl_description = (
        "Process a newly-added puppet against the already-converted master. "
        "Puppets added before running HM2 Setup are processed automatically"
    )
    bl_options = {'REGISTER', 'UNDO'}

    rest_pose_threshold: FloatProperty(
        name="Rest Pose Threshold",
        description=(
            "Maximum allowed rotation difference (radians) between corresponding "
            "rest-pose bones. Pairs beyond this threshold abort the operation"
        ),
        default=0.05, min=0.0, max=1.5707963, subtype='ANGLE',
    )

    @classmethod
    def poll(cls, context: Context) -> bool:
        if not (context.mode == 'OBJECT' and is_armature(context.active_object)):
            return False
        arm = context.active_object
        if not HM2_OT_Process._is_hm2_applied(arm):
            return False
        hm2 = arm.kitsunetools.hm2
        return 0 <= hm2.hm2_puppets_index < len(hm2.hm2_puppets)

    def invoke(self, context: Context, event) -> set:
        master_obj = context.active_object
        master_hm2 = master_obj.kitsunetools.hm2
        idx = master_hm2.hm2_puppets_index
        entry = master_hm2.hm2_puppets[idx]
        puppet_obj = entry.armature

        if not puppet_obj or not is_armature(puppet_obj):
            self.report({'ERROR'}, "Puppet entry has no valid armature")
            return {'CANCELLED'}
        if puppet_obj == master_obj:
            self.report({'ERROR'}, "Puppet cannot be the same object as master")
            return {'CANCELLED'}

        # Hard stop only in MIMIC mode. SELF mode handles rest pose differences
        # by skipping mismatched bones during position sync rather than aborting.
        if entry.mode == 'MIMIC':
            errors = HM2_OT_Process._check_rest_pose_match(
                master_obj, puppet_obj, self.rest_pose_threshold)
            if errors:
                preview = '\n'.join(errors[:5])
                if len(errors) > 5:
                    preview += f'\n... and {len(errors) - 5} more'
                self.report({'ERROR'}, f"Rest pose mismatch — puppet does not match master:\n{preview}")
                return {'CANCELLED'}

        return self.execute(context)

    def draw(self, context: Context) -> None:
        self.layout.prop(self, 'rest_pose_threshold')

    def execute(self, context: Context) -> set:
        master_obj = context.active_object
        master_hm2 = master_obj.kitsunetools.hm2
        idx = master_hm2.hm2_puppets_index

        if not (0 <= idx < len(master_hm2.hm2_puppets)):
            self.report({'ERROR'}, "Invalid puppet entry index")
            return {'CANCELLED'}

        puppet_entry = master_hm2.hm2_puppets[idx]
        puppet_obj = puppet_entry.armature
        if not puppet_obj or not is_armature(puppet_obj):
            self.report({'ERROR'}, "Puppet entry has no valid armature")
            return {'CANCELLED'}

        bpy.ops.ed.undo_push(message="Before HM2 Process Puppet")
        try:
            proc = HM2_OT_Process._make_proc()
            warnings = proc._run_puppet(context, master_obj, puppet_obj, puppet_entry.mode)
        except Exception as e:
            traceback.print_exc()
            self.report({'ERROR'}, f"Puppet processing failed and was reverted: {e}")
            HM2_OT_Process._schedule_revert()
            return {'CANCELLED'}

        for w in warnings:
            self.report({'WARNING'}, w)
        context.view_layer.objects.active = master_obj
        self.report({'INFO'}, f"Puppet '{puppet_obj.name}' processed successfully")
        return {'FINISHED'}


class HM2_OT_DisconnectPuppet(Operator):
    bl_idname = "kitsunetools.hm2_disconnect_puppet"
    bl_label = "Disconnect from Master"
    bl_description = (
        "Remove COPY_TRANSFORMS constraints and object parent, making this "
        "armature a standalone rig. Run HM2 Setup to add IK controllers."
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: Context) -> bool:
        if not (context.mode == 'OBJECT' and is_armature(context.active_object)):
            return False
        return context.active_object.kitsunetools.hm2.hm2_is_puppet

    def execute(self, context: Context) -> set:
        puppet_obj = context.active_object
        puppet_hm2 = puppet_obj.kitsunetools.hm2
        master_obj = puppet_hm2.hm2_puppet_master

        bpy.ops.object.mode_set(mode='POSE')
        for pb in puppet_obj.pose.bones:
            for c in list(pb.constraints):
                if c.type == 'COPY_TRANSFORMS' and getattr(c, 'target', None) == master_obj:
                    pb.constraints.remove(c)
        bpy.ops.object.mode_set(mode='OBJECT')

        saved_world = puppet_obj.matrix_world.copy()
        puppet_obj.parent = None
        puppet_obj.matrix_world = saved_world

        puppet_coll = puppet_obj.data.collections.get("Puppet")
        if puppet_coll:
            puppet_coll.is_visible = True

        puppet_hm2.hm2_is_puppet = False
        puppet_hm2.hm2_puppet_master = None

        if master_obj and is_armature(master_obj):
            master_hm2 = master_obj.kitsunetools.hm2
            for i, entry in enumerate(master_hm2.hm2_puppets):
                if entry.armature == puppet_obj:
                    master_hm2.hm2_puppets.remove(i)
                    master_hm2.hm2_puppets_index = max(
                        0, min(i, len(master_hm2.hm2_puppets) - 1))
                    break

        self.report({'INFO'}, f"'{puppet_obj.name}' disconnected from master")
        return {'FINISHED'}


class HM2_OT_SyncPuppetExportConfig(Operator):
    bl_idname = "kitsunetools.hm2_sync_puppet_export"
    bl_label = "Sync Export Config to Puppets"
    bl_description = (
        "Copy the master's current per-bone .vs export settings (names, offsets) "
        "to all connected puppet armatures. Run this after manually editing any "
        "bone's VS export data on the master"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context: Context) -> bool:
        if not (context.mode == 'OBJECT' and is_armature(context.active_object)):
            return False
        arm = context.active_object
        if not HM2_OT_Process._is_hm2_applied(arm):
            return False
        hm2 = arm.kitsunetools.hm2
        return any(
            e.armature and e.armature.kitsunetools.hm2.hm2_is_puppet
            for e in hm2.hm2_puppets
        )

    def execute(self, context: Context) -> set:
        master_obj = context.active_object
        master_hm2 = master_obj.kitsunetools.hm2
        synced = 0
        for entry in master_hm2.hm2_puppets:
            puppet_obj = entry.armature
            if not puppet_obj or not puppet_obj.kitsunetools.hm2.hm2_is_puppet:
                continue
            HM2_OT_Process._copy_vs_to_puppet(master_obj, puppet_obj)
            synced += 1
        self.report({'INFO'}, f"Synced export config to {synced} puppet(s)")
        return {'FINISHED'}


class HM2_OT_FirstPersonArms(Operator):
    bl_idname = "kitsunetools.hm2_first_person_arms"
    bl_label = "Create First Person Arms"
    bl_description = ("Duplicate the armature and its meshes into an arms-only version: keep each "
                     "starting bone and its children, delete everything else, and cull the meshes "
                     "to the geometry weighted to the kept bones")
    bl_options = {'REGISTER', 'UNDO'}

    bisect_use_bisect: BoolProperty(name="Bisect Seam", default=True)
    bisect_axis: EnumProperty(
        name="Bisect Axis",
        items=[('X', 'World X', ''), ('Y', 'World Y', ''), ('Z', 'World Z', '')],
        default='Z',
    )
    bisect_offset: FloatProperty(name="Bisect Offset", default=0.0, subtype='DISTANCE')

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.mode == 'OBJECT' and is_armature(context.active_object)

    def invoke(self, context: Context, event) -> set:
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2
        self.bisect_use_bisect = hm2.fpa_use_bisect
        self.bisect_axis       = hm2.fpa_bisect_axis
        self.bisect_offset     = hm2.fpa_bisect_offset
        self._fpa_arm    = arm
        self._fpa_starts = [s for s in (hm2.fpa_starting_bone_l, hm2.fpa_starting_bone_r) if s]
        from ..utils.utils_fpa_preview import register_preview
        register_preview(self)
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return context.window_manager.invoke_props_dialog(
            self, title="First Person Arms – Bisect Preview", width=320
        )

    def draw(self, context: Context) -> None:
        layout = self.layout
        layout.prop(self, "bisect_use_bisect")
        row = layout.row(align=True)
        row.enabled = self.bisect_use_bisect
        row.prop(self, "bisect_axis", text="")
        row.prop(self, "bisect_offset", text="Offset")
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def cancel(self, context: Context) -> None:
        from ..utils.utils_fpa_preview import unregister_preview
        unregister_preview()
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def execute(self, context: Context) -> set:
        from ..utils.utils_fpa_preview import unregister_preview
        unregister_preview()
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2
        hm2.fpa_use_bisect    = self.bisect_use_bisect
        hm2.fpa_bisect_axis   = self.bisect_axis
        hm2.fpa_bisect_offset = self.bisect_offset

        start_l = hm2.fpa_starting_bone_l
        start_r = hm2.fpa_starting_bone_r
        starts = [s for s in (start_l, start_r) if s]
        if not starts:
            self.report({'ERROR'}, "Assign at least one starting bone (L or R)")
            return {'CANCELLED'}
        for s in starts:
            if s not in arm.data.bones:
                self.report({'ERROR'}, f"Starting bone '{s}' not found in armature")
                return {'CANCELLED'}

        if hm2.fpa_rig_type == 'HM2':
            is_hm2 = True
        elif hm2.fpa_rig_type == 'PLAIN':
            is_hm2 = False
        else:
            is_hm2 = detect_hm2_rig(arm)

        try:
            with unhide_all_objects():
                return self._run(context, arm, hm2, starts, is_hm2)
        except Exception as e:
            traceback.print_exc()
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            self.report({'ERROR'}, f"First Person Arms failed: {e}")
            return {'CANCELLED'}

    def _run(self, context: Context, src_arm: Object, hm2, starts: list[str], is_hm2: bool) -> set:
        # One bisect plane per starting bone, each through that bone's head in world space.
        # When bones span opposite sides of the origin along the chosen axis (e.g. L/R shoulders
        # at ±X), each normal is flipped inward so each plane only removes body-side geometry and
        # leaves the other arm intact.  When all bones are on the same side (e.g. both shoulders
        # above Z=0), all normals are identical and the cuts are parallel.
        axis_index = {'X': 0, 'Y': 1, 'Z': 2}[hm2.fpa_bisect_axis]
        base_no = Vector((0.0, 0.0, 0.0))
        base_no[axis_index] = 1.0
        heads = [src_arm.matrix_world @ src_arm.data.bones[s].head_local for s in starts]
        positions = [h[axis_index] for h in heads]
        opposite_sides = (len(positions) > 1
                          and min(positions) < -1e-4
                          and max(positions) > 1e-4)
        plane_nos = []
        plane_cos = []
        for h in heads:
            no = -base_no.copy() if (opposite_sides and h[axis_index] > 1e-4) else base_no.copy()
            plane_nos.append(no)
            plane_cos.append(h + no * hm2.fpa_bisect_offset)

        # --- Duplicate armature + its meshes -------------------------------------
        src_meshes = get_armature_meshes(src_arm)
        src_colls = list(src_arm.users_collection) or [context.scene.collection]
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        for ob in src_meshes:
            ob.select_set(True)
        src_arm.select_set(True)
        context.view_layer.objects.active = src_arm
        bpy.ops.object.duplicate(linked=False)

        dup_arm = context.view_layer.objects.active
        dup_arm.name = f"{src_arm.name}_FPArms"
        dup_meshes = list(get_armature_meshes(dup_arm))

        # Place all duplicated objects in the source armature's collection(s) so the
        # first-person-arms set stays grouped together regardless of where the
        # originals or the active collection were.
        for ob in [dup_arm, *dup_meshes]:
            for c in list(ob.users_collection):
                c.objects.unlink(ob)
            for c in src_colls:
                c.objects.link(ob)

        # --- Compute kept bones --------------------------------------------------
        kept = compute_fpa_kept_bones(
            dup_arm, hm2.fpa_starting_bone_l, hm2.fpa_starting_bone_r,
            hm2.fpa_preserve_ik, is_hm2,
        )
        if not kept:
            self.report({'ERROR'}, "No bones matched the starting selection")
            return {'CANCELLED'}

        # Deform bones that meshes are weighted to (controllers never deform).
        kept_deform = {n for n in kept if not n.startswith(HM2_CONTROLLER_PREFIXES)}

        # --- Delete unwanted bones ----------------------------------------------
        context.view_layer.objects.active = dup_arm
        bpy.ops.object.mode_set(mode='EDIT')
        eb = dup_arm.data.edit_bones
        for bone in eb:
            if bone.name in kept and bone.parent and bone.parent.name not in kept:
                bone.parent = None
        for bone in [b for b in eb if b.name not in kept]:
            eb.remove(bone)

        # On an HM2 rig with IK kept, move the master controller (CTRL_Ground) to
        # sit between the starting bones for a natural first-person pivot. Its IK
        # children keep their own rest positions, so only the control origin moves.
        relocate_ground = is_hm2 and hm2.fpa_preserve_ik
        if relocate_ground:
            cg = eb.get('CTRL_Ground')
            heads = [eb[s].head.copy() for s in starts if s in eb]
            if cg and heads:
                delta = (sum(heads, Vector()) / len(heads)) - cg.head
                cg.head = cg.head + delta
                cg.tail = cg.tail + delta
                # Rig the starting bones to the ground controller so the whole arm
                # assembly follows it as a single first-person root.
                for s in starts:
                    sb = eb.get(s)
                    if sb and sb is not cg:
                        sb.use_connect = False
                        sb.parent = cg
        bpy.ops.object.mode_set(mode='OBJECT')

        # --- Strip constraints/drivers referencing deleted bones ----------------
        bone_names = set(dup_arm.data.bones.keys())
        bpy.ops.object.mode_set(mode='POSE')
        for pb in dup_arm.pose.bones:
            for con in list(pb.constraints):
                tgt = getattr(con, 'target', None)
                refs = [getattr(con, a, '') for a in ('subtarget', 'pole_subtarget')]
                if tgt == dup_arm and any(r and r not in bone_names for r in refs):
                    pb.constraints.remove(con)
        if dup_arm.animation_data:
            for fc in list(dup_arm.animation_data.drivers):
                dp = fc.data_path
                if dp.startswith('pose.bones['):
                    try:
                        bname = dp.split('"')[1]
                    except IndexError:
                        bname = None
                    if bname is not None and bname not in bone_names:
                        dup_arm.animation_data.drivers.remove(fc)
        bpy.ops.object.mode_set(mode='OBJECT')

        # Give the relocated master controller a box widget for clearer FP control.
        if relocate_ground:
            cg_pb = dup_arm.pose.bones.get('CTRL_Ground')
            box = get_hm2_shape('box')
            if cg_pb and box is not None:
                cg_pb.custom_shape = box
                cg_pb.custom_shape_translation = Vector((0.0, 0.0, 0.0))

        # --- Cull meshes ---------------------------------------------------------
        culled, deleted = 0, 0
        for mesh in dup_meshes:
            poly_count = cull_mesh_to_bones(
                mesh, kept_deform,
                threshold=hm2.fpa_weight_threshold,
                use_bisect=hm2.fpa_use_bisect,
                plane_cos_world=plane_cos,
                plane_nos_world=plane_nos,
            )
            if poly_count == 0:
                bpy.data.objects.remove(mesh, do_unlink=True)
                deleted += 1
            else:
                culled += 1

        bpy.ops.object.select_all(action='DESELECT')
        dup_arm.select_set(True)
        context.view_layer.objects.active = dup_arm

        self.report(
            {'INFO'},
            f"First Person Arms: kept {len(kept)} bones, {culled} mesh(es) culled, "
            f"{deleted} empty mesh(es) deleted",
        )
        return {'FINISHED'}

