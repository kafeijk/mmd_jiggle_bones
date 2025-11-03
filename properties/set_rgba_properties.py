import bpy


class SetRgbaProperty(bpy.types.PropertyGroup):
    filepath: bpy.props.StringProperty(
        name="模型文件",
        description="模型文件地址",
        subtype='FILE_PATH',
        default=''
    )

    factor: bpy.props.FloatProperty(
        name="权重比例",
        description="胸部权重比例，用于调节胸部运动幅度",
        default=0.6,
        min=0.0,
        max=1.0,
        precision=2,
        step=0.1,
    )

    rb_scale_factor: bpy.props.FloatProperty(
        name="刚体比例",
        description="胸部刚体比例，用于调节胸部刚体大小。胸部与肢体/胸饰物理部分穿模时可适当增大，碰撞物理异常时可适当减小",
        default=0.8,
        min=0.0,
        max=10.0,
        precision=2,
        step=0.1,
    )

    @staticmethod
    def register():
        bpy.types.Scene.mmd_jiggle_tools_set_rgba = bpy.props.PointerProperty(type=SetRgbaProperty)

    @staticmethod
    def unregister():
        del bpy.types.Scene.mmd_jiggle_tools_set_rgba
