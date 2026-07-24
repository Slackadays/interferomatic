"""UI font-scale presets, chooser dialog, and persistence helpers."""

import dearpygui.dearpygui as dpg

from src.config import CONFIG_PATH, load_config, save_config

# Presets roughly correspond to common display densities relative to a 4K baseline.
SCALE_PRESETS = [
    ("Tiny", 0.25),
    ("Small", 0.35),
    ("Medium", 0.50),
    ("Large", 0.75),
    ("Full (4K)", 1.00),
]
DEFAULT_FONT_SCALE = 0.50


def load_font_scale():
    """Return a saved font scale, or None if none is configured yet."""
    config = load_config()
    scale = config.get("font_scale")
    if isinstance(scale, (int, float)) and scale > 0:
        return float(scale)
    return None


def show_scale_chooser(default_scale=DEFAULT_FONT_SCALE, on_done=None):
    """
    Show a modal scale chooser. Applies scale live for preview.

    If *on_done* is provided, the chooser is non-blocking: Continue calls
    on_done(scale) and closes the window. Use this from UI callbacks while
    start_dearpygui() is already running (nested render_dearpygui_frame
    causes GLFW context errors).

    If *on_done* is None, blocks with a local render loop until Continue
    (only safe before start_dearpygui()). Returns the chosen scale.
    """
    if dpg.does_item_exist("scale_window"):
        dpg.focus_item("scale_window")
        return default_scale

    state = {"scale": float(default_scale), "done": False}

    def apply_scale(scale):
        state["scale"] = float(scale)
        dpg.set_global_font_scale(state["scale"])
        if dpg.does_item_exist("scale_value_text"):
            dpg.set_value("scale_value_text", f"Current scale: {state['scale']:.2f}")
        if dpg.does_item_exist("scale_slider"):
            dpg.set_value("scale_slider", state["scale"])

    def on_preset(sender, app_data, user_data):
        apply_scale(user_data)

    def on_slider(sender, app_data):
        apply_scale(app_data)

    def on_continue():
        state["done"] = True
        scale = state["scale"]
        if dpg.does_item_exist("scale_window"):
            dpg.delete_item("scale_window")
        if on_done is not None:
            on_done(scale)

    dpg.set_global_font_scale(default_scale)

    with dpg.window(
        label="UI Scale",
        tag="scale_window",
        modal=True,
        no_close=True,
        no_collapse=True,
        width=1200,
        height=800,
        pos=(80, 80),
    ):
        dpg.add_text("Choose a UI scale that looks good on this display.")
        dpg.add_text(
            "The text and controls below update live. "
            "Your choice is saved and reused next launch."
        )
        dpg.add_spacer(height=12)

        dpg.add_text(f"Current scale: {default_scale:.2f}", tag="scale_value_text")
        dpg.add_slider_float(
            label="Custom scale",
            tag="scale_slider",
            default_value=default_scale,
            min_value=0.15,
            max_value=1.25,
            width=500,
            callback=on_slider,
            format="%.2f",
        )
        dpg.add_spacer(height=8)

        with dpg.group(horizontal=True):
            for label, scale in SCALE_PRESETS:
                dpg.add_button(
                    label=f"{label} ({scale:.2f})",
                    callback=on_preset,
                    user_data=scale,
                )

        dpg.add_spacer(height=16)
        dpg.add_separator()
        dpg.add_spacer(height=8)
        dpg.add_text("Preview")
        dpg.add_text("Sample heading — Interferomatic")
        dpg.add_button(label="Sample Button")
        dpg.add_input_int(label="Sample input", default_value=100000, width=400)
        dpg.add_slider_float(label="Sample slider", default_value=0.5, max_value=1, width=400)
        dpg.add_spacer(height=16)
        dpg.add_button(label="Continue", callback=on_continue, width=200)

    # Blocking path: only for pre-start_dearpygui startup. Never nest this
    # inside a callback while the main loop is already driving frames.
    if on_done is None:
        while dpg.is_dearpygui_running() and not state["done"]:
            dpg.render_dearpygui_frame()
        if dpg.does_item_exist("scale_window"):
            dpg.delete_item("scale_window")
        return state["scale"]

    return default_scale


def resolve_font_scale():
    """Load font scale from config, or prompt the user and save their choice."""
    saved = load_font_scale()
    if saved is not None:
        print(f"Loaded font scale {saved:.2f} from {CONFIG_PATH}")
        return saved

    print("No saved font scale found; prompting for UI scale...")
    scale = show_scale_chooser()
    save_config({"font_scale": scale})
    return scale


def change_font_scale():
    """
    Open the scale chooser while the main app is running.

    Must not call render_dearpygui_frame() here: start_dearpygui() already
    owns the frame loop, and nested rendering breaks the GLFW context.
    """
    current = dpg.get_global_font_scale()

    def on_done(scale):
        save_config({"font_scale": scale})
        dpg.set_global_font_scale(scale)
        print(f"Font scale changed to {scale:.2f} and saved to {CONFIG_PATH}")

    show_scale_chooser(default_scale=current, on_done=on_done)
