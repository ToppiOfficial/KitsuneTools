import bpy, importlib, sys
from bpy.types import PropertyGroup, Material
from bpy.props import EnumProperty, BoolProperty, StringProperty, IntProperty, CollectionProperty

from .gui import (
    panels_view3d,
    panels_nodeeditor,
)
from .op import (
    ops_armature,
    ops_bone,
    ops_nodeeditor,
    ops_vertexgroup,
    ops_mesh,
    ops_action
)
from .utils import (
    utils_armature,
    utils_contextmanagers,
    utils_panels,
    utils_object,
    utils_vertexgroup,
    utils_bone,
    utils_material,
    utils_mesh
)

#
#   RELOAD MODULES
#

pkg_name = __name__

# Reload all modules that belong to this package
for modname, module in list(sys.modules.items()):
    if modname.startswith(pkg_name + ".") and module:
        importlib.reload(module)

for collection in [bpy.app.handlers.depsgraph_update_post, bpy.app.handlers.load_post]:
    for func in collection[:]:
        if func.__module__.startswith(pkg_name):
            collection.remove(func)

#
#   PROPERTIES
#

class BakeNodeItem(PropertyGroup):
    resolutions = [
        ('8', '8', ''),
        ('16', '16', ''),
        ('32', '32', ''),
        ('128', '128', ''),
        ('256', '256', ''),
        ('512', '512', ''),
        ('1024', '1024', ''),
        ('2048', '2048', ''),
        ('4096', '4096', ''),
    ]

    color_space = [
        ('sRGB', 'sRGB (Color)', ''),
        ('Non-Color', 'Non-Color (Data)', '')
    ]

    node_name: StringProperty(name="Node Name")
    name: StringProperty(name="Suffix", default="")
    socket_index: EnumProperty(name="Output", items=utils_material._get_socket_items)
    has_alpha_channel : BoolProperty(name="Has Alpha Channel", default=False)
    alpha_socket_index : EnumProperty(name="Output", items=utils_material._get_socket_items)

    sync_y_with_x: BoolProperty(name="Sync Resolution", default=True)
    resolution_x: EnumProperty(name="X Resolution",items=resolutions,default='2048')
    resolution_y: EnumProperty(name="Y Resolution",items=resolutions,default='2048')

    color_space: EnumProperty(name="Type",items=color_space,default='Non-Color')

    def get_node(self):
        mat = self.id_data
        if isinstance(mat, Material) and mat.node_tree:
            return mat.node_tree.nodes.get(self.node_name)
        return None


class KitsuneTool_SceneProperties(PropertyGroup):
    _bone_merging_options_base = [
        ('DEFAULT', 'Default', 'Merge bones and remove target bone and weights', 'NONE', 0),
        ('KEEP_BONE', 'Keep Bone', 'Keep target bone but merge weights', 'BONE_DATA', 1),
        ('KEEP_BOTH', 'Keep Both', 'Keep target bone and original weights', 'COPYDOWN', 2),
    ]
    bone_merging_options_parent = _bone_merging_options_base + [('SNAP_PARENT', 'Snap Parent Tip', 'Re-align parent tip when merging to parent', 'SNAP_ON', 3),]
    bone_merging_options_active = _bone_merging_options_base + [('CENTRALIZE', 'Centralize', 'Centralize bone position between source and target', 'PIVOT_MEDIAN', 3),]

    merge_bone_options_parent: EnumProperty(name="Merge to Parent Options",items=bone_merging_options_parent,default='DEFAULT')
    merge_bone_options_active: EnumProperty(name="Merge to Active Options",items=bone_merging_options_active,default='DEFAULT')
    visible_mesh_only : BoolProperty(name='Visible Meshes Only', default=False)

    node_baker_export_dir: StringProperty(name="Export Dir", default="//textures\\", subtype='DIR_PATH', options={'PATH_SUPPORTS_BLEND_RELATIVE'})
    node_baker_file_format: EnumProperty(name="Format",items=[('PNG', 'PNG', ''), ('TARGA', 'TGA', '')],default='TARGA')
    node_baker_material_listmode : EnumProperty(name='Material List Mode',items=[
        ('ALL', 'All', 'All materials available within the BLEND file'),
        ('ACTIVE', 'Active', 'All materials in the active object'),
    ], default='ACTIVE')
    node_baker_material_list_index : IntProperty(default=-1)


class KitsuneTool_MaterialProperties(PropertyGroup):
    node_baker_list : CollectionProperty(type=BakeNodeItem)
    node_baker_list_index : IntProperty(default=-1)

#
#   CLASSES
#

_classes = (
    # PROPERTIES
    BakeNodeItem,
    KitsuneTool_SceneProperties,
    KitsuneTool_MaterialProperties,

    # PANELS
    panels_view3d.TOOLS_PT_KitsuneTool_Armature,
    panels_view3d.TOOLS_PT_KitsuneTool_Bone,
    panels_view3d.TOOLS_PT_KitsuneTool_Mesh,
    panels_view3d.TOOLS_PT_KitsuneTool_VertexGroup,
    panels_view3d.TOOLS_PT_KitsuneTool_Animation,
    
    panels_nodeeditor.NODE_UL_nodes_to_bake,
    panels_nodeeditor.NODE_UL_material_list,
    panels_nodeeditor.NODE_PT_KitsuneTool_custom_nodes,
    panels_nodeeditor.NODE_PT_KitsuneTool_NodeBaker,

    # OPERATORS
    ops_armature.ARMATURE_OT_ApplyPoseAsRestPose,
    ops_armature.ARMATURE_OT_ApplyPoseAsShapekey,
    ops_armature.ARMATURE_OT_CopyVisPosture,
    ops_armature.ARMATURE_OT_MergeArmatures,
    ops_armature.ARMATURE_OT_CleanUnWeightedBones,

    ops_bone.BONE_OT_MergeBones,
    ops_bone.BONE_OT_ReAlignBones,
    ops_bone.BONE_OT_CopyTargetRotation,
    ops_bone.BONE_OT_align_bone_to_axis,
    ops_bone.BONE_OT_SubdivideBone,
    ops_bone.BONE_OT_mirror_by_position,
    ops_bone.BONE_OT_FlipBone,
    ops_bone.BONE_OT_CreateCenterBone,
    ops_bone.BONE_OT_SplitActiveWeightLinear,

    ops_mesh.MESH_OT_CleanShapeKeys,
    ops_mesh.MESH_OT_RemoveUnusedVertexGroups,
    ops_mesh.MESH_OT_Delete_Faces_by_ImageMask,
    ops_mesh.MESH_OT_CleanDuplicateMaterials,
    ops_mesh.MESH_OT_SelectShapekeyVets,
    ops_mesh.MESH_OT_Select_Faces_by_ImageMask,
    ops_mesh.MESH_OT_transfer_topology_shapekeys,
    ops_mesh.MESH_OT_unlock_all_vertexgroups,

    ops_vertexgroup.VERTEXGROUP_OT_WeightMath,
    ops_vertexgroup.VERTEXGROUP_OT_SwapVertexGroups,
    ops_vertexgroup.VERTEXGROUP_OT_curve_ramp_weights,
    ops_vertexgroup.VERTEXGROUP_OT_multi_weight_paint_start,
    ops_vertexgroup.VERTEXGROUP_OT_multi_weight_paint_finish,
    ops_vertexgroup.VERTEXGROUP_OT_multi_weight_paint_cancel,

    ops_action.ACTION_OT_merge_animation_slots,
    ops_action.ACTION_OT_merge_two_actions,
    ops_action.ACTION_OT_convert_rotation_keyframes,
    ops_action.ACTION_OT_propagate_pose_offset,
    ops_action.ACTION_OT_copy_bone_keyframes,
    ops_action.ACTION_OT_Make_Proportion_Animation,
    ops_action.ACTION_OT_delete_action_slot,

    ops_nodeeditor.NODE_OT_import_custom_nodes,
    ops_nodeeditor.NODE_OT_node_bake_add,
    ops_nodeeditor.NODE_OT_node_bake_all_materials,
    ops_nodeeditor.NODE_OT_node_bake_remove,
    ops_nodeeditor.NODE_OT_node_bake_run,
)

def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.kitsunetools = utils_contextmanagers.make_pointer(KitsuneTool_SceneProperties)
    bpy.types.Material.kitsunetools = utils_contextmanagers.make_pointer(KitsuneTool_MaterialProperties)

def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.kitsunetools
    del bpy.types.Material.kitsunetools

if __name__ == "__main__":
    register()