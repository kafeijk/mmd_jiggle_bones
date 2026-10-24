import math

import bpy

from .batch_properties import BatchProperty


class SetRgbaProperty(bpy.types.PropertyGroup):
    jiggle_adjustment_mode: bpy.props.EnumProperty(
        name="摇晃幅度",
        description="通过何种方式调节胸部摇晃幅度",
        items=[
            ("DEFAULT", "默认", "自动分析胸部参数，以此来设置摇晃幅度"),
            ("CUSTOM_SIMPLIFY", "自定义（简化）", "手动填写部分胸部参数，以此来设置摇晃幅度，适用于单个模型的调整"),
            ("CUSTOM", "自定义", "手动填写完整胸部参数，以此来设置摇晃幅度，适用于单个模型的调整")
        ],
        default="DEFAULT",
        update=lambda self, context: self.set_default_params(context, "jiggle_adjustment_mode"),
    )

    limit_lin_x_lower: bpy.props.FloatProperty(
        name="移动下限X",
        description="",
        subtype="DISTANCE",
        default=-0.5,
        min=-0.5,
        max=0.5,
        precision=3,
        step=0.1,
        update=lambda self, context: self.update_limits(context, "limit_lin_x_lower"),
    )
    limit_lin_x_upper: bpy.props.FloatProperty(
        name="移动上限X",
        subtype="DISTANCE",
        description="",
        default=0.5,
        min=-0.5,
        max=0.5,
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
        subtype="DISTANCE",
        description="",
        default=-0.4,
        min=-0.5,
        max=0.5,
        precision=3,
        step=0.1,
        update=lambda self, context: self.update_limits(context, "limit_lin_y_lower"),
    )
    limit_lin_y_upper: bpy.props.FloatProperty(
        name="移动上限Y",
        subtype="DISTANCE",
        description="",
        default=0.3,
        min=-0.5,
        max=0.5,
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
        subtype="DISTANCE",
        description="",
        default=-0.5,
        min=-0.5,
        max=0.5,
        precision=3,
        step=0.1,
        update=lambda self, context: self.update_limits(context, "limit_lin_z_lower"),
    )
    limit_lin_z_upper: bpy.props.FloatProperty(
        name="移动上限Z",
        subtype="DISTANCE",
        description="",
        default=0.5,
        min=-0.5,
        max=0.5,
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
        max=math.radians(180),
        min=math.radians(-180),
        update=lambda self, context: self.update_limits(context, "limit_ang_x_lower"),
    )
    limit_ang_x_upper: bpy.props.FloatProperty(
        name="角度上限X",
        description="",
        subtype="ANGLE",
        default=math.radians(45),
        max=math.radians(180),
        min=math.radians(-180),
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
        max=math.radians(180),
        min=math.radians(-180),
        update=lambda self, context: self.update_limits(context, "limit_ang_y_lower"),
    )
    limit_ang_y_upper: bpy.props.FloatProperty(
        name="角度上限Y",
        description="",
        subtype="ANGLE",
        default=math.radians(15),
        max=math.radians(180),
        min=math.radians(-180),
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
        max=math.radians(180),
        min=math.radians(-180),
        update=lambda self, context: self.update_limits(context, "limit_ang_z_lower"),
    )
    limit_ang_z_upper: bpy.props.FloatProperty(
        name="角度上限Z",
        description="",
        subtype="ANGLE",
        default=math.radians(60),
        max=math.radians(180),
        min=math.radians(-180),
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
        default=1,
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

    extra_bones_str: bpy.props.StringProperty(
        name="骨骼扩展",
        description="通过额外的胸部骨骼名称，扩展默认胸部匹配范围\n在此处输入胸部骨骼名称，并以中文逗号或英文逗号作为分隔",
        default='breast.L,breast.R',
        update=lambda self, context: self.sync_extra(context),
    )
    # max设为1，主要用于为显示胸部参数预留一定冗余空间
    breast_x: bpy.props.FloatProperty(
        name="单侧胸宽",
        subtype="DISTANCE",
        description="胸部所占宽度的一半，可影响到胸部的左右摇晃",
        default=0.125,
        min=0,
        max=1,
        precision=3,
        step=0.1,
    )
    breast_y: bpy.props.FloatProperty(
        name="胸部深度",
        subtype="DISTANCE",
        description="胸部前侧与胸部底部之间的水平距离，可影响到胸部的前后摇晃",
        default=0.075,
        min=0,
        max=1,
        precision=3,
        step=0.1,
    )
    breast_z: bpy.props.FloatProperty(
        name="胸部高度",
        subtype="DISTANCE",
        description="胸部前侧与胸部底部之间的垂直距离的2倍，可影响到胸部的上下摇晃",
        default=0.5,
        min=0,
        max=1,
        precision=3,
        step=0.1,
    )

    sensitivity: bpy.props.FloatProperty(
        name="灵敏度",
        description="胸部灵敏度，用于提高幅度较小动作下胸部的响应效果\n调整该值可能会导致开启物理演算后，胸部默认姿态出现向下偏转的情况\n如出现该问题，可将角度限制X的下限调整为0，以避免胸部向下偏转",
        default=1,
        min=1,
        max=15,
        update=lambda self, context: self.update_ang_x(context),
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

    def sync_extra(self, context):
        target = context.scene.mmd_jiggle_tools_analysis
        if target.extra_bones_str == self.extra_bones_str:
            return
        target.extra_bones_str = self.extra_bones_str

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
        if self.jiggle_adjustment_mode in ("DEFAULT", "CUSTOM_SIMPLIFY"):
            return
        self._sync_pair(self.limit_lin_x_sync, "limit_lin_x_lower", "limit_lin_x_upper", changed_property)
        self._sync_pair(self.limit_lin_y_sync, "limit_lin_y_lower", "limit_lin_y_upper", changed_property)
        self._sync_pair(self.limit_lin_z_sync, "limit_lin_z_lower", "limit_lin_z_upper", changed_property)
        self._sync_pair(self.limit_ang_x_sync, "limit_ang_x_lower", "limit_ang_x_upper", changed_property)
        self._sync_pair(self.limit_ang_y_sync, "limit_ang_y_lower", "limit_ang_y_upper", changed_property)
        self._sync_pair(self.limit_ang_z_sync, "limit_ang_z_lower", "limit_ang_z_upper", changed_property)

    def set_default_params(self, context, changed_property):
        struct = context.scene.mmd_jiggle_tools_set_rgba
        batch = struct.batch

        if self.jiggle_adjustment_mode == "DEFAULT":
            pass
        else:
            batch.suffix_type = "RGBA"

        if self.jiggle_adjustment_mode == "CUSTOM_SIMPLIFY":
            target_props = {"breast_x", "breast_y", "breast_z"}
        elif self.jiggle_adjustment_mode == "CUSTOM":
            target_props = {
                "limit_lin_x_lower", "limit_lin_x_sync", "limit_lin_x_upper",
                "limit_lin_y_lower", "limit_lin_y_sync", "limit_lin_y_upper",
                "limit_lin_z_lower", "limit_lin_z_sync", "limit_lin_z_upper",
                "limit_ang_x_lower", "limit_ang_x_sync", "limit_ang_x_upper",
                "limit_ang_y_lower", "limit_ang_y_sync", "limit_ang_y_upper",
                "limit_ang_z_lower", "limit_ang_z_sync", "limit_ang_z_upper"}
        else:
            return

        props = struct.bl_rna.properties

        for name in target_props:
            setattr(struct, name, props[name].default)

    def update_ang_x(self, context):
        if self.jiggle_adjustment_mode in ("DEFAULT", "CUSTOM_SIMPLIFY"):
            return
        if self.sensitivity != 1:
            self.limit_ang_x_sync = False
            self.limit_ang_x_lower = math.radians(0)
