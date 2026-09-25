"""Blender Animate MCP: lets an AI shape motion in Blender through the Model Context Protocol.

The add-on runs a small local bridge; the ``blender-animate-mcp`` server
connects to it and exposes animation tools (easing, springs, motion paths,
velocity/acceleration/jerk analysis) to MCP clients such as Claude.
"""

bl_info = {
    "name": "Blender Animate MCP",
    "author": "Blender-Animate contributors",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Animate MCP",
    "description": "MCP bridge for AI-driven motion design: easing, springs, paths and motion analysis",
    "category": "Animation",
}

import bpy
from bpy.app.handlers import persistent

from . import bridge, preferences


class ANIMATE_MCP_OT_start(bpy.types.Operator):
    bl_idname = "animate_mcp.start"
    bl_label = "Start MCP Bridge"
    bl_description = "Listen for the Blender Animate MCP server"

    def execute(self, context):
        prefs = preferences.get()
        try:
            bridge.start(prefs.host, prefs.port)
        except OSError as exc:
            self.report({"ERROR"}, "Could not listen on %s:%d (%s)" % (prefs.host, prefs.port, exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "MCP bridge listening on %s:%d" % (prefs.host, prefs.port))
        return {"FINISHED"}


class ANIMATE_MCP_OT_stop(bpy.types.Operator):
    bl_idname = "animate_mcp.stop"
    bl_label = "Stop MCP Bridge"

    def execute(self, context):
        bridge.stop()
        return {"FINISHED"}


class ANIMATE_MCP_PT_panel(bpy.types.Panel):
    bl_label = "Animate MCP"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Animate MCP"

    def draw(self, context):
        layout = self.layout
        srv = bridge.get_server()
        if srv and srv.running:
            layout.label(text="Listening on port %d" % srv.port, icon="LINKED")
            layout.label(text="Clients: %d" % srv.clients)
            if srv.last_command:
                layout.label(text="Last: %s" % srv.last_command)
            layout.operator("animate_mcp.stop", icon="PAUSE")
        else:
            layout.label(text="Bridge stopped", icon="UNLINKED")
            layout.operator("animate_mcp.start", icon="PLAY")
        prefs = preferences.get()
        layout.prop(prefs, "port")
        layout.prop(prefs, "allow_python")


_CLASSES = (
    preferences.BLENDER_ANIMATE_MCP_Preferences,
    ANIMATE_MCP_OT_start,
    ANIMATE_MCP_OT_stop,
    ANIMATE_MCP_PT_panel,
)


def _autostart():
    try:
        prefs = preferences.get()
        if prefs.autostart:
            bridge.start(prefs.host, prefs.port)
    except Exception as exc:  # noqa: BLE001 - never break Blender startup
        print("[Blender Animate MCP] autostart failed: %s" % exc)
    return None


@persistent
def _on_load(_):
    srv = bridge.get_server()
    if srv is None or not srv.running:
        _autostart()


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.app.handlers.load_post.append(_on_load)
    # Preferences are not readable during register(); start on the first tick.
    bpy.app.timers.register(_autostart, first_interval=0.5)


def unregister():
    bridge.stop()
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
