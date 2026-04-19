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
    ARMATURE_OT_CleanUnWeightedBones,
    ARMATURE_OT_TransferBoneData
)
from ..op.ops_bone import (
    BONE_OT_MergeBones,
    BONE_OT_ReAlignBones,
    BONE_OT_CopyTargetRotation,
    BONE_OT_align_bone_to_axis,
    BONE_OT_SubdivideBone,
    BONE_OT_mirror_by_position,
    BONE_OT_FlipBone,
    BONE_OT_CreateCenterBone,
    BONE_OT_SplitActiveWeightLinear,
    BONE_OT_parent_bone_in_pose,
    BONE_OT_RemoveBone,
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
    VERTEXGROUP_OT_TransferSelectedGroup
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
        self.layout.operator(BONE_OT_mirror_by_position.bl_idname, icon='MOD_MIRROR')


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

        # Bone Modifiers
        box = layout.box()
        box.label(text='Bone Modifiers', icon='MODIFIER')

        col = box.column(align=True)
        col.operator(BONE_OT_SplitActiveWeightLinear.bl_idname, icon='SPLIT_VERTICAL')


class TOOLS_PT_KitsuneTool_Mesh(TOOLS_PT_KitsuneTool_Panel):
    def draw_header(self, context):
        self.layout.label(text=get_label_with_object_name('Mesh', context.active_object, 'MESH'))

    def draw(self, context) -> None:
        layout = self.layout


class TOOLS_PT_KitsuneTool_VertexGroup(TOOLS_PT_KitsuneTool_Panel):
    def draw_header(self, context):
        active_object = context.active_object
        self.layout.label(text=get_label_with_vertex_group_name('Vertex Group', active_object.vertex_groups.active if is_mesh(active_object) else None))
    
    def draw(self, context) -> None:
        layout = self.layout
        ob  = context.active_object

        if not is_mesh(ob) and not is_armature(ob): return

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


class TOOLS_PT_KitsuneTool_Animation(TOOLS_PT_KitsuneTool_Panel):
    bl_label = "Animation"
    
    def draw(self, context) -> None:
        layout = self.layout