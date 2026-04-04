from bpy.app.translations import pgettext

#
#   GET FUNCTIONS
#

def get_label_with_object_name(label: str, active_object=None, match_type : str | list[str] | None = None):
    if isinstance(match_type, str): match_type = [match_type]

    to_output = label
    if active_object is not None: 
        if match_type is None or active_object.type in match_type: to_output = '{} ({})'.format(pgettext(label), active_object.name)

    return to_output


def get_label_with_material_name(label: str, active_material=None):
    if active_material is not None: return '{} ({})'.format(pgettext(label), active_material.name)
    return label


def get_label_with_bone_name(label: str, active_bone=None):
    if active_bone is not None: return '{} ({})'.format(pgettext(label), active_bone.name)
    return label


def get_label_with_vertex_group_name(label: str, active_vertex_group=None):
    if active_vertex_group is not None: return '{} ({})'.format(pgettext(label), active_vertex_group.name)
    return label