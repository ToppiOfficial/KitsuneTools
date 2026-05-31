import bpy, math, bmesh
import mathutils
from mathutils import Vector
from bpy.types import Object, BoneCollection, EditBone


# -- Bone collection helpers ---------------------------------------------------

def ensure_bone_collection(arm: Object, name: str, parent_coll: BoneCollection | None = None) -> BoneCollection:
    coll = next((c for c in arm.data.collections_all if c.name == name), None)
    if coll is None:
        if parent_coll is not None:
            coll = arm.data.collections.new(name, parent=parent_coll)
        else:
            coll = arm.data.collections.new(name)
    elif parent_coll is not None and coll.parent != parent_coll:
        coll.parent = parent_coll
    return coll


def find_layer_collection(root_lc, name: str):
    if root_lc.name == name:
        return root_lc
    for child in root_lc.children:
        result = find_layer_collection(child, name)
        if result is not None:
            return result
    return None


# -- Chain traversal -----------------------------------------------------------

def collect_chain(bones, start_name: str, end_name: str) -> list:
    if not (start_name and end_name):
        return []
    if start_name not in bones or end_name not in bones:
        return []

    start_bone = bones[start_name]
    end_bone = bones[end_name]

    chain = []
    current = end_bone
    while current:
        chain.append(current)
        if current == start_bone:
            break
        current = current.parent

    if chain and chain[-1] == start_bone:
        chain.reverse()
        return chain
    return []


def find_intermediate_bones_in_chain(start_bone: EditBone, end_bone: EditBone) -> list:
    intermediates = []
    current = start_bone
    while current.children:
        if len(current.children) != 1:
            break
        child = current.children[0]
        if child == end_bone:
            break
        intermediates.append(child)
        current = child
    return intermediates


# -- Twist bones ---------------------------------------------------------------

def create_twist_bones(arm: Object, bone_name: str, count: int) -> list[str]:
    if count <= 0:
        return []

    bone = arm.data.edit_bones.get(bone_name)
    if not bone:
        return []

    base_head = bone.head.copy()
    total_vec = bone.tail - bone.head
    seg = 1.0 / count
    names = []

    for i in range(count):
        twist_name = f"{bone_name}.{str(i + 1).zfill(3)}"
        existing = arm.data.edit_bones.get(twist_name)
        tb = existing if existing else arm.data.edit_bones.new(twist_name)
        tb.head = base_head + total_vec * (i * seg)
        tb.tail = base_head + total_vec * ((i + 1) * seg)
        tb.roll = bone.roll
        tb.parent = bone
        tb.use_connect = False
        names.append(twist_name)

    return names


# -- Y-rotation drivers (same approach as HM1) ---------------------------------

def add_twist_driver(arm: Object, pb, target_bone_name: str, influence: float, invert: bool = False) -> None:
    pb.rotation_mode = 'XYZ'

    try:
        pb.driver_remove('rotation_euler', 1)
    except Exception:
        pass

    fc = pb.driver_add('rotation_euler', 1)
    drv = fc.driver
    drv.type = 'SCRIPTED'

    var = drv.variables.new()
    var.name = 'twist'
    var.type = 'TRANSFORMS'
    t = var.targets[0]
    t.id = arm
    t.bone_target = target_bone_name
    t.transform_type = 'ROT_Y'
    t.transform_space = 'LOCAL_SPACE'
    t.rotation_mode = 'SWING_TWIST_Y'

    sign = '-' if invert else ''
    drv.expression = f'{sign}twist * {influence:.6f}'


# -- Roll recalculation --------------------------------------------------------

def recalculate_rolls_for_ik(arm: Object, bone_names: list[str], ref_vector: Vector) -> None:
    world_ref = arm.matrix_world.to_3x3() @ ref_vector
    for name in bone_names:
        eb = arm.data.edit_bones.get(name)
        if eb:
            eb.align_roll(world_ref)


# -- Pole target placement -----------------------------------------------------

def get_pole_offset(mid_editbone: EditBone, offset_distance: float, arm_matrix_world) -> Vector:
    bone_dir = (mid_editbone.tail - mid_editbone.head).normalized()
    world_up = arm_matrix_world.to_3x3() @ Vector((0, 0, 1))
    side = bone_dir.cross(world_up)
    if side.length < 0.001:
        side = Vector((1, 0, 0))
    side.normalize()
    world_head = arm_matrix_world @ mid_editbone.head
    world_pole = world_head + side * offset_distance
    return arm_matrix_world.inverted() @ world_pole


# -- Custom shapes -------------------------------------------------------------

_SHAPES_COLLECTION = "HM2 Shapes"

_SHAPE_NAMES = {
    'box':      'HM2Shape_Box',
    'circle':   'HM2Shape_Circle',
    'sphere':   'HM2Shape_Sphere',
    'arrow':    'HM2Shape_Arrow',
    'goggle':   'HM2Shape_Goggle',
    'shoulder': 'HM2Shape_Shoulder',
    'master':   'HM2Shape_Master',
}


def _create_box_mesh(name: str) -> Object:
    """Wireframe box (edges only, no faces) so it looks like a cage in the viewport."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    # half-extents: wide left-right, moderate depth along bone, tall up-down
    # Unit box ±0.5 so custom_shape_scale_xyz gives exact world dimensions:
    # scale_xyz = (width, depth, height) -> box spans exactly that size.
    w = d = h = 0.5
    vs = [
        bm.verts.new(Vector((-w, -d, -h))), bm.verts.new(Vector(( w, -d, -h))),
        bm.verts.new(Vector(( w,  d, -h))), bm.verts.new(Vector((-w,  d, -h))),
        bm.verts.new(Vector((-w, -d,  h))), bm.verts.new(Vector(( w, -d,  h))),
        bm.verts.new(Vector(( w,  d,  h))), bm.verts.new(Vector((-w,  d,  h))),
    ]
    for a, b in [(0,1),(1,2),(2,3),(3,0), (4,5),(5,6),(6,7),(7,4),
                 (0,4),(1,5),(2,6),(3,7)]:
        bm.edges.new((vs[a], vs[b]))
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    return obj


def _create_circle_mesh(name: str) -> Object:
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, cap_ends=False, segments=32, radius=1.0)
    # Rotate 90° around X so the circle lies in the bone's local XZ plane,
    # i.e. perpendicular to the bone direction (+Y).  This makes the shape
    # appear as a halo ring around the bone from any viewing angle.
    rot = mathutils.Matrix.Rotation(math.pi / 2, 4, 'X')
    bmesh.ops.transform(bm, matrix=rot, verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    return obj


def _create_sphere_mesh(name: str) -> Object:
    """Three orthogonal great circles (XZ / XY / YZ) - same wireframe look as Blender's bone display."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    seg = 32
    for ai, bi in ((0, 2), (0, 1), (1, 2)):   # XZ, XY, YZ planes
        ring = []
        for i in range(seg):
            t = (i / seg) * 2 * math.pi
            co = [0.0, 0.0, 0.0]
            co[ai] = math.cos(t)
            co[bi] = math.sin(t)
            ring.append(bm.verts.new(Vector(co)))
        for i in range(seg):
            bm.edges.new((ring[i], ring[(i + 1) % seg]))
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    return obj


def _create_arrow_mesh(name: str) -> Object:
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    # Arrow pointing in +Y (= bone direction in Blender custom shapes).
    # Stem 0->0.6, arrowhead 0.6->1.0.
    verts = [
        bm.verts.new(Vector((-0.1, 0,   0))),
        bm.verts.new(Vector(( 0.1, 0,   0))),
        bm.verts.new(Vector(( 0.1, 0.6, 0))),
        bm.verts.new(Vector((-0.1, 0.6, 0))),
        bm.verts.new(Vector((-0.25, 0.6, 0))),
        bm.verts.new(Vector(( 0.25, 0.6, 0))),
        bm.verts.new(Vector(( 0,   1.0, 0))),
    ]
    bm.edges.new((verts[0], verts[1]))
    bm.edges.new((verts[1], verts[2]))
    bm.edges.new((verts[2], verts[3]))
    bm.edges.new((verts[3], verts[0]))
    bm.edges.new((verts[4], verts[5]))
    bm.edges.new((verts[5], verts[6]))
    bm.edges.new((verts[6], verts[4]))
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    return obj


def _create_goggle_mesh(name: str) -> Object:
    """Two goggle rings in the bone's XZ plane (perpendicular to +Y bone dir)."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    seg = 20
    r   = 0.38
    off = 0.52  # X offset of each goggle's centre

    for x_off in (off, -off):
        ring = []
        for i in range(seg):
            a = (i / seg) * 2 * math.pi
            ring.append(bm.verts.new(Vector((x_off + r * math.cos(a), 0.0, r * math.sin(a)))))
        for i in range(seg):
            bm.edges.new((ring[i], ring[(i + 1) % seg]))

    # Nose-bridge connecting the two inner edges
    v0 = bm.verts.new(Vector(( off - r, 0.0, 0.0)))
    v1 = bm.verts.new(Vector((-off + r, 0.0, 0.0)))
    bm.edges.new((v0, v1))

    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    return obj


def _create_master_mesh(name: str) -> Object:
    """
    Circle + 4 inward arrows in the XY plane.
    Flat on the floor when the bone points forward (+Y world).
    This is the standard master/ground-root control shape.
    """
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()

    # Outer ring
    seg = 32
    ring = []
    for i in range(seg):
        a = (i / seg) * 2 * math.pi
        ring.append(bm.verts.new(Vector((math.cos(a), math.sin(a), 0.0))))
    for i in range(seg):
        bm.edges.new((ring[i], ring[(i + 1) % seg]))

    # 4 arrows pointing outward: +X, -X, +Y, -Y
    sw  = 0.07   # stem half-width
    hw  = 0.18   # arrowhead half-width
    s0  = 0.12   # stem inner radius
    s1  = 0.60   # stem outer / arrowhead base radius
    tip = 0.85   # arrowhead tip radius

    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        px, py = -dy, dx  # perpendicular to arrow direction (90° CCW)

        # Stem - two side edges + inner cap
        vs0 = bm.verts.new(Vector((dx*s0 + px*sw, dy*s0 + py*sw, 0.0)))
        vs1 = bm.verts.new(Vector((dx*s0 - px*sw, dy*s0 - py*sw, 0.0)))
        vs2 = bm.verts.new(Vector((dx*s1 + px*sw, dy*s1 + py*sw, 0.0)))
        vs3 = bm.verts.new(Vector((dx*s1 - px*sw, dy*s1 - py*sw, 0.0)))
        bm.edges.new((vs0, vs1))
        bm.edges.new((vs0, vs2))
        bm.edges.new((vs1, vs3))

        # Arrowhead - base + two sides to tip
        va0 = bm.verts.new(Vector((dx*s1 + px*hw, dy*s1 + py*hw, 0.0)))
        va1 = bm.verts.new(Vector((dx*s1 - px*hw, dy*s1 - py*hw, 0.0)))
        vt  = bm.verts.new(Vector((dx*tip, dy*tip, 0.0)))
        bm.edges.new((va0, va1))
        bm.edges.new((va0, vt))
        bm.edges.new((va1, vt))

    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    return obj


def _create_shoulder_mesh(name: str) -> Object:
    """240° arc in the XZ plane - scapula / shoulder-blade controller shape."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    seg = 20
    start = math.radians(-120)
    end   = math.radians(120)
    verts = []
    for i in range(seg + 1):
        t = start + (end - start) * i / seg
        verts.append(bm.verts.new(Vector((math.cos(t), 0.0, math.sin(t)))))
    for i in range(seg):
        bm.edges.new((verts[i], verts[i + 1]))
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    return obj


def ensure_hm2_shapes(context) -> dict[str, Object]:
    shapes = {}
    scene = context.scene

    shapes_coll = bpy.data.collections.get(_SHAPES_COLLECTION)
    if shapes_coll is None:
        shapes_coll = bpy.data.collections.new(_SHAPES_COLLECTION)
        scene.collection.children.link(shapes_coll)

    lc = find_layer_collection(context.view_layer.layer_collection, _SHAPES_COLLECTION)
    if lc:
        lc.exclude = True

    creators = {
        'box':      _create_box_mesh,
        'circle':   _create_circle_mesh,
        'sphere':   _create_sphere_mesh,
        'arrow':    _create_arrow_mesh,
        'goggle':   _create_goggle_mesh,
        'shoulder': _create_shoulder_mesh,
        'master':   _create_master_mesh,
    }

    for key, shape_name in _SHAPE_NAMES.items():
        # Always recreate so geometry changes (e.g. arrow direction fix) take effect.
        old = bpy.data.objects.get(shape_name)
        if old is not None:
            old_mesh = old.data
            bpy.data.objects.remove(old, do_unlink=True)
            if old_mesh and old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)
        obj = creators[key](shape_name)
        shapes_coll.objects.link(obj)
        obj.hide_render = True
        shapes[key] = obj

    return shapes


# -- Validation ----------------------------------------------------------------

def hm2_validate_mapping(arm: Object, hm2) -> list[str]:
    errors = []
    bones = arm.data.bones

    required = {
        'Root': hm2.hm2_map_root,
        'Chest': hm2.hm2_map_chest,
        'Neck': hm2.hm2_map_neck,
        'Head': hm2.hm2_map_head,
    }
    for label, val in required.items():
        if not val:
            errors.append(f"Required: {label} is not assigned")
        elif val not in bones:
            errors.append(f"Required: {label} bone '{val}' not found in armature")

    if hm2.hm2_spine_count < 1:
        errors.append("Spine Count must be at least 1")

    chain_checks = []
    if hm2.hm2_map_root and hm2.hm2_map_chest:
        chain_checks.append((hm2.hm2_map_root, hm2.hm2_map_chest, "Root → Chest"))
    if hm2.hm2_map_chest and hm2.hm2_map_head:
        chain_checks.append((hm2.hm2_map_chest, hm2.hm2_map_head, "Chest → Head"))

    for side, attrs in [
        ('L', ('hm2_map_hip_l', 'hm2_map_knee_l', 'hm2_map_ankle_l')),
        ('R', ('hm2_map_hip_r', 'hm2_map_knee_r', 'hm2_map_ankle_r')),
    ]:
        hip, knee, ankle = (getattr(hm2, a) for a in attrs)
        if hip and knee:
            chain_checks.append((hip, knee, f"{side} Hip → Knee"))
        if knee and ankle:
            chain_checks.append((knee, ankle, f"{side} Knee → Ankle"))

    for side, attrs in [
        ('L', ('hm2_map_shoulder_l', 'hm2_map_elbow_l', 'hm2_map_hand_l')),
        ('R', ('hm2_map_shoulder_r', 'hm2_map_elbow_r', 'hm2_map_hand_r')),
    ]:
        shoulder, elbow, hand = (getattr(hm2, a) for a in attrs)
        if shoulder and elbow:
            chain_checks.append((shoulder, elbow, f"{side} Shoulder → Elbow"))
        if elbow and hand:
            chain_checks.append((elbow, hand, f"{side} Elbow → Hand"))

    for start, end, label in chain_checks:
        if start in bones and end in bones:
            chain = collect_chain(bones, start, end)
            if not chain:
                errors.append(f"Broken chain: {label} ({start} is not an ancestor of {end})")

    for i, finger in enumerate(hm2.hm2_fingers):
        if finger.source_bone and finger.source_bone not in bones:
            errors.append(f"Finger #{i+1}: bone '{finger.source_bone}' not found")

    return errors
