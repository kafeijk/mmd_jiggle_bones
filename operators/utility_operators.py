import bpy


class GetSelectedBoneNameOperator(bpy.types.Operator):
    bl_idname = "mmd_kafei_tools.get_selected_bone_name"
    bl_label = "获取选中骨骼名称"
    bl_description ="获取选中骨骼名称并填充到参数中（支持物体模式、姿态模式和编辑模式）\n请按需选择需要获取的骨骼"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object

        # 只需为骨架即可，不限于MMD模型。
        if obj is None or obj.type != 'ARMATURE':
            self.report({'ERROR'}, "请选择骨架！")
            return {'CANCELLED'}

        # 根据骨架当前模式获取选中骨骼，避免选择状态未同步
        if obj.mode == 'EDIT':
            bones = [bone.name for bone in obj.data.edit_bones if bone.select]

        elif obj.mode == 'POSE':
            if bpy.app.version >= (5, 0, 0):
                bones = [bone.name for bone in obj.pose.bones if bone.select]
            else:
                bones = [bone.name for bone in obj.pose.bones if bone.bone.select]

        else:
            if bpy.app.version >= (5, 0, 0):
                bones = [bone.name for bone in obj.pose.bones if bone.select]
            else:
                bones = [bone.name for bone in obj.data.bones if bone.select]

        if not bones:
            self.report({'ERROR'}, "请选择骨骼！")
            return {'CANCELLED'}

        bones.sort(key=lambda name: (
            0 if name.endswith(".L") else
            1 if name.endswith(".R") else
            2,
            name
        ))

        result = ",".join(bones)

        # 填充参数
        scene = context.scene
        set_rgba = scene.mmd_jiggle_tools_set_rgba
        analysis = scene.mmd_jiggle_tools_analysis

        if set_rgba.extra_bones_str != result:
            set_rgba.extra_bones_str = result

        if analysis.extra_bones_str != result:
            analysis.extra_bones_str = result

        return {'FINISHED'}