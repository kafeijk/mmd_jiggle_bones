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

        col.prop(props, "jiggle_adjustment_mode")

        if props.jiggle_adjustment_mode == "DEFAULT":
            col.prop(props, "factor")
        else:
            row1 = col.row(align=True)
            row1.prop(props, "limit_lin_x_lower",text = "移动限制 X")
            row1.prop(props, "limit_lin_x_sync", text="", icon="LINKED" if props.limit_lin_x_sync else "UNLINKED", emboss=True)
            row1.prop(props, "limit_lin_x_upper",text = "")

            row1 = col.row(align=True)
            row1.prop(props, "limit_lin_y_lower",text = "Y")
            row1.prop(props, "limit_lin_y_sync", text="", icon="LINKED" if props.limit_lin_y_sync else "UNLINKED", emboss=True)
            row1.prop(props, "limit_lin_y_upper",text = "")

            row1 = col.row(align=True)
            row1.prop(props, "limit_lin_z_lower",text = "Z")
            row1.prop(props, "limit_lin_z_sync", text="", icon="LINKED" if props.limit_lin_z_sync else "UNLINKED", emboss=True)
            row1.prop(props, "limit_lin_z_upper",text = "")

            row1 = col.row(align=True)
            row1.prop(props, "limit_ang_x_lower", text="角度限制 X")
            row1.prop(props, "limit_ang_x_sync", text="", icon="LINKED" if props.limit_ang_x_sync else "UNLINKED", emboss=True)
            row1.prop(props, "limit_ang_x_upper", text="")

            row1 = col.row(align=True)
            row1.prop(props, "limit_ang_y_lower", text="Y")
            row1.prop(props, "limit_ang_y_sync", text="", icon="LINKED" if props.limit_ang_y_sync else "UNLINKED", emboss=True)
            row1.prop(props, "limit_ang_y_upper", text="")

            row1 = col.row(align=True)
            row1.prop(props, "limit_ang_z_lower", text="Z")
            row1.prop(props, "limit_ang_z_sync", text="", icon="LINKED" if props.limit_ang_z_sync else "UNLINKED", emboss=True)
            row1.prop(props, "limit_ang_z_upper", text="")



        col.prop(props, "collision")
        col.prop(props, "rb_scale_factor")
        col.prop(props, "collision_group_number")

        batch_box = col.box()
        batch_ui = batch_box.column()
        batch_ui.prop(batch, "directory")
        batch_ui.prop(batch, "search_strategy")
        batch_ui.prop(batch, "threshold")
        batch_ui_row = batch_ui.row()
        batch_ui_row.prop(batch, "suffix")
        batch_ui_row.label(text="", icon='ADD')
        batch_ui_row2 = batch_ui_row.row()
        batch_ui_row2.prop(batch, "suffix_dummy", text="")
        batch_ui_row2.enabled = False
        batch_ui.prop(batch, "conflict_strategy")


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
