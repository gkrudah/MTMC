"""

@author: ha

wavelet transform
"""

import numpy as np

import pywt


def wvtransform_cA(arr_data, wavelet_type):
	cA, (cH, cV, cD) = pywt.dwt2(data=arr_data, wavelet=wavelet_type)
	return cA


def wvtransform_cH(arr_data, wavelet_type):
	cA, (cH, cV, cD) = pywt.dwt2(data=arr_data, wavelet=wavelet_type)
	return cH


def wvtransform_cV(arr_data, wavelet_type):
	cA, (cH, cV, cD) = pywt.dwt2(data=arr_data, wavelet=wavelet_type)
	return cV


def wvtransform_cD(arr_data, wavelet_type):
	cA, (cH, cV, cD) = pywt.dwt2(data=arr_data, wavelet=wavelet_type)
	return cD


def main():
	return


if __name__ == "__main__":
	main()
