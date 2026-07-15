#from distutils.core import setup, Extension
from setuptools import setup, Extension
import os
import sysconfig
import numpy as np

extra_compile_args = sysconfig.get_config_var('CFLAGS').split()
extra_compile_args += ["utf-8"]

module1 = Extension('PyGage',
                    define_macros = [('MAJOR_VERSION', '1'),
                                     ('MINOR_VERSION', '0')],
                    include_dirs = ['../../../Include/Public', 
                                    '../../../Include/Private', 
                                    '../../../Include/Linux',
                                    np.get_include()],
                    libraries = ['CsSsm'],
                    library_dirs = ['/usr/local/lib'],
                    sources = ['PyGage.cpp'])


setup (name = 'PyGage',
       version = '1.0',
       description = 'This is a c extension',
       author = 'Gage Applied Technologies Inc.',
       author_email = '',
       url = 'http://www.gage-applied.com',
       long_description = '''
This is really just a demo package.
''',
       ext_modules = [module1])
