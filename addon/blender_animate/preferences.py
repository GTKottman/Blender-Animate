import bpy

from . import bridge


def get():
    return bpy.context.preferences.addons[__package__].preferences


class BLENDER_ANIMATE_MCP_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    host: bpy.props.StringProperty(name="Host", default="127.0.0.1",
                                   description="Interface the bridge listens on. Keep 127.0.0.1 unless you know why")
    port: bpy.props.IntProperty(name="Port", default=9877, min=1024, max=65535)
    autostart: bpy.props.BoolProperty(name="Start bridge automatically", default=True,
                                      description="Start listening for the MCP server when Blender starts")
    allow_python: bpy.props.BoolProperty(
        name="Allow execute_python", default=False,
        description="Let the AI run arbitrary Python inside Blender. Only enable for trusted clients")

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.prop(self, "host")
        row.prop(self, "port")
        layout.prop(self, "autostart")
        layout.prop(self, "allow_python")
        srv = bridge.get_server()
        layout.label(text="Bridge: %s" % ("running on port %d" % srv.port if srv and srv.running else "stopped"))
