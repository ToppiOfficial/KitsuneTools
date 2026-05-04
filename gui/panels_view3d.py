from bpy.types import Panel, UILayout, Menu, UIList, Context
from typing import Any

from ..utils.utils_panels import (
    get_label_with_object_name,
    get_label_with_bone_name,
    get_label_with_vertex_group_name
)
from ..utils.utils_object import has_selected_bones, is_armature, is_mesh
from ..op.ops_armature import (
    ARMATURE_OT_ApplyPoseAsRestPose,
    ARMATURE_OT_ApplyPoseAsShapekey, 
    ARMATURE_OT_MergeArmatures,
    ARMATURE_OT_CopyVisPosture,
    ARMATURE_OT_CleanUnWeightedBones,
    ARMATURE_OT_TransferBoneData
)
from ..op.ops_bone import (
    BONE_OT_MergeBones,
    BONE_OT_ReAlignBones,
    BONE_OT_CopyTargetRotation,
    BONE_OT_align_bone_to_axis,
    BONE_OT_SubdivideBone,
    BONE_OT_FlipBone,
    BONE_OT_CreateCenterBone,
    BONE_OT_parent_bone_in_pose,
    BONE_OT_RemoveBone,
    BONE_OT_kitsune_mirror_pose
)
from ..op.ops_mesh import (
    MESH_OT_CleanShapeKeys,
    MESH_OT_RemoveUnusedVertexGroups,
    MESH_OT_Delete_Faces_by_ImageMask,
    MESH_OT_CleanDuplicateMaterials,
    MESH_OT_SelectShapekeyVerts,
    MESH_OT_Select_Faces_by_ImageMask,
    MESH_OT_transfer_topology_shapekeys,
    MESH_OT_convex_hull_selection,
    MESH_OT_replace_verts_with_spheres
)
from ..op.ops_vertexgroup import (
    VERTEXGROUP_OT_WeightMath,
    VERTEXGROUP_OT_SwapVertexGroups,
    VERTEXGROUP_OT_curve_ramp_weights,
    VERTEXGROUP_OT_multi_weight_paint_start,
    VERTEXGROUP_OT_multi_weight_paint_finish,
    VERTEXGROUP_OT_multi_weight_paint_cancel,
    VERTEXGROUP_OT_TransferSelectedGroup,
    VERTEXGROUP_OT_SplitActiveWeightLinear
)
from ..op.ops_action import (
    ACTION_OT_merge_animation_slots,
    ACTION_OT_merge_two_actions,
    ACTION_OT_convert_rotation_keyframes,
    ACTION_OT_propagate_pose_offset,
    ACTION_OT_copy_bone_keyframes,
    ACTION_OT_Make_Proportion_Animation,
    ACTION_OT_delete_action_slot
)
from ..op.ops_humanoidmapper import (
    HUMANOIDMAPPER_OT_CopyToSelected,
    HUMANOIDMAPPER_OT_LoadPreset,
    HUMANOIDMAPPER_OT_LoadConfig,
    HUMANOIDMAPPER_OT_RemoveItem,
    HUMANOIDMAPPER_OT_AddItem,
    HUMANOIDMAPPER_OT_MirrorBoneNames,
    HUMANOIDMAPPER_OT_WriteConfig,
)

class TOOLS_PT_KitsuneTool_Panel(Panel):
    bl_label = ""
    bl_category = 'KitsuneTools'
    bl_region_type = 'UI'
    bl_space_type = 'VIEW_3D'
    bl_options = {'DEFAULT_CLOSED'}


class TOOLS_MT_KitsuneTool_PoseBoneTools(Menu):
    bl_idname = 'TOOLS_MT_KitsuneTool_PoseBoneTools'
    bl_label = 'Pose Bone Tools'

    @classmethod
    def poll(cls, context):
        return bool(context.area and context.area.type == 'VIEW_3D')

    def draw(self, context):
        self.layout.operator(BONE_OT_parent_bone_in_pose.bl_idname, icon='BONE_DATA')
        self.layout.operator(BONE_OT_FlipBone.bl_idname, icon='BONE_DATA')


class TOOLS_PT_KitsuneTool_Armature(TOOLS_PT_KitsuneTool_Panel):
    bl_options = set()

    def draw_header(self, context):
        self.layout.label(text=get_label_with_object_name('Armature', context.active_object, 'ARMATURE'))

    def draw(self, context):
        l = self.layout
        active_armature = context.active_object

        if active_armature is not None and active_armature.type == 'ARMATURE':
            bx = l.box().column(align=True)
            bx.prop(active_armature.data.kitsunetools, 'x_mirror_pose')
            bx.prop(active_armature.data.kitsunetools, 'x_mirror_tolerance')
            bx.separator()
            bx.operator(BONE_OT_kitsune_mirror_pose.bl_idname)

        bx = l.box()

        col = bx.column(align=True)
        col.label(text='Apply Pose As Rest Pose')
        in_pose = context.mode == 'POSE'

        row = col.row(align=True)
        row.scale_y = 1.5
        row.operator(ARMATURE_OT_ApplyPoseAsRestPose.bl_idname, icon='POSE_HLT', text='Entire Armature').selected_only = False

        op_col = row.column(align=True)
        op_col.enabled = has_selected_bones() and in_pose
        op_col.operator(ARMATURE_OT_ApplyPoseAsRestPose.bl_idname, icon='POSE_HLT', text='Selected Bones').selected_only = True
        
        row = col.row(align=True)
        row.enabled = in_pose
        row.operator(ARMATURE_OT_ApplyPoseAsShapekey.bl_idname, icon='SHAPEKEY_DATA')

        sub = col.column(align=True)
        sub.label(text='Transfer Armature Bone Data')

        row = sub.row(align=True)
        row.operator(ARMATURE_OT_TransferBoneData.bl_idname, icon='ARMATURE_DATA', text='All Bones').mode = 'ALL'

        op_col = row.column(align=True)
        op_col.enabled = in_pose
        op_col.operator(ARMATURE_OT_TransferBoneData.bl_idname, icon='BONE_DATA', text='Selected Bones').mode = 'SELECTED'

        sub.operator(ARMATURE_OT_TransferBoneData.bl_idname, icon='GROUP_BONE', text='By Collection').mode = 'COLLECTION'

        col = bx.column(align=True)
        col.operator(ACTION_OT_Make_Proportion_Animation.bl_idname, icon='ACTION_SLOT')
        col.operator(ARMATURE_OT_CopyVisPosture.bl_idname, icon='POSE_HLT', text=f'{ARMATURE_OT_CopyVisPosture.bl_label} (LOCATION)').copy_type = 'ORIGIN'
        col.operator(ARMATURE_OT_CopyVisPosture.bl_idname, icon='POSE_HLT', text=f'{ARMATURE_OT_CopyVisPosture.bl_label} (ROTATION)').copy_type = 'ANGLES'


class TOOLS_PT_KitsuneTool_Bone(TOOLS_PT_KitsuneTool_Panel):
    bl_options = set()

    def draw_header(self, context):
        self.layout.label(text=get_label_with_bone_name('Bone', context.active_bone))

    def draw(self, context):
        layout = self.layout
        kt = context.scene.kitsunetools

        # Bone Merging
        box = layout.box()
        box.label(text='Bone Merging', icon='AUTOMERGE_ON')

        row = box.row(align=True)
        row.scale_y = 1.3
        row.operator(BONE_OT_MergeBones.bl_idname, text='To Active').mode = 'TO_ACTIVE'
        row.operator(BONE_OT_MergeBones.bl_idname, text='To Parent').mode = 'TO_PARENT'

        col = box.column(align=True)
        col.scale_y = 0.9
        split = col.split(align=True)
        split.prop(kt, 'merge_bone_options_active', expand=True)
        split.prop(kt, 'merge_bone_options_parent', expand=True)
        col.prop(kt, 'visible_mesh_only')

        # Bone Alignment
        box = layout.box()
        box.label(text='Bone Alignment', icon='ORIENTATION_VIEW')

        box.operator(BONE_OT_ReAlignBones.bl_idname, icon='ALIGN_JUSTIFY', text='Re-Align Bones')

        row = box.row(align=True)
        row.scale_y = 1.2
        row.operator(BONE_OT_CopyTargetRotation.bl_idname, text='Copy Active').copy_source = 'ACTIVE'
        row.operator(BONE_OT_CopyTargetRotation.bl_idname, text='Copy Parent').copy_source = 'PARENT'

        col = box.column(align=True)
        col.label(text='Point to Axis (Edit):')
        row = col.row(align=True)
        row.scale_y = 1.2
        for axis in ('X', 'Y', 'Z', '-X', '-Y', '-Z'):
            row.operator(BONE_OT_align_bone_to_axis.bl_idname, text=axis).axis = axis


class TOOLS_PT_KitsuneTool_VertexGroup(TOOLS_PT_KitsuneTool_Panel):
    def draw_header(self, context):
        active_object = context.active_object
        self.layout.label(text=get_label_with_vertex_group_name('Vertex Group', active_object.vertex_groups.active if is_mesh(active_object) else None))
    
    def draw(self, context) -> None:
        layout = self.layout
        ob  = context.active_object

        if not is_mesh(ob) and not is_armature(ob):
            layout.box().label(text='Select Mesh or Armature',icon='HELP')
            return

        bx = layout.box()
            
        def draw_multi_ob_weightmode(col : UILayout):
            col2 = col.column()
            col2.scale_y = 1.5
            if ob.get("is_temp_weight_paint"):
                col2.operator(VERTEXGROUP_OT_multi_weight_paint_finish.bl_idname)
                col2.operator(VERTEXGROUP_OT_multi_weight_paint_cancel.bl_idname)
            else:
                col2.operator(VERTEXGROUP_OT_multi_weight_paint_start.bl_idname)
        
        col = bx.column(align=True)
        
        draw_multi_ob_weightmode(col)
        
        col.operator(VERTEXGROUP_OT_WeightMath.bl_idname, icon='LINENUMBERS_ON')
        col.operator(VERTEXGROUP_OT_SwapVertexGroups.bl_idname,icon='AREA_SWAP')
        col.operator(BONE_OT_SubdivideBone.bl_idname, icon='MOD_SUBSURF', text=BONE_OT_SubdivideBone.bl_label + " (Weights Only)").weights_only = True
        col.prop(context.scene.kitsunetools, 'visible_mesh_only')
        
        if context.active_object.mode == 'WEIGHT_PAINT':
            col = bx.column(align=True)
            tool_settings = context.tool_settings
            brush = tool_settings.weight_paint.brush
            
            col.operator(VERTEXGROUP_OT_curve_ramp_weights.bl_idname)
            row = col.row(align=True)
                
            col.template_curve_mapping(brush, "curve", brush=False)
            row = col.row(align=True)
            row.operator("brush.curve_preset", icon='SMOOTHCURVE', text="").shape = 'SMOOTH'
            row.operator("brush.curve_preset", icon='SPHERECURVE', text="").shape = 'ROUND'
            row.operator("brush.curve_preset", icon='ROOTCURVE', text="").shape = 'ROOT'
            row.operator("brush.curve_preset", icon='SHARPCURVE', text="").shape = 'SHARP'
            row.operator("brush.curve_preset", icon='LINCURVE', text="").shape = 'LINE'
            row.operator("brush.curve_preset", icon='NOCURVE', text="").shape = 'MAX'


class HUMANOIDMAPPER_UL_ConfigList(UIList):
    def draw_item(self, context: Context, layout: UILayout, data: Any | None, item: Any | None, icon: int | None, active_data: Any, active_property: str | None, index: int | None, flt_flag: int | None) -> None:
        if item:
            row = layout.row()
            split = row.split(factor=0.9)
            split.prop_search(item, "boneExportName", context.active_object.data, "bones", text="")
            split.label(text="", )
            row.operator(HUMANOIDMAPPER_OT_RemoveItem.bl_idname, text="", icon="X").index = index


class TOOLS_PT_KitsuneTool_Humanoidmapper(TOOLS_PT_KitsuneTool_Panel):
    def draw_header(self, context):
        self.layout.label(text=get_label_with_object_name('Humanoid Armature Mapper', context.active_object, 'Humanoid Armature Mapper'))

    def draw(self, context : Context) -> None:
        layout = self.layout
        bx = layout.box()

        ob  = context.active_object
        if is_armature(ob): pass
        else:
            bx.label(text='Select Armature',icon='HELP')
            return

        col = bx.column()
        row = bx.row(align=True)
        row.prop(context.scene.kitsunetools, 'humanoid_armature_map_menu', expand=True)

        if context.scene.kitsunetools.humanoid_armature_map_menu == 'WRITE':
            self.draw_write_mode(context, bx)
        else:
            self.draw_read_mode(context, bx)

    def draw_write_mode(self, context : Context, layout : UILayout) -> None:
        col = layout.column()
        col.operator(HUMANOIDMAPPER_OT_LoadPreset.bl_idname)

        col = layout.column(align=False)
        row = layout.row()
        row.template_list(
            "HUMANOIDMAPPER_UL_ConfigList",
            "",
            context.active_object.kitsunetools,
            "humanoid_armature_map_bonecollections",
            context.active_object.kitsunetools,
            "humanoid_armature_map_bonecollections_index",
            rows=3
        )
        row = layout.row()
        row.scale_y = 1.25
        split = row.split(factor=0.4,align=True)
        split.operator(HUMANOIDMAPPER_OT_AddItem.bl_idname, icon="ADD", text=HUMANOIDMAPPER_OT_AddItem.bl_label).add_type = 'SINGLE'
        split.operator(HUMANOIDMAPPER_OT_AddItem.bl_idname, icon="ADD", text=HUMANOIDMAPPER_OT_AddItem.bl_label + " (Selected Bones)").add_type = 'SELECTED'

        if 0 <= context.active_object.kitsunetools.humanoid_armature_map_bonecollections_index < len(context.active_object.kitsunetools.humanoid_armature_map_bonecollections):
            self.draw_bone_item_properties(context, layout)

        layout.operator(HUMANOIDMAPPER_OT_WriteConfig.bl_idname, icon='FILE')

    def draw_bone_item_properties(self, context : Context, layout : UILayout) -> None:
        item = context.active_object.kitsunetools.humanoid_armature_map_bonecollections[context.active_object.kitsunetools.humanoid_armature_map_bonecollections_index]

        col = layout.column(align=True)
        col.prop(item, "boneExportName")
        col.alert = not bool(item.boneName.strip())
        col.prop(item, "boneName")
        col.alert = False
        col.prop(item, "parentBone")
        col.row().prop(item, "writeRotation", expand=True)
        col.prop(item, "writeExportRotationOffset")
        col.prop(item, "writeTwistBone")
        if item.writeTwistBone:
            col.prop(item, "twistBoneTarget")
            col.prop(item, "twistBoneCount", slider=True)

    def get_bone_assignments(self, context : Context) -> dict:
        assignments = {}
        kitsunetools = context.active_object.kitsunetools
        
        bone_props = [
            ('armature_map_head', 'Head'),
            ('armature_map_chest', 'Chest'),
            ('armature_map_spine', 'Spine'),
            ('armature_map_pelvis', 'Pelvis'),
            ('armature_map_eye_l', 'Eye L'),
            ('armature_map_eye_r', 'Eye R'),
            ('armature_map_thigh_l', 'Thigh L'),
            ('armature_map_thigh_r', 'Thigh R'),
            ('armature_map_knee_l', 'Knee L'),
            ('armature_map_knee_r', 'Knee R'),
            ('armature_map_ankle_l', 'Ankle L'),
            ('armature_map_ankle_r', 'Ankle R'),
            ('armature_map_toe_l', 'Toe L'),
            ('armature_map_toe_r', 'Toe R'),
            ('armature_map_shoulder_l', 'Shoulder L'),
            ('armature_map_shoulder_r', 'Shoulder R'),
            ('armature_map_upperarm_l', 'UpperArm L'),
            ('armature_map_upperarm_r', 'UpperArm R'),
            ('armature_map_forearm_l', 'ForeArm L'),
            ('armature_map_forearm_r', 'ForeArm R'),
            ('armature_map_wrist_l', 'Wrist L'),
            ('armature_map_wrist_r', 'Wrist R'),
            ('armature_map_thumb_f_l', 'Thumb L'),
            ('armature_map_thumb_f_r', 'Thumb R'),
            ('armature_map_index_f_l', 'Index L'),
            ('armature_map_index_f_r', 'Index R'),
            ('armature_map_middle_f_l', 'Middle L'),
            ('armature_map_middle_f_r', 'Middle R'),
            ('armature_map_ring_f_l', 'Ring L'),
            ('armature_map_ring_f_r', 'Ring R'),
            ('armature_map_pinky_f_l', 'Pinky L'),
            ('armature_map_pinky_f_r', 'Pinky R'),
        ]
        
        for prop, label in bone_props:
            bone_name = getattr(kitsunetools, prop, "").strip()
            if bone_name:
                if bone_name not in assignments:
                    assignments[bone_name] = []
                assignments[bone_name].append(label)
        
        return assignments

    def draw_read_mode(self, context : Context, layout : UILayout) -> None:
        col = layout.column(align=False)
        
        bone_assignments = self.get_bone_assignments(context)
        duplicates = {bone: labels for bone, labels in bone_assignments.items() if len(labels) > 1}
        
        if duplicates:
            duplicate_messages = []
            for bone, labels in duplicates.items():
                duplicate_messages.append(f"'{bone}' assigned to: {', '.join(labels)}")
            
            err_box = layout.box()
            err_box_col = err_box.column(align=True)
            err_box_col.alert = True
            err_box_col.label(text='Duplicate Bone Assignments Detected!', icon='ERROR')
            for err_msg in duplicate_messages:
                err_box_col.label(text=err_msg)
        
        self.draw_humanoid_bone_mapping(context, layout, duplicates)

        layout.operator(HUMANOIDMAPPER_OT_MirrorBoneNames.bl_idname, icon='MOD_MIRROR')
        layout.operator(HUMANOIDMAPPER_OT_LoadConfig.bl_idname)

    def draw_humanoid_bone_mapping(self, context : Context, layout : UILayout, duplicates : dict) -> None:
        col = layout.column(align=True)
        
        bx = col.box()
        col = bx.column()

        col.label(text='Head, Chest and Pelvis are required', icon='HELP')
        
        col = bx.column(align=True)
        self.draw_bone_prop(col, context, 'armature_map_head', "Head", duplicates)
        self.draw_bone_prop(col, context, 'armature_map_chest', "Chest", duplicates)
        self.draw_bone_prop(col, context, 'armature_map_spine', "Spine", duplicates)
        self.draw_bone_prop(col, context, 'armature_map_pelvis', "Pelvis", duplicates)

        col.separator()
        col.separator(type='LINE')
        col.separator()

        self.draw_bone_pair(col, context, 'Eye', 'armature_map_eye_l', 'armature_map_eye_r', duplicates)

        col.separator()
        col.separator(type='LINE')
        col.separator()

        col.label(text="Legs:")
        self.draw_bone_pair(col, context, 'Thigh', 'armature_map_thigh_l', 'armature_map_thigh_r', duplicates)
        self.draw_bone_pair(col, context, 'Knee', 'armature_map_knee_l', 'armature_map_knee_r', duplicates)
        self.draw_bone_pair(col, context, 'Ankle', 'armature_map_ankle_l', 'armature_map_ankle_r', duplicates)
        self.draw_bone_pair(col, context, 'Toe', 'armature_map_toe_l', 'armature_map_toe_r', duplicates)

        col.separator()
        col.separator(type='LINE')
        col.separator()

        col.label(text="Arms:")
        self.draw_bone_pair(col, context, 'Shoulder', 'armature_map_shoulder_l', 'armature_map_shoulder_r', duplicates)
        self.draw_bone_pair(col, context, 'UpperArm', 'armature_map_upperarm_l', 'armature_map_upperarm_r', duplicates)
        self.draw_bone_pair(col, context, 'ForeArm', 'armature_map_forearm_l', 'armature_map_forearm_r', duplicates)
        self.draw_bone_pair(col, context, 'Wrist', 'armature_map_wrist_l', 'armature_map_wrist_r', duplicates)

        col.separator()
        col.separator(type='LINE')
        col.separator()

        col.label(text="Fingers:")
        self.draw_bone_pair(col, context, 'Thumb', 'armature_map_thumb_f_l', 'armature_map_thumb_f_r', duplicates)
        self.draw_bone_pair(col, context, 'Index', 'armature_map_index_f_l', 'armature_map_index_f_r', duplicates)
        self.draw_bone_pair(col, context, 'Middle', 'armature_map_middle_f_l', 'armature_map_middle_f_r', duplicates)
        self.draw_bone_pair(col, context, 'Ring', 'armature_map_ring_f_l', 'armature_map_ring_f_r', duplicates)
        self.draw_bone_pair(col, context, 'Pinky', 'armature_map_pinky_f_l', 'armature_map_pinky_f_r', duplicates)

    def draw_bone_prop(self, layout : UILayout, context : Context, prop : str, text : str, duplicates : dict) -> None:
        bone_name = getattr(context.active_object.kitsunetools, prop, "").strip()
        layout.alert = bone_name in duplicates
        layout.prop_search(context.active_object.kitsunetools, prop, context.active_object.data, "bones", text=text)
        layout.alert = False

    def draw_bone_pair(self, layout : UILayout, context : Context, label : str, prop_l : str, prop_r : str, duplicates : dict = None) -> None:
        if duplicates is None:
            duplicates = {}
        
        row = layout.row(align=True)
        row.scale_x = 0.2
        row.label(text=f'{label} L & R')
        
        bone_l = getattr(context.active_object.kitsunetools, prop_l, "").strip()
        bone_r = getattr(context.active_object.kitsunetools, prop_r, "").strip()
        
        row.alert = bone_l in duplicates
        row.prop_search(context.active_object.kitsunetools, prop_l, context.active_object.data, "bones", text="")
        row.alert = bone_r in duplicates
        row.prop_search(context.active_object.kitsunetools, prop_r, context.active_object.data, "bones", text="")
        row.alert = False
