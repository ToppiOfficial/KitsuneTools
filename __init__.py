import bpy, importlib, sys
from bpy.types import PropertyGroup, Material
from bpy.props import EnumProperty, BoolProperty, StringProperty, IntProperty, CollectionProperty, FloatProperty, PointerProperty

from .gui import (
    panels_view3d,
    panels_nodeeditor,
    panels_humanoidmapper2,
)
from .op import (
    ops_armature,
    ops_object,
    ops_bone,
    # ops_humanoidmapper,  # HM1 disabled
    ops_humanoidmapper2,
    ops_nodeeditor,
    ops_vertexgroup,
    ops_mesh,
    ops_action,
)
from .utils import (
    utils_fpa_preview,
    utils_armature,
    utils_contextmanagers,
    utils_panels,
    utils_object,
    utils_vertexgroup,
    utils_bone,
    utils_material,
    utils_mesh,
    utils_pose,
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
#   MENU
#

def draw_node_menu_items(self, context):
    if context.space_data.tree_type != 'ShaderNodeTree':
        return
    self.layout.separator()
    self.layout.operator(ops_nodeeditor.NODE_OT_copy_node_values.bl_idname, icon='COPYDOWN')

def draw_add_menu_items(self, context):
    if context.space_data.tree_type != 'ShaderNodeTree':
        return
    self.layout.separator()
    self.layout.operator(ops_nodeeditor.NODE_OT_import_custom_nodes.bl_idname, icon='IMPORT')

def draw_vertex_group_menu_items(self, context):
    self.layout.separator(type='LINE')
    self.layout.operator(ops_vertexgroup.VERTEXGROUP_OT_unlock_all_vertexgroups.bl_idname, icon='UNLOCKED')
    self.layout.operator(ops_vertexgroup.VERTEXGROUP_OT_TransferSelectedGroup.bl_idname)
    self.layout.operator(ops_vertexgroup.VERTEXGROUP_OT_JoinWeights.bl_idname)

def draw_shapekey_menu_items(self, context):
    self.layout.separator(type='LINE')
    self.layout.operator(ops_mesh.MESH_OT_SelectShapekeyVerts.bl_idname, icon='SELECT_SET')
    self.layout.operator(ops_mesh.MESH_OT_transfer_topology_shapekeys.bl_idname, icon='MOD_DATA_TRANSFER')

def draw_edit_mesh_menu_items(self, context):
    self.layout.separator(type='LINE')
    self.layout.operator(ops_mesh.MESH_OT_convex_hull_selection.bl_idname)
    self.layout.operator(ops_mesh.MESH_OT_Delete_Faces_by_ImageMask.bl_idname, icon='UV_FACESEL')

def draw_select_edit_mesh_menu_items(self, context):
    self.layout.separator(type='LINE')
    self.layout.operator(ops_mesh.MESH_OT_Select_Faces_by_ImageMask.bl_idname)

def draw_object_menu_items(self, context):
    self.layout.separator(type='LINE')
    self.layout.operator(ops_armature.ARMATURE_OT_MergeArmatures.bl_idname)
    # self.layout.operator(ops_humanoidmapper.HUMANOIDMAPPER_OT_CopyToSelected.bl_idname)  # HM1 disabled

def draw_object_apply_menu_items(self, context):
    self.layout.separator()
    self.layout.operator(ops_object.OBJECT_OT_ApplyTransformsSafe.bl_idname, icon='CON_TRANSFORM')

def draw_object_cleanup_menu_items(self, context):
    self.layout.separator(type='LINE')
    self.layout.operator(ops_armature.ARMATURE_OT_CleanUnWeightedBones.bl_idname)
    self.layout.operator(ops_mesh.MESH_OT_CleanShapeKeys.bl_idname)
    self.layout.operator(ops_mesh.MESH_OT_RemoveUnusedVertexGroups.bl_idname)
    self.layout.operator(ops_mesh.MESH_OT_CleanDuplicateMaterials.bl_idname)

def draw_edit_bone_menu_items(self, context):
    self.layout.separator(type='LINE')
    self.layout.operator(ops_bone.BONE_OT_FlipBone.bl_idname)
    self.layout.operator(ops_bone.BONE_OT_SubdivideBone.bl_idname, text='Subdivide (With Weights)').weights_only = False
    self.layout.operator(ops_bone.BONE_OT_CreateCenterBone.bl_idname)
    self.layout.operator(ops_vertexgroup.VERTEXGROUP_OT_SplitActiveWeightLinear.bl_idname)
    self.layout.operator(ops_bone.BONE_OT_RemoveBone.bl_idname, icon='TRASH')

def draw_action_menu_items(self, context):
    self.layout.separator(type='LINE')
    self.layout.operator(ops_action.ACTION_OT_delete_action_slot.bl_idname)
    self.layout.operator(ops_action.ACTION_OT_merge_two_actions.bl_idname)
    self.layout.operator(ops_action.ACTION_OT_merge_animation_slots.bl_idname)
    self.layout.operator(ops_action.ACTION_OT_convert_rotation_keyframes.bl_idname)
    self.layout.operator(ops_action.ACTION_OT_copy_bone_keyframes.bl_idname)
    self.layout.operator(ops_action.ACTION_OT_propagate_pose_offset.bl_idname)

def draw_weight_paint_menu_items(self, context):
    self.layout.separator(type='LINE')
    self.layout.operator(ops_vertexgroup.VERTEXGROUP_OT_SplitActiveWeightLinear.bl_idname)


#
#   PROPERTIES
#

class BakeNodeItem(PropertyGroup):
    resolutions = [
        ('8', '8', ''),
        ('16', '16', ''),
        ('32', '32', ''),
        ('64', '64', ''),
        ('128', '128', ''),
        ('256', '256', ''),
        ('512', '512', ''),
        ('1024', '1024', ''),
        ('2048', '2048', ''),
        ('4096', '4096', ''),
        ('8192', '8192', ''),
    ]

    color_space = [
        ('sRGB', 'sRGB (Color)', ''),
        ('Non-Color', 'Non-Color (Data)', '')
    ]

    node_name: StringProperty(
        name="Node Name",
        description="Name of the source node in the material node tree to bake from",
    )
    name: StringProperty(
        name="Suffix",
        description="Suffix appended to the material name when saving the baked file. Defaults to the socket name if left empty",
        default="",
    )
    socket_index: EnumProperty(
        name="Output",
        description="Output socket on the source node whose value will be baked",
        items=utils_material._get_socket_items,
    )
    has_alpha_channel: BoolProperty(
        name="Has Alpha Channel",
        description="Bake a second pass for the alpha channel and merge it into the final image using PIL",
        default=False,
    )
    alpha_socket_index: EnumProperty(
        name="Alpha Output",
        description="Output socket to use as the alpha channel in the merged image",
        items=utils_material._get_socket_items,
    )
    sync_y_with_x: BoolProperty(
        name="Sync Resolution",
        description="Lock the Y resolution to match X, producing a square texture",
        default=True,
    )
    resolution_x: EnumProperty(
        name="X Resolution",
        description="Horizontal resolution of the baked texture in pixels",
        items=resolutions,
        default='2048',
    )
    resolution_y: EnumProperty(
        name="Y Resolution",
        description="Vertical resolution of the baked texture in pixels. Only used when Sync Resolution is disabled",
        items=resolutions,
        default='2048',
    )
    color_space: EnumProperty(
        name="Color Space",
        description="Color space for the baked image. Use sRGB for color data and Non-Color for masks, normals, or other non-color data",
        items=color_space,
        default='Non-Color',
    )
    bypass_texture_mapping: BoolProperty(
        name="Bypass Texture Mapping",
        description="Temporarily disconnect the Vector input on all upstream texture nodes before baking, so textures use their default UV coordinates instead of any Mapping or vector node chain",
        default=False,
    )

    def get_node(self):
        mat = self.id_data
        if isinstance(mat, Material) and mat.node_tree:
            return mat.node_tree.nodes.get(self.node_name)
        return None


class HM2_FingerItem(PropertyGroup):
    source_bone: StringProperty(name="Bone")
    finger_type: EnumProperty(name="Type", items=[
        ('THUMB',  'Thumb',  ''),
        ('INDEX',  'Index',  ''),
        ('MIDDLE', 'Middle', ''),
        ('RING',   'Ring',   ''),
        ('PINKY',  'Pinky',  ''),
    ], default='INDEX')
    side: EnumProperty(name="Side", items=[
        ('L', 'Left',  ''),
        ('R', 'Right', ''),
    ], default='L')
    joint_count: IntProperty(name="Joints", default=3, min=1, max=5)
    generate_ik: BoolProperty(name="Finger Rig", default=True)


def _hm2_puppet_arm_poll(self, obj: bpy.types.Object) -> bool:
    return obj.type == 'ARMATURE'


class HM2_PuppetEntry(PropertyGroup):
    armature: PointerProperty(name="Armature", type=bpy.types.Object, poll=_hm2_puppet_arm_poll)
    mode: EnumProperty(
        name="Mode",
        items=[
            ('MIMIC', "Mimic", "Copy all deform-bone transforms from master (follows master pose)"),
            ('SELF',  "Self",  "Own IK controllers; only VS export config synced from master"),
        ],
        default='MIMIC',
    )


_HM2_TWIST_MODE_ITEMS = [
    ('FOLLOW',  'Follow',  'Twist rotates in the same direction as the target bone'),
    ('AGAINST', 'Against', 'Twist rotates opposite to the target bone'),
]


class KitsuneTool_HM2Properties(PropertyGroup):
    # Core body
    hm2_map_root:  StringProperty(name="Root")
    hm2_map_chest: StringProperty(name="Chest")
    hm2_map_neck:  StringProperty(name="Neck")
    hm2_map_head:  StringProperty(name="Head")
    hm2_map_eye_l: StringProperty(name="Eye L")
    hm2_map_eye_r: StringProperty(name="Eye R")

    # Rig mode
    hm2_first_person_mode: BoolProperty(
        name="First Person Mode",
        default=False,
        description=(
            "Build an arms-only rig: only Shoulder, Elbow and Hand are required "
            "(Scapula optional). Legs, hips, spine, neck, head and eyes are ignored. "
            "If no Root is assigned, one is generated at the average of the shoulder "
            "(or scapula) positions and given the standard root control shape"
        ),
    )

    # Arms
    hm2_map_scapula_l:  StringProperty(name="Scapula L")
    hm2_map_scapula_r:  StringProperty(name="Scapula R")
    hm2_map_shoulder_l: StringProperty(name="Shoulder L")
    hm2_map_shoulder_r: StringProperty(name="Shoulder R")
    hm2_map_elbow_l:    StringProperty(name="Elbow L")
    hm2_map_elbow_r:    StringProperty(name="Elbow R")
    hm2_map_hand_l:     StringProperty(name="Hand L")
    hm2_map_hand_r:     StringProperty(name="Hand R")

    # Legs
    hm2_map_hip_l:   StringProperty(name="Hip L")
    hm2_map_hip_r:   StringProperty(name="Hip R")
    hm2_map_knee_l:  StringProperty(name="Knee L")
    hm2_map_knee_r:  StringProperty(name="Knee R")
    hm2_map_ankle_l: StringProperty(name="Ankle L")
    hm2_map_ankle_r: StringProperty(name="Ankle R")
    hm2_map_toe_l:   StringProperty(name="Toe L")
    hm2_map_toe_r:   StringProperty(name="Toe R")

    # Spine
    hm2_spine_count: IntProperty(name="Spine Count", default=3, min=1, max=8)

    # Fingers
    hm2_fingers:       CollectionProperty(type=HM2_FingerItem)
    hm2_fingers_index: IntProperty(default=-1)

    # Puppet armatures
    hm2_puppets:       CollectionProperty(type=HM2_PuppetEntry)
    hm2_puppets_index: IntProperty(default=-1)
    hm2_is_puppet:     BoolProperty(name="Is Puppet", default=False)
    hm2_puppet_master: PointerProperty(
        name="Puppet Master",
        type=bpy.types.Object,
        poll=_hm2_puppet_arm_poll,
    )

    # Twist counts
    hm2_twist_shoulder: IntProperty(name="Shoulder Twists", default=3, min=0, max=6)
    hm2_twist_elbow:    IntProperty(name="Elbow Twists",    default=4, min=0, max=6)
    hm2_twist_hip:      IntProperty(name="Hip Twists",      default=4, min=0, max=6)
    hm2_twist_knee:     IntProperty(name="Knee Twists",     default=2, min=0, max=6)

    # Twist driver config - per joint per side
    hm2_twist_shoulder_target_l: StringProperty(name="Shoulder Twist Target L", default="L_Shoulder")
    hm2_twist_shoulder_target_r: StringProperty(name="Shoulder Twist Target R", default="R_Shoulder")
    hm2_twist_shoulder_mode_l:   EnumProperty(name="Shoulder Mode L", items=_HM2_TWIST_MODE_ITEMS, default='AGAINST')
    hm2_twist_shoulder_mode_r:   EnumProperty(name="Shoulder Mode R", items=_HM2_TWIST_MODE_ITEMS, default='AGAINST')

    hm2_twist_elbow_target_l: StringProperty(name="Elbow Twist Target L", default="L_Hand")
    hm2_twist_elbow_target_r: StringProperty(name="Elbow Twist Target R", default="R_Hand")
    hm2_twist_elbow_mode_l:   EnumProperty(name="Elbow Mode L", items=_HM2_TWIST_MODE_ITEMS, default='FOLLOW')
    hm2_twist_elbow_mode_r:   EnumProperty(name="Elbow Mode R", items=_HM2_TWIST_MODE_ITEMS, default='FOLLOW')

    hm2_twist_hip_target_l: StringProperty(name="Hip Twist Target L", default="L_Hip")
    hm2_twist_hip_target_r: StringProperty(name="Hip Twist Target R", default="R_Hip")
    hm2_twist_hip_mode_l:   EnumProperty(name="Hip Mode L", items=_HM2_TWIST_MODE_ITEMS, default='AGAINST')
    hm2_twist_hip_mode_r:   EnumProperty(name="Hip Mode R", items=_HM2_TWIST_MODE_ITEMS, default='AGAINST')

    hm2_twist_knee_target_l: StringProperty(name="Knee Twist Target L", default="L_Ankle")
    hm2_twist_knee_target_r: StringProperty(name="Knee Twist Target R", default="R_Ankle")
    hm2_twist_knee_mode_l:   EnumProperty(name="Knee Mode L", items=_HM2_TWIST_MODE_ITEMS, default='FOLLOW')
    hm2_twist_knee_mode_r:   EnumProperty(name="Knee Mode R", items=_HM2_TWIST_MODE_ITEMS, default='FOLLOW')

    hm2_legacy_roll: BoolProperty(
        name="Legacy Roll",
        default=False,
        description=("Use the old pose-derived bone roll alignment. When off (default), bone "
                     "rolls are recomputed from fixed world axes, so the result is identical "
                     "regardless of the source rest pose. Requires the character to face -Y "
                     "(Blender's standard front view)"),
    )

    # IK options
    hm2_generate_ik:       BoolProperty(name="Generate IK Rig", default=True)
    hm2_generate_shapes:   BoolProperty(name="Generate Custom Shapes", default=True)
    hm2_ik_pole_angle_arm: FloatProperty(name="Arm Pole Angle", subtype='ANGLE',
                               default=-1.5707963, description="Pole angle for arm IK (radians)")
    hm2_ik_pole_angle_leg: FloatProperty(name="Leg Pole Angle", subtype='ANGLE',
                               default=1.5707963, description="Pole angle for leg IK (radians)")

    hm2_json_filepath: StringProperty(name="Optional JSON", subtype='FILE_PATH', default="")

    # First Person Arms
    fpa_starting_bone_l: StringProperty(name="Starting Bone L")
    fpa_starting_bone_r: StringProperty(name="Starting Bone R")
    fpa_rig_type: EnumProperty(
        name="Rig Type",
        items=[
            ('AUTO',  'Auto-Detect',    'Detect whether the armature is an HM2 rig or a plain skeleton'),
            ('HM2',   'HM2 Rig',        'Treat as an HM2 rig (has IK/finger controllers)'),
            ('PLAIN', 'Plain Skeleton', 'Treat as a plain deform skeleton (no controllers)'),
        ],
        default='AUTO',
    )
    fpa_preserve_ik: BoolProperty(
        name="Preserve IK & Fingers", default=True,
        description=("HM2 rigs only: keep the arm IK and finger controllers. When off, strip "
                     "CTRL_/IK_/MCH_/VIS_/FK_ controllers leaving plain FK deform bones "
                     "(twist bones are kept)"))
    fpa_weight_threshold: FloatProperty(
        name="Kept Weight Bias", default=0.5, min=0.0, max=1.0, subtype='FACTOR',
        description=("A vertex is kept only when the kept bones hold at least this fraction of its "
                     "total weight. Higher is stricter: e.g. a vertex weighted 0.9 to the shoulder "
                     "and 0.1 to the kept upper arm is dropped at 0.5"))
    fpa_use_bisect: BoolProperty(
        name="Bisect Seam", default=True,
        description=("After vertex-group culling, slice the mesh with a plane through the starting "
                     "bones for a clean straight cut edge"))
    fpa_bisect_axis: EnumProperty(
        name="Bisect Axis",
        items=[('X', 'World X', ''), ('Y', 'World Y', ''), ('Z', 'World Z', '')],
        default='Z')
    fpa_bisect_offset: FloatProperty(name="Bisect Offset", default=0.0, subtype='DISTANCE')


class Humanoidmapper(PropertyGroup):
    boneExportName : StringProperty(
        name='Bone',
        description="The original bone name in the source armature. Used when writing JSON for retargeting."
    )

    boneName : StringProperty(
        name='Target Name',
        description="The target name that this bone should be mapped to during retargeting. When loading JSON, any bone matching this name will be treated as the original bone."
    )
    
    writeRotation : EnumProperty(name='Write Rotation', items=[
        ('NONE', 'Do Not Write', ''),
        ('ROTATION', 'Rotation', ''),
        ('ROLL', 'Roll Only', '')
    ], default='ROLL')
    
    writeTwistBone : BoolProperty(name='Write TwistBone', default=False)
    twistBoneTarget : StringProperty(name='TwistBone Target Bone')
    twistBoneCount : IntProperty(name='TwistBone Count', default=1, min=1, soft_max=5)
    writeExportRotationOffset : BoolProperty(name='Write Export Rotation Offset', default=True)
    parentBone : StringProperty(name='Parent Bone', default='', description='Overwrite Parent bone on JSON parse')


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

    humanoid_armature_map_menu : EnumProperty(name='Define Armature Category',items=[('LOAD', 'Load', ''),('WRITE', 'Write', ''),])


class KitsuneTool_ObjectProperties(PropertyGroup):
    humanoid_armature_map_bonecollections : CollectionProperty(name='JSON Bone Collection',type=Humanoidmapper)
    humanoid_armature_map_bonecollections_index : IntProperty()
    
    armature_map_pelvis : StringProperty(name="Pelvis")
    armature_map_chest  : StringProperty(name="Chest")
    armature_map_spine  : StringProperty(name="Spine")
    armature_map_head   : StringProperty(name="Head")
    armature_map_thigh_l : StringProperty(name="Left Thigh")
    armature_map_ankle_l : StringProperty(name="Left Ankle")
    armature_map_toe_l   : StringProperty(name="Left Toe")
    armature_map_thigh_r : StringProperty(name="Right Thigh")
    armature_map_ankle_r : StringProperty(name="Right Ankle")
    armature_map_toe_r   : StringProperty(name="Right Toe")
    armature_map_shoulder_l : StringProperty(name="Left Shoulder")
    armature_map_wrist_l    : StringProperty(name="Left Wrist")
    armature_map_index_f_l  : StringProperty(name="Left Index Finger")
    armature_map_middle_f_l : StringProperty(name="Left Middle Finger")
    armature_map_ring_f_l   : StringProperty(name="Left Ring Finger")
    armature_map_pinky_f_l  : StringProperty(name="Left Pinky Finger")
    armature_map_thumb_f_l  : StringProperty(name="Left Thumb Finger")
    armature_map_shoulder_r : StringProperty(name="Right Shoulder")
    armature_map_wrist_r    : StringProperty(name="Right Wrist")
    armature_map_index_f_r  : StringProperty(name="Right Index Finger")
    armature_map_middle_f_r : StringProperty(name="Right Middle Finger")
    armature_map_ring_f_r   : StringProperty(name="Right Ring Finger")
    armature_map_pinky_f_r  : StringProperty(name="Right Pinky Finger")
    armature_map_thumb_f_r  : StringProperty(name="Right Thumb Finger")
    armature_map_eye_l  : StringProperty(name="Left Eye")
    armature_map_eye_r  : StringProperty(name="Right Eye")
    
    armature_map_upperarm_l: StringProperty(name="Left Upper Arm",)
    armature_map_upperarm_r: StringProperty(name="Right Upper Arm",)
    armature_map_forearm_l: StringProperty(name="Left Fore Arm",)
    armature_map_forearm_r: StringProperty(name="Right Fore Arm",)
    armature_map_knee_l: StringProperty(name="Left Knee",)
    armature_map_knee_r: StringProperty(name="Right Knee",)

    hm2: PointerProperty(type=KitsuneTool_HM2Properties)


class KitsuneTool_MaterialProperties(PropertyGroup):
    node_baker_list : CollectionProperty(type=BakeNodeItem)
    node_baker_list_index : IntProperty(default=-1)


class KitsuneTool_ArmatureProperties(PropertyGroup):
    x_mirror_pose: BoolProperty(name="X Mirror Pose",
        description="Automatically mirror selected bone transforms across the X axis in Pose Mode",
        default=False)
    
    x_mirror_tolerance: FloatProperty(name="Mirror Tolerance",
        description="Distance threshold for finding the opposing mirror bone",
        default=0.001,min=0.0,precision=4,)

#
#   CLASSES
#

_classes = (
    # PROPERTIES
    BakeNodeItem,
    Humanoidmapper,
    HM2_FingerItem,
    HM2_PuppetEntry,
    KitsuneTool_HM2Properties,

    KitsuneTool_SceneProperties,
    KitsuneTool_ObjectProperties,
    KitsuneTool_MaterialProperties,
    KitsuneTool_ArmatureProperties,

    # List
    # panels_view3d.HUMANOIDMAPPER_UL_ConfigList,  # HM1 disabled
    panels_humanoidmapper2.HM2_UL_FingerList,
    panels_humanoidmapper2.HM2_UL_PuppetList,

    # MENU
    panels_view3d.TOOLS_MT_KitsuneTool_PoseBoneTools,

    # PANELS
    panels_view3d.TOOLS_PT_KitsuneTool_Armature,
    panels_view3d.TOOLS_PT_KitsuneTool_Bone,
    panels_view3d.TOOLS_PT_KitsuneTool_VertexGroup,
    panels_humanoidmapper2.TOOLS_PT_KitsuneTool_HumanoidMapping,
    # panels_view3d.TOOLS_PT_KitsuneTool_Humanoidmapper,  # HM1 disabled
    panels_humanoidmapper2.TOOLS_PT_KitsuneTool_HM2_FirstPersonArms,
    panels_humanoidmapper2.TOOLS_PT_KitsuneTool_HM2,
    panels_humanoidmapper2.TOOLS_PT_KitsuneTool_HM2_Core,
    panels_humanoidmapper2.TOOLS_PT_KitsuneTool_HM2_Arms,
    panels_humanoidmapper2.TOOLS_PT_KitsuneTool_HM2_Legs,
    panels_humanoidmapper2.TOOLS_PT_KitsuneTool_HM2_Fingers,
    panels_humanoidmapper2.TOOLS_PT_KitsuneTool_HM2_Twist,
    panels_humanoidmapper2.TOOLS_PT_KitsuneTool_HM2_IK,
    panels_humanoidmapper2.TOOLS_PT_KitsuneTool_HM2_Export,
    panels_humanoidmapper2.TOOLS_PT_KitsuneTool_HM2_Puppets,
    panels_humanoidmapper2.TOOLS_PT_KitsuneTool_HM2_Actions,

    panels_nodeeditor.NODE_UL_nodes_to_bake,
    panels_nodeeditor.NODE_UL_material_list,
    panels_nodeeditor.NODE_PT_KitsuneTool_NodeBaker,

    # OPERATORS
    ops_armature.ARMATURE_OT_ApplyPoseAsRestPose,
    ops_armature.ARMATURE_OT_ApplyPoseAsShapekey,
    ops_armature.ARMATURE_OT_CopyVisPosture,
    ops_armature.ARMATURE_OT_FitPoseToActive,
    ops_armature.ARMATURE_OT_MergeArmatures,
    ops_armature.ARMATURE_OT_CleanUnWeightedBones,
    ops_armature.ARMATURE_OT_TransferBoneData,

    ops_object.OBJECT_OT_ApplyTransformsSafe,

    ops_bone.BONE_OT_MergeBones,
    ops_bone.BONE_OT_ReAlignBones,
    ops_bone.BONE_OT_CopyTargetRotation,
    ops_bone.BONE_OT_align_bone_to_axis,
    ops_bone.BONE_OT_SubdivideBone,
    ops_bone.BONE_OT_FlipBone,
    ops_bone.BONE_OT_CreateCenterBone,
    ops_bone.BONE_OT_parent_bone_in_pose,
    ops_bone.BONE_OT_RemoveBone,
    ops_bone.BONE_OT_kitsune_mirror_pose,

    ops_mesh.MESH_OT_CleanShapeKeys,
    ops_mesh.MESH_OT_RemoveUnusedVertexGroups,
    ops_mesh.MESH_OT_Delete_Faces_by_ImageMask,
    ops_mesh.MESH_OT_CleanDuplicateMaterials,
    ops_mesh.MESH_OT_SelectShapekeyVerts,
    ops_mesh.MESH_OT_Select_Faces_by_ImageMask,
    ops_mesh.MESH_OT_transfer_topology_shapekeys,
    ops_mesh.MESH_OT_convex_hull_selection,
    ops_mesh.MESH_OT_replace_verts_with_spheres,

    ops_vertexgroup.VERTEXGROUP_OT_WeightMath,
    ops_vertexgroup.VERTEXGROUP_OT_SwapVertexGroups,
    ops_vertexgroup.VERTEXGROUP_OT_curve_ramp_weights,
    ops_vertexgroup.VERTEXGROUP_OT_multi_weight_paint_start,
    ops_vertexgroup.VERTEXGROUP_OT_multi_weight_paint_finish,
    ops_vertexgroup.VERTEXGROUP_OT_multi_weight_paint_cancel,
    ops_vertexgroup.VERTEXGROUP_OT_TransferSelectedGroup,
    ops_vertexgroup.VERTEXGROUP_OT_unlock_all_vertexgroups,
    ops_vertexgroup.VERTEXGROUP_OT_SplitActiveWeightLinear,
    ops_vertexgroup.VERTEXGROUP_OT_JoinWeights,

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
    ops_nodeeditor.NODE_OT_copy_node_values,
    ops_nodeeditor.NODE_OT_set_copy_input,
    ops_nodeeditor.NODE_OT_node_bake_auto_resolution,
    ops_nodeeditor.NODE_OT_node_bake_auto_colorspace,
    ops_nodeeditor.NODE_OT_node_bake_copy,
    ops_nodeeditor.NODE_OT_node_bake_paste,

    # ops_humanoidmapper.HUMANOIDMAPPER_OT_CopyToSelected,  # HM1 disabled
    # ops_humanoidmapper.HUMANOIDMAPPER_OT_LoadPreset,
    # ops_humanoidmapper.HUMANOIDMAPPER_OT_LoadConfig,
    # ops_humanoidmapper.HUMANOIDMAPPER_OT_RemoveItem,
    # ops_humanoidmapper.HUMANOIDMAPPER_OT_AddItem,
    # ops_humanoidmapper.HUMANOIDMAPPER_OT_MirrorBoneNames,
    # ops_humanoidmapper.HUMANOIDMAPPER_OT_WriteConfig,

    ops_humanoidmapper2.HM2_OT_AddFinger,
    ops_humanoidmapper2.HM2_OT_RemoveFinger,
    ops_humanoidmapper2.HM2_OT_MirrorFingers,
    ops_humanoidmapper2.HM2_OT_MirrorBodyMapping,
    ops_humanoidmapper2.HM2_OT_CopyMappingToSelected,
    ops_humanoidmapper2.HM2_OT_ValidateMapping,
    ops_humanoidmapper2.HM2_OT_Process,
    ops_humanoidmapper2.HM2_OT_JsonFormatHelp,
    ops_humanoidmapper2.HM2_OT_AddPuppet,
    ops_humanoidmapper2.HM2_OT_RemovePuppet,
    ops_humanoidmapper2.HM2_OT_ProcessPuppet,
    ops_humanoidmapper2.HM2_OT_DisconnectPuppet,
    ops_humanoidmapper2.HM2_OT_SyncPuppetExportConfig,
    ops_humanoidmapper2.HM2_OT_FirstPersonArms,
)

def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.app.handlers.depsgraph_update_post.append(utils_pose.mirror_pose_handler)

    bpy.types.Scene.kitsunetools = utils_contextmanagers.make_pointer(KitsuneTool_SceneProperties)
    bpy.types.Object.kitsunetools = utils_contextmanagers.make_pointer(KitsuneTool_ObjectProperties)
    bpy.types.Material.kitsunetools = utils_contextmanagers.make_pointer(KitsuneTool_MaterialProperties)
    bpy.types.Armature.kitsunetools = utils_contextmanagers.make_pointer(KitsuneTool_ArmatureProperties)

    bpy.types.NODE_MT_node.append(draw_node_menu_items)
    bpy.types.NODE_MT_add.append(draw_add_menu_items)
    bpy.types.MESH_MT_vertex_group_context_menu.append(draw_vertex_group_menu_items)
    bpy.types.MESH_MT_shape_key_context_menu.append(draw_shapekey_menu_items)
    bpy.types.VIEW3D_MT_edit_mesh.append(draw_edit_mesh_menu_items)
    bpy.types.VIEW3D_MT_select_edit_mesh.append(draw_select_edit_mesh_menu_items)
    bpy.types.VIEW3D_MT_object.append(draw_object_menu_items)
    bpy.types.VIEW3D_MT_object_apply.append(draw_object_apply_menu_items)
    bpy.types.VIEW3D_MT_object_cleanup.append(draw_object_cleanup_menu_items)
    bpy.types.VIEW3D_MT_pose.append(draw_edit_bone_menu_items)
    bpy.types.VIEW3D_MT_edit_armature.append(draw_edit_bone_menu_items)
    bpy.types.VIEW3D_MT_paint_weight.append(draw_weight_paint_menu_items)
    bpy.types.DOPESHEET_MT_action.append(draw_action_menu_items)

    utils_contextmanagers.register_keymap('Node Editor', 'NODE_EDITOR', ops_nodeeditor.NODE_OT_copy_node_values.bl_idname, 'C', ctrl=True, shift=True)
    utils_contextmanagers.register_keymap('Window', 'EMPTY', 'wm.call_menu', 'P', ctrl=True, shift=True, properties={'name': panels_view3d.TOOLS_MT_KitsuneTool_PoseBoneTools.bl_idname})

def unregister():
    utils_fpa_preview.unregister_preview()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

    if utils_pose.mirror_pose_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(utils_pose.mirror_pose_handler)

    for km, kmi in utils_contextmanagers._addon_keymaps:
        km.keymap_items.remove(kmi)
    utils_contextmanagers._addon_keymaps.clear()

    bpy.types.NODE_MT_node.remove(draw_node_menu_items)
    bpy.types.NODE_MT_add.remove(draw_add_menu_items)
    bpy.types.MESH_MT_vertex_group_context_menu.remove(draw_vertex_group_menu_items)
    bpy.types.MESH_MT_shape_key_context_menu.remove(draw_shapekey_menu_items)
    bpy.types.VIEW3D_MT_edit_mesh.remove(draw_edit_mesh_menu_items)
    bpy.types.VIEW3D_MT_select_edit_mesh.remove(draw_select_edit_mesh_menu_items)
    bpy.types.VIEW3D_MT_object.remove(draw_object_menu_items)
    bpy.types.VIEW3D_MT_object_apply.remove(draw_object_apply_menu_items)
    bpy.types.VIEW3D_MT_object_cleanup.remove(draw_object_cleanup_menu_items)
    bpy.types.VIEW3D_MT_pose.remove(draw_edit_bone_menu_items)
    bpy.types.VIEW3D_MT_edit_armature.remove(draw_edit_bone_menu_items)
    bpy.types.VIEW3D_MT_paint_weight.remove(draw_weight_paint_menu_items)
    bpy.types.DOPESHEET_MT_action.remove(draw_action_menu_items)

    del bpy.types.Scene.kitsunetools
    del bpy.types.Object.kitsunetools
    del bpy.types.Material.kitsunetools
    del bpy.types.Armature.kitsunetools
    

if __name__ == "__main__":
    register()