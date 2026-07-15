import dearpygui.dearpygui as dpg
import playsound3
from math import sin, cos
import sys
from pathlib import Path

GAGE_API_DIR = Path(__file__).resolve().parent / "gage_api"
if str(GAGE_API_DIR) not in sys.path:
    sys.path.insert(0, str(GAGE_API_DIR))

sindatax = []
sindatay = []
for i in range(0, 500):
    sindatax.append(i / 1000)
    sindatay.append(0.5 + 0.5 * sin(50 * i / 1000))

def show_error_window(message):
    with dpg.window(label="Internal Error", modal=True, tag="error_window"):
        dpg.add_text(f"An error occurred: {message}")
        dpg.add_button(label="Close", callback=lambda: dpg.delete_item("error_window"))
        playsound3.playsound("src/half-life-2-episode-2-base-alarm.mp3", block=False)

    while dpg.does_item_exist("error_window"):
        dpg.render_dearpygui_frame()

class ifmstate:
    gathering = False
    has_gage = False
    save_file = ""

ifm = ifmstate()

try: 
    import PyGage
    import GageSupport as gs
    import GageConstants as gc
    ifm.has_gage = True
except ImportError:
    print("Running in Gage-less mode. PyGage module not found.")

def button1_callback(sender, app_data):
    print("Button 1 clicked")
    playsound3.playsound("src/knopka-iz-igry-2.mp3", block=False)
    if ifm.gathering:
        print("Stopping data gathering...")
        ifm.gathering = False
        # set the button color back to green
        dpg.bind_item_theme("startstop_button", "start_button_theme")
        dpg.set_item_label("startstop_button", "Start")
    else:
        print("Starting data gathering...")
        ifm.gathering = True
        # set the button color to red
        dpg.bind_item_theme("startstop_button", "stop_button_theme")
        dpg.set_item_label("startstop_button", "Stop")

def save_file_callback(sender, app_data):
    if isinstance(app_data, dict):
        selected_file = app_data.get("file_path_name", "")
    else:
        selected_file = app_data

    ifm.save_file = selected_file
    dpg.set_value("save_file_input", selected_file)
    print(f"Save file set to: {ifm.save_file}")

def main():




    # Setup Dear PyGUI
    dpg.create_context()
    dpg.create_viewport(title="Interferomatic", width=800, height=600)

    with dpg.font_registry():
        default_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 80)
        giant_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 160)
        small_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 50)
    
    dpg.bind_font(default_font)

    with dpg.window(label="Main Window", tag="main_window"):
        dpg.add_button(label="Start", tag="startstop_button", callback=button1_callback)
        
        dpg.add_input_int(label="Interferograms to collect", tag="interferograms_input", default_value=100000, width=600)
        with dpg.tooltip("interferograms_input"):
            dpg.add_text("Number of interferograms to collect before stopping")

        dpg.add_slider_float(label="Cross correlational threshold", tag="threshold_slider", default_value=0.5, max_value=1, width=600)
        with dpg.tooltip("threshold_slider"):
            dpg.add_text("Minimum threshold for cross-correlation to trigger interferogram averaging")

        dpg.add_file_dialog(label="Select save file", directory_selector=False, show=False, callback= save_file_callback, tag="file_dialog", modal=True, width=2400, height=2000)
        dpg.add_input_text(label="Save file", tag="save_file_input", width=1200)
        dpg.add_button(label="Browse", callback=lambda: dpg.show_item("file_dialog"))
        with dpg.tooltip("save_file_input"):
            dpg.add_text("File to save the averaged interferogram to")

        dpg.add_button(label="Fullscreen", tag="fullscreen_button", callback=lambda: dpg.toggle_viewport_fullscreen())
        dpg.add_button(label="Exit", callback=lambda: dpg.stop_dearpygui())

        with dpg.plot(label="Live View", tag="chart1", height=800, width=1600):
            dpg.add_plot_legend()
            dpg.add_plot_axis(dpg.mvXAxis, label="samples")
            dpg.add_plot_axis(dpg.mvYAxis, label="amplitude", tag="amp")
            dpg.add_line_series(sindatax, sindatay, label="Channel 1", parent="amp", tag="series1")

        dpg.bind_item_font("startstop_button", giant_font)

    with dpg.theme(tag="start_button_theme"):
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 180, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (0, 210, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (0, 140, 0))

    with dpg.theme(tag="stop_button_theme"):
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (180, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (210, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (140, 0, 0))


    dpg.bind_item_theme("startstop_button", "start_button_theme")
    dpg.bind_item_font("chart1", small_font)

    dpg.setup_dearpygui()

    dpg.maximize_viewport()

    dpg.set_primary_window("main_window", True)

    dpg.show_viewport()


    dpg.start_dearpygui()


    

    if not ifm.has_gage:
        return
    
    try:
        run_gage()
    except Exception as e:
        # Show an error window
        show_error_window(str(e))

    dpg.destroy_context()


def run_gage():
    import PyGage
    import GageSupport as gs
    import GageConstants as gc
    # 1. Init + open first system
    status = PyGage.Initialize()
    handle = PyGage.GetSystem(0, 0, 0, 0)
    if handle < 0:
        raise RuntimeError(PyGage.GetErrorString(handle))

    # 2. Configure from INI (or set dicts yourself)
    acq, _ = gs.LoadAcquisitionConfiguration(handle, "src/Acquire.ini")
    PyGage.SetAcquisitionConfig(handle, acq)

    chan, _ = gs.LoadChannelConfiguration(handle, 1, "src/Acquire.ini")
    PyGage.SetChannelConfig(handle, 1, chan)

    trig, _ = gs.LoadTriggerConfiguration(handle, 1, "src/Acquire.ini")
    PyGage.SetTriggerConfig(handle, 1, trig)

    status = PyGage.Commit(handle)
    if status < 0:
        raise RuntimeError(PyGage.GetErrorString(status))

    # 3. Capture
    PyGage.StartCapture(handle)
    while PyGage.GetStatus(handle) != gc.ACQ_STATUS_READY:
        pass

    # 4. Transfer
    buf, start, length = PyGage.TransferData(handle, 1, 0, 1, 0, 2040)

    # 5. Convert raw counts → volts (same formula as SaveVoltageFile)
    acq = PyGage.GetAcquisitionConfig(handle)
    chan = PyGage.GetChannelConfig(handle, 1)
    scale = chan["InputRange"] / 2000.0
    offset = chan["DcOffset"] / 1000.0
    volts = ((acq["SampleOffset"] - buf) / acq["SampleResolution"]) * scale + offset

    PyGage.FreeSystem(handle)

if __name__ == "__main__":
    main()