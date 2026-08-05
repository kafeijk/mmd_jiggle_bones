# The following section references and adapts functions from the mmd_tools addon

from typing import Optional

import bpy


class FnModel:
    @staticmethod
    def find_rigid_group_object(root_object: Optional[bpy.types.Object]) -> Optional[bpy.types.Object]:
        if root_object is None:
            return None
        for o in root_object.children:
            if o.type == "EMPTY" and o.mmd_type == "RIGID_GRP_OBJ":
                return o
        return None

    @staticmethod
    def __new_group_object(context: bpy.types.Context, name: str, mmd_type: str,
                           parent: bpy.types.Object) -> bpy.types.Object:
        group_object = FnContext.new_and_link_object(context, name=name, object_data=None)
        group_object.mmd_type = mmd_type
        group_object.parent = parent
        group_object.hide_set(True)
        group_object.hide_select = True
        group_object.lock_rotation = group_object.lock_location = group_object.lock_scale = [True, True, True]
        return group_object

    @staticmethod
    def ensure_rigid_group_object(context: bpy.types.Context, root_object: bpy.types.Object) -> bpy.types.Object:
        if root_object is None:
            raise ValueError("root_object cannot be None")
        rigid_group_object = FnModel.find_rigid_group_object(root_object)
        if rigid_group_object is not None:
            return rigid_group_object
        return FnModel.__new_group_object(context, name="rigidbodies", mmd_type="RIGID_GRP_OBJ", parent=root_object)

    @staticmethod
    def find_joint_group_object(root_object: Optional[bpy.types.Object]) -> Optional[bpy.types.Object]:
        if root_object is None:
            return None
        for o in root_object.children:
            if o.type == "EMPTY" and o.mmd_type == "JOINT_GRP_OBJ":
                return o
        return None

    @staticmethod
    def ensure_joint_group_object(context: bpy.types.Context, root_object: bpy.types.Object) -> bpy.types.Object:
        if root_object is None:
            raise ValueError("root_object cannot be None")
        joint_group_object = FnModel.find_joint_group_object(root_object)
        if joint_group_object is not None:
            return joint_group_object
        return FnModel.__new_group_object(context, name="joints", mmd_type="JOINT_GRP_OBJ", parent=root_object)


class FnContext:
    def __init__(self):
        raise NotImplementedError("This class is not expected to be instantiated.")

    @staticmethod
    def new_and_link_object(context: bpy.types.Context, name: str,
                            object_data: Optional[bpy.types.ID]) -> bpy.types.Object:
        return FnContext.link_object(context, bpy.data.objects.new(name=name, object_data=object_data))

    @staticmethod
    def link_object(context: bpy.types.Context, obj: bpy.types.Object) -> bpy.types.Object:
        context.collection.objects.link(obj)
        return obj
