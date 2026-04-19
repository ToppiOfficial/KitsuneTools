import bpy, re
from bpy.types import Object, Operator, Context, PoseBone
from bpy.props import BoolProperty, StringProperty, EnumProperty, FloatProperty
from ..utils.utils_armature import apply_current_pose_as_restpose, apply_current_pose_shapekey, get_selected_bones, copy_armature_visual_pose, merge_armatures, transfer_armature_bonedata
from ..utils.utils_contextmanagers import preserve_context_mode
from ..utils.utils_object import get_armature, is_armature, get_armature_meshes
from ..utils.utils_vertexgroup import remove_unused_vertexgroups
from ..utils.utils_bone import remove_bone


class _apply_pose:
    selected_only : BoolProperty(name='Selected Bones Only', default=False)

    def execute(self, context : Context) -> set:
        as_shapekey = hasattr(self, 'as_shapekey')
        
        with preserve_context_mode(None, 'OBJECT'):
            armatures : set[Object | None] = {get_armature(o) for o in context.selected_objects}
            success_count = 0
            
            for armature in armatures:
                try:
                    if self.selected_only:
                        selected_bones = get_selected_bones(armature=armature, bone_type='POSEBONE')
                        
                        for posebone in armature.pose.bones:
                            if posebone.name in {pb.name for pb in selected_bones}: continue
                            posebone.matrix_basis.identity()

                    if as_shapekey:
                        apply_current_pose_shapekey(armature=armature, shapekey_name=self.shapekey_name.strip())
                    else:
                        apply_current_pose_as_restpose(armature=armature)

                    success_count += 1

                except Exception as e:
                    self.report({'ERROR'}, f"Failed to apply pose: {str(e)}")
                    continue

        if success_count > 0:
            if len(armatures) == 1: message = 'Applied as Rest Pose'
            else: message = f'Applied {len(armatures)} Armatures as Rest Pose'

            if not as_shapekey: bpy.ops.object.mode_set(mode='OBJECT')
            
            self.report({'INFO'}, message)
            return {'FINISHED'}

        else:
            return {'CANCELLED'}


class ARMATURE_OT_ApplyPoseAsRestPose(_apply_pose, Operator):
    bl_idname = "kitsunetools.apply_pose_as_restpose"
    bl_label = "Apply Pose As Restpose"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        active_object = context.active_object
        return is_armature(active_object) and not active_object.mode == 'EDIT' and not active_object.hide_get()
    
    def draw(self, context):
        layout = self.layout


class ARMATURE_OT_ApplyPoseAsShapekey(_apply_pose, Operator):
    bl_idname = "kitsunetools.apply_pose_as_shapekey"
    bl_label = "Apply Pose As Shapekey"
    bl_options = {'REGISTER', 'UNDO'}
    
    as_shapekey : BoolProperty(default=True)
    shapekey_name : StringProperty(name='Shapekey Name', default='Pose_Shape')
    
    @classmethod
    def poll(cls, context : Context) -> bool:
        return bool(is_armature(context.active_object) and context.mode in {'POSE', 'OBJECT'}) and not context.active_object.hide_get()
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=200)
        
    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'selected_only')
        layout.prop(self, 'shapekey_name')


class ARMATURE_OT_MergeArmatures(Operator):
    bl_idname = "kitsunetools.merge_armatures"
    bl_label = "Merge Armatures"
    bl_options = {'REGISTER', 'UNDO'}
    
    match_posture : BoolProperty(name='Match Visual Pose', default=True)
    clean_bones : BoolProperty(name='Clean Bones', default=True)
    use_anchor_bone : BoolProperty(name='Anchor Root Bones', default=False)
    anchor_bone : StringProperty(name='Anchor Bone', default="")
    apply_pose : BoolProperty(name='Apply Pose', default=True)
    group_bone_collections : BoolProperty(name='Group Bone Collections', default=False)
    
    @classmethod
    def poll(cls, context : Context) -> bool:
        return bool(is_armature(context.active_object) and {ob for ob in context.selected_objects if is_armature(ob) and ob != context.active_object})
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)
    
    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'group_bone_collections')
        layout.prop(self, 'match_posture')

        if not self.match_posture:
            layout.prop(self, 'apply_pose')

        layout.prop(self, 'clean_bones')
        layout.prop(self, 'use_anchor_bone')

        if self.use_anchor_bone:
            row = layout.row()
            row.prop_search(self, 'anchor_bone', context.active_object.data, 'bones', text='Bone')
    
    def execute(self, context : Context) -> set:
        active_object = context.active_object
        original_active = context.view_layer.objects.active
        self.apply_pose = self.match_posture if self.match_posture else self.apply_pose
        
        if active_object is None: return {'CANCELLED'}
        
        armatures_to_merge = [ob for ob in context.selected_objects if ob != active_object and is_armature(ob)]
        
        if not armatures_to_merge:
            self.report({'WARNING'}, "No other armatures selected to merge.")
            return {'CANCELLED'}

        resolved_anchor = self.anchor_bone if (self.use_anchor_bone and self.anchor_bone in active_object.data.bones) else ""
        
        success_count = 0
        
        with preserve_context_mode(original_active, 'OBJECT'):
            try:
                for arm in armatures_to_merge:
                    try:
                        if self.clean_bones:
                            bpy.ops.object.select_all(action='DESELECT')
                            context.view_layer.objects.active = arm
                            arm.select_set(True)
                            bpy.ops.kitsunetools.clean_unweighted_bones('EXEC_DEFAULT', cleaning_mode='FULL_CLEAN', remove_empty_vertex_groups=True)
                    
                        merge_armatures(active_object, arm, match_posture=self.match_posture, anchor_bone=resolved_anchor, apply_pose=self.apply_pose, group_bone_collections=self.group_bone_collections)
                        success_count += 1
                    except Exception as e:
                        self.report({'ERROR'}, f"Failed to merge '{arm.name}': {str(e)}")
                        continue
                
            finally:
                self.report({'INFO'}, f'Merged {success_count} armatures to active armature')
                
        return {'FINISHED'}


class ARMATURE_OT_CopyVisPosture(Operator):
    bl_idname = "kitsunetools.copy_armature_visual_pose"
    bl_label = "Copy Visual Pose"
    bl_options = {'REGISTER', 'UNDO'}

    copy_type: EnumProperty(items=[('ORIGIN', 'Location', ''), ('ANGLES', 'Rotation', '')])
        
    @classmethod
    def poll(cls,context : Context) -> bool:
        if context.mode != 'OBJECT': return False
        currob  = context.active_object
        if not is_armature(currob): return False
        
        obs = {ob for ob in context.selected_objects  if not ob.hide_get() and ob != currob}
        return bool(obs)
    
    def execute(self, context : Context) -> set:
        currArm  = context.active_object
        if currArm is None: return {'CANCELLED'}
        
        obs = {ob for ob in context.selected_objects if not ob.hide_get() and ob != currArm}

        copiedcount = 0
        for otherArm in obs:
            
            if not all([currArm.data.bones, otherArm.data.bones]):
                continue
            
            copy_armature_visual_pose(base_armature=currArm,target_armature=otherArm,copy_type=self.copy_type,)
        
        return {'FINISHED'} if copiedcount > 0 else {'CANCELLED'}
    

class ARMATURE_OT_CleanUnWeightedBones(Operator):
    bl_idname= 'kitsunetools.clean_unweighted_bones'
    bl_label= 'Clean Unweighted Bones'
    bl_options = {'REGISTER', 'UNDO'}
    
    cleaning_mode: EnumProperty(
        name='Cleaning Mode',
        description='How to handle animated and constrained bones',
        items=[
            ('RESPECT_ANIMATION', 'Respect Animation Rigging', 
             'Preserve bones with keyframes, constraints, drivers, or that are constraint targets'),
            ('HIERARCHY_ONLY', 'Respect Hierarchy', 
             'Only preserve bones with weighted children, ignoring animation'),
            ('FULL_CLEAN', 'Full Clean', 
             'Remove all unweighted bones regardless of animation or hierarchy')
        ],
        default='RESPECT_ANIMATION'
    )
    
    remove_empty_vertex_groups: BoolProperty(
        name='Remove Empty Vertex Groups',
        description='Also remove vertex groups with no weights',
        default=True
    )
    
    weight_threshold: FloatProperty(
        name='Weight Threshold',
        description='Remove weights below this value',
        default=0.001,
        min=0.0001,
        max=0.1,
        precision=4
    )
    
    preserve_deform_bones: BoolProperty(
        name='Preserve Deform Bones',
        description='Keep bones marked as deform even if unweighted',
        default=False
    )
    
    remove_unused_bonecollections : BoolProperty(name='Remove Unused Bone Collections', default=True)

    respect_mirror : BoolProperty(name='Respect Mirror', default=True)
    
    @classmethod
    def poll(cls, context: Context) -> bool:
        return bool(is_armature(context.active_object) and context.mode in {'POSE', 'OBJECT'})
        
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)
    
    def draw(self, context: Context) -> None:
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        
        col = layout.column(align=True)
        col.prop(self, 'cleaning_mode')
        
        col.separator()
        
        col.prop(self, 'preserve_deform_bones')
        col.prop(self, 'respect_mirror')
        col.prop(self, 'remove_unused_bonecollections')
        col.prop(self, 'remove_empty_vertex_groups')
        
        subcol = col.column(align=True)
        subcol.enabled = self.remove_empty_vertex_groups
        subcol.prop(self, 'weight_threshold', slider=True)
        
        if self.cleaning_mode == 'FULL_CLEAN':
            col.separator()
            box = layout.box()
            row = box.row()
            row.alert = True
            row.label(text='WARNING: May break rigs with IK/constraints!', icon='ERROR')
            
    def is_excluded_bone(self, bone_name) -> bool:
        twist_pattern = re.compile(r'.+ twist( \d+)?$', re.IGNORECASE)
        return bool(twist_pattern.match(bone_name))

    def execute(self, context: Context) -> set:
        armatures: set[Object | None] = {get_armature(ob) for ob in context.selected_objects}
        
        total_vgroups_removed = 0
        total_bones_removed = 0
        total_collection_removed = 0
        
        for armature in armatures:
            bones = armature.pose.bones
            meshes = get_armature_meshes(armature)
            bones_with_children = self.get_bones_with_parented_objects(armature)

            if not meshes and not bones_with_children:
                self.report({'WARNING'}, f"Armature '{armature.name}' has no meshes or parented objects.")
                continue

            if not bones:
                self.report({'WARNING'}, f"Armature '{armature.name}' has no bones.")
                continue

            if self.remove_empty_vertex_groups and meshes:
                removed_vgroups = remove_unused_vertexgroups(
                    armature, 
                    armature.data.bones,
                    weight_limit=self.weight_threshold,
                    respect_mirror=self.respect_mirror
                )
                total_vgroups_removed += sum(len(vgs) for vgs in removed_vgroups.values())

            remaining_vgroups = {
                mesh: set(vg.name for vg in mesh.vertex_groups)
                for mesh in meshes
            }

            constraint_targets = self.get_constraint_targets(armature)
            constraint_owners = self.get_constraint_owners(armature)

            while True:
                bones_to_remove = set()
                for b in bones:
                    if self.should_preserve_bone(
                        armature, b, meshes, remaining_vgroups, 
                        constraint_targets, constraint_owners, bones_with_children
                    ):
                        continue
                    
                    if not self.is_excluded_bone(b.name):
                        bones_to_remove.add(b.name)

                if bones_to_remove:
                    with preserve_context_mode(armature, 'EDIT'):
                        remove_bone(armature, bones_to_remove)
                        
                        total_bones_removed += len(bones_to_remove)
                        bones = armature.pose.bones
                        
                        remaining_vgroups = {
                            mesh: set(vg.name for vg in mesh.vertex_groups)
                            for mesh in meshes
                        }
                        
                        constraint_targets = self.get_constraint_targets(armature)
                        constraint_owners = self.get_constraint_owners(armature)
                        bones_with_children = self.get_bones_with_parented_objects(armature)
                else:
                    
                    if self.remove_unused_bonecollections:
                        if armature.data.collections:
                            bpy.ops.armature.collection_remove_unused()
                    break

        if total_bones_removed == 0 and total_vgroups_removed == 0:
            self.report({'INFO'}, 'No bones or vertex groups to remove.')
        else:
            self.report({'INFO'}, f'{total_bones_removed} bones removed, and {total_vgroups_removed} empty vertex groups cleaned.')
        return {'FINISHED'}

    def should_preserve_bone(self, armature : Object, bone : PoseBone, meshes : list[Object], remaining_vgroups, constraint_targets, constraint_owners, bones_with_children) -> bool:
        if self.preserve_deform_bones and bone.bone.use_deform:
            return True
        
        has_weight = any(bone.name in remaining_vgroups[mesh] for mesh in meshes)
        if has_weight:
            return True
        
        if bone.name in bones_with_children:
            return True
        
        if self.cleaning_mode == 'FULL_CLEAN':
            return False
        
        if self.cleaning_mode == 'HIERARCHY_ONLY':
            return self.has_weighted_descendants(bone, meshes, remaining_vgroups, bones_with_children)
        
        if self.cleaning_mode == 'RESPECT_ANIMATION':
            if self.bone_has_animation(armature, bone.name):
                return True
            
            if bone.name in constraint_targets or bone.name in constraint_owners:
                return True
            
            if self.has_animated_or_constrained_descendants(
                armature, bone, meshes, remaining_vgroups, constraint_targets, constraint_owners, bones_with_children
            ):
                return True
        
        return False

    def has_weighted_descendants(self, bone, meshes, remaining_vgroups, bones_with_children) -> bool:
        for child in bone.children:
            if self.is_excluded_bone(child.name):
                return True
            if any(child.name in remaining_vgroups[mesh] for mesh in meshes):
                return True
            if child.name in bones_with_children:
                return True
            if self.has_weighted_descendants(child, meshes, remaining_vgroups, bones_with_children):
                return True
        return False

    def has_animated_or_constrained_descendants(self, armature, bone, meshes, remaining_vgroups, constraint_targets, constraint_owners, bones_with_children) -> bool:
        for child in bone.children:
            if self.is_excluded_bone(child.name):
                return True
            if any(child.name in remaining_vgroups[mesh] for mesh in meshes):
                return True
            if child.name in bones_with_children:
                return True
            if self.bone_has_animation(armature, child.name):
                return True
            if child.name in constraint_targets or child.name in constraint_owners:
                return True
            if self.has_animated_or_constrained_descendants(
                armature, child, meshes, remaining_vgroups, constraint_targets, constraint_owners, bones_with_children
            ):
                return True
        return False

    def bone_has_animation(self, armature : Object, bone_name : str) -> bool:
        bone = armature.pose.bones.get(bone_name)
        if not bone:
            return False

        for action in bpy.data.actions:
            for fcurve in action.fcurves:
                if fcurve.data_path.startswith(f'pose.bones["{bone_name}"]'):
                    if any(kw in fcurve.data_path for kw in ('location', 'rotation', 'scale')):
                        if len(fcurve.keyframe_points) > 1:
                            return True

        if armature.animation_data and armature.animation_data.drivers:
            bone_path = f'pose.bones["{bone_name}"]'
            for driver in armature.animation_data.drivers:
                if driver.data_path.startswith(bone_path):
                    if any(kw in driver.data_path for kw in ('location', 'rotation', 'scale')):
                        return True

        return False

    def get_constraint_targets(self, armature : Object) -> set:
        targets = set()
        for bone in armature.pose.bones:
            for constraint in bone.constraints:
                target = getattr(constraint, 'target', None)
                if target == armature:
                    subtarget = getattr(constraint, 'subtarget', None)
                    if subtarget:
                        targets.add(subtarget)
                    
                    if constraint.type == 'IK':
                        pole_target = getattr(constraint, 'pole_target', None)
                        pole_subtarget = getattr(constraint, 'pole_subtarget', None)
                        if pole_target == armature and pole_subtarget:
                            targets.add(pole_subtarget)
        return targets

    def get_constraint_owners(self, armature : Object) -> set:
        owners = set()
        for bone in armature.pose.bones:
            if bone.constraints:
                owners.add(bone.name)
        return owners

    def get_bones_with_parented_objects(self, armature : Object) -> set:
        bones_with_children = set()
        for obj in bpy.data.objects:
            if obj.parent == armature and obj.parent_type == 'BONE' and obj.parent_bone:
                bones_with_children.add(obj.parent_bone)
        return bones_with_children


class ARMATURE_OT_TransferBoneData(Operator):
    bl_idname = "kitsunetools.reevaluate_armatures"
    bl_label = "Re-evaluate Armatures"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(
        name="Bone Selection",
        items=[
            ('ALL', "All Matching Bones", "Match all bones shared between armatures"),
            ('COLLECTION', "All Matching Bones of Collection", "Match bones from a specific bone collection"),
            ('SELECTED', "Selected Pose Bones", "Only match currently selected pose bones on the active armature"),
        ],
        default='ALL',
    )

    data_mode: EnumProperty(
        name="Data",
        items=[
            ('ALL', "All", "Copy bone transforms and custom properties"),
            ('TRANSFORMS', "Transforms Only", "Copy only head, tail, roll and bone settings"),
            ('PROPERTIES', "Properties Only", "Copy only custom properties"),
        ],
        default='ALL',
    )

    sync_bone_collections: BoolProperty(
        name="Sync Bone Collections",
        description="Assign matched bones to the same bone collections as the source, creating collections if missing",
        default=False,
    )

    collection_name: StringProperty(name="Bone Collection", default="")
    include_child_collections: BoolProperty(name="Include Child Collections", default=False)

    @classmethod
    def poll(cls, context: Context) -> bool:
        return bool(
            is_armature(context.active_object)
            and {ob for ob in context.selected_objects if is_armature(ob) and ob != context.active_object}
        )

    def invoke(self, context: Context, event) -> set:
        if self.mode == 'SELECTED' and context.mode != 'POSE':
            self.report({'WARNING'}, "Must be in Pose Mode to use 'Selected Pose Bones'.")
            return {'CANCELLED'}

        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context: Context):
        layout = self.layout
        layout.prop(self, "data_mode")
        if self.data_mode in ('ALL', 'PROPERTIES'):
            layout.prop(self, "sync_bone_collections")
        if self.mode == 'COLLECTION':
            layout.prop_search(self, "collection_name", context.active_object.data, "collections", text="Collection")
            layout.prop(self, "include_child_collections")

    def execute(self, context: Context) -> set:
        active_object = context.active_object
        if active_object is None:
            return {'CANCELLED'}

        targets = [ob for ob in context.selected_objects if ob != active_object and is_armature(ob)]
        if not targets:
            self.report({'WARNING'}, "No other armatures selected to re-evaluate.")
            return {'CANCELLED'}

        bone_filter = None

        if self.mode == 'SELECTED':
            if context.mode != 'POSE':
                self.report({'WARNING'}, "Must be in Pose Mode to use 'Selected Pose Bones'.")
                return {'CANCELLED'}
            bone_filter = {pb.name for pb in context.selected_pose_bones if pb.id_data == active_object}
            if not bone_filter:
                self.report({'WARNING'}, "No pose bones selected on the active armature.")
                return {'CANCELLED'}

        elif self.mode == 'COLLECTION':
            collection = active_object.data.collections.get(self.collection_name)
            if not collection:
                self.report({'WARNING'}, f"Bone collection '{self.collection_name}' not found.")
                return {'CANCELLED'}

            collections_to_check = [collection]
            if self.include_child_collections:
                def collect_children(col):
                    for child in col.children:
                        collections_to_check.append(child)
                        collect_children(child)
                collect_children(collection)

            bone_filter = {b.name for col in collections_to_check for b in col.bones}
            if not bone_filter:
                self.report({'WARNING'}, f"No bones found in collection '{self.collection_name}'.")
                return {'CANCELLED'}

        with preserve_context_mode(context.view_layer.objects.active, 'OBJECT'):
            transfer_armature_bonedata(active_object, targets, bone_filter=bone_filter, data_mode=self.data_mode, sync_bone_collections=self.sync_bone_collections)
            self.report({'INFO'}, f"Re-evaluated {len(targets)} armature(s) from '{active_object.name}'")

        return {'FINISHED'}
