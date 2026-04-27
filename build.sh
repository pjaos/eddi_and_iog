#!/bin/bash
if [ ! -d "src/eddi_and_iog/assets" ]; then
    mkdir src/eddi_and_iog/assets
fi


set -e # Stop on code check or test errors
./check_code.sh # Run some checks on the code before building it
./run_tests.sh
cp pyproject.toml src/eddi_and_iog/assets
# Use poetry command to build python wheel
poetry --output=linux --clean -vvv build
