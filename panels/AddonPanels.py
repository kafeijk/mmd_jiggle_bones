import addon_utils
import bpy

from ..operators.set_rgba_operators import SetRgbaOperator


class RGBAPanel(bpy.types.Panel):
    bl_idname = "RGBA_PT_rgba"
    bl_label = "RGBA式胸部物理移植"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'  # N面板
    bl_category = 'MMD Jiggle'  # 追加到其它面板或独自一个面板
    bl_order = 0

    def draw(self, context):
        scene = context.scene
        props = scene.mmd_jiggle_tools_set_rgba
        batch = props.batch

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()

        col.prop(batch, "directory")
        col.prop(props, "factor")
        col.prop(props, "rb_scale_factor")
        col.prop(props, "collision")
        col.prop(batch, "threshold")
        col.prop(batch, "conflict_strategy")
        col.operator(SetRgbaOperator.bl_idname, text=SetRgbaOperator.bl_label)


class AboutPanel(bpy.types.Panel):
    bl_idname = "RGBA_PT_about"
    bl_label = "About"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'  # N面板
    bl_category = 'MMD Jiggle'  # 追加到其它面板或独自一个面板
    bl_order = 1
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column(align=True)

        # 版本号
        col.label(
            text='版本号：' + str([addon.bl_info.get('version', (-1, -1, -1)) for addon in addon_utils.modules() if
                                  addon.bl_info['name'] == 'mmd_jiggle_bones'][0]))
        col.label(text='作者：KafeiMMD')
