from ctypes import *
from array import *
import matplotlib.pyplot as pyplot
import numpy as np
import sys

class SIGFILEINFO(Structure):
    _pack_ = 1
    _fields_ = [("file_version", c_char * 14),
	            ("crlf1", c_short),
                ("name", c_char * 9),
	            ("crlf2", c_short),	
                ("comment", c_char * 256),
	            ("crlf3", c_short),					
	            ("control_z", c_short),					
                ("sample_rate_index", c_short),
                ("operation_mode", c_short),				
                ("trigger_depth", c_int),
                ("trigger_slope", c_short),								
                ("trigger_source", c_short),
                ("trigger_level", c_short),
                ("sample_depth", c_int),
                ("captured_gain", c_short),
                ("captured_coupling", c_short),				
				("current_mem_ptr", c_int),
                ("starting_address", c_int),
                ("trigger_address", c_int),				
                ("ending_address", c_int),				
                ("trigger_time", c_ushort),					
                ("trigger_date", c_ushort),
                ("trigger_coupling", c_short),				
                ("trigger_gain", c_short),												
                ("probe", c_short),
                ("inverted_data", c_short),				
                ("board_type", c_ushort),				
                ("resolution_12_bits", c_short),				
                ("multiple_record", c_short),				
                ("trigger_probe", c_short),				
                ("sample_offset", c_short),				
                ("sample_resolution", c_short),				
                ("sample_bits", c_short),												
                ("extended_trigger_time", c_uint),
                ("imped_a", c_short),												
                ("imped_b", c_short),																
                ("external_tbs", c_float),																				
                ("external_clock_rate", c_float),	
                ("file_options", c_uint),
                ("version", c_ushort),
                ("eeprom_options", c_uint),				
                ("trigger_hardware", c_uint),								
                ("record_depth", c_uint),								
                ("sample_offset_32", c_int),												
                ("sample_resolution_32", c_int),												
                ("multiple_record_count", c_uint),																
                ("dc_offset", c_short),																				
                ("UnitFactor", c_float),																				                
				("UnitString", c_char * 5)]


def tovolts(x):
    offset = mystruct.sample_offset - x
    value = offset / mystruct.sample_resolution_32
    value = value * 1 #scalefactor
    value = value + mystruct.dc_offset / 1000.0
    return value


def display_header(mystruct):
    print ("File version: ", mystruct.file_version.decode('ISO-8859-1'))
    print ("File name: ", mystruct.name.decode('ISO-8859-1'))
    print ("Comment: ", mystruct.comment.decode('ISO_8859-1'))
    print ("Sample Rate Index: ", mystruct.sample_rate_index)
    print ("Acquisition mode: ", mystruct.operation_mode)
    print ("Trigger depth: ", mystruct.trigger_depth)
    print ("Trigger slope: ", mystruct.trigger_slope)
    print ("Trigger source: ", mystruct.trigger_slope)
    print ("Trigger level: ",  mystruct.trigger_level)
    print ("Sample depth: ", mystruct.sample_depth)
    print ("Gain: ", mystruct.captured_gain)
    print ("Coupling: ", mystruct.captured_coupling)
    print ("Starting address: ", mystruct.starting_address)
    print ("Trigger address: ", mystruct.trigger_address)
    print ("Ending address: ", mystruct.ending_address)
    print ("Trigger time: ", mystruct.trigger_time)
    print ("Trigger date: ", mystruct.trigger_date)			
    print ("Trigger coupling: ", mystruct.trigger_coupling)
    print ("Trigger gain: ", mystruct.trigger_gain)
    print ("Board type: ", mystruct.board_type)
    print ("Resolution_12_bits: ", mystruct.resolution_12_bits)
    print ("Multiple record: ", mystruct.multiple_record)		
    print ("Sample offset: ", mystruct.sample_offset)
    print ("Sample resolution: ", mystruct.sample_resolution)
    print ("Sample offset: ", mystruct.sample_bits)
    print ("Extended trigger time: ", mystruct.extended_trigger_time)
    print ("Imped_a: ", mystruct.imped_a)
    print ("Imped_b: ", mystruct.imped_b)
    print ("External tbs: ", mystruct.external_tbs)
    print ("External clock rate: ", mystruct.external_clock_rate)
    print ("File options: ", mystruct.file_options)
    print ("Version: ", mystruct.version)
    print ("Eeprom options: ", mystruct.eeprom_options)
    print ("Record depth: ", mystruct.record_depth)
    print ("Sample offset_32: ", mystruct.sample_offset_32)
    print ("Sample resolution_32: ", mystruct.sample_resolution_32)
    print ("Multiple record count: ", mystruct.multiple_record_count)
    print ("DC Offset: ", mystruct.dc_offset)


def main(argv):
    filename = ''

    try:
        filename = sys.argv[1]
    except:
        print("usage: python ReadSigFile.py filename")
        sys.exit(2)

    try:
        file = open(filename, "rb")
        mystruct = SIGFILEINFO()
        file.readinto(mystruct)
    except:
        print("Could not open or read ", filename)
        sys.exit(2)

    display_header(mystruct)

    # jump over the header
    file.seek(512)

    
    # 8 bit samples are 1 byte long, others are 2 byte signed values
    if mystruct.sample_bits == 8:
        data = array('B') # B for unsigned char
    else:
        data = array('h') # h for signed short

    data.fromfile(file, mystruct.sample_depth-1)

    datax = array('f')

    # go thru each data sample in data and convert it to a voltage
    datal = map(lambda x: (((mystruct.sample_offset-x)/mystruct.sample_resolution_32)+(mystruct.dc_offset / 1000.0)), data)

    # matplotlib wants a list, not a generator
    datax = [x for x in datal]

    datay = array('i')	# signed int
    datay.fromlist(list(range(0, mystruct.sample_depth-1)))

    pyplot.plot(datay, datax)
    pyplot.xlabel('Samples')
    pyplot.ylabel('Voltage')
    pyplot.show()

if __name__ == "__main__":
    main(sys.argv[1:])