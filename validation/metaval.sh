#!/bin/bash
eval "$(conda shell.bash hook)"
conda activate metamaterial

cd ./cython
python ./cython_setup.py build_ext --inplace
