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
    'goggle':   'HM2Shape_Goggle',
    'shoulder': 'HM2Shape_Shoulder',
    'master':   'HM2Shape_Master',
    'limb_end': 'HM2Shape_LimbEnd',
    'line':     'HM2Shape_Line',
    'pivot':    'HM2Shape_Pivot',
    'twist':    'HM2Shape_Twist',
    'hand':     'HM2Shape_Hand',
    'foot':     'HM2Shape_Foot',
    'finger_master': 'HM2Shape_FingerMaster',
}


def _mesh_from_pydata(name: str, verts, edges) -> Object:
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    return bpy.data.objects.new(name, mesh)


def _create_box_mesh(name: str) -> Object:
    """Rigify create_cube_widget : wireframe cube at ±0.5."""
    r = 0.5
    return _mesh_from_pydata(name,
        [(r,r,r),(r,-r,r),(-r,-r,r),(-r,r,r),(r,r,-r),(r,-r,-r),(-r,-r,-r),(-r,r,-r)],
        [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)],
    )


def _create_circle_mesh(name: str) -> Object:
    """Rigify create_circle_widget : 32-segment ring in the XZ plane (perpendicular to bone +Y)."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, cap_ends=False, segments=32, radius=1.0)
    rot = mathutils.Matrix.Rotation(math.pi / 2, 4, 'X')
    bmesh.ops.transform(bm, matrix=rot, verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    return bpy.data.objects.new(name, mesh)


def _create_sphere_mesh(name: str) -> Object:
    """Rigify create_sphere_widget : three perpendicular 16-segment circles, scaled to radius 1."""
    # Exact Rigify vertex data (original radius 0.5), scaled ×2 → radius 1.0
    s = 2.0
    verts = [
        (0.3535533845424652*s, 0.3535533845424652*s, 0.0),
        (0.4619397521018982*s, 0.19134171307086945*s, 0.0),
        (0.5*s, -2.1855694143368964e-08*s, 0.0),
        (0.4619397521018982*s, -0.19134175777435303*s, 0.0),
        (0.3535533845424652*s, -0.3535533845424652*s, 0.0),
        (0.19134174287319183*s, -0.4619397521018982*s, 0.0),
        (7.549790126404332e-08*s, -0.5*s, 0.0),
        (-0.1913416087627411*s, -0.46193981170654297*s, 0.0),
        (-0.35355329513549805*s, -0.35355350375175476*s, 0.0),
        (-0.4619397521018982*s, -0.19134178757667542*s, 0.0),
        (-0.5*s, 5.962440319251527e-09*s, 0.0),
        (-0.4619397222995758*s, 0.1913418024778366*s, 0.0),
        (-0.35355326533317566*s, 0.35355350375175476*s, 0.0),
        (-0.19134148955345154*s, 0.46193987131118774*s, 0.0),
        (3.2584136988589307e-07*s, 0.5*s, 0.0),
        (0.1913420855998993*s, 0.46193960309028625*s, 0.0),
        (7.450580596923828e-08*s, 0.46193960309028625*s, 0.19134199619293213*s),
        (5.9254205098113744e-08*s, 0.5*s, 2.323586443253589e-07*s),
        (4.470348358154297e-08*s, 0.46193987131118774*s, -0.1913415789604187*s),
        (2.9802322387695312e-08*s, 0.35355350375175476*s, -0.3535533547401428*s),
        (2.9802322387695312e-08*s, 0.19134178757667542*s, -0.46193981170654297*s),
        (5.960464477539063e-08*s, -1.1151834122813398e-08*s, -0.5000000596046448*s),
        (5.960464477539063e-08*s, -0.1913418024778366*s, -0.46193984150886536*s),
        (5.960464477539063e-08*s, -0.35355350375175476*s, -0.3535533845424652*s),
        (7.450580596923828e-08*s, -0.46193981170654297*s, -0.19134166836738586*s),
        (9.348272556053416e-08*s, -0.5*s, 1.624372103492533e-08*s),
        (1.043081283569336e-07*s, -0.4619397521018982*s, 0.19134168326854706*s),
        (1.1920928955078125e-07*s, -0.3535533845424652*s, 0.35355329513549805*s),
        (1.1920928955078125e-07*s, -0.19134174287319183*s, 0.46193966269493103*s),
        (1.1920928955078125e-07*s, -4.7414250303745575e-09*s, 0.49999991059303284*s),
        (1.1920928955078125e-07*s, 0.19134172797203064*s, 0.46193966269493103*s),
        (8.940696716308594e-08*s, 0.3535533845424652*s, 0.35355329513549805*s),
        (0.3535534739494324*s, 0.0*s, 0.35355329513549805*s),
        (0.1913418173789978*s, -2.9802322387695312e-08*s, 0.46193966269493103*s),
        (8.303572940349113e-08*s, -5.005858838558197e-08*s, 0.49999991059303284*s),
        (-0.19134165346622467*s, -5.960464477539063e-08*s, 0.46193966269493103*s),
        (-0.35355329513549805*s, -8.940696716308594e-08*s, 0.35355329513549805*s),
        (-0.46193963289260864*s, -5.960464477539063e-08*s, 0.19134168326854706*s),
        (-0.49999991059303284*s, -5.960464477539063e-08*s, 1.624372103492533e-08*s),
        (-0.4619397521018982*s, -2.9802322387695312e-08*s, -0.19134166836738586*s),
        (-0.3535534143447876*s, -2.9802322387695312e-08*s, -0.3535533845424652*s),
        (-0.19134171307086945*s, 0.0*s, -0.46193984150886536*s),
        (7.662531942287387e-08*s, 9.546055501630235e-09*s, -0.5000000596046448*s),
        (0.19134187698364258*s, 5.960464477539063e-08*s, -0.46193981170654297*s),
        (0.3535535931587219*s, 5.960464477539063e-08*s, -0.3535533547401428*s),
        (0.4619399905204773*s, 5.960464477539063e-08*s, -0.1913415789604187*s),
        (0.5000000596046448*s, 5.960464477539063e-08*s, 2.323586443253589e-07*s),
        (0.4619396924972534*s, 2.9802322387695312e-08*s, 0.19134199619293213*s),
    ]
    edges = [
        (0,1),(1,2),(2,3),(3,4),(4,5),(5,6),(6,7),(7,8),(8,9),(9,10),
        (10,11),(11,12),(12,13),(13,14),(14,15),(0,15),(16,31),(16,17),
        (17,18),(18,19),(19,20),(20,21),(21,22),(22,23),(23,24),(24,25),
        (25,26),(26,27),(27,28),(28,29),(29,30),(30,31),(32,33),(33,34),
        (34,35),(35,36),(36,37),(37,38),(38,39),(39,40),(40,41),(41,42),
        (42,43),(43,44),(44,45),(45,46),(46,47),(32,47),
    ]
    return _mesh_from_pydata(name, verts, edges)


def _gen_circle_2d(verts: list, edges: list, center, radius: float,
                   angle_range=None, steps: int = 24) -> None:
    """Port of Rigify generate_circle_geometry working in a 2D (x, y) list.
    Appends an open arc when angle_range is given, else a closed loop."""
    assert steps >= 3
    start = 0.0
    delta = math.pi * 2 / steps
    closed = True
    if angle_range:
        start, end = angle_range
        closed = False
        if start == end:
            steps = 1
        else:
            steps = max(3, math.ceil(abs(end - start) / delta) + 1)
            delta = (end - start) / (steps - 1)
    base = len(verts)
    for i in range(steps):
        a = start + delta * i
        verts.append((center[0] + math.cos(a) * radius, center[1] + math.sin(a) * radius))
        if i > 0:
            edges.append((base + i - 1, base + i))
    if closed:
        edges.append((len(verts) - 1, base))


def _gen_circle_hull_2d(verts: list, edges: list, points: list,
                        radius: float, gap: float, steps: int = 24) -> None:
    """Port of Rigify generate_circle_hull_geometry : wraps a rounded outline
    around a set of 2D points (a 'goggle' hull for two eyes)."""
    assert radius >= gap
    if len(points) <= 1:
        if points:
            _gen_circle_2d(verts, edges, points[0], radius, steps=steps)
        return
    base = len(verts)
    points_ex = [points[-1], *points, points[0]]
    angle_gap = math.asin(gap / radius)
    for pt_prev, pt_cur, pt_next in zip(points_ex[0:], points_ex[1:], points_ex[2:]):
        vec_prev = (pt_prev[0] - pt_cur[0], pt_prev[1] - pt_cur[1])
        vec_next = (pt_next[0] - pt_cur[0], pt_next[1] - pt_cur[1])
        len_prev = math.hypot(*vec_prev)
        len_next = math.hypot(*vec_next)
        angle_prev = math.atan2(vec_prev[1], vec_prev[0])
        angle_next = math.atan2(vec_next[1], vec_next[0])
        if angle_next <= angle_prev:
            angle_next += math.pi * 2
        angle_prev += max(angle_gap, math.acos(min(1, len_prev / radius / 2)))
        angle_next -= max(angle_gap, math.acos(min(1, len_next / radius / 2)))
        if angle_next > angle_prev:
            if len(verts) > base:
                edges.append((len(verts) - 1, len(verts)))
            _gen_circle_2d(verts, edges, pt_cur, radius,
                           angle_range=(angle_prev, angle_next), steps=steps)
    if len(verts) > base:
        edges.append((len(verts) - 1, base))


def _create_goggle_mesh(name: str) -> Object:
    """Rigify create_eye_cluster_widget : two nested rounded hulls ('goggles')
    wrapping the pair of eye points. Ported from rigify.rigs.face.skin_eye:
    the combined/master eye look target widget. Built in the bone's XZ plane
    (perpendicular to the +Y bone direction) so it faces down the look axis."""
    size = 0.4
    points = [(-0.5, 0.0), (0.5, 0.0)]
    verts2d: list = []
    edges: list = []
    _gen_circle_hull_2d(verts2d, edges, points, size * 0.75, size * 0.6)
    _gen_circle_hull_2d(verts2d, edges, points, size, size * 0.85)
    # Map 2D (x, y) into the bone's XZ plane (y = 0, perpendicular to bone +Y).
    verts = [(x, 0.0, y) for (x, y) in verts2d]
    return _mesh_from_pydata(name, verts, edges)


def _create_master_mesh(name: str) -> Object:
    """Rigify create_root_widget : 4-directional arrow root control."""
    verts = [
        (0.7071067690849304, 0.7071067690849304, 0.0), (0.7071067690849304, -0.7071067690849304, 0.0),
        (-0.7071067690849304, 0.7071067690849304, 0.0), (-0.7071067690849304, -0.7071067690849304, 0.0),
        (0.8314696550369263, 0.5555701851844788, 0.0), (0.8314696550369263, -0.5555701851844788, 0.0),
        (-0.8314696550369263, 0.5555701851844788, 0.0), (-0.8314696550369263, -0.5555701851844788, 0.0),
        (0.9238795042037964, 0.3826834261417389, 0.0), (0.9238795042037964, -0.3826834261417389, 0.0),
        (-0.9238795042037964, 0.3826834261417389, 0.0), (-0.9238795042037964, -0.3826834261417389, 0.0),
        (0.9807852506637573, 0.19509035348892212, 0.0), (0.9807852506637573, -0.19509035348892212, 0.0),
        (-0.9807852506637573, 0.19509035348892212, 0.0), (-0.9807852506637573, -0.19509035348892212, 0.0),
        (0.19509197771549225, 0.9807849526405334, 0.0), (0.19509197771549225, -0.9807849526405334, 0.0),
        (-0.19509197771549225, 0.9807849526405334, 0.0), (-0.19509197771549225, -0.9807849526405334, 0.0),
        (0.3826850652694702, 0.9238788485527039, 0.0), (0.3826850652694702, -0.9238788485527039, 0.0),
        (-0.3826850652694702, 0.9238788485527039, 0.0), (-0.3826850652694702, -0.9238788485527039, 0.0),
        (0.5555717945098877, 0.8314685821533203, 0.0), (0.5555717945098877, -0.8314685821533203, 0.0),
        (-0.5555717945098877, 0.8314685821533203, 0.0), (-0.5555717945098877, -0.8314685821533203, 0.0),
        (0.19509197771549225, 1.2807848453521729, 0.0), (0.19509197771549225, -1.2807848453521729, 0.0),
        (-0.19509197771549225, 1.2807848453521729, 0.0), (-0.19509197771549225, -1.2807848453521729, 0.0),
        (1.280785322189331, 0.19509035348892212, 0.0), (1.280785322189331, -0.19509035348892212, 0.0),
        (-1.280785322189331, 0.19509035348892212, 0.0), (-1.280785322189331, -0.19509035348892212, 0.0),
        (0.3950919806957245, 1.2807848453521729, 0.0), (0.3950919806957245, -1.2807848453521729, 0.0),
        (-0.3950919806957245, 1.2807848453521729, 0.0), (-0.3950919806957245, -1.2807848453521729, 0.0),
        (1.280785322189331, 0.39509034156799316, 0.0), (1.280785322189331, -0.39509034156799316, 0.0),
        (-1.280785322189331, 0.39509034156799316, 0.0), (-1.280785322189331, -0.39509034156799316, 0.0),
        (0.0, 1.5807849168777466, 0.0), (0.0, -1.5807849168777466, 0.0),
        (1.5807852745056152, 0.0, 0.0), (-1.5807852745056152, 0.0, 0.0),
    ]
    edges = [
        (0,4),(1,5),(2,6),(3,7),(4,8),(5,9),(6,10),(7,11),(8,12),
        (9,13),(10,14),(11,15),(16,20),(17,21),(18,22),(19,23),(20,24),
        (21,25),(22,26),(23,27),(0,24),(1,25),(2,26),(3,27),(16,28),
        (17,29),(18,30),(19,31),(12,32),(13,33),(14,34),(15,35),(28,36),
        (29,37),(30,38),(31,39),(32,40),(33,41),(34,42),(35,43),(36,44),
        (37,45),(38,44),(39,45),(40,46),(41,46),(42,47),(43,47),
    ]
    return _mesh_from_pydata(name, verts, edges)


def _create_shoulder_mesh(name: str) -> Object:
    """Rigify create_shoulder_widget : scapula/shoulder bone shape."""
    r = 1.0  # radius * 2, default radius=0.5
    verts = [
        (0, 0, 0), (0, 1, 0),
        (0.41214*r, 0.5+(0.276111-0.5)*r, 0.282165*r),
        (0.469006*r, 0.5+(0.31436-0.5)*r, 0.168047*r),
        (0.492711*r, 0.5+(0.370708-0.5)*r, 0.0740018*r),
        (0.498419*r, 0.5+(0.440597-0.5)*r, 0.0160567*r),
        (0.5*r, 0.5, 0),
        (0.498419*r, 0.5+(0.559402-0.5)*r, 0.0160563*r),
        (0.492712*r, 0.5+(0.629291-0.5)*r, 0.074001*r),
        (0.469006*r, 0.5+(0.68564-0.5)*r, 0.168046*r),
        (0.412141*r, 0.5+(0.723889-0.5)*r, 0.282164*r),
        (0.316952*r, 0.5+(0.742335-0.5)*r, 0.383591*r),
        (0.207152*r, 0.5+(0.74771-0.5)*r, 0.453489*r),
        (0.0999976*r, 0.5+(0.74949-0.5)*r, 0.489649*r),
        (0, 0.5+(0.75-0.5)*r, 0.5*r),
        (-0.099997*r, 0.5+(0.74949-0.5)*r, 0.489649*r),
        (-0.207152*r, 0.5+(0.74771-0.5)*r, 0.453489*r),
        (-0.316951*r, 0.5+(0.742335-0.5)*r, 0.383592*r),
        (-0.412141*r, 0.5+(0.723889-0.5)*r, 0.282165*r),
        (-0.469006*r, 0.5+(0.68564-0.5)*r, 0.168046*r),
        (-0.492711*r, 0.5+(0.629291-0.5)*r, 0.0740011*r),
        (-0.498419*r, 0.5+(0.559402-0.5)*r, 0.0160563*r),
        (-0.5*r, 0.5, 0),
        (-0.498419*r, 0.5+(0.440598-0.5)*r, 0.0160563*r),
        (-0.492711*r, 0.5+(0.370709-0.5)*r, 0.0740012*r),
        (-0.469006*r, 0.5+(0.31436-0.5)*r, 0.168047*r),
        (-0.41214*r, 0.5+(0.276111-0.5)*r, 0.282165*r),
        (-0.316951*r, 0.5+(0.257665-0.5)*r, 0.383592*r),
        (-0.207151*r, 0.5+(0.25229-0.5)*r, 0.453489*r),
        (-0.0999959*r, 0.5+(0.25051-0.5)*r, 0.489649*r),
        (0, 0.5+(0.25-0.5)*r, 0.5*r),
        (0.0999986*r, 0.5+(0.25051-0.5)*r, 0.489648*r),
        (0.207153*r, 0.5+(0.25229-0.5)*r, 0.453488*r),
        (0.316953*r, 0.5+(0.257665-0.5)*r, 0.38359*r),
    ]
    edges = [
        (0,1),(2,3),(4,3),(5,4),(5,6),(6,7),(8,7),(8,9),(10,9),(10,11),
        (11,12),(13,12),(14,13),(14,15),(16,15),(16,17),(17,18),(19,18),(19,20),(21,20),
        (21,22),(22,23),(24,23),(25,24),(25,26),(27,26),(27,28),(29,28),(29,30),(30,31),
        (32,31),(32,33),(2,33),
    ]
    return _mesh_from_pydata(name, verts, edges)


def _create_limb_end_mesh(name: str) -> Object:
    """Rigify create_diamond_widget : octahedron IK end-effector, scaled to radius 1."""
    r = 1.0  # Rigify default r=0.5, scaled ×2
    verts = [(r,0,0),(0,-r,0),(0,r,0),(0,0,-r),(0,0,r),(-r,0,0)]
    edges = [(0,1),(2,3),(4,5),(1,5),(5,2),(0,2),(4,2),(3,1),(1,4),(5,3),(3,0),(4,0)]
    return _mesh_from_pydata(name, verts, edges)


def _create_line_mesh(name: str) -> Object:
    """Rigify create_line_widget : simple line spanning bone length."""
    return _mesh_from_pydata(name, [(0,0,0),(0,1,0)], [(0,1)])


def _create_pivot_mesh(name: str) -> Object:
    """Rigify create_pivot_widget (square=True) : plain-axes with square caps, scaled to radius 1."""
    # Rigify default radius=0.5 → axis=0.5, cap=0.05; scaled ×2 → axis=1.0, cap=0.1
    a, c = 1.0, 0.1
    verts = [
        (0,0,-a),(-a,0,0),(0,0,a),(a,0,0),(a,c,-c),(a,c,c),(0,-a,0),(0,a,0),
        (c,a,c),(c,a,-c),(a,-c,-c),(a,-c,c),(-c,a,c),(-c,a,-c),(-a,c,c),(-a,c,-c),
        (-a,-c,c),(-a,-c,-c),(-c,-a,c),(-c,-a,-c),(c,-a,c),(c,-a,-c),
        (-c,-c,-a),(-c,c,-a),(c,-c,-a),(c,c,-a),(-c,c,a),(-c,-c,a),(c,c,a),(c,-c,a),
    ]
    edges = [
        (10,4),(4,5),(8,9),(0,2),(12,8),(6,7),(11,10),(13,12),(5,11),(9,13),(3,1),
        (14,15),(16,14),(17,16),(15,17),(18,19),(20,18),(21,20),(19,21),
        (22,23),(24,22),(25,24),(23,25),(26,27),(28,26),(29,28),(27,29),
    ]
    return _mesh_from_pydata(name, verts, edges)


def _create_twist_mesh(name: str) -> Object:
    """Twist control widget : a ring that encircles the limb (lying in the bone's
    XZ plane, perpendicular to the +Y bone axis) with an inner curved arrow that
    reads as the rotation/twist direction. Sized absolutely at assignment time so
    it stays as visible as the FK/IK controllers."""
    verts: list = []
    edges: list = []

    def _ring(radius: float, steps: int) -> None:
        base = len(verts)
        for i in range(steps):
            a = 2 * math.pi * i / steps
            verts.append((radius * math.sin(a), 0.0, radius * math.cos(a)))
        for i in range(steps):
            edges.append((base + i, base + (i + 1) % steps))

    def _double_arrow() -> None:
        # Rigify create_ik_arrow_widget : two parallel flat arrows offset in Z,
        # forming one thick 3D arrow running along the bone axis (+Y).
        s = 1.3   # a touch larger than the bare Rigify widget
        base = len(verts)
        av = [
            (x * s, y * s, z * s) for (x, y, z) in (
                ( 0.1, 0.0, -0.3), ( 0.1, 0.7, -0.3), (-0.1, 0.0, -0.3), (-0.1, 0.7, -0.3),
                ( 0.2, 0.7, -0.3), ( 0.0, 1.0, -0.3), (-0.2, 0.7, -0.3),
                ( 0.1, 0.0,  0.3), ( 0.1, 0.7,  0.3), (-0.1, 0.0,  0.3), (-0.1, 0.7,  0.3),
                ( 0.2, 0.7,  0.3), ( 0.0, 1.0,  0.3), (-0.2, 0.7,  0.3),
            )
        ]
        ae = [
            (0, 1), (2, 3), (1, 4), (4, 5), (3, 6), (5, 6), (0, 2),           # front arrow
            (7, 8), (9, 10), (8, 11), (11, 12), (10, 13), (12, 13), (7, 9),   # back arrow
        ]
        verts.extend(av)
        edges.extend((base + a, base + b) for a, b in ae)

    # Outer ring (the limb-encircling control) + the thick parallel arrow running
    # along the bone direction.
    _ring(1.0, 32)
    _double_arrow()
    return _mesh_from_pydata(name, verts, edges)


def _chaikin_closed(pts: list, iterations: int = 2) -> list:
    """Chaikin corner-cutting on a closed polygon : equivalent to Catmull-Clark on a
    closed edge loop with no faces (which is what Rigify's subsurf= does to wire shapes).
    Each iteration doubles the point count and smooths every corner.
    """
    for _ in range(iterations):
        n = len(pts)
        result = []
        for i in range(n):
            p0 = pts[i]
            p1 = pts[(i + 1) % n]
            result.append(tuple(0.75 * a + 0.25 * b for a, b in zip(p0, p1)))
            result.append(tuple(0.25 * a + 0.75 * b for a, b in zip(p0, p1)))
        pts = result
    return pts


def _create_hand_mesh(name: str) -> Object:
    """Rigify create_hand_widget smoothed with 2 Chaikin iterations (= subsurf=2).

    Control points are given in closed-loop order (front top → bottom → back bottom →
    top → close), so Chaikin rounds the 4 corners into smooth arcs.
    """
    ctrl = [
        (0.0,  1.5,   -0.7),   # top-front
        (0.0,  0.723, -0.7),   # mid-upper-front
        (0.0,  0.0,   -0.7),   # mid-lower-front
        (0.0, -0.25,  -0.7),   # bottom-front
        (0.0, -0.25,   0.7),   # bottom-back
        (0.0,  0.0,    0.7),   # mid-lower-back
        (0.0,  0.723,  0.7),   # mid-upper-back
        (0.0,  1.5,    0.7),   # top-back
    ]
    pts = _chaikin_closed(ctrl, 2)
    n = len(pts)
    edges = [(i, (i + 1) % n) for i in range(n)]
    return _mesh_from_pydata(name, pts, edges)


def _create_foot_mesh(name: str) -> Object:
    """Rigify create_foot_widget smoothed with 2 Chaikin iterations (= subsurf=2).

    Same topology as the hand widget but lying flat in the XY plane (Z=0).
    """
    ctrl = [
        (-0.7, -0.524, 0.0),   # bottom-left
        (-0.7,  0.253, 0.0),   # mid-left
        (-0.7,  0.976, 0.0),   # upper-mid-left
        (-0.7,  1.226, 0.0),   # top-left
        ( 0.7,  1.226, 0.0),   # top-right
        ( 0.7,  0.976, 0.0),   # upper-mid-right
        ( 0.7,  0.253, 0.0),   # mid-right
        ( 0.7, -0.524, 0.0),   # bottom-right
    ]
    pts = _chaikin_closed(ctrl, 2)
    n = len(pts)
    edges = [(i, (i + 1) % n) for i in range(n)]
    return _mesh_from_pydata(name, pts, edges)


def _create_finger_master_mesh(name: str) -> Object:
    """Rigify super_finger make_master_control_widget : a thin axis line along +Y
    with a small flag at the tip. Spans Y 0..1.1 so it scales to the whole finger
    when assigned with use_custom_shape_bone_size=True."""
    verts = [
        (0.0,   0.0, 0.0),   # 0 base
        (0.0,   1.0, 0.0),   # 1 tip
        (0.05,  1.0, 0.0),   # 2
        (0.05,  1.1, 0.0),   # 3
        (-0.05, 1.1, 0.0),   # 4
        (-0.05, 1.0, 0.0),   # 5
    ]
    edges = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 1)]
    return _mesh_from_pydata(name, verts, edges)


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
        'goggle':   _create_goggle_mesh,
        'shoulder': _create_shoulder_mesh,
        'master':   _create_master_mesh,
        'limb_end': _create_limb_end_mesh,
        'line':     _create_line_mesh,
        'pivot':    _create_pivot_mesh,
        'twist':    _create_twist_mesh,
        'hand':     _create_hand_mesh,
        'foot':     _create_foot_mesh,
        'finger_master': _create_finger_master_mesh,
    }

    for key, shape_name in _SHAPE_NAMES.items():
        obj = bpy.data.objects.get(shape_name)
        if obj is None or obj.data is None:
            # If object or its mesh data is missing, create it
            obj = creators[key](shape_name)
            shapes_coll.objects.link(obj)
        else:
            # If object exists and has data, ensure it's in the correct collection.
            # Check by name, as bpy_prop_collection.__contains__ expects a string.
            if obj.name not in shapes_coll.objects:
                for coll in list(obj.users_collection):
                    coll.objects.unlink(obj)
                shapes_coll.objects.link(obj)
        obj.hide_render = True # Ensure it's hidden
        shapes[key] = obj

    return shapes


# -- Validation ----------------------------------------------------------------

def hm2_validate_mapping(arm: Object, hm2) -> list[str]:
    errors = []
    bones = arm.data.bones

    first_person = getattr(hm2, 'hm2_first_person_mode', False)

    chain_checks = []

    if first_person:
        # First person: arms only. Each side that has any arm bone assigned must
        # have Shoulder, Elbow and Hand all set; at least one full arm is required.
        # Root is optional (generated if absent). Legs/spine/neck/head are ignored.
        complete_sides = 0
        for side, attrs in [
            ('L', ('hm2_map_shoulder_l', 'hm2_map_elbow_l', 'hm2_map_hand_l')),
            ('R', ('hm2_map_shoulder_r', 'hm2_map_elbow_r', 'hm2_map_hand_r')),
        ]:
            shoulder, elbow, hand = (getattr(hm2, a) for a in attrs)
            if not (shoulder or elbow or hand):
                continue
            for label, val in (('Shoulder', shoulder), ('Elbow', elbow), ('Hand', hand)):
                if not val:
                    errors.append(f"Required: {side} {label} is not assigned")
                elif val not in bones:
                    errors.append(f"Required: {side} {label} bone '{val}' not found in armature")
            if shoulder and elbow:
                chain_checks.append((shoulder, elbow, f"{side} Shoulder → Elbow"))
            if elbow and hand:
                chain_checks.append((elbow, hand, f"{side} Elbow → Hand"))
            if shoulder and elbow and hand:
                complete_sides += 1
        if complete_sides == 0:
            errors.append("First Person mode requires at least one full arm (Shoulder, Elbow, Hand)")
    else:
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


# -- First Person Arms ---------------------------------------------------------

# Prefixes used by HM2_OT_Process for the bones it generates (controllers,
# IK targets, mechanism, visualization and FK controls).
HM2_CONTROLLER_PREFIXES = ('CTRL_', 'IK_', 'MCH_', 'VIS_', 'FK_')


def get_hm2_shape(key: str) -> Object | None:
    """Return an already-created HM2 custom-shape widget object by key (e.g. 'box'),
    or None if it does not exist. Unlike ensure_hm2_shapes this never regenerates
    the widgets, so it won't disturb shapes already referenced by other rigs."""
    name = _SHAPE_NAMES.get(key)
    return bpy.data.objects.get(name) if name else None


def detect_hm2_rig(arm: Object) -> bool:
    """Heuristic: an armature is treated as an HM2 rig if it contains any of the
    controller bones that HM2_OT_Process generates."""
    return any(b.name.startswith(HM2_CONTROLLER_PREFIXES) for b in arm.data.bones)


def compute_fpa_kept_bones(arm: Object, start_l: str, start_r: str,
                           preserve_ik: bool, is_hm2: bool) -> set[str]:
    """Return the set of bone names to keep for a first-person-arms copy.

    Always keeps each starting bone plus all of its descendants (this naturally
    includes twist bones and, on an HM2 rig, the finger FK/MCH controls that are
    parented under the hand/joint deform bones).

    On an HM2 rig:
      * preserve_ik=True  -> additionally keep arm IK / pole / visualization
        controllers (which live under the root, not under the arm) by following
        constraint references to/from the kept bones, plus their ancestor chain.
      * preserve_ik=False -> drop every controller-prefixed bone, leaving plain
        FK deform bones (twist bones are kept since they carry no controller prefix).
    """
    bones = arm.data.bones

    desc: set[str] = set()
    for start_name in (start_l, start_r):
        b = bones.get(start_name) if start_name else None
        if not b:
            continue
        desc.add(b.name)
        for child in b.children_recursive:
            desc.add(child.name)

    if not is_hm2:
        return desc

    if not preserve_ik:
        return {name for name in desc if not name.startswith(HM2_CONTROLLER_PREFIXES)}

    kept = set(desc)

    def add_with_ancestors(bone_name: str) -> bool:
        cur = bones.get(bone_name)
        added = False
        while cur is not None and cur.name not in kept:
            kept.add(cur.name)
            added = True
            cur = cur.parent
        return added

    # Follow constraint references in both directions until stable:
    #   - a kept bone (e.g. arm deform) targeting a controller (IK_Hand / pole)
    #   - a controller targeting an already-kept bone (e.g. VIS pole line)
    pbones = arm.pose.bones
    changed = True
    while changed:
        changed = False
        for pb in pbones:
            is_ctrl = pb.name.startswith(HM2_CONTROLLER_PREFIXES)
            pb_kept = pb.name in kept
            if not (pb_kept or is_ctrl):
                continue
            for con in pb.constraints:
                tgt = getattr(con, 'target', None)
                if tgt is not None and tgt != arm:
                    continue
                for attr in ('subtarget', 'pole_subtarget'):
                    ref = getattr(con, attr, '')
                    if not ref:
                        continue
                    if pb_kept and ref.startswith(HM2_CONTROLLER_PREFIXES) and ref not in kept:
                        if add_with_ancestors(ref):
                            changed = True
                    if is_ctrl and not pb_kept and ref in kept:
                        if add_with_ancestors(pb.name):
                            changed = True
                            pb_kept = True

    return kept


def cull_mesh_to_bones(mesh: Object, kept_deform_names: set[str], threshold: float = 0.5,
                       use_bisect: bool = False, plane_cos_world=None, plane_nos_world=None) -> int:
    """Cull a mesh to geometry weighted to ``kept_deform_names``.

    A vertex is kept only when the kept vertex groups hold at least ``threshold``
    of its total weight (a dominance test, so a vertex barely grazed by a kept
    bone but mostly weighted elsewhere is dropped). Deleting a vertex removes its
    incident faces. Optionally bisects the result with one plane per entry in
    ``plane_cos_world``/``plane_nos_world`` (parallel lists), clearing the geometry
    on the +normal side of each plane for a clean straight cut. Returns the
    remaining polygon count.
    """
    kept_idx = {vg.index for vg in mesh.vertex_groups if vg.name in kept_deform_names}

    bm = bmesh.new()
    bm.from_mesh(mesh.data)
    bm.verts.ensure_lookup_table()

    me_verts = mesh.data.vertices
    del_verts = []
    for v in bm.verts:
        keep = False
        if kept_idx:
            total_w = 0.0
            kept_w = 0.0
            for g in me_verts[v.index].groups:
                w = g.weight
                if w <= 0.0:
                    continue
                total_w += w
                if g.group in kept_idx:
                    kept_w += w
            keep = total_w > 1e-6 and (kept_w / total_w) >= threshold
        if not keep:
            del_verts.append(v)

    if del_verts:
        bmesh.ops.delete(bm, geom=del_verts, context='VERTS')

    if use_bisect and plane_cos_world and plane_nos_world and len(bm.verts) > 0:
        inv = mesh.matrix_world.inverted()
        n3 = inv.to_3x3()

        # When the planes have different normals (opposite-sides case, e.g. L/R arms on ±X),
        # restrict each bisect to geometry on the same side as its plane so the two cuts don't
        # cancel each other out.  The centroid of all plane positions is the dividing line.
        normals_vary = (len(plane_nos_world) > 1 and any(
            abs(plane_nos_world[i].dot(plane_nos_world[0]) - 1.0) > 1e-4
            for i in range(1, len(plane_nos_world))
        ))
        if normals_vary:
            centroid_local = sum((inv @ c for c in plane_cos_world), Vector()) / len(plane_cos_world)

        for plane_co_w, plane_no_w in zip(plane_cos_world, plane_nos_world):
            if not bm.verts:
                break
            plane_co = inv @ plane_co_w
            plane_no = (n3 @ plane_no_w).normalized()

            if normals_vary:
                # Include only geometry on the keep side (same side as this bone relative to centroid).
                # Geometry on the other arm is excluded from geom and therefore untouched.
                keep_v = frozenset(
                    v for v in bm.verts
                    if (v.co - centroid_local).dot(plane_no) <= 0
                )
                keep_e = frozenset(
                    e for e in bm.edges
                    if e.verts[0] in keep_v or e.verts[1] in keep_v
                )
                keep_f = frozenset(
                    f for f in bm.faces
                    if any(v in keep_v for v in f.verts)
                )
                geom = list(keep_v) + list(keep_e) + list(keep_f)
            else:
                geom = list(bm.verts) + list(bm.edges) + list(bm.faces)

            if geom:
                bmesh.ops.bisect_plane(
                    bm, geom=geom, dist=1e-6,
                    plane_co=plane_co, plane_no=plane_no,
                    clear_inner=False, clear_outer=True,
                )

    bm.to_mesh(mesh.data)
    bm.free()
    mesh.data.update()
    return len(mesh.data.polygons)
