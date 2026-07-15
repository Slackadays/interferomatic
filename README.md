# Interferomatic

Collect interferograms and generate a transmission spectrum through frequency comb spectroscopy.

## Requirements

- A Gage data acquisition card (we use the Razor)
- Python
- numpy
- python-dev
- Gage Linux driver

## Installation

First build the Gage Linux driver, then build the Python API with

```
cd gage-linux-driver/Sdk/Python/PyGage
python3 setup.py install
```
