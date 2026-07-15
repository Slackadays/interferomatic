from ctypes import *
from array import *
import matplotlib.pyplot as pyplot
import numpy as np
import sys
import platform

from array import array

os_name = platform.system()

if os_name == 'Windows':
    is_64_bits = sys.maxsize > 2**32

    if is_64_bits:
        if sys.version_info >= (3, 0):
            import PyGage3_64 as PyGage
        else:
            import PyGage2_64 as PyGage
    else:        
        if sys.version_info > (3, 0):
            import PyGage3_32 as PyGage
        else:
            import PyGage2_32 as PyGage
else:
    import PyGage       

def display_signal_info(file_struct):
    print ("Sample rate:   ", file_struct['SampleRate'])
    print ("Record start:  ", file_struct['RecordStart'])
    print ("Record length: ", file_struct['RecordLength'])
    print ("Record count:  ", file_struct['RecordCount'])
    print ("Sample bits:   ", file_struct['SampleBits'])
    print ("Sample size:   ", file_struct['SampleSize'])
    print ("Sample offset: ", file_struct['SampleOffset'])
    print ("Sample res:    ", file_struct['SampleRes'])
    print ("Channel:       ", file_struct['Channel'])
    print ("Input range:   ", file_struct['InputRange'])
    print ("DC offset:     ", file_struct['DcOffset'])
    print ("Hour:          ", file_struct['Hour'])
    print ("Minute:        ", file_struct['Minute'])
    print ("Second:        ", file_struct['Second'])
    print ("Millisecond:   ", file_struct['Millisecond'])            


def main(argv):

    filename = ''

    try:
        filename = sys.argv[1]
    except:
        print("usage: python ReadSigFile.py filename")
        sys.exit(2)

    try:
        file = open(filename, "rb")        
        header = bytearray(file.read(512))

    except:
        print("Could not open or read ", filename)
        sys.exit(2)

    # PyGage.ConvertFromSigHeader takes a bytearray containing the Sig fileheader
    # and converts it into a dictionary
    file_struct = PyGage.ConvertFromSigHeader(header)    

    display_signal_info(file_struct)

    if isinstance(file_struct, int):
        print("An error has occurred")
        sys.exit(2)

    # jump over the header
    file = open(filename, 'rb')
    file.seek(512)

    
    # 8 bit samples are 1 byte long, others are 2 byte signed values
    if file_struct['SampleSize'] == 1:
        data = array('B') # B for unsigned char
    else:
        data = array('h') # h for signed short

    data.fromfile(file, file_struct['RecordLength']-1)

    datax = array('f')

    # go thru each data sample in data and convert it to a voltage
    datal = map(lambda x: (((file_struct['SampleOffset']-x)/file_struct['SampleRes'])+(file_struct['DcOffset'] / 1000.0)), data)

    # matplotlib wants a list, not a generator
    datax = [x for x in datal]

    datay = array('i')	# signed int
    datay.fromlist(list(range(0, file_struct['RecordLength']-1)))    

    pyplot.plot(datay, datax)
    pyplot.xlabel('Samples')
    pyplot.ylabel('Voltage')
    pyplot.show()

if __name__ == "__main__":
    main(sys.argv[1:])