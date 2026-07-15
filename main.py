import dearpygui.dearpygui as dpg
import playsound3

class ifmstate:
    gathering = False
    has_gage = False

ifm = ifmstate()

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

def main():

    try: 
        import PyGage
        import GageSupport as gs
        import GageConstants as gc
        ifm.has_gage = True
    except ImportError:
        print("Running in Gage-less mode. PyGage module not found.")


    # Setup Dear PyGUI
    dpg.create_context()
    dpg.create_viewport(title="Interferomatic", width=800, height=600)

    with dpg.font_registry():
        default_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 40)
        giant_font = dpg.add_font("src/ScienceGothic-Medium.ttf", 80)
    
    dpg.bind_font(default_font)

    with dpg.window(label="Main Window", tag="main_window"):
        dpg.add_text("Hello, world")
        dpg.add_button(label="Start", tag="startstop_button", callback=button1_callback)
        dpg.add_input_text(label="string", default_value="Quick brown fox")
        dpg.add_slider_float(label="float", default_value=0.273, max_value=1)

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

    dpg.setup_dearpygui()

    dpg.set_primary_window("main_window", True)

    dpg.show_viewport()


    dpg.start_dearpygui()


    dpg.destroy_context()

    if not has_gage:
        return

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
    while PyGage.GetStatus(handle) != gc.ACQ_STATUS_READY:import PyGage
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