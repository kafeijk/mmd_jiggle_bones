import bpy

from .batch_properties import BatchProperty


class SetRgbaProperty(bpy.types.PropertyGroup):
    factor: bpy.props.FloatProperty(
        name="权重比例",
        description="用于调节胸部骨骼对顶点的影响强度，从而控制胸部的运动幅度",
        default=0.7,
        min=0.0,
        max=1.0,
        precision=2,
        step=1,
    )

    collision: bpy.props.EnumProperty(
        name="碰撞策略",
        description="控制胸部与身体之间的物理碰撞行为",
        items=[
            ("DEFAULT", "默认", "双臂会影响胸部的物理运动；胸部会影响其他物理部位，但其他部位不会反向影响胸部"),
            ("NO_COLLISION", "无碰撞", "胸部不参与任何物理碰撞计算")
        ],
        default="DEFAULT",
    )

    rb_scale_factor: bpy.props.FloatProperty(
        name="刚体比例",
        description="胸部刚体比例，用于调节胸部刚体大小。胸部与肢体/胸饰物理部分穿模时可适当增大，碰撞物理异常时可适当减小",
        default=0.6,
        min=0.01,
        max=2,
        precision=2,
        step=1,
    )

    batch: bpy.props.PointerProperty(type=BatchProperty)

    @staticmethod
    def register():
        bpy.types.Scene.mmd_jiggle_tools_set_rgba = bpy.props.PointerProperty(type=SetRgbaProperty)

    @staticmethod
    def unregister():
        del bpy.types.Scene.mmd_jiggle_tools_set_rgba
