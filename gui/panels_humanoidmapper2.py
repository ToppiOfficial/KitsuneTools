from bpy.types import Panel, UIList, UILayout, Context
from typing import Any

from ..utils.utils_object import is_armature
from ..op.ops_humanoidmapper2 import (
    HM2_OT_AddFinger,
    HM2_OT_RemoveFinger,
    HM2_OT_MirrorFingers,
    HM2_OT_MirrorBodyMapping,
    HM2_OT_CopyMappingToSelected,
    HM2_OT_ValidateMapping,
    HM2_OT_Process,
    HM2_OT_JsonFormatHelp,
    HM2_OT_FirstPersonArms,
    HM2_OT_AddPuppet,
    HM2_OT_RemovePuppet,
    HM2_OT_ProcessPuppet,
    HM2_OT_DisconnectPuppet,
    HM2_OT_SyncPuppetExportConfig,
)


class HM2_UL_PuppetList(UIList):
    def draw_item(self, context: Context, layout: UILayout, data: Any, item: Any,
                  icon: int, active_data: Any, active_property: str,
                  index: int, flt_flag: int) -> None:
        arm_obj = item.armature
        if arm_obj:
            is_proc = arm_obj.kitsunetools.hm2.hm2_is_puppet
            row = layout.row(align=True)
            row.label(text=arm_obj.name, icon='ARMATURE_DATA')
            sub = row.row(align=True)
            sub.scale_x = 0.75
            sub.prop(item, "mode", text="")
            row.label(
                text="Puppet" if is_proc else "Pending",
                icon='CHECKMARK' if is_proc else 'QUESTION',
            )
        else:
            layout.label(text="(None)", icon='ERROR')


class HM2_UL_FingerList(UIList):
    def draw_item(self, context: Context, layout: UILayout, data: Any, item: Any,
                  icon: int, active_data: Any, active_property: str,
                  index: int, flt_flag: int) -> None:
        if not item:
            return
        row = layout.row(align=True)
        split = row.split(factor=0.45)
        split.prop_search(item, "source_bone", context.active_object.data, "bones", text="")
        sub = split.row(align=True)
        sub.prop(item, "finger_type", text="")
        sub.prop(item, "side", text="")
        sub.prop(item, "joint_count", text="")
        remove_op = row.operator(HM2_OT_RemoveFinger.bl_idname, text="", icon="X")
        remove_op.index = index


class TOOLS_PT_KitsuneTool_HumanoidMapping(Panel):
    bl_idname = 'TOOLS_PT_KitsuneTool_HumanoidMapping'
    bl_label = 'Humanoid Mapping'
    bl_category = 'KitsuneTools'
    bl_region_type = 'UI'
    bl_space_type = 'VIEW_3D'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return is_armature(context.active_object)

    def draw(self, context: Context) -> None:
        pass


class _HM2PanelBase(Panel):
    bl_category = 'KitsuneTools'
    bl_region_type = 'UI'
    bl_space_type = 'VIEW_3D'


def _draw_bone_pair(layout: UILayout, data, prop_l: str, prop_r: str, label: str, arm) -> None:
    split = layout.split(factor=0.15)
    split.label(text=label)
    row = split.row(align=True)
    row.prop_search(data, prop_l, arm.data, "bones", text="L")
    row.prop_search(data, prop_r, arm.data, "bones", text="R")


def _draw_bone_single(layout: UILayout, data, prop: str, label: str, arm) -> None:
    layout.prop_search(data, prop, arm.data, "bones", text=label)


class TOOLS_PT_KitsuneTool_HM2(_HM2PanelBase):
    bl_idname = 'TOOLS_PT_KitsuneTool_HM2'
    bl_label = 'Humanoid Mapper 2'
    bl_parent_id = 'TOOLS_PT_KitsuneTool_HumanoidMapping'

    def draw(self, context: Context) -> None:
        pass


class TOOLS_PT_KitsuneTool_HM2_Core(_HM2PanelBase):
    bl_idname = 'TOOLS_PT_KitsuneTool_HM2_Core'
    bl_label = 'Core Bones'
    bl_parent_id = 'TOOLS_PT_KitsuneTool_HM2'

    def draw(self, context: Context) -> None:
        layout = self.layout
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2

        layout.prop(hm2, 'hm2_first_person_mode', icon='VIEW_CAMERA')

        if hm2.hm2_first_person_mode:
            col = layout.column(align=True)
            col.label(text="Arms-only rig. Assign arms below.", icon='INFO')
            col.separator()
            col.label(text="Optional:")
            _draw_bone_single(col, hm2, 'hm2_map_root', 'Root (auto if empty)', arm)
            return

        col = layout.column(align=True)
        col.label(text="Required:", icon='ERROR')
        _draw_bone_single(col, hm2, 'hm2_map_root',  'Root',  arm)
        _draw_bone_single(col, hm2, 'hm2_map_chest', 'Chest', arm)
        _draw_bone_single(col, hm2, 'hm2_map_neck',  'Neck',  arm)
        _draw_bone_single(col, hm2, 'hm2_map_head',  'Head',  arm)

        col.separator()
        col.prop(hm2, 'hm2_spine_count', text="Spine Count")

        col.separator()
        col.label(text="Optional:")
        _draw_bone_pair(col, hm2, 'hm2_map_eye_l', 'hm2_map_eye_r', 'Eyes', arm)
        col.operator(HM2_OT_MirrorBodyMapping.bl_idname, icon='MOD_MIRROR', text="Mirror Eyes").scope = 'EYES'


class TOOLS_PT_KitsuneTool_HM2_Arms(_HM2PanelBase):
    bl_idname = 'TOOLS_PT_KitsuneTool_HM2_Arms'
    bl_label = 'Arms'
    bl_parent_id = 'TOOLS_PT_KitsuneTool_HM2'

    def draw(self, context: Context) -> None:
        layout = self.layout
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2
        col = layout.column(align=True)
        if hm2.hm2_first_person_mode:
            col.label(text="Required (Scapula optional):", icon='ERROR')
        _draw_bone_pair(col, hm2, 'hm2_map_scapula_l',  'hm2_map_scapula_r',  'Scapula',  arm)
        _draw_bone_pair(col, hm2, 'hm2_map_shoulder_l', 'hm2_map_shoulder_r', 'Shoulder', arm)
        _draw_bone_pair(col, hm2, 'hm2_map_elbow_l',    'hm2_map_elbow_r',    'Elbow',    arm)
        _draw_bone_pair(col, hm2, 'hm2_map_hand_l',     'hm2_map_hand_r',     'Hand',     arm)
        col.separator()
        col.operator(HM2_OT_MirrorBodyMapping.bl_idname, icon='MOD_MIRROR', text="Mirror Arms").scope = 'ARMS'


class TOOLS_PT_KitsuneTool_HM2_Legs(_HM2PanelBase):
    bl_idname = 'TOOLS_PT_KitsuneTool_HM2_Legs'
    bl_label = 'Legs'
    bl_parent_id = 'TOOLS_PT_KitsuneTool_HM2'

    def draw(self, context: Context) -> None:
        layout = self.layout
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2
        if hm2.hm2_first_person_mode:
            layout.label(text="Ignored in First Person mode", icon='INFO')
            return
        col = layout.column(align=True)
        _draw_bone_pair(col, hm2, 'hm2_map_hip_l',   'hm2_map_hip_r',   'Hip',   arm)
        _draw_bone_pair(col, hm2, 'hm2_map_knee_l',  'hm2_map_knee_r',  'Knee',  arm)
        _draw_bone_pair(col, hm2, 'hm2_map_ankle_l', 'hm2_map_ankle_r', 'Ankle', arm)
        _draw_bone_pair(col, hm2, 'hm2_map_toe_l',   'hm2_map_toe_r',   'Toe',   arm)
        col.separator()
        col.operator(HM2_OT_MirrorBodyMapping.bl_idname, icon='MOD_MIRROR', text="Mirror Legs").scope = 'LEGS'


class TOOLS_PT_KitsuneTool_HM2_Fingers(_HM2PanelBase):
    bl_idname = 'TOOLS_PT_KitsuneTool_HM2_Fingers'
    bl_label = 'Fingers'
    bl_parent_id = 'TOOLS_PT_KitsuneTool_HM2'

    def draw(self, context: Context) -> None:
        layout = self.layout
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2

        row = layout.row()
        row.template_list(
            "HM2_UL_FingerList", "",
            hm2, "hm2_fingers",
            hm2, "hm2_fingers_index",
            rows=4
        )

        col = layout.column(align=True)
        col.operator(HM2_OT_AddFinger.bl_idname, icon='ADD', text="Add Finger")
        col.operator(HM2_OT_MirrorFingers.bl_idname, icon='MOD_MIRROR', text="Mirror L ↔ R")

        idx = hm2.hm2_fingers_index
        if 0 <= idx < len(hm2.hm2_fingers):
            item = hm2.hm2_fingers[idx]
            box = layout.box()
            box.label(text="Selected Finger:", icon='BONE_DATA')
            box.prop_search(item, "source_bone", arm.data, "bones", text="Start Bone")
            box.prop(item, "finger_type")
            box.prop(item, "side")
            box.prop(item, "joint_count")
            box.prop(item, "generate_ik")


class TOOLS_PT_KitsuneTool_HM2_Twist(_HM2PanelBase):
    bl_idname = 'TOOLS_PT_KitsuneTool_HM2_Twist'
    bl_label = 'Twist Bones'
    bl_parent_id = 'TOOLS_PT_KitsuneTool_HM2'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context: Context) -> None:
        layout = self.layout
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2

        def draw_joint_twist(joint_label: str, count_prop: str,
                             target_l_prop: str, mode_l_prop: str,
                             target_r_prop: str, mode_r_prop: str):
            box = layout.box()
            row = box.row()
            row.label(text=joint_label)
            row.prop(hm2, count_prop, text="Count")

            col = box.column(align=True)
            row = col.row(align=True)
            row.label(text="L:", icon='BLANK1')
            row.prop_search(hm2, target_l_prop, arm.data, "bones", text="")
            row.prop(hm2, mode_l_prop, text="")

            row = col.row(align=True)
            row.label(text="R:", icon='BLANK1')
            row.prop_search(hm2, target_r_prop, arm.data, "bones", text="")
            row.prop(hm2, mode_r_prop, text="")

        draw_joint_twist(
            "Shoulder", 'hm2_twist_shoulder',
            'hm2_twist_shoulder_target_l', 'hm2_twist_shoulder_mode_l',
            'hm2_twist_shoulder_target_r', 'hm2_twist_shoulder_mode_r',
        )
        draw_joint_twist(
            "Elbow", 'hm2_twist_elbow',
            'hm2_twist_elbow_target_l', 'hm2_twist_elbow_mode_l',
            'hm2_twist_elbow_target_r', 'hm2_twist_elbow_mode_r',
        )
        draw_joint_twist(
            "Hip", 'hm2_twist_hip',
            'hm2_twist_hip_target_l', 'hm2_twist_hip_mode_l',
            'hm2_twist_hip_target_r', 'hm2_twist_hip_mode_r',
        )
        draw_joint_twist(
            "Knee", 'hm2_twist_knee',
            'hm2_twist_knee_target_l', 'hm2_twist_knee_mode_l',
            'hm2_twist_knee_target_r', 'hm2_twist_knee_mode_r',
        )


class TOOLS_PT_KitsuneTool_HM2_IK(_HM2PanelBase):
    bl_idname = 'TOOLS_PT_KitsuneTool_HM2_IK'
    bl_label = 'IK Options'
    bl_parent_id = 'TOOLS_PT_KitsuneTool_HM2'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context: Context) -> None:
        layout = self.layout
        hm2 = context.active_object.kitsunetools.hm2
        col = layout.column(align=True)
        col.prop(hm2, 'hm2_generate_ik')
        col.prop(hm2, 'hm2_generate_shapes')

        col.separator()
        col.label(text="Bone Roll:")
        col.prop(hm2, 'hm2_legacy_roll')

        col.separator()
        col.label(text="Pole Angles:")
        col.prop(hm2, 'hm2_ik_pole_angle_arm', text="Arms")
        col.prop(hm2, 'hm2_ik_pole_angle_leg', text="Legs")


class TOOLS_PT_KitsuneTool_HM2_Export(_HM2PanelBase):
    bl_idname = 'TOOLS_PT_KitsuneTool_HM2_Export'
    bl_label = 'Export'
    bl_parent_id = 'TOOLS_PT_KitsuneTool_HM2'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context: Context) -> None:
        layout = self.layout
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2
        col = layout.column(align=True)
        col.label(text="Optional JSON (export names):")
        row = col.row(align=True)
        row.prop(hm2, 'hm2_json_filepath', text="")
        row.operator(HM2_OT_JsonFormatHelp.bl_idname, text="", icon='QUESTION')



class TOOLS_PT_KitsuneTool_HM2_FirstPersonArms(_HM2PanelBase):
    bl_idname = 'TOOLS_PT_KitsuneTool_HM2_FirstPersonArms'
    bl_label = 'First Person Arms'
    bl_parent_id = 'TOOLS_PT_KitsuneTool_HumanoidMapping'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context: Context) -> None:
        layout = self.layout
        arm = context.active_object
        hm2 = arm.kitsunetools.hm2

        col = layout.column(align=True)
        _draw_bone_pair(col, hm2, 'fpa_starting_bone_l', 'fpa_starting_bone_r', 'Start', arm)

        col.separator()
        col.prop(hm2, 'fpa_rig_type')
        col.prop(hm2, 'fpa_preserve_ik')
        col.prop(hm2, 'fpa_weight_threshold')

        col.separator()
        col.prop(hm2, 'fpa_use_bisect')
        if hm2.fpa_use_bisect:
            row = col.row(align=True)
            row.prop(hm2, 'fpa_bisect_axis', text="")
            row.prop(hm2, 'fpa_bisect_offset', text="Offset")

        col.separator()
        run = col.row()
        run.scale_y = 1.5
        run.operator(HM2_OT_FirstPersonArms.bl_idname, icon='MOD_ARRAY', text="Create First Person Arms")


class TOOLS_PT_KitsuneTool_HM2_Actions(_HM2PanelBase):
    bl_idname = 'TOOLS_PT_KitsuneTool_HM2_Actions'
    bl_label = 'Run'
    bl_parent_id = 'TOOLS_PT_KitsuneTool_HM2'

    def draw(self, context: Context) -> None:
        layout = self.layout
        col = layout.column(align=True)
        col.operator(HM2_OT_ValidateMapping.bl_idname, icon='CHECKMARK', text="Validate Mapping")
        col.separator()

        row = col.row()
        row.scale_y = 2.0
        row.operator(HM2_OT_Process.bl_idname, icon='ARMATURE_DATA', text="Run HM2 Setup")

        col.separator()
        has_multi = any(
            o != context.active_object and is_armature(o)
            for o in context.selected_objects
        )
        copy_row = col.row()
        copy_row.enabled = has_multi
        copy_row.operator(HM2_OT_CopyMappingToSelected.bl_idname, icon='COPYDOWN')


class TOOLS_PT_KitsuneTool_HM2_Puppets(_HM2PanelBase):
    bl_idname = 'TOOLS_PT_KitsuneTool_HM2_Puppets'
    bl_label = 'Puppet Armatures'
    bl_parent_id = 'TOOLS_PT_KitsuneTool_HM2'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return is_armature(context.active_object)

    def draw(self, context: Context) -> None:
        layout = self.layout
        obj = context.active_object
        hm2 = obj.kitsunetools.hm2

        if hm2.hm2_is_puppet:
            col = layout.column(align=True)
            master = hm2.hm2_puppet_master
            if master:
                col.label(text=f"Puppet of: {master.name}", icon='ARMATURE_DATA')
            else:
                col.label(text="Puppet (master reference lost)", icon='ERROR')
            col.separator()
            col.operator(HM2_OT_DisconnectPuppet.bl_idname, icon='UNLINKED')
            return

        row = layout.row()
        row.template_list(
            "HM2_UL_PuppetList", "",
            hm2, "hm2_puppets",
            hm2, "hm2_puppets_index",
            rows=3,
        )
        col = row.column(align=True)
        col.operator(HM2_OT_AddPuppet.bl_idname, icon='ADD', text="")
        col.operator(HM2_OT_RemovePuppet.bl_idname, icon='REMOVE', text="")

        idx = hm2.hm2_puppets_index
        if 0 <= idx < len(hm2.hm2_puppets):
            puppet_arm = hm2.hm2_puppets[idx].armature
            if puppet_arm:
                is_processed = puppet_arm.kitsunetools.hm2.hm2_is_puppet
                action_row = layout.row()
                action_row.scale_y = 1.5
                if is_processed:
                    action_row.operator(
                        HM2_OT_ProcessPuppet.bl_idname,
                        text="Re-apply Mode", icon='FILE_REFRESH')
                else:
                    action_row.operator(
                        HM2_OT_ProcessPuppet.bl_idname, icon='PLAY')
            else:
                layout.label(text="(No armature set in entry)", icon='ERROR')

        has_active_puppets = any(
            e.armature and e.armature.kitsunetools.hm2.hm2_is_puppet
            for e in hm2.hm2_puppets
        )
        if HM2_OT_Process._is_hm2_applied(obj) and has_active_puppets:
            layout.separator()
            layout.operator(HM2_OT_SyncPuppetExportConfig.bl_idname, icon='LINKED')
