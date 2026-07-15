// PyGage.cpp : Defines the exported functions for the DLL application.
//

#ifdef _WIN32
	#include "stdafx.h"
#else
	#include "CsLinuxPort.h"
#endif

#include "CsPrototypes.h"
#include "DiskHead.h"
#include "CsExpert.h" 
#include "CsPrivateStruct.h"
#include <vector>
#include <string>
#include <algorithm>


#ifdef _DEBUG
#define _DEBUG_WAS_DEFINED 1
#undef _DEBUG
#endif

#define Py_LIMITED_API

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION

#include <Python.h>
#ifdef _WIN32
#include <arrayobject.h>
#else
#include <numpy/arrayobject.h>
#endif



#ifndef __linux__
#include "PyGage_Platforms.h" // must go after #include <python.h> because it needs PY_MAJOR_VERSION	
#endif

#ifdef _DEBUG_WAS_DEFINED
#define _DEBUG 1
#endif

#ifdef __linux__  // ignore last 2 compiler warnings
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wconversion-null"
#endif

// need to have all of the fields from AcquisitionConfig even if they don't get set
const std::vector<std::string> acquisitionFields{ "SampleRate", "ExtClk", "ExtClkSampleSkip", "Mode", "SampleBits",
												  "SampleResolution", "SampleSize", "SegmentCount", "Depth",
												  "SegmentSize", "TriggerTimeout", "TriggerDelay", "TriggerHoldoff",
												  "SampleOffset", "TimeStampConfig" };


const std::vector<std::string> channelFields{ "InputRange", "Impedance", "Filter", "DcOffset", "Coupling" };

const std::vector<std::string> triggerFields{ "Condition", "Level", "Source", "ExtCoupling", 
  											  "ExtRange", "ExtImpedance", "Relation" };

const std::vector<std::string> fftConfigFields{ "FftSize", "Enable", "Average", "RealOnly", "Windowing", "IFft", "FftMr" };


int ConvertToUnicodeString(char* asciiStr, wchar_t* wideStr)
{
#ifdef _WIN32
	return (MultiByteToWideChar(CP_ACP, 0, asciiStr, -1, wideStr, 32));
#else
	return -1;
#endif
}

static PyObject *
PyGage_Initialize(PyObject *self, PyObject *args)
{
    int sts;

	if (!PyArg_ParseTuple(args, ""))
	{
		return NULL;
	}
	sts = CsInitialize();
    return PyLong_FromLong(sts);
}

static PyObject *
PyGage_GetSystem(PyObject *self, PyObject *args)
{
	unsigned int u32BoardType, u32Channels, u32SampleBits;
	short i16Index;
	int sts;
	CSHANDLE csHandle;

	if (!PyArg_ParseTuple(args, "IIIh", &u32BoardType, &u32Channels, &u32SampleBits, &i16Index))
	{
		return NULL;
	}
	sts = CsGetSystem(&csHandle, u32BoardType, u32Channels, u32SampleBits, i16Index);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	return PyLong_FromUnsignedLong(csHandle);
}

static PyObject *
PyGage_FreeSystem(PyObject *self, PyObject *args)
{
    CSHANDLE csHandle;
    int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	sts = CsFreeSystem(csHandle);
    return PyLong_FromLong(sts);
}

static PyObject *
PyGage_GetSystemInfo(PyObject *self, PyObject *args)
{
    CSHANDLE csHandle;
	CSSYSTEMINFO sysInfo;
	sysInfo.u32Size = sizeof(CSSYSTEMINFO);

    int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	sts = CsGetSystemInfo(csHandle, &sysInfo);

	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	PyObject *d = PyDict_New();
	if (!d)
	{
		return NULL;
	}

	PyObject* p = Py_BuildValue("L", sysInfo.i64MaxMemory);
	PyDict_SetItemString(d, "MaxMemory", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sysInfo.u32SampleBits);
	PyDict_SetItemString(d, "SampleBits", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", sysInfo.i32SampleResolution);
	PyDict_SetItemString(d, "SampleResolution", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sysInfo.u32SampleSize);
	PyDict_SetItemString(d, "SampleSize", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", sysInfo.i32SampleOffset);
	PyDict_SetItemString(d, "SampleOffset", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sysInfo.u32BoardType);
	PyDict_SetItemString(d, "BoardType", p);
	Py_XDECREF(p);

	p = Py_BuildValue("s", sysInfo.strBoardName);
	PyDict_SetItemString(d, "BoardName", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sysInfo.u32AddonOptions);
	PyDict_SetItemString(d, "AddonOptions", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sysInfo.u32BaseBoardOptions);
	PyDict_SetItemString(d, "BaseBoardOptions", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sysInfo.u32TriggerMachineCount);
	PyDict_SetItemString(d, "TriggerMachineCount", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sysInfo.u32ChannelCount);
	PyDict_SetItemString(d, "ChannelCount", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sysInfo.u32BoardCount);
	PyDict_SetItemString(d, "BoardCount", p);
	Py_XDECREF(p);

	return d;
}

static PyObject *
PyGage_GetBoardsInfo(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	CSSYSTEMINFO sysInfo;
	int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	sysInfo.u32Size = sizeof(CSSYSTEMINFO);
	sts = CsGetSystemInfo(csHandle, &sysInfo);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	ARRAY_BOARDINFO* pArrayBoardInfo = (ARRAY_BOARDINFO*)malloc(((sysInfo.u32BoardCount - 1) * sizeof(CSBOARDINFO)) + sizeof(ARRAY_BOARDINFO));
	if (NULL == pArrayBoardInfo)
	{
		sts = CS_MEMORY_ERROR;
		return PyLong_FromLong(sts);
	}

	pArrayBoardInfo->u32BoardCount = sysInfo.u32BoardCount;

	for (unsigned long index = 0; index < pArrayBoardInfo->u32BoardCount; index++)
	{
		pArrayBoardInfo->aBoardInfo[index].u32BoardIndex = index + 1;
		pArrayBoardInfo->aBoardInfo[index].u32Size = sizeof(CSBOARDINFO);
	}
	sts = CsGet(csHandle, CS_BOARD_INFO, 0, pArrayBoardInfo);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}


	PyObject* list = PyList_New(sysInfo.u32BoardCount);
	if (!list)
	{
		return NULL;
	}

	for (unsigned int i = 0; i < sysInfo.u32BoardCount; i++)
	{
		PyObject *d = PyDict_New();
		if (!d)
		{
			return NULL;
		}
// RG should I check Py_BuildValue for NULL
		PyObject* p = Py_BuildValue("I", pArrayBoardInfo->aBoardInfo[i].u32BoardType);
		PyDict_SetItemString(d, "BoardType", p);
		Py_XDECREF(p);

		p = Py_BuildValue("s", pArrayBoardInfo->aBoardInfo[i].strSerialNumber);
		PyDict_SetItemString(d, "SerialNumber", p);
		Py_XDECREF(p);

		p = Py_BuildValue("I", pArrayBoardInfo->aBoardInfo[i].u32BaseBoardVersion);
		PyDict_SetItemString(d, "BaseBoardVersion", p);
		Py_XDECREF(p);

		p = Py_BuildValue("I", pArrayBoardInfo->aBoardInfo[i].u32BaseBoardFirmwareVersion);
		PyDict_SetItemString(d, "BaseBoardFirmwareVersion", p);
		Py_XDECREF(p);

		p = Py_BuildValue("I", pArrayBoardInfo->aBoardInfo[i].u32AddonBoardVersion);
		PyDict_SetItemString(d, "AddonBoardVersion", p);
		Py_XDECREF(p);

		p = Py_BuildValue("I", pArrayBoardInfo->aBoardInfo[i].u32AddonBoardFirmwareVersion);
		PyDict_SetItemString(d, "AddonBoardFirmwareVersion", p);
		Py_XDECREF(p);

		p = Py_BuildValue("I", pArrayBoardInfo->aBoardInfo[i].u32AddonFwOptions);
		PyDict_SetItemString(d, "AddonFwOptions", p);
		Py_XDECREF(p);

		p = Py_BuildValue("I", pArrayBoardInfo->aBoardInfo[i].u32BaseBoardFwOptions);
		PyDict_SetItemString(d, "BaseBoardFwOptions", p);
		Py_XDECREF(p);

		p = Py_BuildValue("I", pArrayBoardInfo->aBoardInfo[i].u32AddonHwOptions);
		PyDict_SetItemString(d, "AddonHwOptions", p);
		Py_XDECREF(p);

		p = Py_BuildValue("I", pArrayBoardInfo->aBoardInfo[i].u32BaseBoardFwOptions);
		PyDict_SetItemString(d, "BaseBoardHwOptions", p);
		Py_XDECREF(p);

		PyList_SetItem(list, i, d); 
		// d is stolen so it's ref count does not have to be decremented
		// Python should handle that when the list is freed
	}
	return list;
}


static PyObject *
PyGage_GetAcquisitionConfig(PyObject *self, PyObject *args)
{
    CSHANDLE csHandle;

    int sts;
	int config = CS_CURRENT_CONFIGURATION;
	if (!PyArg_ParseTuple(args, "I|i", &csHandle, &config))
	{
		return NULL;
	}

	CSACQUISITIONCONFIG acqConfig;
	acqConfig.u32Size = sizeof(CSACQUISITIONCONFIG);
	sts = CsGet(csHandle, CS_ACQUISITION, config, &acqConfig);

	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	PyObject *d = PyDict_New();
	if (!d)
	{
		return NULL;
	}

	PyObject* p = Py_BuildValue("L", acqConfig.i64SampleRate);
	PyDict_SetItemString(d, "SampleRate", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", acqConfig.u32ExtClk);
	PyDict_SetItemString(d, "ExtClk", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", acqConfig.u32ExtClkSampleSkip);
	PyDict_SetItemString(d, "ExtClkSampleSkip", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", acqConfig.u32Mode);
	PyDict_SetItemString(d, "Mode", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", acqConfig.u32SampleBits);
	PyDict_SetItemString(d, "SampleBits", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", acqConfig.i32SampleRes);
	PyDict_SetItemString(d, "SampleResolution", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", acqConfig.u32SampleSize);
	PyDict_SetItemString(d, "SampleSize", p);
	Py_XDECREF(p);

	if (-1 == acqConfig.i32SegmentCountHigh) // segment was set to infinite
	{
		p = Py_BuildValue("I", -1);
		PyDict_SetItemString(d, "SegmentCount", p);
		Py_XDECREF(p);
	}
	else if (0 == acqConfig.i32SegmentCountHigh) // regular segment count
	{
		p = Py_BuildValue("I", acqConfig.u32SegmentCount);
		PyDict_SetItemString(d, "SegmentCount", p);
		Py_XDECREF(p);
	}
	else  // segment is larger than UINT_MAX
	{
		int64 i64SegmentCount = (((int64)acqConfig.i32SegmentCountHigh << 32) & 0xFFFFFFFF00000000) | acqConfig.u32SegmentCount;
		p = Py_BuildValue("L", i64SegmentCount);
		PyDict_SetItemString(d, "SegmentCount", p);
		Py_XDECREF(p);
	}
	p = Py_BuildValue("L", acqConfig.i64Depth);
	PyDict_SetItemString(d, "Depth", p);
	Py_XDECREF(p);

	p = Py_BuildValue("L", acqConfig.i64SegmentSize);
	PyDict_SetItemString(d, "SegmentSize", p);
	Py_XDECREF(p);

	p = Py_BuildValue("L", acqConfig.i64TriggerTimeout);
	PyDict_SetItemString(d, "TriggerTimeout", p);
	Py_XDECREF(p);

	p = Py_BuildValue("L", acqConfig.i64TriggerDelay);
	PyDict_SetItemString(d, "TriggerDelay", p);
	Py_XDECREF(p);

	p = Py_BuildValue("L", acqConfig.i64TriggerHoldoff);
	PyDict_SetItemString(d, "TriggerHoldoff", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", acqConfig.i32SampleOffset);
	PyDict_SetItemString(d, "SampleOffset", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", acqConfig.u32TimeStampConfig);
	PyDict_SetItemString(d, "TimeStampConfig", p);
	Py_XDECREF(p);

	return d;
}


static PyObject *
PyGage_SetAcquisitionConfig(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;

	PyObject* dict = NULL;

	if (!PyArg_ParseTuple(args, "IO", &csHandle, &dict))
	{
		return NULL;
	}

	if (!PyDict_Check(dict))
	{
		PyErr_SetString(PyExc_TypeError, "Expecting a dictionary");
		return NULL;
	}
#ifdef PYTHON3
	if (!PyArg_ValidateKeywordArguments(dict))
	{
		// error of some kind
	}
#endif

	PyObject *key, *value;
	Py_ssize_t pos = 0;

	while (PyDict_Next(dict, &pos, &key, &value))
	{
#if PY_MAJOR_VERSION < 3
		PyObject* obj = PyObject_Str(key);
		if (obj == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "All fields in AcquisitionConfig must be strings");
			return NULL;
		}
		const char* key_string = PyString_AsString(obj);
		if (key_string == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "All fields in AcquisitionConfig must be strings");
			Py_XDECREF(obj);
			return NULL;
		}
#else
		PyObject* obj = PyUnicode_AsEncodedString(key, "utf-8", "~E~");
		if (obj == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "Cannot read field from AcquisitionConfig");
			return NULL;
		}
		const char *key_string = PyBytes_AsString(obj);
		if (key_string == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "Cannot read field from AcquisitionConfig");
			Py_XDECREF(obj);
			return NULL;
		}
#endif
		if (std::find(acquisitionFields.begin(), acquisitionFields.end(), key_string) == acquisitionFields.end())
		{
			// key is not in the vector
			char s[100];
			sprintf_s(s, 100, "%s is not a valid field in AcquisitionConfig", key_string);
			PyErr_SetString(PyExc_TypeError, s);

			Py_XDECREF(obj);
			return NULL;
		}
		Py_XDECREF(obj);
	}

	CSACQUISITIONCONFIG csAcqCfg;
	csAcqCfg.u32Size = sizeof(CSACQUISITIONCONFIG);
	int sts = CsGet(csHandle, CS_ACQUISITION, CS_CURRENT_CONFIGURATION, &csAcqCfg);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}

	PyObject* p = PyDict_GetItemString(dict, "SampleRate");
	if (p)
	{
		csAcqCfg.i64SampleRate = PyLong_AsLongLong(p);
	}

	p = PyDict_GetItemString(dict, "ExtClk");
	if (p)
	{
		csAcqCfg.u32ExtClk = PyLong_AsUnsignedLong(p);
	}

	p = PyDict_GetItemString(dict, "ExtClkSampleSkip");
	if (p)
	{
		csAcqCfg.u32ExtClkSampleSkip = PyLong_AsUnsignedLong(p);
	}

	p = PyDict_GetItemString(dict, "Mode");
	if (p)
	{
		csAcqCfg.u32Mode = PyLong_AsUnsignedLong(p);
	}

	p = PyDict_GetItemString(dict, "SegmentCount");
	if (p)
	{
		int64 i64SegmentCount = PyLong_AsLongLong(p);
		if (-1 == i64SegmentCount)
		{
			csAcqCfg.u32SegmentCount = UINT_MAX;
			csAcqCfg.i32SegmentCountHigh = -1;
		}
		else if (i64SegmentCount > UINT_MAX)
		{
			csAcqCfg.u32SegmentCount = (uInt32)(i64SegmentCount & 0x00000000FFFFFFFF);
			csAcqCfg.i32SegmentCountHigh = (uInt32)((i64SegmentCount & 0xFFFFFFFF00000000) >> 32);
		}
		else
		{
			csAcqCfg.u32SegmentCount = (uInt32)i64SegmentCount;
			csAcqCfg.i32SegmentCountHigh = 0;
		}
	}
	p = PyDict_GetItemString(dict, "Depth");
	if (p)
	{
		csAcqCfg.i64Depth = PyLong_AsLongLong(p);
	}
	p = PyDict_GetItemString(dict, "SegmentSize");
	if (p)
	{
		csAcqCfg.i64SegmentSize = PyLong_AsLongLong(p);
	}
	p = PyDict_GetItemString(dict, "TriggerTimeout");
	if (p)
	{
		csAcqCfg.i64TriggerTimeout = PyLong_AsLongLong(p);
	}
	p = PyDict_GetItemString(dict, "TriggerDelay");
	if (p)
	{
		csAcqCfg.i64TriggerDelay = PyLong_AsLongLong(p);
	}
	p = PyDict_GetItemString(dict, "TriggerHoldoff");
	if (p)
	{
		csAcqCfg.i64TriggerHoldoff = PyLong_AsLongLong(p);
	}
	p = PyDict_GetItemString(dict, "TimeStampConfig");
	if (p)
	{
		csAcqCfg.u32TimeStampConfig = PyLong_AsUnsignedLong(p);
	}
	sts = CsSet(csHandle, CS_ACQUISITION, &csAcqCfg);
	return PyLong_FromLong(sts);
}

static PyObject *
PyGage_GetChannelConfig(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;

	int sts;
	unsigned long channel;
	int config = CS_CURRENT_CONFIGURATION;

	if (!PyArg_ParseTuple(args, "II|i", &csHandle, &channel, &config))
	{
		return NULL;
	}
	// check if channel is within bounds (or let the call to the drivers do it)

	CSCHANNELCONFIG chanConfig;
	chanConfig.u32Size = sizeof(CSCHANNELCONFIG);
	chanConfig.u32ChannelIndex = channel;

	sts = CsGet(csHandle, CS_CHANNEL, config, &chanConfig);

	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	PyObject *d = PyDict_New();
	if (!d)
	{
		return NULL;
	}

	PyObject* p = Py_BuildValue("I", chanConfig.u32InputRange);
	PyDict_SetItemString(d, "InputRange", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", chanConfig.u32Impedance);
	PyDict_SetItemString(d, "Impedance", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", chanConfig.u32Filter);
	PyDict_SetItemString(d, "Filter", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", chanConfig.i32DcOffset);
	PyDict_SetItemString(d, "DcOffset", p);
	Py_XDECREF(p);

	// need to fix this one - maybe have a value i.e "AC" or "DC" and must put in 
	// right place in term
	p = Py_BuildValue("i", chanConfig.u32Term);
	PyDict_SetItemString(d, "Coupling", p);
	Py_XDECREF(p);

	return d;

}

static PyObject *
PyGage_SetChannelConfig(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	unsigned int channel;
	PyObject* dict = NULL;

	if (!PyArg_ParseTuple(args, "IIO", &csHandle, &channel, &dict))
	{
		return NULL;
	}
	// check channel, or let Cs call check it

	if (!PyDict_Check(dict))
	{
		PyErr_SetString(PyExc_TypeError, "Expecting a dictionary");
		return NULL;
	}
#ifdef PYTHON3
	if (!PyArg_ValidateKeywordArguments(dict))
	{
		// error of some kind
	}
#endif

	PyObject *key, *value;
	Py_ssize_t pos = 0;

	while (PyDict_Next(dict, &pos, &key, &value))
	{
#if PY_MAJOR_VERSION < 3
		PyObject* obj = PyObject_Str(key);
		if (obj == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "All fields in ChannelConfig must be strings");
			return NULL;
		}
		const char* key_string = PyString_AsString(obj);
		if (key_string == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "All fields in ChannelConfig must be strings");
			Py_XDECREF(obj);
			return NULL;
		}
#else
		PyObject* obj = PyUnicode_AsEncodedString(key, "utf-8", "~E~");
		if (obj == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "Cannot read field from ChannnelConfig");
			return NULL;
		}
		const char *key_string = PyBytes_AsString(obj);
		if (key_string == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "Cannot read field from ChannelConfig");
			Py_XDECREF(obj);
			return NULL;
		}
#endif
		if (std::find(channelFields.begin(), channelFields.end(), key_string) == channelFields.end())
		{
			// key is not in the vector
			char s[100];
			sprintf_s(s, 100, "%s is not a valid field in ChannelConfig", key_string);
			PyErr_SetString(PyExc_TypeError, s);
			Py_XDECREF(obj);
			return NULL;
		}
		Py_XDECREF(obj);
	}

	CSCHANNELCONFIG chanConfig;
	chanConfig.u32Size = sizeof(CSCHANNELCONFIG);
	chanConfig.u32ChannelIndex = channel;

	int sts = CsGet(csHandle, CS_CHANNEL, CS_CURRENT_CONFIGURATION, &chanConfig);
	if (CS_FAILED(sts))
	{
		// RG - return error (or set Pyerror and return ????
		return PyLong_FromLong(sts);
	}

	PyObject* p = PyDict_GetItemString(dict, "InputRange");
	if (p)
	{
		chanConfig.u32InputRange = PyLong_AsUnsignedLong(p);
	}

	p = PyDict_GetItemString(dict, "Impedance");
	if (p)
	{
		chanConfig.u32Impedance = PyLong_AsUnsignedLong(p);
	}

	p = PyDict_GetItemString(dict, "Filter");
	if (p)
	{
		chanConfig.u32Filter = PyLong_AsUnsignedLong(p);
	}

	p = PyDict_GetItemString(dict, "DcOffset");
	if (p)
	{
		chanConfig.i32DcOffset = PyLong_AsLong(p);
	}
	p = PyDict_GetItemString(dict, "Coupling");
	if (p)		// RG NEED TO FIX THIS ONE
	{
		chanConfig.u32Term = PyLong_AsUnsignedLong(p);
	}
	sts = CsSet(csHandle, CS_CHANNEL, &chanConfig);
	return PyLong_FromLong(sts);
}


static PyObject *
PyGage_GetTriggerConfig(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;

	int sts;
	unsigned long trigger;
	int config = CS_CURRENT_CONFIGURATION;

	if (!PyArg_ParseTuple(args, "II|i", &csHandle, &trigger, &config))
	{
		return NULL;
	}
	// check if channel is within bounds (or let the call to the drivers do it)

	CSTRIGGERCONFIG trigConfig;
	trigConfig.u32Size = sizeof(CSTRIGGERCONFIG);
	trigConfig.u32TriggerIndex = trigger;

	sts = CsGet(csHandle, CS_TRIGGER, config, &trigConfig);

	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	PyObject *d = PyDict_New();
	if (!d)
	{
		return NULL;
	}

	PyObject* p = Py_BuildValue("I", trigConfig.u32Condition);
	PyDict_SetItemString(d, "Condition", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", trigConfig.i32Level);
	PyDict_SetItemString(d, "Level", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", trigConfig.i32Source);
	PyDict_SetItemString(d, "Source", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", trigConfig.u32ExtCoupling);
	PyDict_SetItemString(d, "ExtCoupling", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", trigConfig.u32ExtTriggerRange);
	PyDict_SetItemString(d, "ExtRange", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", trigConfig.u32ExtImpedance);
	PyDict_SetItemString(d, "ExtImpedance", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", trigConfig.u32Relation);
	PyDict_SetItemString(d, "Relation", p);
	Py_XDECREF(p);

	return d;
}

static PyObject *
PyGage_SetTriggerConfig(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	unsigned int trigger;
	PyObject* dict = NULL;

	if (!PyArg_ParseTuple(args, "IIO", &csHandle, &trigger, &dict))
	{
		return NULL;
	}

	if (!PyDict_Check(dict))
	{
		PyErr_SetString(PyExc_TypeError, "Expecting a dictionary");
		return NULL;
	}
#ifdef PYTHON3
	if (!PyArg_ValidateKeywordArguments(dict))
	{
		// error of some kind
	}
#endif

	PyObject *key, *value;
	Py_ssize_t pos = 0;

	while (PyDict_Next(dict, &pos, &key, &value))
	{
#if PY_MAJOR_VERSION < 3
		PyObject* obj = PyObject_Str(key);
		if (obj == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "All fields in TriggerConfig must be strings");
			return NULL;
		}
		const char* key_string = PyString_AsString(obj);
		if (key_string == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "All fields in AcquisitionConfig must be strings");
			Py_XDECREF(obj);
			return NULL;
		}
#else
		PyObject* obj = PyUnicode_AsEncodedString(key, "utf-8", "~E~");
		if (obj == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "Cannot read field from TriggerConfig");
			return NULL;
		}
		const char *key_string = PyBytes_AsString(obj);
		if (key_string == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "Cannot read field from TriggerConfig");
			Py_XDECREF(obj);
			return NULL;
		}
#endif

		if (std::find(triggerFields.begin(), triggerFields.end(), key_string) == triggerFields.end())
		{
			// key is not in the vector
			char s[100];
			sprintf_s(s, 100, "%s is not a valid field in TriggerConfig", key_string);
			PyErr_SetString(PyExc_TypeError, s);
			Py_XDECREF(obj);
			return NULL;
		}
		Py_XDECREF(obj);
	}

	CSTRIGGERCONFIG trigConfig;
	trigConfig.u32Size = sizeof(CSTRIGGERCONFIG);
	trigConfig.u32TriggerIndex = trigger;

	int sts = CsGet(csHandle, CS_TRIGGER, CS_CURRENT_CONFIGURATION, &trigConfig);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}

	PyObject* p = PyDict_GetItemString(dict, "Condition");
	if (p)
	{
		trigConfig.u32Condition = PyLong_AsUnsignedLong(p);
	}

	p = PyDict_GetItemString(dict, "Level");
	if (p)
	{
		trigConfig.i32Level = PyLong_AsLong(p);
	}

	p = PyDict_GetItemString(dict, "Source");
	if (p)
	{
		trigConfig.i32Source = PyLong_AsLong(p);
	}

	p = PyDict_GetItemString(dict, "ExtCoupling");
	if (p)
	{
		trigConfig.u32ExtCoupling = PyLong_AsUnsignedLong(p);
	}
	p = PyDict_GetItemString(dict, "ExtRange");
	if (p)		// RG NEED TO FIX THIS ONE
	{
		trigConfig.u32ExtTriggerRange = PyLong_AsUnsignedLong(p);
	}
	p = PyDict_GetItemString(dict, "ExtImpedance");
	if (p)
	{
		trigConfig.u32ExtImpedance = PyLong_AsUnsignedLong(p);
	}
	p = PyDict_GetItemString(dict, "Relation");
	if (p)
	{
		trigConfig.u32Relation = PyLong_AsUnsignedLong(p);
	}

	sts = CsSet(csHandle, CS_TRIGGER, &trigConfig);
	return PyLong_FromLong(sts);
}

static PyObject *
PyGage_GetExtendedBoardOptions(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int64 i64Value;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	int sts = CsGet(csHandle, 0, CS_EXTENDED_BOARD_OPTIONS, &i64Value);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	else
	{
		return PyLong_FromLongLong(i64Value);
	}
}

static PyObject *
PyGage_GetStreamTotalDataSizeInBytes(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int64 i64Value;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	int sts = CsGet(csHandle, 0, CS_STREAM_TOTALDATA_SIZE_BYTES, &i64Value);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	else
	{
		return PyLong_FromLongLong(i64Value);
	}
}

static PyObject *
PyGage_GetStreamSegmentDataSizeInSamples(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int64 i64Value;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	int sts = CsGet(csHandle, 0, CS_STREAM_SEGMENTDATA_SIZE_SAMPLES, &i64Value);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	else
	{
		return PyLong_FromLongLong(i64Value);
	}
}

static PyObject *
PyGage_GetTimeStampFrequency(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int64 i64Value;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	int sts = CsGet(csHandle, 0, CS_TIMESTAMP_TICKFREQUENCY, &i64Value);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	else
	{
		return PyLong_FromLongLong(i64Value);
	}
}


static PyObject *
PyGage_GetDataPackingMode(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	CsDataPackMode packConfig;
	sts = CsGet(csHandle, 0, CS_DATAPACKING_MODE, &packConfig);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	else
	{
		return PyLong_FromLong(packConfig);
	}
}

static PyObject *
PyGage_GetStreamingCaptureMode(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	CsCaptureMode captureConfig;
	sts = CsGet(csHandle, 0, CS_CAPTURE_MODE, &captureConfig);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	else
	{
		return PyLong_FromLong(captureConfig);
	}
}

static PyObject *
PyGage_GetDataFormatInfo(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}

	CS_STRUCT_DATAFORMAT_INFO dataFormatInfo;
	dataFormatInfo.u32Size = sizeof(CS_STRUCT_DATAFORMAT_INFO);

	sts = CsGet(csHandle, 0, CS_GET_DATAFORMAT_INFO, &dataFormatInfo);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	else
	{
		// make dictionary  RG = Maybe make it a list of tuples RG
		PyObject *d = PyDict_New();
		if (!d)
		{
			return NULL;
		}

		PyObject* p = Py_BuildValue("I", dataFormatInfo.u32Signed);
		PyDict_SetItemString(d, "Signed", p);
		Py_XDECREF(p);

		p = Py_BuildValue("I", dataFormatInfo.u32Packed);
		PyDict_SetItemString(d, "Packed", p);
		Py_XDECREF(p);

		p = Py_BuildValue("I", dataFormatInfo.u32SampleBits);
		PyDict_SetItemString(d, "SampleBits", p);
		Py_XDECREF(p);

		p = Py_BuildValue("I", dataFormatInfo.u32SampleSize_Bits);
		PyDict_SetItemString(d, "SampleSizeBits", p);
		Py_XDECREF(p);

		p = Py_BuildValue("i", dataFormatInfo.i32SampleOffset);
		PyDict_SetItemString(d, "SampleOffset", p);
		Py_XDECREF(p);

		p = Py_BuildValue("i", dataFormatInfo.i32SampleRes);
		PyDict_SetItemString(d, "SampleResolution", p);
		Py_XDECREF(p);

		return d;
	}
}


static PyObject *
PyGage_GetTriggeredInfo(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;
	unsigned long start = 0;
	unsigned long count = 0;

	if (!PyArg_ParseTuple(args, "I|II", &csHandle, &start, &count))
	{
		return NULL;
	}
	CSACQUISITIONCONFIG csAcqCfg;
	csAcqCfg.u32Size = sizeof(CSACQUISITIONCONFIG);
	sts = CsGet(csHandle, CS_ACQUISITION, CS_ACQUISITION_CONFIGURATION, &csAcqCfg);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}

	TRIGGERED_INFO_STRUCT triggerStruct;
	triggerStruct.u32Size = sizeof(TRIGGERED_INFO_STRUCT);
	// if parameter2 and parameter3 are 0 they were not passed in to the function
	// so set some defaults
	triggerStruct.u32StartSegment = (start != 0) ? start : 1;
	triggerStruct.u32NumOfSegments = (count != 0) ? count : csAcqCfg.u32SegmentCount;

	triggerStruct.u32BufferSize = triggerStruct.u32NumOfSegments * sizeof(int16);

	int16* buffer = (int16 *)malloc(triggerStruct.u32BufferSize);
	if (!buffer)
	{
		sts = CS_MEMORY_ERROR;
		return PyLong_FromLong(sts);
	}
	memset(buffer, 0, triggerStruct.u32BufferSize); // to avoid compiler warning in linux
	sts = CsGet(csHandle, 0, CS_TRIGGERED_INFO, &triggerStruct);
	if (CS_FAILED(sts))
	{
		free(buffer);
		return PyLong_FromLong(sts);
	}
	else
	{
		PyObject* list = PyList_New(triggerStruct.u32NumOfSegments);
		if (list)
		{
			for (unsigned int i = 0; i < triggerStruct.u32NumOfSegments; i++)
			{
				// PyList_SetItem steals a reference to the object from Py_BuildValue
				// so it doesn't need to be decremented
				PyList_SetItem(list, i, Py_BuildValue("h", buffer[i]));
			}
		}
		free(buffer);
		return list;
	}
}

static PyObject *
PyGage_GetFftConfig(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	CS_FFT_CONFIG_PARAMS fftParams = { 0 };
	fftParams.u32Size = sizeof(CS_FFT_CONFIG_PARAMS);

	sts = CsGet(csHandle, 0, CS_FFT_CONFIG, &fftParams);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	// make dictionary
	PyObject *d = PyDict_New();
	if (!d)
	{
		return NULL;
	}

	PyObject* p = Py_BuildValue("H", fftParams.u16FFTSize);
	PyDict_SetItemString(d, "FftSize", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", fftParams.bEnable);
	PyDict_SetItemString(d, "Enable", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", fftParams.bAverage);
	PyDict_SetItemString(d, "Average", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", fftParams.bRealOnly);
	PyDict_SetItemString(d, "RealOnly", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", fftParams.bWindowing);
	PyDict_SetItemString(d, "Windowing", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", fftParams.bIFFT);
	PyDict_SetItemString(d, "IFft", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", fftParams.bFftMr);
	PyDict_SetItemString(d, "FftMr", p);

	return d;
}


static PyObject *
PyGage_GetSegmentTailSizeInBytes(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	uInt32 u32Value;
	sts = CsGet(csHandle, 0, CS_SEGMENTTAIL_SIZE_BYTES, &u32Value);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	else
	{
		return PyLong_FromUnsignedLong(u32Value);
	}
}

static PyObject *
PyGage_GetMulRecAverageCount(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	uInt32 u32Value;
	sts = CsGet(csHandle, 0, CS_MULREC_AVG_COUNT, &u32Value);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	else
	{
		return PyLong_FromUnsignedLong(u32Value);
	}
}


static PyObject *
PyGage_SetDataPackingMode(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;
	int mode;

	if (!PyArg_ParseTuple(args, "Ii", &csHandle, &mode))
	{
		return NULL;
	}
	sts = CsSet(csHandle, CS_DATAPACKING_MODE, &mode);
	return PyLong_FromLong(sts);
}

static PyObject *
PyGage_SetStreamingCaptureMode(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;
	int mode;

	if (!PyArg_ParseTuple(args, "Ii", &csHandle, &mode))
	{
		return NULL;
	}
	sts = CsSet(csHandle, CS_CAPTURE_MODE, &mode);
	return PyLong_FromLong(sts);
}


static PyObject *
PyGage_SetFftConfig(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;

	PyObject* dict = NULL;

	if (!PyArg_ParseTuple(args, "IO", &csHandle, &dict))
	{
		return NULL;
	}

	if (!PyDict_Check(dict))
	{
		PyErr_SetString(PyExc_TypeError, "Expecting a dictionary");
		return NULL;
	}
#ifdef PYTHON3
	if (!PyArg_ValidateKeywordArguments(dict))
	{
		// error of some kind
	}
#endif

	PyObject *key, *value;
	Py_ssize_t pos = 0;

	while (PyDict_Next(dict, &pos, &key, &value))
	{
#if PY_MAJOR_VERSION < 3
		PyObject* obj = PyObject_Str(key);
		if (obj == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "All fields in FftConfig must be strings");
			return NULL;
		}
		const char* key_string = PyString_AsString(obj);
		if (key_string == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "All fields in FftConfig must be strings");
			Py_XDECREF(obj);
			return NULL;
		}
#else
		PyObject* obj = PyUnicode_AsEncodedString(key, "utf-8", "~E~");
		if (obj == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "Cannot read field from FftConfig");
			return NULL;
		}
		const char *key_string = PyBytes_AsString(obj);
		if (key_string == NULL)
		{
			PyErr_SetString(PyExc_TypeError, "Cannot read field from FftConfig");
			Py_XDECREF(obj);
			return NULL;
		}
#endif

		if (std::find(fftConfigFields.begin(), fftConfigFields.end(), key_string) == fftConfigFields.end())
		{
			// key is not in the vector
			char s[100];
			sprintf_s(s, 100, "%s is not a valid field in FftConfig", key_string);
			PyErr_SetString(PyExc_TypeError, s);
			Py_XDECREF(obj);
			return NULL;
		}
		Py_XDECREF(obj);
	}

	CS_FFT_CONFIG_PARAMS fftParams = { 0 };
	fftParams.u32Size = sizeof(CS_FFT_CONFIG_PARAMS);
	// Get what's already there in case  parameter is missing
	int sts = CsGet(csHandle, 0, CS_FFT_CONFIG, &fftParams);

	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}

	PyObject* p = PyDict_GetItemString(dict, "FftSize");
	if (p)
	{
		fftParams.u16FFTSize = (unsigned short)PyLong_AsUnsignedLong(p);
	}

	p = PyDict_GetItemString(dict, "Enable");
	if (p)
	{
		fftParams.bEnable = PyLong_AsLong(p);
	}
	p = PyDict_GetItemString(dict, "Average");
	if (p)
	{
		fftParams.bAverage = PyLong_AsLong(p);
	}
	p = PyDict_GetItemString(dict, "RealOnly");
	if (p)
	{
		fftParams.bRealOnly = PyLong_AsLong(p);
	}
	p = PyDict_GetItemString(dict, "Windowing");
	if (p)
	{
		fftParams.bWindowing = PyLong_AsLong(p);
	}
	p = PyDict_GetItemString(dict, "IFft");
	if (p)
	{
		fftParams.bIFFT = PyLong_AsLong(p);
	}
	p = PyDict_GetItemString(dict, "FftMr");
	if (p)
	{
		fftParams.bFftMr = PyLong_AsLong(p);
	}

	sts = CsSet(csHandle, CS_FFT_CONFIG, &fftParams);
	return PyLong_FromLong(sts);
}



static PyObject *
PyGage_SetFftWindowConfig(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	unsigned short windowSize;
	PyObject* list = NULL;

	if (!PyArg_ParseTuple(args, "IHO", &csHandle, &windowSize, &list))
	{
		return NULL;
	}
	if (!PyList_Check(list))
	{
		PyErr_SetString(PyExc_TypeError, "Expecting a list");
		return NULL;
	}
/*
	if (!PyDict_Check(dict))
	{
		// return an error message !! RICKY
	}
	if (!PyArg_ValidateKeywordArguments(dict))
	{
		// error of some kind
	}
*/

	PCS_FFTWINDOW_CONFIG_PARAMS pFFTWindowConfig = (PCS_FFTWINDOW_CONFIG_PARAMS)malloc(sizeof(CS_FFTWINDOW_CONFIG_PARAMS) + windowSize * sizeof(int16));
	if (!pFFTWindowConfig)
	{
		int sts = CS_MEMORY_ERROR;
		return PyLong_FromLong(sts);
	}
	
	// RG - maybe call CsGetFFtConfig to get the size and compare to this one
	pFFTWindowConfig->u16FFTWinSize = windowSize;
	// RG Do I even need windowSize if I have PyList_GetSize(list)
	// RG if PyList_Size(list) does not equal windowSize or FFT_CONFIG_PARAMS::fft_size
	size_t len = PyList_Size(list); // RG - check if len == fft size
	for (size_t i = 0; i < len; i++)
	{
		pFFTWindowConfig->i16Coefficients[i] = (short)PyLong_AsLong(PyList_GetItem(list, i));
	}

	int sts = CsSet(csHandle, CS_FFTWINDOW_CONFIG, pFFTWindowConfig);

	free(pFFTWindowConfig);
	return PyLong_FromLong(sts);
}

static PyObject *
PyGage_SetMulRecAverageCount(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	unsigned long averageCount;


	if (!PyArg_ParseTuple(args, "II", &csHandle, &averageCount))
	{
		return NULL;
	}
	int sts = CsSet(csHandle, CS_MULREC_AVG_COUNT, &averageCount);
	return PyLong_FromLong(sts);
}

static PyObject *
PyGage_SetIdentifyLed(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	int sts = CsSet(csHandle, CS_IDENTIFY_LED, NULL);
	return PyLong_FromLong(sts);
}

static PyObject *
PyGage_SetOneSampleResolution(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
	{
		return NULL;
	}
	int sts = CsSet(csHandle, CS_ONE_SAMPLE_DEPTH_RESOLUTION, NULL);
	return PyLong_FromLong(sts);
}


static PyObject *
PyGage_GetSystemCaps(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	unsigned int capsId;
	int sts;

	if (!PyArg_ParseTuple(args, "II", &csHandle, &capsId))
		return NULL;

	unsigned int mainCapsId = capsId & 0xFFFF0000;

	switch (mainCapsId)
	{
		case CAPS_SAMPLE_RATES:
		{
			uInt32 requiredSize = 0;
			sts = CsGetSystemCaps(csHandle, capsId, NULL, &requiredSize);
			if (CS_FAILED(sts))
			{
				return PyLong_FromLong(sts);
			}
			CSSAMPLERATETABLE* sampleRateTable = (CSSAMPLERATETABLE* )malloc(requiredSize);
			if (!sampleRateTable)
			{
				sts = CS_MEMORY_ERROR;
				return PyLong_FromLong(sts);
			}
			sts = CsGetSystemCaps(csHandle, capsId, sampleRateTable, &requiredSize);
			if (CS_FAILED(sts))
			{
				free(sampleRateTable);
				return PyLong_FromLong(sts);
			}
			size_t listSize = requiredSize / sizeof(CSSAMPLERATETABLE);
			PyObject* list = PyList_New(listSize);
			if (!list)
			{
				free(sampleRateTable);
				return NULL;
			}

			for (size_t i = 0; i < listSize; i++)
			{
				wchar_t str[32];
				PyObject* tuple = PyTuple_New(2);
				ConvertToUnicodeString(sampleRateTable[i].strText, str);

				// PyTuple_SetItem and PyList_SetItem both steal a reference to the third item
				// so it doesn't have to be decremented
				PyTuple_SetItem(tuple, 0, Py_BuildValue("u", str));  // RG this steals a reference to 
				PyTuple_SetItem(tuple, 1, Py_BuildValue("L", sampleRateTable[i].i64SampleRate));
				PyList_SetItem(list, i, tuple);
			}
			free(sampleRateTable);
			return list;
		}
		break;

		case CAPS_INPUT_RANGES:
		{
			uInt32 requiredSize = 0;

			sts = CsGetSystemCaps(csHandle, capsId, NULL, &requiredSize);
			if (CS_FAILED(sts))
			{
				return PyLong_FromLong(sts);
			}
			CSRANGETABLE* rangeTable = (CSRANGETABLE*)malloc(requiredSize);
			if (!rangeTable)
			{
				sts = CS_MEMORY_ERROR;
				return PyLong_FromLong(sts);
			}
			sts = CsGetSystemCaps(csHandle, capsId, rangeTable, &requiredSize);
			if (CS_FAILED(sts))
			{
				free(rangeTable);
				return PyLong_FromLong(sts);
			}
			size_t listSize = requiredSize / sizeof(CSRANGETABLE);

			PyObject* list = PyList_New(listSize);
			if (!list)
			{
				free(rangeTable);
				return NULL;
			}

			for (size_t i = 0; i < listSize; i++)
			{
				wchar_t str[32];
				PyObject* tuple = PyTuple_New(2);
				ConvertToUnicodeString(rangeTable[i].strText, str);
				// PyTuple_SetItem and PyList_SetItem both steal a reference to the third item
				// so it doesn't have to be decremented
				PyTuple_SetItem(tuple, 0, Py_BuildValue("u", str));  // RG this steals a reference to 
				PyTuple_SetItem(tuple, 1, Py_BuildValue("I", rangeTable[i].u32InputRange));
				PyList_SetItem(list, i, tuple);
			}
			free(rangeTable);
			return list;
		}
		break;

		case CAPS_IMPEDANCES:
		{
			uInt32 requiredSize = 0;

			sts = CsGetSystemCaps(csHandle, capsId, NULL, &requiredSize);
			if (CS_FAILED(sts))
			{
				return PyLong_FromLong(sts);
			}
			CSIMPEDANCETABLE* impedanceTable = (CSIMPEDANCETABLE*)malloc(requiredSize);
			if (!impedanceTable)
			{
				sts = CS_MEMORY_ERROR;
				return PyLong_FromLong(sts);
			}
			sts = CsGetSystemCaps(csHandle, capsId, impedanceTable, &requiredSize);
			if (CS_FAILED(sts))
			{
				free(impedanceTable);
				return PyLong_FromLong(sts);
			}
			size_t listSize = requiredSize / sizeof(CSIMPEDANCETABLE);
			PyObject* list = PyList_New(listSize);
			if (!list)
			{
				free(impedanceTable);
				return NULL;
			}

			// PyTuple_SetItem and PyList_SetItem both steal a reference to the third item
			// so it doesn't have to be decremented
			for (size_t i = 0; i < listSize; i++)
			{
				wchar_t str[32];
				PyObject* tuple = PyTuple_New(2);
				ConvertToUnicodeString(impedanceTable[i].strText, str);
				PyTuple_SetItem(tuple, 0, Py_BuildValue("u", str));  // RG this steals a reference to 
				PyTuple_SetItem(tuple, 1, Py_BuildValue("I", impedanceTable[i].u32Imdepdance));
				PyList_SetItem(list, i, tuple);
			}
			free(impedanceTable);
			return list;
		}
		break;

		case CAPS_COUPLINGS:
		{
			uInt32 requiredSize = 0;

			sts = CsGetSystemCaps(csHandle, capsId, NULL, &requiredSize);
			if (CS_FAILED(sts))
			{
				return PyLong_FromLong(sts);
			}
			CSCOUPLINGTABLE* couplingTable = (CSCOUPLINGTABLE*)malloc(requiredSize);
			if (!couplingTable)
			{
				sts = CS_MEMORY_ERROR;
				return PyLong_FromLong(sts);
			}
			sts = CsGetSystemCaps(csHandle, capsId, couplingTable, &requiredSize);
			if (CS_FAILED(sts))
			{
				free(couplingTable);
				return PyLong_FromLong(sts);
			}
			size_t listSize = requiredSize / sizeof(CSCOUPLINGTABLE);
			PyObject* list = PyList_New(listSize);
			if (!list)
			{
				free(couplingTable);
				return NULL;
			}
			/* RG - CHECK IF THE TUPLE NEEDS TO BE DECREMENTED */
			// PyTuple_SetItem and PyList_SetItem both steal a reference to the third item
			// so it doesn't have to be decremented
			for (size_t i = 0; i < listSize; i++)
			{
				wchar_t str[32];
				PyObject* tuple = PyTuple_New(2);
				ConvertToUnicodeString(couplingTable[i].strText, str);
				PyTuple_SetItem(tuple, 0, Py_BuildValue("u", str));  // RG this steals a reference to 
				PyTuple_SetItem(tuple, 1, Py_BuildValue("I", couplingTable[i].u32Coupling));
				PyList_SetItem(list, i, tuple);
			}
			free(couplingTable);
			return list;
		}
		break;

		case CAPS_ACQ_MODES:
		{
			unsigned long modesSupported;
			uInt32 requiredSize = sizeof(modesSupported);
			sts = CsGetSystemCaps(csHandle, capsId, &modesSupported, &requiredSize);
			if (CS_FAILED(sts))
			{
				return PyLong_FromLong(sts);
			}
			int modeCount = 0;

			if ((modesSupported & CS_MODE_OCT) != 0)
				modeCount++;
			if ((modesSupported & CS_MODE_QUAD) != 0)
				modeCount++;
			if ((modesSupported & CS_MODE_DUAL) != 0)
				modeCount++;
			if ((modesSupported & CS_MODE_SINGLE) != 0)
				modeCount++;

			// if modeCount == 0 there's a problem
			if (0 == modeCount)
			{
				sts = CS_MISC_ERROR; // rg - check if this is appropriate
				return PyLong_FromLong(sts);
			}

			PyObject* list = PyList_New(modeCount);
			if (!list)
			{
				return NULL;
			}
			// PyTuple_SetItem and PyList_SetItem both steal a reference to the third item
			// so it doesn't have to be decremented
			int index = 0;
			if ((modesSupported & CS_MODE_OCT) != 0)
			{
				PyObject* tuple = PyTuple_New(2);
				PyTuple_SetItem(tuple, 0, Py_BuildValue("s", "Octal"));  // RG this steals a reference to 
				PyTuple_SetItem(tuple, 1, Py_BuildValue("I", CS_MODE_OCT));
				PyList_SetItem(list, index++, tuple);
			}
			if ((modesSupported & CS_MODE_QUAD) != 0)
			{
				PyObject* tuple = PyTuple_New(2);
				PyTuple_SetItem(tuple, 0, Py_BuildValue("s", "Quad"));  // RG this steals a reference to 
				PyTuple_SetItem(tuple, 1, Py_BuildValue("I", CS_MODE_QUAD));
				PyList_SetItem(list, index++, tuple);
			}
			if ((modesSupported & CS_MODE_DUAL) != 0)
			{
				PyObject* tuple = PyTuple_New(2);
				PyTuple_SetItem(tuple, 0, Py_BuildValue("s", "Dual"));  // RG this steals a reference to 
				PyTuple_SetItem(tuple, 1, Py_BuildValue("I", CS_MODE_DUAL));
				PyList_SetItem(list, index++, tuple);
			}
			if ((modesSupported & CS_MODE_SINGLE) != 0)
			{
				PyObject* tuple = PyTuple_New(2);
				PyTuple_SetItem(tuple, 0, Py_BuildValue("s", "Single"));  // RG this steals a reference to 
				PyTuple_SetItem(tuple, 1, Py_BuildValue("I", CS_MODE_SINGLE));
				PyList_SetItem(list, index++, tuple);
			}
			return list;
		}
		break;

		case CAPS_TERMINATIONS:
		{
			uInt32 termsSupported = 0;
			uInt32 terminationSize = sizeof(termsSupported);
			sts = CsGetSystemCaps(csHandle, capsId, &termsSupported, &terminationSize);
			if (CS_FAILED(sts))
			{
				return PyLong_FromLong(sts);
			}
			size_t listSize = 0;
			if (termsSupported & CS_DIRECT_ADC_INPUT)
			{
				listSize++;
			}
			if (termsSupported & CS_DIFFERENTIAL_INPUT)
			{
				listSize++;
			}
			if (0 == listSize)
			{
				sts = CS_FUNCTION_NOT_SUPPORTED;
				return PyLong_FromLong(sts);
			}

			PyObject* list = PyList_New(listSize);
			if (!list)
			{
				return NULL;
			}
			size_t i = 0;
			if (termsSupported & CS_DIRECT_ADC_INPUT)
			{
				PyObject* tuple = PyTuple_New(2);
				PyTuple_SetItem(tuple, 0, Py_BuildValue("s", "Direct ADC"));  // RG this steals a reference to 
				PyTuple_SetItem(tuple, 1, Py_BuildValue("I", CS_DIRECT_ADC_INPUT));
				PyList_SetItem(list, i++, tuple);
			}
			if (termsSupported & CS_DIFFERENTIAL_INPUT)
			{
				PyObject* tuple = PyTuple_New(2);
				PyTuple_SetItem(tuple, 0, Py_BuildValue("s", "Differential Input"));  // RG this steals a reference to 
				PyTuple_SetItem(tuple, 1, Py_BuildValue("I", CS_DIFFERENTIAL_INPUT));
				PyList_SetItem(list, i++, tuple);
			}
			return list;
		}
		break;

		case CAPS_TRIGGER_SOURCES:
		{
			uInt32 requiredSize = 0;
			sts = CsGetSystemCaps(csHandle, capsId, NULL, &requiredSize);
			if (CS_FAILED(sts))
			{
				return PyLong_FromLong(sts);
			}

			int* trigSrc = (int*)malloc(requiredSize);
			if (!trigSrc)
			{
				sts = CS_MEMORY_ERROR;
				return PyLong_FromLong(sts);
			}
			sts = CsGetSystemCaps(csHandle, capsId, trigSrc, &requiredSize);
			if (CS_FAILED(sts))
			{
				free(trigSrc);
				return PyLong_FromLong(sts);
			}
			size_t listSize = requiredSize / sizeof(int32);
			PyObject* list = PyList_New(listSize);
			if (!list)
			{
				free(trigSrc);
				sts = CS_MEMORY_ERROR;
				return PyLong_FromLong(sts);
			}
			for (size_t index = 0; index < listSize; index++)
			{
				PyObject* tuple = PyTuple_New(2);
				if (!tuple)
				{
					free(trigSrc);
					Py_DECREF(list);
					sts = CS_MEMORY_ERROR;
					return PyLong_FromLong(sts);
				}
				if (CS_TRIG_SOURCE_EXT == trigSrc[index])
				{
					PyTuple_SetItem(tuple, 0, Py_BuildValue("s", "External"));  // RG this steals a reference to 
					PyTuple_SetItem(tuple, 1, Py_BuildValue("i", CS_TRIG_SOURCE_EXT));
				}
				else if (CS_TRIG_SOURCE_DISABLE == trigSrc[index])
				{
					PyTuple_SetItem(tuple, 0, Py_BuildValue("s", "Disable"));  // RG this steals a reference to 
					PyTuple_SetItem(tuple, 1, Py_BuildValue("i", CS_TRIG_SOURCE_DISABLE));
				}					
				else
				{
					char str[10];
					sprintf_s(str, _countof(str), "Channel %d", trigSrc[index]);
					PyTuple_SetItem(tuple, 0, Py_BuildValue("s", str));  // RG this steals a reference to 
					PyTuple_SetItem(tuple, 1, Py_BuildValue("i", trigSrc[index]));
				}
				PyList_SetItem(list, index, tuple); 
			}
			free(trigSrc);
			return list;
		}
		break;

		case CAPS_FILTERS:
		{
			uInt32 requiredSize = 0;

			sts = CsGetSystemCaps(csHandle, capsId, NULL, &requiredSize);
			if (CS_FAILED(sts))
			{
				return PyLong_FromLong(sts);
			}
			CSFILTERTABLE* filterTable = (CSFILTERTABLE*)malloc(requiredSize);
			if (!filterTable)
			{
				return PyLong_FromLong(sts);
			}
			sts = CsGetSystemCaps(csHandle, capsId, filterTable, &requiredSize);
			if (CS_FAILED(sts))
			{
				free(filterTable);
				return PyLong_FromLong(sts);
			}
			size_t listSize = requiredSize / sizeof(CSFILTERTABLE);
			PyObject* list = PyList_New(listSize);
			if (!list)
			{
				free(filterTable);
				sts = CS_MEMORY_ERROR;
				return PyLong_FromLong(sts);
			}

			PyObject* tupleLow = PyTuple_New(2); // ?? RG when do i delete this
			PyTuple_SetItem(tupleLow, 0, Py_BuildValue("s", "Low Cutoff"));  // RG this steals a reference to 
			PyTuple_SetItem(tupleLow, 1, Py_BuildValue("I", filterTable->u32LowCutoff));
			PyList_SetItem(list, 0, tupleLow);
			PyObject* tupleHigh = PyTuple_New(2); // ?? RG when do i delete this
			PyTuple_SetItem(tupleHigh, 0, Py_BuildValue("s", "High Cutoff"));  // RG this steals a reference to 
			PyTuple_SetItem(tupleHigh, 1, Py_BuildValue("I", filterTable->u32HighCutoff));
			PyList_SetItem(list, 1, tupleHigh);

			free(filterTable);
			return list;
		}
		break;

		case CAPS_FLEXIBLE_TRIGGER:
		case CAPS_MAX_SEGMENT_PADDING:
		case CAPS_BOARD_TRIGGER_ENGINES:
		case CAPS_FWCHANGE_REBOOT:
		case CAPS_SKIP_COUNT:
		case CAPS_MAX_PRE_TRIGGER:
		case CAPS_DEPTH_INCREMENT:
		case CAPS_TRIGGER_DELAY_INCREMENT:
		case CAPS_TRIGGER_RES:
		case CAPS_TRIG_ENGINES_PER_CHAN:
		case CAPS_STM_TRANSFER_SIZE_BOUNDARY:
		{
			uInt32 requiredSize = sizeof(uInt32);
			uInt32 value;
			sts = CsGetSystemCaps(csHandle, capsId, &value, &requiredSize);
			if (CS_FAILED(sts))
			{
				return PyLong_FromLong(sts);  //RG -check how value is returned
			}
			else
			{
				return PyLong_FromUnsignedLong(value);
			}
		}
		break;

		case CAPS_DC_OFFSET_ADJUST:
		case CAPS_CLK_IN:
		case CAPS_BOOTIMAGE0:
		case CAPS_EXT_TRIGGER_UNIPOLAR:
		case CAPS_MULREC:
		case CAPS_SELF_IDENTIFY:
		case CAPS_SINGLE_CHANNEL2:
		case CAPS_TRANSFER_EX:
		{
			return PyLong_FromLong(CsGetSystemCaps(csHandle, capsId, 0, 0));
		}
		break;

		case CAPS_MIN_EXT_RATE:
		case CAPS_MAX_EXT_RATE:
		{
			uInt32 requiredSize = sizeof(int64);
			int64 value;
			sts = CsGetSystemCaps(csHandle, capsId, &value, &requiredSize);
			if (CS_FAILED(sts))
			{
				return PyLong_FromLong(sts);  //RG -check how value is returned
			}
			else
			{
				return PyLong_FromLongLong(value);
			}
		}
		break;

		case CAPS_AUX_CONFIG:
		{
			CS_CAPS_AUX_CONFIG auxConfig = { 0 };
			auxConfig.u32Size = sizeof(CS_CAPS_AUX_CONFIG);

			sts = CsGetSystemCaps(csHandle, capsId, &auxConfig, &auxConfig.u32Size);
			if (CS_FAILED(sts))
			{
				return PyLong_FromLong(sts);
			}

			PyObject* list = PyList_New(5);
			if (!list)
			{
				sts = CS_MEMORY_ERROR;
				return PyLong_FromLong(sts);
			}
			PyObject* tuple = PyTuple_New(2); // ?? RG when do i delete this
			if (!tuple)
			{
				Py_DECREF(list);
				sts = CS_MEMORY_ERROR;
				return PyLong_FromLong(sts);
			}
			// RG - might need a new tuple for each of these - USE ARRAY
			PyTuple_SetItem(tuple, 0, Py_BuildValue("s", "ClockOut"));  // RG this steals a reference to 
			PyTuple_SetItem(tuple, 1, Py_BuildValue("i", auxConfig.bClockOut));
			PyList_SetItem(list, 0, tuple);
			PyObject* tuple2 = PyTuple_New(2);
			PyTuple_SetItem(tuple2, 0, Py_BuildValue("s", "TriggerOut"));  // RG this steals a reference to 
			PyTuple_SetItem(tuple2, 1, Py_BuildValue("i", auxConfig.bTrigOut));
			PyList_SetItem(list, 1, tuple2);
			PyObject* tuple3 = PyTuple_New(2);
			PyTuple_SetItem(tuple3, 0, Py_BuildValue("s", "TrigOut_In"));  // RG this steals a reference to 
			PyTuple_SetItem(tuple3, 1, Py_BuildValue("i", auxConfig.bTrigOut_IN));
			PyList_SetItem(list, 2, tuple3);
			PyObject* tuple4 = PyTuple_New(2);
			PyTuple_SetItem(tuple4, 0, Py_BuildValue("s", "AuxInput"));  // RG this steals a reference to 
			PyTuple_SetItem(tuple4, 1, Py_BuildValue("i", auxConfig.bAuxIN));
			PyList_SetItem(list, 3, tuple4);
			PyObject* tuple5 = PyTuple_New(2);
			PyTuple_SetItem(tuple5, 0, Py_BuildValue("s", "AuxOutput"));  // RG this steals a reference to 
			PyTuple_SetItem(tuple5, 1, Py_BuildValue("i", auxConfig.bAuxOUT));
			PyList_SetItem(list, 4, tuple5);

			return list;

/*
			PyDict_SetItemString(d, "ClockOut", Py_BuildValue("i", auxConfig.bClockOut));
			PyDict_SetItemString(d, "TriggerOut", Py_BuildValue("i", auxConfig.bTrigOut));
			PyDict_SetItemString(d, "TrigOut_In", Py_BuildValue("i", auxConfig.bTrigOut_IN));
			PyDict_SetItemString(d, "AuxInput", Py_BuildValue("i", auxConfig.bAuxIN));
			PyDict_SetItemString(d, "AuxOutput", Py_BuildValue("i", auxConfig.bAuxOUT));


			return d;
*/

		}
		break;

		case CAPS_CLOCK_OUT:
		case CAPS_TRIG_OUT:
		case CAPS_AUX_OUT:
		case CAPS_AUX_IN_TIMESTAMP:
		case CAPS_TRIG_ENABLE:
		{
			uInt32 requiredSize = 0;
			sts = CsGetSystemCaps(csHandle, capsId, NULL, &requiredSize);
			if (CS_FAILED(sts))
			{
				return PyLong_FromLong(sts);
			}
			VALID_AUX_CONFIG* pAuxConfig = (VALID_AUX_CONFIG*)malloc(requiredSize);
			if (NULL == pAuxConfig)
			{
				sts = CS_MEMORY_ERROR;
				return PyLong_FromLong(sts);
			}
			sts = CsGetSystemCaps(csHandle, capsId, pAuxConfig, &requiredSize);
			if (CS_FAILED(sts))
			{
				free(pAuxConfig);
				return PyLong_FromLong(sts);
			}
			int count = requiredSize / sizeof(pAuxConfig[0]);  //RG not sure about this

			PyObject* list = PyList_New(count);
			if (!list)
			{
				free(pAuxConfig);
				sts = CS_MEMORY_ERROR;
				return PyLong_FromLong(sts);
			}
			for (int index = 0; index < count; index++)
			{
				PyObject* tuple = PyTuple_New(2);
				if (!tuple)
				{
					free(pAuxConfig);
					Py_DECREF(list);
					sts = CS_MEMORY_ERROR;
					return PyLong_FromLong(sts);
				}
				PyTuple_SetItem(tuple, 0, Py_BuildValue("s", pAuxConfig[index].Str));  // RG this steals a reference to 
				PyTuple_SetItem(tuple, 1, Py_BuildValue("I", pAuxConfig[index].u32Ctrl));
				PyList_SetItem(list, index, tuple);
			}
			free(pAuxConfig);
			return list;
		}
		break;

		default:
		{
			sts = CS_INVALID_PARAMETER;
			return PyLong_FromLong(sts);
		}
	}
}


static PyObject *
PyGage_Commit(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
		return NULL;

	sts = CsDo(csHandle, ACTION_COMMIT);
	return PyLong_FromLong(sts);
}


static PyObject *
PyGage_StartCapture(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
		return NULL;

	sts = CsDo(csHandle, ACTION_START);
	return PyLong_FromLong(sts);
}


static PyObject *
PyGage_ForceCapture(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
		return NULL;

	sts = CsDo(csHandle, ACTION_FORCE);
	return PyLong_FromLong(sts);
}

static PyObject *
PyGage_AbortCapture(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
		return NULL;

	sts = CsDo(csHandle, ACTION_ABORT);
	return PyLong_FromLong(sts);
}


static PyObject *
PyGage_GetStatus(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	int sts;

	if (!PyArg_ParseTuple(args, "I", &csHandle))
		return NULL;

	sts = CsGetStatus(csHandle);
	return PyLong_FromLong(sts);
}

static PyObject *
PyGage_TransferData(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;

	int sts;
	unsigned int mode, segment;
	unsigned short channel;
	long long start, length;


	if (!PyArg_ParseTuple(args, "IHIILL", &csHandle, &channel, &mode, &segment, &start, &length))
	{
		return NULL;
	}
	
	IN_PARAMS_TRANSFERDATA inData;
	inData.u32Mode = mode;
	inData.u16Channel = channel;
	inData.u32Segment = segment;
	inData.i64StartAddress = start;
	inData.i64Length = length;
	inData.hNotifyEvent = NULL;

	
	OUT_PARAMS_TRANSFERDATA outData;
	CSACQUISITIONCONFIG csAcqCfg;
	csAcqCfg.u32Size = sizeof(CSACQUISITIONCONFIG);
	CsGet(csHandle, CS_ACQUISITION, CS_CURRENT_CONFIGURATION, &csAcqCfg);

	int typenum;
	int sampleSize = 0;
	if (TxMODE_DEFAULT == mode || TxMODE_DATA_ANALOGONLY == mode)
	{
		typenum = (1 == csAcqCfg.u32SampleSize) ? NPY_UBYTE : NPY_INT16;
		sampleSize = csAcqCfg.u32SampleSize;
	}
	else if (TxMODE_DATA_32 == mode) // signed 32-bit
	{
		typenum = NPY_INT32;
		sampleSize = 4;
	}
	else if (TxMODE_DATA_FFT == mode) // unsigned 32-bit
	{
		typenum = NPY_UINT32;
		sampleSize = 4;
	}
	else if (TxMODE_TIMESTAMP == mode) // signed 64-bit
	{
		typenum = NPY_INT64;
		sampleSize = 8;
	}
	else
	{
		sts = CS_INVALID_TRANSFER_MODE;
		return PyLong_FromLong(sts);
	}
	
//	void *buffer = malloc(size_t(inData.i64Length * sampleSize));
	// allocating with PyDataMem_NEW allow us to use the 'N' flag in Py_BuildValue without
	// crashing. This prevents the memory leak we get when using the 'O' flag
	void *buffer = PyDataMem_NEW(size_t(inData.i64Length * sampleSize));
	if (!buffer)
	{
		// print out python error message
		sts = CS_MEMORY_ERROR;
		return PyLong_FromLong(sts);
//		PyErr_Format(PyExc_ValueError, "Can not allocate %lld bytes", inData.i64Length * csAcqCfg.u32SampleSize);
//		return NULL;
	}
	// The buffer will be freed by numpy because we're setting the
	// NPY_ARRAY_OWNDATA flag below
	inData.pDataBuffer = buffer;  

	// check for errors
	sts = CsTransfer(csHandle, &inData, &outData);

	if (CS_FAILED(sts))
	{
		free(buffer);
		return PyLong_FromLong(sts);
	}
	else
	{
		int64 dims = outData.i64ActualLength;
		PyObject* p = PyArray_SimpleNewFromData(1, (npy_intp*)&dims, typenum, buffer);
		// next line should ensure that numpy frees the buffer when it's done
		PyArray_ENABLEFLAGS((PyArrayObject*)p, NPY_ARRAY_OWNDATA);
		// "N" does not increase the reference count but causes a crash because
		// we used NPY_ARRAY_OWNDATA, use "O" instead

		// Update May 17, 2018.  Using 'O' was causing a memory leak.  When can use
		// 'N' asl long as we allocate the memory with PyDataMem_NEW
		return Py_BuildValue("NLL", p, outData.i64ActualStart, outData.i64ActualLength);
	}
}

static PyObject *
PyGage_GetStreamingBuffer(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	unsigned short cardIndex;
	unsigned long bufferSize = 0;  // in bytes
	void* buffer = NULL;

	if (!PyArg_ParseTuple(args, "IHI", &csHandle, &cardIndex, &bufferSize))
	{
		return NULL;
	}

	int status = CsStmAllocateBuffer(csHandle, cardIndex, bufferSize, &buffer);

	if (CS_FAILED(status))
	{
		return PyLong_FromLong(status);
		// error
		// return Py_None ??? and raise exception ??
	}
	// RG for now it's an numpy buffer might have to change that
	int64 dims = (int64)bufferSize;
	return PyArray_SimpleNewFromData(1, (npy_intp*)&dims, NPY_UBYTE, buffer);
}

static PyObject *
PyGage_FreeStreamingBuffer(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	unsigned short cardIndex;

	PyObject* p;

	if (!PyArg_ParseTuple(args, "IHO", &csHandle, &cardIndex, &p))
	{
		return NULL;
	}
	void* buffer = NULL;

	buffer = PyArray_DATA((PyArrayObject*)p);

	int status = CsStmFreeBuffer(csHandle, cardIndex, buffer);
	return PyLong_FromLong(status);
}

static PyObject *
PyGage_TransferStreamingData(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	unsigned short cardIndex;
	unsigned long transferSizeInSamples;
	PyObject* p;


	if (!PyArg_ParseTuple(args, "IHOI", &csHandle, &cardIndex, &p, &transferSizeInSamples))
	{
		return NULL;
	}

	void* buffer = PyArray_DATA((PyArrayObject*)p);

  	int status = CsStmTransferToBuffer(csHandle, cardIndex, buffer, transferSizeInSamples);
	return PyLong_FromLong(status);

}

static PyObject *
PyGage_GetStreamingTransferStatus(PyObject *self, PyObject *args)
{
	CSHANDLE csHandle;
	unsigned short cardIndex;
	unsigned long waitTimeout = 0;

	if (!PyArg_ParseTuple(args, "IHI", &csHandle, &cardIndex, &waitTimeout))
	{
		return NULL;
	}
	uInt32 errorFlag;
	uInt32 actualLength;
	unsigned char endOfData;

	int status = CsStmGetTransferStatus(csHandle, cardIndex, waitTimeout, &errorFlag, &actualLength, &endOfData);

	if (CS_FAILED(status))
	{
		return PyLong_FromLong(status);
	}
	else
	{
		return Py_BuildValue("IIB", errorFlag, actualLength, endOfData);
	}
}

static PyObject *
PyGage_ConvertToSigHeader(PyObject *self, PyObject *args)
{
	PyObject* dict = NULL;

#ifdef __linux__	
	TCHAR comment[DISK_FILE_COMMENT_SIZE] = "";
	TCHAR name[DISK_FILE_CHANNAMESIZE] = "";
#else	
	TCHAR comment[DISK_FILE_COMMENT_SIZE] = L"";
	TCHAR name[DISK_FILE_CHANNAMESIZE] = L"";
#endif	

	if (!PyArg_ParseTuple(args, "Oss", &dict, &comment, &name))
	{
		return NULL;
	}
	if (!PyDict_Check(dict))
	{
		PyErr_SetString(PyExc_TypeError, "Expecting a dictionary");
		return NULL;
	}
#ifdef PYTHON3
	if (!PyArg_ValidateKeywordArguments(dict))
	{
		// error of some kind
	}
#endif
	CSSIGSTRUCT sigStruct;
	sigStruct.u32Size = sizeof(CSSIGSTRUCT);

	// set reasonable defaults
	sigStruct.i64SampleRate = 10000000;
	sigStruct.i64RecordStart = 0;
	sigStruct.i64RecordLength = 8160;
	sigStruct.u32RecordCount = 1;
	sigStruct.u32SampleBits = 14;
	sigStruct.u32SampleSize = 2;
	sigStruct.i32SampleOffset = -4;
	sigStruct.i32SampleRes = -32768;
	sigStruct.u32Channel = 1;
	sigStruct.u32InputRange = 2000;
	sigStruct.i32DcOffset = 0;
	sigStruct.TimeStamp.u16Hour = 0;
	sigStruct.TimeStamp.u16Minute = 0;
	sigStruct.TimeStamp.u16Second = 0;
	sigStruct.TimeStamp.u16Point1Second = 0;
	

	PyObject* p = PyDict_GetItemString(dict, "SampleRate");
	if (p)
	{
		sigStruct.i64SampleRate = PyLong_AsLongLong(p);
	}
	p = PyDict_GetItemString(dict, "RecordStart");
	if (p)
	{
		sigStruct.i64RecordStart = PyLong_AsLongLong(p);
	}
	p = PyDict_GetItemString(dict, "RecordLength");
	if (p)
	{
		sigStruct.i64RecordLength = PyLong_AsLongLong(p);
	}
	p = PyDict_GetItemString(dict, "RecordCount");
	if (p)
	{
		sigStruct.u32RecordCount = PyLong_AsUnsignedLong(p);
	}
	p = PyDict_GetItemString(dict, "SampleBits");
	if (p)
	{
		sigStruct.u32SampleBits = PyLong_AsUnsignedLong(p);
	}
	p = PyDict_GetItemString(dict, "SampleSize");
	if (p)
	{
		sigStruct.u32SampleSize = PyLong_AsUnsignedLong(p);
	}
	p = PyDict_GetItemString(dict, "SampleOffset");
	if (p)
	{
		sigStruct.i32SampleOffset = PyLong_AsLong(p);
	}
	p = PyDict_GetItemString(dict, "SampleRes");
	if (p)
	{
		sigStruct.i32SampleRes = PyLong_AsLong(p);
	}
	p = PyDict_GetItemString(dict, "Channel");
	if (p)
	{
		sigStruct.u32Channel = PyLong_AsUnsignedLong(p);
	}
	p = PyDict_GetItemString(dict, "InputRange");
	if (p)
	{
		sigStruct.u32InputRange = PyLong_AsUnsignedLong(p);
	}
	p = PyDict_GetItemString(dict, "DcOffset");
	if (p)
	{
		sigStruct.i32DcOffset = PyLong_AsLong(p);
	}

	p = PyDict_GetItemString(dict, "TimeStamp");
	if (p)
	{
		if (!PyDict_Check(p))
		{
			PyErr_SetString(PyExc_TypeError, "Expecting a dictionary");
			return NULL;
		}
#ifdef PYTHON3
		if (!PyArg_ValidateKeywordArguments(p))
		{
			// error of some kind
		}
#endif
		PyObject* pobject = PyDict_GetItemString(p, "Hour");
		if (pobject)
		{
			sigStruct.TimeStamp.u16Hour = (unsigned short)PyLong_AsUnsignedLong(pobject);
		}
		pobject = PyDict_GetItemString(p, "Minute");
		if (pobject)
		{
			sigStruct.TimeStamp.u16Minute = (unsigned short)PyLong_AsUnsignedLong(pobject);
		}
		pobject = PyDict_GetItemString(p, "Second");
		if (pobject)
		{  
			sigStruct.TimeStamp.u16Second = (unsigned short)PyLong_AsUnsignedLong(pobject);
		}
		pobject = PyDict_GetItemString(p, "Point1Second");
		if (pobject)
		{
			sigStruct.TimeStamp.u16Point1Second = (unsigned short)PyLong_AsUnsignedLong(pobject);
		}
	}

	CSDISKFILEHEADER header;
	int sts = CsConvertToSigHeader(&header, &sigStruct, comment, name);
	if (sts < 1)
	{
		PyErr_SetString(PyExc_TypeError, "Cannot convert to Sig file header");
		return NULL;	
	}
	//check for error on sts
	int64 dims = 512;
	PyObject* arr = PyArray_SimpleNew(1, (npy_intp*)&dims, NPY_UBYTE);
	PyArray_ENABLEFLAGS((PyArrayObject*)arr, NPY_ARRAY_OWNDATA);
	memcpy(PyArray_DATA((PyArrayObject*)arr), &header, sizeof(header));

	return arr;
}


static PyObject *
PyGage_ConvertFromSigHeader(PyObject *self, PyObject *args)
{
	int sts = 1;
	PyObject* buffer = NULL;

	if (!PyArg_ParseTuple(args, "Y", &buffer)) // y* could by Y
	{
		return NULL;
	}

	// check if channel is within bounds (or let the call to the drivers do it)
	CSDISKFILEHEADER header;

	memcpy(&header.cData, (void*)(PyByteArray_AsString(buffer)), 512);

	CSSIGSTRUCT sigStruct;
	sigStruct.u32Size = sizeof(CSSIGSTRUCT);
	sts = CsConvertFromSigHeader(&header, &sigStruct, NULL, NULL);


	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}

	PyObject *d = PyDict_New();

	PyObject *p = Py_BuildValue("L", sigStruct.i64SampleRate);
	PyDict_SetItemString(d, "SampleRate", p);
	Py_XDECREF(p);

	p = Py_BuildValue("L", sigStruct.i64RecordStart);
	PyDict_SetItemString(d, "RecordStart", p);
	Py_XDECREF(p);

	p = Py_BuildValue("L", sigStruct.i64RecordLength);
	PyDict_SetItemString(d, "RecordLength", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sigStruct.u32RecordCount);
	PyDict_SetItemString(d, "RecordCount", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sigStruct.u32SampleBits);
	PyDict_SetItemString(d, "SampleBits", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sigStruct.u32SampleSize);
	PyDict_SetItemString(d, "SampleSize", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", sigStruct.i32SampleOffset);
	PyDict_SetItemString(d, "SampleOffset", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", sigStruct.i32SampleRes);
	PyDict_SetItemString(d, "SampleRes", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sigStruct.u32Channel);
	PyDict_SetItemString(d, "Channel", p);
	Py_XDECREF(p);

	p = Py_BuildValue("I", sigStruct.u32InputRange);
	PyDict_SetItemString(d, "InputRange", p);
	Py_XDECREF(p);

	p = Py_BuildValue("i", sigStruct.i32DcOffset);
	PyDict_SetItemString(d, "DcOffset", p);
	Py_XDECREF(p);

	p = Py_BuildValue("H", sigStruct.TimeStamp.u16Hour);
	PyDict_SetItemString(d, "Hour", p);
	Py_XDECREF(p);

	p = Py_BuildValue("H", sigStruct.TimeStamp.u16Minute);
	PyDict_SetItemString(d, "Minute", p);
	Py_XDECREF(p);

	p = Py_BuildValue("H", sigStruct.TimeStamp.u16Second);
	PyDict_SetItemString(d, "Second", p);
	Py_XDECREF(p);

	p = Py_BuildValue("H", sigStruct.TimeStamp.u16Point1Second);
	PyDict_SetItemString(d, "Millisecond", p);
	Py_XDECREF(p);




/*
	PyDict_SetItemString(d, "SampleRate", Py_BuildValue("L", sigStruct.i64SampleRate));
	PyDict_SetItemString(d, "RecordStart", Py_BuildValue("L", sigStruct.i64RecordStart));
	PyDict_SetItemString(d, "RecordLength", Py_BuildValue("L", sigStruct.i64RecordLength));
	PyDict_SetItemString(d, "RecordCount", Py_BuildValue("I", sigStruct.u32RecordCount));
	PyDict_SetItemString(d, "SampleBits", Py_BuildValue("I", sigStruct.u32SampleBits));
	PyDict_SetItemString(d, "SampleSize", Py_BuildValue("I", sigStruct.u32SampleSize));
	PyDict_SetItemString(d, "SampleOffset", Py_BuildValue("i", sigStruct.i32SampleOffset));
	PyDict_SetItemString(d, "SampleRes", Py_BuildValue("i", sigStruct.i32SampleRes));
	PyDict_SetItemString(d, "Channel", Py_BuildValue("I", sigStruct.u32Channel));
	PyDict_SetItemString(d, "InputRange", Py_BuildValue("I", sigStruct.u32InputRange));
	PyDict_SetItemString(d, "DcOffset", Py_BuildValue("i", sigStruct.i32DcOffset));
	PyDict_SetItemString(d, "Hour", Py_BuildValue("H", sigStruct.TimeStamp.u16Hour));
	PyDict_SetItemString(d, "Minute", Py_BuildValue("H", sigStruct.TimeStamp.u16Minute));
	PyDict_SetItemString(d, "Second", Py_BuildValue("H", sigStruct.TimeStamp.u16Second));
	PyDict_SetItemString(d, "Millisecond", Py_BuildValue("H", sigStruct.TimeStamp.u16Point1Second));
*/
	return d;
}    


static PyObject *
PyGage_GetErrorString(PyObject *self, PyObject *args)
{
	int sts = 1;
	int errorCode;

	if (!PyArg_ParseTuple(args, "i", &errorCode))
	{
		return NULL;
	}
	TCHAR szErrorString[255];
	sts = CsGetErrorString(errorCode, szErrorString, 255);
	if (CS_FAILED(sts))
	{
		return PyLong_FromLong(sts);
	}
	else
	{
#ifdef _WIN32
		return Py_BuildValue("u", szErrorString, 50);
#else
		return Py_BuildValue("s", szErrorString, 50); // this way works in Linux
#endif
	}
}


static PyMethodDef GageMethods[] = {
//    ...
    {"Initialize",  PyGage_Initialize, METH_VARARGS,
     "Initialize a CompuScope system."},
	{"GetSystem", PyGage_GetSystem, METH_VARARGS, 
	"Get a handle to a CompuScope system"},
	{"FreeSystem", PyGage_FreeSystem, METH_VARARGS, 
	"Frees the handle associated with a CompuScope system"},
	{"GetSystemInfo", PyGage_GetSystemInfo, METH_VARARGS, 
	"Returns information about a CompuScope system"},
	{"GetBoardsInfo", PyGage_GetBoardsInfo, METH_VARARGS,
	"Returns information about the boards in a CompuScope system"},
	{"GetAcquisitionConfig", PyGage_GetAcquisitionConfig, METH_VARARGS, 
	"Returns capture parameters from a CompuScope system"},
	{"SetAcquisitionConfig", PyGage_SetAcquisitionConfig, METH_VARARGS,
	"Sets the capture parameters for a CompuScope system"},
	{"GetChannelConfig", PyGage_GetChannelConfig, METH_VARARGS,
	"Returns channel parameters from a CompuScope system"},
	{"SetChannelConfig", PyGage_SetChannelConfig, METH_VARARGS,
	"Sets the channel parameters for a CompuScope system"},
	{"GetTriggerConfig", PyGage_GetTriggerConfig, METH_VARARGS,
	"Returns trigger parameters from a CompuScope system"},
	{"SetTriggerConfig", PyGage_SetTriggerConfig, METH_VARARGS,
	"Sets the trigger parameters for a CompuScope system"},
	{"Commit", PyGage_Commit, METH_VARARGS,
	"Sends the capture parameters to the driver and hardware"},
	{"StartCapture", PyGage_StartCapture, METH_VARARGS,
	"Starts a capture from a CompuScope system"},
	{"ForceCapture", PyGage_ForceCapture, METH_VARARGS,
	"Forces a capture in a CompuScope system"},
	{"AbortCapture", PyGage_AbortCapture, METH_VARARGS,
	"Aborts a capture in a CompuScope system" },
	{"GetStatus", PyGage_GetStatus, METH_VARARGS,
	"Returns the capture status from a CompuScope system"},
	{"TransferData", PyGage_TransferData, METH_VARARGS,
	"Transfers data from a CompuScope system"},
	{"GetStreamingBuffer", PyGage_GetStreamingBuffer, METH_VARARGS,
	"Returns a buffer suitable for streaming"},
	{ "FreeStreamingBuffer", PyGage_FreeStreamingBuffer, METH_VARARGS,
	"Frees a streaming buffer"},
	{"TransferStreamingData", PyGage_TransferStreamingData, METH_VARARGS,
	"Transfer streaming data to a buffer"},
	{"GetStreamingTransferStatus", PyGage_GetStreamingTransferStatus, METH_VARARGS,
	"Get status of streaming transfer"},
	{"ConvertToSigHeader", PyGage_ConvertToSigHeader, METH_VARARGS,
	"Convert structure to a SIG file header"},
	{"ConvertFromSigHeader", PyGage_ConvertFromSigHeader, METH_VARARGS,
	"Convert SIG file header to a structure"},
	{"GetErrorString", PyGage_GetErrorString, METH_VARARGS,
	"Convert error code to a string"},
	{"GetSystemCaps", PyGage_GetSystemCaps, METH_VARARGS,
	"Returns various system capabilities"},
	{"SetDataPackingMode", PyGage_SetDataPackingMode, METH_VARARGS,
	"Set mode for packing streameing data"},
	{"SetStreamingCaptureMode", PyGage_SetStreamingCaptureMode, METH_VARARGS,
	"Set streaming mode on or off"},
	{"SetFftConfig", PyGage_SetFftConfig, METH_VARARGS,
	"Set various expert FFT parameters"},
	{"SetFftWindowConfig", PyGage_SetFftConfig, METH_VARARGS,
	"Set expert FFT windowing coefficients"},
	{"SetMulRecAverageCount", PyGage_SetMulRecAverageCount, METH_VARARGS,
	"Set number of averages for expert multiple record averaging"},
	{"SetIdentifyLed", PyGage_SetIdentifyLed, METH_VARARGS,
	"Flash an identifying LED on a board"},
	{"SetOneSampleResolution", PyGage_SetOneSampleResolution, METH_VARARGS,
	"Change depth resolution to one sample"},
	{"GetExtendedBoardOptions", PyGage_GetExtendedBoardOptions, METH_VARARGS,
	"Return extended board options"},
	{"GetStreamTotalDataSizeInBytes", PyGage_GetStreamTotalDataSizeInBytes, METH_VARARGS,
	"Return total stsreaming data size in bytes"},
	{"GetStreamSegmentDataSizeInSamples", PyGage_GetStreamSegmentDataSizeInSamples, METH_VARARGS,
	"Return size of streaming segment in samples"},
	{"GetTimeStampFrequency", PyGage_GetTimeStampFrequency, METH_VARARGS,
	"Return frequency of the time stamp clock"},
	{"GetDataPackingMode", PyGage_GetDataPackingMode, METH_VARARGS,
	"Return type of packing for streaming data"},
	{"GetStreamingCaptureMode", PyGage_GetStreamingCaptureMode, METH_VARARGS,
	"Return capture mode, memory or streaming"},
	{"GetDataFormatInfo", PyGage_GetDataFormatInfo, METH_VARARGS,
	"Return format of data in streaming mode"},
	{"GetTriggeredInfo", PyGage_GetTriggeredInfo, METH_VARARGS,
	"Return infomration about what triggered the capture" },
	{"GetFftConfig", PyGage_GetFftConfig, METH_VARARGS,
	"Return fft firmware configuration"},
	{"GetSegmentTailSizeInBytes", PyGage_GetSegmentTailSizeInBytes, METH_VARARGS,
	"Return size of the tail data in bytes"},
	{"GetMulRecAverageCount", PyGage_GetMulRecAverageCount, METH_VARARGS,
	"Return number of averages set for mulrec averaging"},

//    ...
    {NULL, NULL, 0, NULL}        /* Sentinel */
};


#if PY_MAJOR_VERSION >= 3

static struct PyModuleDef gagemodule = {
   PyModuleDef_HEAD_INIT,
   "PyGage",   /* name of module */
   NULL, /* module documentation, may be NULL */
   -1,       /* size of per-interpreter state of the module,
                or -1 if the module keeps state in global variables. */
   GageMethods
};

#endif // PY_MAJOR_VERSION >= 3


// This next part is needed because import_array() returns NUMPY_IMPORT_ARRAY_RETVAL (NULL) in Python 3
// and nothing in Python 2. Calling import_array() directly give an error in Python 2
#if PY_MAJOR_VERSION >= 3
int
#else
void
#endif
init_numpy()
{
	import_array()
#if PY_MAJOR_VERSION >= 3
	return 1;
#endif
}

static PyObject * 
moduleinit(void)
{
	PyObject *mod;
#if PY_MAJOR_VERSION >= 3
	mod = PyModule_Create(&gagemodule);
#else
//	mod = Py_InitModule3("PyGage", GageMethods, NULL);
#if _WIN64	
	mod = Py_InitModule3("PyGage2_64", GageMethods, NULL);
#else
	mod = Py_InitModule3("PyGage2_32", GageMethods, NULL);
#endif
#endif
	if (mod == NULL)
		return NULL;
/*	PyModule_AddIntMacro(mod, MAGIC);*/
  /* Load `numpy` functionality. */
	init_numpy();

	return mod;
}


#if PY_MAJOR_VERSION >= 3
PyMODINIT_FUNC PyInit_PyGage(void)
{
	return moduleinit();
}
#else
PyMODINIT_FUNC initPyGage(void)
{
	moduleinit();
}
#endif
