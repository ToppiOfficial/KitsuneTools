from bpy.types import Panel, UILayout, Menu

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
    ARMATURE_OT_FitPoseToActive,
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
        layout = self.layout
        layout.use_property_decorate = False
        active_armature = context.active_object
        has_armature = is_armature(active_armature)
        in_pose = context.mode == 'POSE'
        selected_bones = in_pose and has_selected_bones()
        other_armatures = any(
            is_armature(ob) and ob != active_armature
            for ob in context.selected_objects
        )

        if not has_armature:
            layout.label(text='Select an armature', icon='INFO')

        box = layout.box()
        box.label(text='Apply Pose', icon='POSE_HLT')
        col = box.column(align=True)
        col.label(text='As Rest Pose')

        row = col.row(align=True)
        row.scale_y = 1.25
        row.operator(ARMATURE_OT_ApplyPoseAsRestPose.bl_idname, text='All Bones').selected_only = False

        op_col = row.column(align=True)
        op_col.enabled = selected_bones
        op_col.operator(ARMATURE_OT_ApplyPoseAsRestPose.bl_idname, text='Selected Bones').selected_only = True

        col.separator()
        col.operator(ARMATURE_OT_ApplyPoseAsShapekey.bl_idname, icon='SHAPEKEY_DATA', text='Create Pose Shape Key')
        if has_armature:
            if context.mode == 'EDIT_ARMATURE':
                box.label(text='Switch to Object or Pose Mode', icon='INFO')
            elif not selected_bones:
                box.label(text='Select bones in Pose Mode', icon='INFO')

        if has_armature:
            box = layout.box()
            box.label(text='Mirror Pose', icon='MOD_MIRROR')
            col = box.column(align=True)
            col.prop(active_armature.data.kitsunetools, 'x_mirror_pose', text='Auto Mirror on X')
            row = col.row()
            row.use_property_split = True
            row.prop(active_armature.data.kitsunetools, 'x_mirror_tolerance', text='Tolerance')
            col.separator()
            row = col.row(align=True)
            row.enabled = selected_bones
            row.operator(BONE_OT_kitsune_mirror_pose.bl_idname, icon='MOD_MIRROR', text='Mirror Selected Bones')
            if not selected_bones:
                box.label(text='Select bones in Pose Mode', icon='INFO')

        box = layout.box()
        box.label(text='Transfer Bone Data', icon='ARMATURE_DATA')
        col = box.column(align=True)
        row = col.row(align=True)
        row.scale_y = 1.25
        row.operator(ARMATURE_OT_TransferBoneData.bl_idname, text='All Bones').mode = 'ALL'

        op_col = row.column(align=True)
        op_col.enabled = selected_bones
        op_col.operator(ARMATURE_OT_TransferBoneData.bl_idname, text='Selected Bones').mode = 'SELECTED'

        col.operator(ARMATURE_OT_TransferBoneData.bl_idname, icon='GROUP_BONE', text='By Collection').mode = 'COLLECTION'
        if has_armature:
            if not other_armatures:
                box.label(text='Select another armature', icon='INFO')
            else:
                box.label(text='Active armature is the source', icon='INFO')

        box = layout.box()
        box.label(text='Match & Copy Pose', icon='CON_ARMATURE')
        col = box.column(align=True)
        col.label(text='Copy Visual Pose')
        row = col.row(align=True)
        row.scale_y = 1.25
        row.operator(ARMATURE_OT_CopyVisPosture.bl_idname, text='Location').copy_type = 'ORIGIN'
        row.operator(ARMATURE_OT_CopyVisPosture.bl_idname, text='Rotation').copy_type = 'ANGLES'
        row.operator(ARMATURE_OT_CopyVisPosture.bl_idname, text='Scale').copy_type = 'SCALE'
        col.separator()
        col.operator(ARMATURE_OT_FitPoseToActive.bl_idname, icon='CON_ARMATURE', text='Fit Pose to Active')
        col.separator()
        col.operator(ACTION_OT_Make_Proportion_Animation.bl_idname, icon='ACTION_SLOT', text='Create Proportion Actions')
        if has_armature:
            if context.mode != 'OBJECT':
                box.label(text='Switch to Object Mode', icon='INFO')
            elif not other_armatures:
                box.label(text='Select another armature', icon='INFO')
            else:
                box.label(text='Active armature is the reference', icon='INFO')


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
