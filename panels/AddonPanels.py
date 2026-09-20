import addon_utils
import bpy

from ..operators.set_rgba_operators import SetRgbaOperator
from ..operators.analysis_operators import AnalysisOperator
from ..operators.utility_operators import GetSelectedBoneNameOperator


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

        col.prop(props, "collision")
        col.prop(props, "rb_scale_factor")
        col.prop(props, "collision_group_number")

        extra_row = col.row()
        extra_row.prop(props, "extra_bones_str")
        extra_row.operator(GetSelectedBoneNameOperator.bl_idname, text="", icon="MOUSE_LMB")

        if props.jiggle_adjustment_mode == "DEFAULT":
            pass
        elif props.jiggle_adjustment_mode == "CUSTOM_SIMPLIFY":
            breast_box = col.box()
            breast_col = breast_box.column()
            breast_col.prop(props, "breast_x")
            breast_col.prop(props, "breast_y")
            breast_col.prop(props, "breast_z")
        else:
            breast_box = col.box()
            breast_col = breast_box.column()
            row1 = breast_col.row(align=True)
            row1.prop(props, "limit_lin_x_lower", text="移动限制 X")
            row1.prop(props, "limit_lin_x_sync", text="", icon="LINKED" if props.limit_lin_x_sync else "UNLINKED",
                      emboss=True)
            row1.prop(props, "limit_lin_x_upper", text="")

            row1 = breast_col.row(align=True)
            row1.prop(props, "limit_lin_y_lower", text="Y")
            row1.prop(props, "limit_lin_y_sync", text="", icon="LINKED" if props.limit_lin_y_sync else "UNLINKED",
                      emboss=True)
            row1.prop(props, "limit_lin_y_upper", text="")

            row1 = breast_col.row(align=True)
            row1.prop(props, "limit_lin_z_lower", text="Z")
            row1.prop(props, "limit_lin_z_sync", text="", icon="LINKED" if props.limit_lin_z_sync else "UNLINKED",
                      emboss=True)
            row1.prop(props, "limit_lin_z_upper", text="")

            row1 = breast_col.row(align=True)
            row1.prop(props, "limit_ang_x_lower", text="角度限制 X")
            row1.prop(props, "limit_ang_x_sync", text="", icon="LINKED" if props.limit_ang_x_sync else "UNLINKED",
                      emboss=True)
            row1.prop(props, "limit_ang_x_upper", text="")

            row1 = breast_col.row(align=True)
            row1.prop(props, "limit_ang_y_lower", text="Y")
            row1.prop(props, "limit_ang_y_sync", text="", icon="LINKED" if props.limit_ang_y_sync else "UNLINKED",
                      emboss=True)
            row1.prop(props, "limit_ang_y_upper", text="")

            row1 = breast_col.row(align=True)
            row1.prop(props, "limit_ang_z_lower", text="Z")
            row1.prop(props, "limit_ang_z_sync", text="", icon="LINKED" if props.limit_ang_z_sync else "UNLINKED",
                      emboss=True)
            row1.prop(props, "limit_ang_z_upper", text="")

            row2 = breast_col.row(align=True)
            row2.prop(props, "sensitivity")

        batch_box = col.box()
        batch_ui = batch_box.column()
        batch_ui.prop(batch, "directory")
        batch_ui.prop(batch, "search_strategy")
        batch_ui.prop(batch, "threshold")

        batch_ui2 = batch_ui.column()
        batch_ui2.prop(batch, "suffix_type")
        if props.jiggle_adjustment_mode == "DEFAULT":
            batch_ui2.enabled = True
        else:
            batch_ui2.enabled = False
        batch_ui.prop(batch, "conflict_strategy")

        row = col.row(align=True)
        row.operator(SetRgbaOperator.bl_idname, text=SetRgbaOperator.bl_label)

        if props.is_processing:
            layout.label(text=props.progress_message)

            # 进度条，Blender 4.0及以上生效
            blender_version = bpy.app.version
            if blender_version >= (4, 0, 0):
                layout.progress(factor=props.progress, type='BAR', text=f"{props.progress_current}/{props.progress_total}")


class AnalysisPanel(bpy.types.Panel):
    bl_idname = "RGBA_PT_analysis"
    bl_label = "胸部尺码分析"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MMD Jiggle'  # 追加到其它面板或独自一个面板
    bl_order = 1
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        scene = context.scene
        props = scene.mmd_jiggle_tools_analysis

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()

        col.prop(props, "type")

        extra_row = col.row()
        extra_row.prop(props, "extra_bones_str")
        extra_row.operator(GetSelectedBoneNameOperator.bl_idname, text="", icon="MOUSE_LMB")

        col.prop(props, "remove_tmp")
        col.operator(AnalysisOperator.bl_idname, text=AnalysisOperator.bl_label)

        if props.is_processing:
            # 进度条，Blender 4.0及以上生效
            blender_version = bpy.app.version
            if blender_version >= (4, 0, 0):
                layout.progress(factor=props.progress, type='BAR', text=f"{props.progress:.2%}")
            else:
                layout.label(text=props.progress_message)


class AboutPanel(bpy.types.Panel):
    bl_idname = "RGBA_PT_about"
    bl_label = "About"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'  # N面板
    bl_category = 'MMD Jiggle'  # 追加到其它面板或独自一个面板
    bl_order = 2
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
