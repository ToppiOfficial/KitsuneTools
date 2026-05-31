import bpy, traceback, math
from mathutils import Vector
from bpy.types import Context, Object, Operator
from bpy.props import IntProperty, StringProperty, BoolProperty, EnumProperty

from ..utils.utils_object import is_armature
from ..utils.utils_bone import bonename_direction_map
from ..utils.utils_humanoidmapper2 import (
    hm2_validate_mapping,
    collect_chain,
    find_intermediate_bones_in_chain,
    create_twist_bones,
    add_twist_driver,
    ensure_hm2_shapes,
    ensure_bone_collection,
)

# -- Shared mirror helper ------------------------------------------------------

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


# -- UIList operators ----------------------------------------------------------

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


# -- JSON format help popup ---------------------------------------------------

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


# -- Validate -----------------------------------------------------------------

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


# -- Main Process operator -----------------------------------------------------

class HM2_OT_Process(Operator):
    bl_idname = "kitsunetools.hm2_process"
    bl_label = "Run HM2 Setup"
    bl_description = "Rename bones, generate twist bones, build IK rig, assign custom shapes, and organize collections"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.mode == 'OBJECT' and is_armature(context.active_object)

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
            self._run(context, arm, hm2)
        except Exception as e:
            traceback.print_exc()
            bpy.ops.ed.undo()
            self.report({'ERROR'}, f"HM2 failed and was reverted: {e}")
            return {'CANCELLED'}

        self.report({'INFO'}, "HM2 processing complete")
        return {'FINISHED'}

    # -- Internals -------------------------------------------------------------

    def _run(self, context: Context, arm: Object, hm2) -> None:
        self._twist_bone_names = {}
        self._computed_pole_angles = {}

        # Step 0: apply scale, reset location and rotation on the armature object
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        arm.location = (0.0, 0.0, 0.0)
        arm.rotation_euler = (0.0, 0.0, 0.0)
        arm.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)

        # Step 1: enter edit mode, disable x-mirror, disconnect all bones
        bpy.ops.object.mode_set(mode='EDIT')
        arm.data.use_mirror_x = False
        for eb in arm.data.edit_bones:
            eb.use_connect = False

        # Step 2: two-pass rename to fixed names
        self._rename_core_bones(arm, hm2)

        # Step 3: handle spine
        self._setup_spine(arm, hm2)

        # Step 4: rename finger chains
        self._rename_fingers(arm, hm2)

        # Step 4.5: connect bone tails to next bone's head in every chain
        self._connect_chains(arm, hm2)

        # Step 5: remove intermediate limb bones
        self._remove_intermediates(arm, hm2)

        # Step 6: align bone rolls, then create twist bones so they inherit correct rolls
        bpy.ops.object.mode_set(mode='EDIT')
        self._align_bone_rolls(arm, hm2)
        self._create_all_twist_bones(arm, hm2)

        # Step 8: create IK bones
        self._create_ik_bones(arm, hm2)

        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='POSE')

        # Step 9a: unlock all pose bone transforms
        self._unlock_all_bones(arm)

        # Step 9b: add IK constraints + FK controller constraints
        if hm2.hm2_generate_ik:
            self._setup_ik_constraints(arm, hm2)
            self._setup_eye_constraints(arm)
        self._setup_fk_controllers(arm)

        # Step 10: add twist drivers
        self._setup_twist_drivers(arm, hm2)

        # Step 11: custom shapes + colors
        if hm2.hm2_generate_shapes:
            shapes = ensure_hm2_shapes(context)
            self._assign_custom_shapes(arm, hm2, shapes)

        # Step 12: bone colors
        self._assign_bone_colors(arm, hm2)

        # Step 13: bone collections
        bpy.ops.object.mode_set(mode='OBJECT')
        self._organize_collections(arm, hm2)

        # Step 14: optional JSON export config (export names + rotation/location offsets)
        if hm2.hm2_json_filepath.strip():
            self._apply_export_config(arm, hm2)

    # -- Rename helpers --------------------------------------------------------

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
                # Interpolate from prev.head (not prev.tail - it may equal chest.head
                # when the bone was connected, producing a zero-length bone).
                n_remaining = count - i
                new_bone.head = prev.head.lerp(chest_eb.head, 1.0 / (n_remaining + 1)) if prev else Vector((0, 0, 0))
                next_head = chest_eb.head if i == count - 1 else prev.head.lerp(chest_eb.head, 2.0 / (n_remaining + 1))
                new_bone.tail = next_head
                new_bone.parent = prev
                new_bone.use_connect = False
                last_parent = new_bone
            if chest_eb and last_parent:
                chest_eb.parent = last_parent

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

        # Neck / head
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
        eb = arm.data.edit_bones

        # Knee copies hip roll
        for hip_n, knee_n in [('L_Hip', 'L_Knee'), ('R_Hip', 'R_Knee')]:
            hip_eb  = eb.get(hip_n)
            knee_eb = eb.get(knee_n)
            if hip_eb and knee_eb:
                knee_eb.roll = hip_eb.roll

        # Elbow copies shoulder roll
        for sho_n, elb_n in [('L_Shoulder', 'L_Elbow'), ('R_Shoulder', 'R_Elbow')]:
            sho_eb = eb.get(sho_n)
            elb_eb = eb.get(elb_n)
            if sho_eb and elb_eb:
                elb_eb.roll = sho_eb.roll

        # Finger joints 2..N copy the roll of the first joint in their chain
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

        # Spine chain - propagate root roll upward through each bone
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

        # Chest, Neck, Head inherit the last spine bone's roll
        top_spine = eb.get(spine_names[-1]) if spine_names else None
        if top_spine:
            for name in ('M_Chest', 'M_Neck', 'M_Head'):
                b = eb.get(name)
                if b:
                    b.roll = top_spine.roll

        # M_Root: align bone-Z to face -Y (character forward) for a consistent spine orientation
        root_eb = eb.get('M_Root')
        if root_eb:
            root_eb.align_roll(Vector((0, -1, 0)))

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
            """
            Project mid joint onto the limb axis (upper.head -> mid.tail).
            The vector from the projected point to mid.head IS the bend direction -
            this is the correct place for the pole target.
            Returns (pole_pos, bend_dir) or None.
            """
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

        def compute_pole_angle(ik_root_name: str, ik_target_pos: Vector, pole_pos: Vector) -> float:
            """
            Compute the IK pole_angle so the IK solver reproduces the rest pose exactly.

            Formula (from Blender rigging community):
              ik_axis = (ik_target_pos - root.head).normalized()
              u = ik_axis × (pole_pos - root.head)
              v = u × ik_axis   ← projection of pole_dir onto plane ⊥ to ik_axis
              pole_angle = signed_angle(root.x_axis, v, around ik_axis)

            ik_root_name: the FIRST bone in the IK chain (e.g. L_Elbow for a
                          chain_count=2 IK on L_Hand - the root of the 2-bone chain).
            """
            root = eb.get(ik_root_name)
            if not root:
                return 0.0
            ik_axis = ik_target_pos - root.head
            if ik_axis.length < 1e-5:
                return 0.0
            ik_axis.normalize()
            pole_dir = pole_pos - root.head
            u = ik_axis.cross(pole_dir)
            v = u.cross(ik_axis)
            if v.length < 1e-5:
                return 0.0
            x_axis = root.x_axis  # armature-space X axis of the root bone
            v_n = v.normalized()
            x_n = x_axis.normalized()
            dot   = max(-1.0, min(1.0, x_n.dot(v_n)))
            angle = math.acos(dot)
            if x_n.cross(v_n).dot(ik_axis) < 0:
                angle = -angle
            return angle

        # -- CTRL_Ground: absolute master / world-space translation bone ---------
        # Placed at foot level, pointing forward (+Y).  All IK targets are
        # parented here so moving CTRL_Ground translates the whole character
        # including the hands and feet - standard root-motion controller.
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
        _gz = 0.0  # arm.location was reset to (0,0,0) so Z=0 is the world floor
        _gpos    = Vector((_gx, _gy, _gz))
        _g_len   = (_rg.length if _rg else 0.1) * 2.0
        make_ik_bone('CTRL_Ground', _gpos, tail=_gpos + Vector((0, _g_len, 0)))

        # -- Arms --------------------------------------------------------------
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
                    ib.roll = hand.roll  # match roll so COPY_ROTATION is neutral at rest
            # Pole distance = 50% of full arm span, scaled with the character.
            arm_len = (shoulder.head - hand.head).length if (shoulder and hand) else 0.3
            pole_dist = max(arm_len * 0.5, 1e-4)
            result = limb_pole_position(shoulder_n, elbow_n, dist=pole_dist)
            if result is not None:
                pole_pos, bend_dir = result
                bone_len = max(arm_len * 0.12, 1e-4)
                make_ik_bone(ik_elbow, pole_pos, tail=pole_pos + bend_dir * bone_len,
                             parent_name='CTRL_Ground')
                # IK chain root for chain_count=2 on L_Elbow is L_Shoulder
                self._computed_pole_angles[ik_elbow] = compute_pole_angle(
                    shoulder_n, hand.head if hand else pole_pos, pole_pos)

        # -- Legs --------------------------------------------------------------
        for hip_n, knee_n, ankle_n, ik_ankle, ik_knee, side in [
            ('L_Hip', 'L_Knee', 'L_Ankle', 'IK_Ankle_L', 'IK_Knee_L', 'L'),
            ('R_Hip', 'R_Knee', 'R_Ankle', 'IK_Ankle_R', 'IK_Knee_R', 'R'),
        ]:
            ankle  = eb.get(ankle_n)
            hip    = eb.get(hip_n)
            toe_eb = eb.get(f'{side}_Toe')

            if ankle:
                ctrl_toe_name = f'CTRL_Toe_{side}'
                mch_name      = f'MCH_FootRoll_{side}'

                if toe_eb:
                    # CTRL_Toe: world-space ball-of-foot controller, child of
                    # CTRL_Ground.  Moving it positions the foot; rotating X
                    # lifts the heel because IK_Ankle is a descendant of this bone.
                    ct = make_ik_bone(ctrl_toe_name, toe_eb.head, tail=toe_eb.tail,
                                      parent_name='CTRL_Ground')
                    if ct:
                        ct.roll = toe_eb.roll

                    # MCH_FootRoll: hidden pivot child of CTRL_Toe.  It inherits
                    # CTRL_Toe's rotation automatically (no constraint needed) so
                    # the pivot centre is exactly the ball of foot.
                    mch_fwd = (toe_eb.tail - toe_eb.head).normalized()
                    make_ik_bone(mch_name, toe_eb.head.copy(),
                                 tail=toe_eb.head + mch_fwd * ankle.length * 0.3,
                                 parent_name=ctrl_toe_name)

                    # IK_Ankle: child of MCH_FootRoll so it traces an arc around
                    # the ball-of-foot when CTRL_Toe is rotated -> tip-toe.
                    ib = make_ik_bone(ik_ankle, ankle.head, tail=ankle.tail,
                                      parent_name=mch_name)
                else:
                    ib = make_ik_bone(ik_ankle, ankle.head, tail=ankle.tail,
                                      parent_name='CTRL_Ground')

                if ib:
                    ib.roll = ankle.roll
            # Pole distance = 50% of full leg span, scaled with the character.
            leg_len = (hip.head - ankle.head).length if (hip and ankle) else 0.3
            pole_dist = max(leg_len * 0.5, 1e-4)
            result = limb_pole_position(hip_n, knee_n, dist=pole_dist)
            if result is not None:
                pole_pos, bend_dir = result
                bone_len = max(leg_len * 0.12, 1e-4)
                make_ik_bone(ik_knee, pole_pos, tail=pole_pos + bend_dir * bone_len,
                             parent_name='CTRL_Ground')
                # IK chain root for chain_count=2 on L_Knee is L_Hip
                self._computed_pole_angles[ik_knee] = compute_pole_angle(
                    hip_n, ankle.head if ankle else pole_pos, pole_pos)

        # -- Eye roll: align to Global +Z so bone-Z faces up (not downward) ---
        for _en in ('L_Eye', 'R_Eye'):
            _eeb = eb.get(_en)
            if _eeb:
                _eeb.align_roll(Vector((0, 0, 1)))

        # -- Eye target --------------------------------------------------------
        head_bone = eb.get('M_Head')
        if head_bone and (hm2.hm2_map_eye_l or hm2.hm2_map_eye_r):
            eye_l = eb.get('L_Eye')
            eye_r = eb.get('R_Eye')
            if eye_l and eye_r:
                eye_mid = (eye_l.head + eye_r.head) / 2.0
                lr_vec  = eye_r.head - eye_l.head
            elif eye_l or eye_r:
                e       = eye_l or eye_r
                eye_mid = e.head.copy()
                lr_vec  = Vector((1, 0, 0))
            else:
                eye_mid = head_bone.head.copy()
                lr_vec  = Vector((1, 0, 0))

            if lr_vec.length > 1e-5:
                lr_vec.normalize()
                # world-up x LR-axis = character's forward direction
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

            # Per-eye look-at bones: children of IK_EyeTarget, each offset to
            # its eye's lateral (X) position.  This keeps eyes parallel at rest
            # instead of cross-eyed when both track to a single centre point.
            _bone_len = head_bone.length * 0.3
            for _eye_eb, _name in ((eye_l, 'IK_EyeTarget_L'), (eye_r, 'IK_EyeTarget_R')):
                if _eye_eb is None:
                    continue
                _lh = Vector((_eye_eb.head.x, eye_head.y, eye_head.z))
                _lt = _lh + fwd * _bone_len
                make_ik_bone(_name, _lh, tail=_lt, parent_name='IK_EyeTarget')

        # -- Finger curl controllers (rotation traversal) ---------------------
        # One CTRL bone per finger at the first knuckle.  Rotating it on X
        # curls all joints; rotating on Z splays the base joint only.
        _ftype_map = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                      'RING': 'Ring', 'PINKY': 'Pinky'}
        for finger in hm2.hm2_fingers:
            if not finger.generate_ik or not finger.source_bone:
                continue
            base      = _ftype_map.get(finger.finger_type, finger.finger_type)
            start_idx = 0 if finger.finger_type == 'THUMB' else 1
            j1_name   = f"{finger.side}_{base}Finger{start_idx}"
            j1_eb     = eb.get(j1_name)
            if j1_eb:
                ctrl_name = f"CTRL_{base}Finger_{finger.side}"
                cb            = eb.new(ctrl_name)
                cb.head       = j1_eb.head.copy()
                cb.tail       = j1_eb.tail.copy()
                cb.roll       = j1_eb.roll
                cb.use_connect = False
                cb.use_deform  = False
                cb.parent      = j1_eb.parent  # sibling of first joint

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
                # Use auto-computed angle; fall back to user override if missing
                ik.pole_angle = computed.get(pole_key or pole,
                                             hm2.hm2_ik_pole_angle_arm if 'Elbow' in (pole or '')
                                             else hm2.hm2_ik_pole_angle_leg)

        # IK is on the MID bone so its .tail (= next joint head) reaches the target:
        #   L_Elbow.tail = L_Hand.head  -> IK_Hand_L is at L_Hand.head  ✓
        #   L_Knee.tail  = L_Ankle.head -> IK_Ankle_L is at L_Ankle.head ✓
        add_ik('L_Elbow', 'IK_Hand_L',  'IK_Elbow_L', 2, 'IK_Elbow_L')
        add_ik('R_Elbow', 'IK_Hand_R',  'IK_Elbow_R', 2, 'IK_Elbow_R')
        add_ik('L_Knee',  'IK_Ankle_L', 'IK_Knee_L',  2, 'IK_Knee_L')
        add_ik('R_Knee',  'IK_Ankle_R', 'IK_Knee_R',  2, 'IK_Knee_R')

        # -- Finger rotation traversal -----------------------------------------
        _ftype_map2 = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                       'RING': 'Ring', 'PINKY': 'Pinky'}
        for finger in hm2.hm2_fingers:
            if not finger.generate_ik:
                continue
            base      = _ftype_map2.get(finger.finger_type, finger.finger_type)
            start_idx = 0 if finger.finger_type == 'THUMB' else 1
            ctrl_name = f"CTRL_{base}Finger_{finger.side}"
            if not pb.get(ctrl_name):
                continue
            for i in range(finger.joint_count):
                jname = f"{finger.side}_{base}Finger{start_idx + i}"
                jb    = pb.get(jname)
                if not jb:
                    continue
                cr               = jb.constraints.new('COPY_ROTATION')
                cr.target        = arm
                cr.subtarget     = ctrl_name
                cr.owner_space   = 'LOCAL'
                cr.target_space  = 'LOCAL'
                if i == 0:
                    # First joint: full rotation (curl X + splay Z)
                    cr.mix_mode  = 'REPLACE'
                else:
                    # Subsequent joints: only inherit curl (X), not splay
                    cr.mix_mode  = 'ADD'
                    cr.use_x     = True
                    cr.use_y     = False
                    cr.use_z     = False

    def _setup_eye_constraints(self, arm: Object) -> None:
        pb = arm.pose.bones
        if not pb.get('IK_EyeTarget'):
            return
        for eye_name, subtarget in (('L_Eye', 'IK_EyeTarget_L'), ('R_Eye', 'IK_EyeTarget_R')):
            bone = pb.get(eye_name)
            if not bone:
                continue
            # Fall back to centre target if per-eye bone wasn't created
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
        """Add Copy Rotation constraints so FK/IK controllers drive the real bones."""
        pb = arm.pose.bones

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

        # Spine is pure FK - no constraints, each bone rotated directly by the animator.

        # Hand / ankle: WORLD space.  The IK target bones were created with the
        # same roll as the deform bone, so in rest pose the world rotations match
        # and the constraint is neutral.  Rotating the IK target then applies an
        # equal world-space rotation to the deform bone (wrist roll, foot tilt).
        for owner, tgt in [
            ('L_Hand',  'IK_Hand_L'),
            ('R_Hand',  'IK_Hand_R'),
            ('L_Ankle', 'IK_Ankle_L'),
            ('R_Ankle', 'IK_Ankle_R'),
        ]:
            copy_rot(owner, tgt, local=False)

        # Tip-toe pivot lifts IK_Ankle structurally, but L_Toe inherits the
        # pivot rotation via the chain and bends upward - wrong.
        # Fix: ADD the inverse of CTRL_Toe's world rotation to L_Toe so the
        # inherited bend is cancelled and the toe stays flat on the ground.
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
                # LOCAL space: rest-pose local rotation is always 0, so the
                # counter-rotation adds nothing at rest - no floor penetration.
                # On tip-toe, CTRL_Toe local = +θ; inverted = −θ cancels the
                # inherited tilt and keeps the toe flat on the ground.
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
            # Stored target may be a pre-rename name; translate via rename map first.
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

    def _assign_custom_shapes(self, arm: Object, hm2, shapes: dict) -> None:
        pb = arm.pose.bones

        # Reference size = 5% of leg span (hip->ankle).  Keeps shapes proportional
        # to the character regardless of Blender scene scale.
        hip_pb   = pb.get('L_Hip') or pb.get('R_Hip')
        ankle_pb = pb.get('L_Ankle') or pb.get('R_Ankle')
        if hip_pb and ankle_pb:
            ref = max((hip_pb.head - ankle_pb.head).length * 0.05, 1e-5)
        else:
            ref = 0.035  # fallback for a typical 0.7 m leg

        def assign(bone_name: str, shape_key: str, mult: float = 1.0) -> None:
            bone = pb.get(bone_name)
            if bone and shape_key in shapes:
                bone.custom_shape = shapes[shape_key]
                sz = ref * mult
                bone.custom_shape_scale_xyz = (sz, sz, sz)
                bone.use_custom_shape_bone_size = False

        # -- FK spine / root / chest (bone-length scaled boxes) ---------------
        for _sbn in ('M_Root', 'M_Spine', 'M_Spine1', 'M_Spine2', 'M_Spine3',
                     'M_Spine4', 'M_Spine5', 'M_Spine6', 'M_Spine7', 'M_Spine8',
                     'M_Chest'):
            _spb = pb.get(_sbn)
            if _spb and 'box' in shapes:
                _bl = _spb.bone.length
                _spb.custom_shape               = shapes['box']
                _spb.custom_shape_scale_xyz     = (_bl * 0.8, _bl * 0.8, _bl * 0.8)
                _spb.use_custom_shape_bone_size = False
                _spb.custom_shape_translation   = Vector((0, _bl * 0.5, 0))
        assign('M_Neck',  'circle', 1.2)
        assign('M_Head',  'sphere', 1.5)
        _mhd = pb.get('M_Head')
        if _mhd:
            _mhd.custom_shape_translation = Vector((0, _mhd.bone.length * 0.5, 0))
        _mhd = pb.get('M_Neck')
        if _mhd:
            _mhd.custom_shape_translation = Vector((0, _mhd.bone.length * 0.5, 0))

        # -- FK Controllers ----------------------------------------------------
        assign('CTRL_Ground', 'master', 6.0)
        for side in ('L', 'R'):
            assign(f'CTRL_Toe_{side}', 'circle', 0.7)

        # -- IK limb targets and poles (all sphere wireframes) -----------------
        for side in ('L', 'R'):
            assign(f'IK_Hand_{side}',  'sphere', 0.9)
            assign(f'IK_Ankle_{side}', 'sphere', 0.9)
            assign(f'IK_Elbow_{side}', 'sphere', 0.8)
            assign(f'IK_Knee_{side}',  'sphere', 0.8)
            assign(f'{side}_Eye',      'circle', 0.7)

        assign('IK_EyeTarget',   'goggle', 1.5)
        assign('IK_EyeTarget_L', 'sphere', 0.6)
        assign('IK_EyeTarget_R', 'sphere', 0.6)

        # -- Twist rings -------------------------------------------------------
        for names in self._twist_bone_names.values():
            for name in names:
                assign(name, 'circle', 0.8)

        # -- Finger curl controllers --------------------------------------------
        _ftm = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                'RING': 'Ring', 'PINKY': 'Pinky'}
        for finger in hm2.hm2_fingers:
            if finger.generate_ik and 'circle' in shapes:
                base      = _ftm.get(finger.finger_type, finger.finger_type)
                ctrl_name = f"CTRL_{base}Finger_{finger.side}"
                cpb = pb.get(ctrl_name)
                if not cpb:
                    continue
                cpb.custom_shape               = shapes['circle']
                cpb.custom_shape_scale_xyz     = (0.4, 0.4, 0.4)
                cpb.use_custom_shape_bone_size = True
                # Rotate 90° around bone-Y so the circle sits in the YZ plane
                # (disc visible from the side, showing the curl arc)
                cpb.custom_shape_rotation_euler = (0.0, math.radians(90), 0.0)

    def _assign_bone_colors(self, arm: Object, hm2) -> None:
        pb = arm.pose.bones

        def color(bone_name: str, palette: str) -> None:
            b = pb.get(bone_name)
            if b:
                b.color.palette = palette

        # Yellow  - root / world-space movement
        for n in ('CTRL_Ground', 'M_Root'):
            color(n, 'THEME07')

        # Lime-green - FK body controllers (spine, chest, neck, head, scapula, toes)
        for n in ('M_Chest', 'M_Spine', 'M_Spine1', 'M_Spine2', 'M_Spine3',
                  'M_Spine4', 'M_Spine5', 'M_Spine6', 'M_Spine7', 'M_Spine8',
                  'CTRL_Toe_L', 'CTRL_Toe_R'):
            color(n, 'THEME05')

        # Red/orange - IK end-effector targets (hands, feet, finger tips)
        for n in ('IK_Hand_L', 'IK_Hand_R', 'IK_Ankle_L', 'IK_Ankle_R'):
            color(n, 'THEME04')

        # Pink - eye / face control
        for n in ('IK_EyeTarget', 'IK_EyeTarget_L', 'IK_EyeTarget_R'):
            color(n, 'THEME08')

        # Purple - IK pole targets
        for n in ('IK_Elbow_L', 'IK_Elbow_R', 'IK_Knee_L', 'IK_Knee_R'):
            color(n, 'THEME06')

        # Cyan - twist bones
        for names in self._twist_bone_names.values():
            for n in names:
                color(n, 'THEME09')

        # Lime-green - finger curl controllers
        _ftmc = {'THUMB': 'Thumb', 'INDEX': 'Index', 'MIDDLE': 'Middle',
                 'RING': 'Ring', 'PINKY': 'Pinky'}
        for finger in hm2.hm2_fingers:
            if finger.generate_ik:
                base = _ftmc.get(finger.finger_type, finger.finger_type)
                color(f"CTRL_{base}Finger_{finger.side}", 'THEME05')

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
                for side, dir_str in (('L', dir_l), ('R', dir_r)):
                    for i in range(10):
                        joint_name = f"{side}_{base}Finger{joint_start + i}"
                        if not pb.get(joint_name):
                            break
                        num = start + i
                        num_str = '' if (ignore_zero and num == 0) else str(num)
                        _apply(joint_name,
                            name_pat.replace('{dir}', dir_str).replace('{*}', num_str),
                            entry)

    def _unlock_all_bones(self, arm: Object) -> None:
        for pb in arm.pose.bones:
            pb.lock_location = [False, False, False]
            pb.lock_rotation = [False, False, False]
            pb.lock_rotation_w = False
            pb.lock_scale = [False, False, False]

    def _organize_collections(self, arm: Object, hm2) -> None:
        default_coll = ensure_bone_collection(arm, 'Default')
        twist_coll   = ensure_bone_collection(arm, 'Twist',     default_coll)
        finger_coll  = ensure_bone_collection(arm, 'Fingers',   default_coll)
        face_coll    = ensure_bone_collection(arm, 'Face',      default_coll)
        misc_coll    = ensure_bone_collection(arm, 'Misc')
        hair_coll    = ensure_bone_collection(arm, 'Hair',      misc_coll)
        mech_coll    = ensure_bone_collection(arm, 'Mechanism', misc_coll)
        mech_coll.is_visible = False
        # All user-interactive bones go here; this collection is auto-soloed
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
            elif name.startswith('MCH_'):
                target = mech_coll
            elif name in ('IK_EyeTarget_L', 'IK_EyeTarget_R'):
                target = mech_coll   # hidden - only the centre IK_EyeTarget is exposed
            elif name.startswith('IK_') or name.startswith('CTRL_'):
                target = ctrl_coll
            elif name in ('M_Root', 'M_Neck', 'M_Chest') or name.startswith('M_Spine') or name == 'M_Spine':
                target = spine_coll
            elif 'Finger' in name:
                target = finger_coll
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

