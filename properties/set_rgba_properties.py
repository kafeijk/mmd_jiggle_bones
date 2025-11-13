import math

import bpy

from .batch_properties import BatchProperty


class SetRgbaProperty(bpy.types.PropertyGroup):
    jiggle_adjustment_mode: bpy.props.EnumProperty(
        name="抖动模式",
        description="通过何种模式调节胸部抖动",
        items=[
            ("DEFAULT", "默认", "各参数统一乘上“抖动强度”系数进行调节，以整体控制胸部的抖动效果"),
            ("CUSTOM", "自定义", "分别填写各参数的数值以进行独立调节")
        ],
        default="DEFAULT",
        update=lambda self, context: self.set_default_limits(context, "jiggle_adjustment_mode"),
    )

    factor: bpy.props.FloatProperty(
        name="抖动强度",
        description="控制胸部抖动强度",
        default=0.5,
        min=0.0,
        max=1.0,
        precision=2,
        step=1,
    )

    limit_lin_x_lower: bpy.props.FloatProperty(
        name="移动下限X",
        description="",
        default=-0.04,
        min=-0.08,
        max=0,
        precision=3,
        step=0.1,
        update=lambda self, context: self.update_limits(context, "limit_lin_x_lower"),
    )
    limit_lin_x_upper: bpy.props.FloatProperty(
        name="移动上限X",
        description="",
        default=0.04,
        min=0,
        max=0.08,
        precision=3,
        step=0.1,
        update=lambda self, context: self.update_limits(context, "limit_lin_x_upper"),
    )
    limit_lin_x_sync: bpy.props.BoolProperty(
        name="移动同步X",
        description="",
        default=True,
    )
    limit_lin_y_lower: bpy.props.FloatProperty(
        name="移动下限Y",
        description="",
        default=-0.032,
        min=-0.08,
        max=0,
        precision=3,
        step=0.1,
        update=lambda self, context: self.update_limits(context, "limit_lin_y_lower"),
    )
    limit_lin_y_upper: bpy.props.FloatProperty(
        name="移动上限Y",
        description="",
        default=0.024,
        min=0,
        max=0.08,
        precision=3,
        step=0.1,
        update=lambda self, context: self.update_limits(context, "limit_lin_y_upper"),
    )
    limit_lin_y_sync: bpy.props.BoolProperty(
        name="移动同步Y",
        description="",
        default=False,
    )

    limit_lin_z_lower: bpy.props.FloatProperty(
        name="移动下限Z",
        description="",
        default=-0.04,
        min=-0.08,
        max=0,
        precision=3,
        step=0.1,
        update=lambda self, context: self.update_limits(context, "limit_lin_z_lower"),
    )
    limit_lin_z_upper: bpy.props.FloatProperty(
        name="移动上限Z",
        description="",
        default=0.04,
        min=0,
        max=0.08,
        precision=3,
        step=0.1,
        update=lambda self, context: self.update_limits(context, "limit_lin_z_upper"),

    )
    limit_lin_z_sync: bpy.props.BoolProperty(
        name="移动同步Z",
        description="",
        default=True,
    )

    limit_ang_x_lower: bpy.props.FloatProperty(
        name="角度下限X",
        description="",
        subtype="ANGLE",
        default=math.radians(-45),
        max=math.radians(0),
        min=math.radians(-180),
        update=lambda self, context: self.update_limits(context, "limit_ang_x_lower"),
    )
    limit_ang_x_upper: bpy.props.FloatProperty(
        name="角度上限X",
        description="",
        subtype="ANGLE",
        default=math.radians(45),
        max=math.radians(180),
        min=math.radians(0),
        update=lambda self, context: self.update_limits(context, "limit_ang_x_upper"),
    )
    limit_ang_x_sync: bpy.props.BoolProperty(
        name="角度同步X",
        description="",
        default=True,
    )
    limit_ang_y_lower: bpy.props.FloatProperty(
        name="角度下限Y",
        description="",
        subtype="ANGLE",
        default=math.radians(-15),
        max=math.radians(0),
        min=math.radians(-180),
        update=lambda self, context: self.update_limits(context, "limit_ang_y_lower"),
    )
    limit_ang_y_upper: bpy.props.FloatProperty(
        name="角度上限Y",
        description="",
        subtype="ANGLE",
        default=math.radians(15),
        max=math.radians(180),
        min=math.radians(0),
        update=lambda self, context: self.update_limits(context, "limit_ang_y_upper"),
    )
    limit_ang_y_sync: bpy.props.BoolProperty(
        name="角度同步Y",
        description="",
        default=True,
    )
    limit_ang_z_lower: bpy.props.FloatProperty(
        name="角度下限Z",
        description="",
        subtype="ANGLE",
        default=math.radians(-60),
        max=math.radians(0),
        min=math.radians(-180),
        update=lambda self, context: self.update_limits(context, "limit_ang_z_lower"),
    )
    limit_ang_z_upper: bpy.props.FloatProperty(
        name="角度上限Z",
        description="",
        subtype="ANGLE",
        default=math.radians(60),
        max=math.radians(180),
        min=math.radians(0),
        update=lambda self, context: self.update_limits(context, "limit_ang_z_upper"),
    )
    limit_ang_z_sync: bpy.props.BoolProperty(
        name="角度同步Z",
        description="",
        default=True,
    )

    rb_scale_factor: bpy.props.FloatProperty(
        name="刚体缩放",
        description="胸部刚体缩放，用于调节胸部刚体大小",
        default=0.6,
        min=0.01,
        max=2,
        precision=2,
        step=1,
    )

    collision: bpy.props.EnumProperty(
        name="碰撞策略",
        description="控制胸部与身体之间的物理碰撞行为",
        items=[
            ("DEFAULT", "自动", "双臂会影响胸部的物理运动；胸部会影响其他物理部位，但其他物理部位不会反向影响胸部"),
            ("NO_COLLISION", "无碰撞", "胸部不参与任何物理碰撞计算")
        ],
        default="DEFAULT",
    )

    collision_group_number: bpy.props.IntProperty(
        name="碰撞组",
        description="胸部碰撞组",
        default=14,
        min=0,
        max=15,
        update=lambda self, context: self.skip_13(context),
    )
    batch: bpy.props.PointerProperty(type=BatchProperty)

    @staticmethod
    def register():
        bpy.types.Scene.mmd_jiggle_tools_set_rgba = bpy.props.PointerProperty(type=SetRgbaProperty)

    @staticmethod
    def unregister():
        del bpy.types.Scene.mmd_jiggle_tools_set_rgba

    def skip_13(self, context):
        # 13 是手臂的碰撞组，避免使用13
        if self.collision_group_number == 13:
            self.collision_group_number = 12

    def _sync_pair(self, sync, lower_attr, upper_attr, changed_property):
        if not sync:
            return
        if changed_property == lower_attr:
            current = getattr(self, upper_attr)
            new_value = -getattr(self, lower_attr)
            if current != new_value:
                setattr(self, upper_attr, new_value)
        elif changed_property == upper_attr:
            current = getattr(self, lower_attr)
            new_value = -getattr(self, upper_attr)
            if current != new_value:
                setattr(self, lower_attr, new_value)

    def update_limits(self, context, changed_property):
        if self.jiggle_adjustment_mode == "DEFAULT":
            return
        self._sync_pair(self.limit_lin_x_sync, "limit_lin_x_lower", "limit_lin_x_upper", changed_property)
        self._sync_pair(self.limit_lin_y_sync, "limit_lin_y_lower", "limit_lin_y_upper", changed_property)
        self._sync_pair(self.limit_lin_z_sync, "limit_lin_z_lower", "limit_lin_z_upper", changed_property)
        self._sync_pair(self.limit_ang_x_sync, "limit_ang_x_lower", "limit_ang_x_upper", changed_property)
        self._sync_pair(self.limit_ang_y_sync, "limit_ang_y_lower", "limit_ang_y_upper", changed_property)
        self._sync_pair(self.limit_ang_z_sync, "limit_ang_z_lower", "limit_ang_z_upper", changed_property)

    def set_default_limits(self, context, changed_property):
        if self.jiggle_adjustment_mode != "CUSTOM":
            return

        struct = context.scene.mmd_jiggle_tools_set_rgba
        for prop in struct.bl_rna.properties:
            if prop.identifier in ["limit_lin_x_lower", "limit_lin_x_upper",
                                   "limit_lin_y_lower", "limit_lin_y_upper",
                                   "limit_lin_z_lower", "limit_lin_z_upper",
                                   "limit_ang_x_lower", "limit_ang_x_upper",
                                   "limit_ang_y_lower", "limit_ang_y_upper",
                                   "limit_ang_z_lower", "limit_ang_z_upper",
                                   "limit_lin_x_sync", "limit_lin_y_sync", "limit_lin_z_sync",
                                   "limit_ang_x_sync", "limit_ang_y_sync", "limit_ang_z_sync",
                                   ]:
                default = prop.default
                setattr(struct, prop.identifier, default)
