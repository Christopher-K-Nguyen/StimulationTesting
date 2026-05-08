# __init__.py - Module setup for PyPlexStim
#
# (c) 2016 Plexon, Inc., Dallas, Texas
# www.plexon.com - support@plexon.com
# 
# This software is provided as-is, without any warranty.
# You are free to modify or share this file, provided that the above
# copyright notice is kept intact.

# __init__.py serves three purposes here:
#   1) Lets Python know that the .py file(s) in this directory are importable
#      modules.
#   2) Sets up classes and variable names for cleaner access in programs using the
#      module. For example, without importing PyPlexStim in __init__.py, you would 
#      have to import PyPlexStim like this:
#           from pyplexstim.pyplexstimlib import PyPlexStim
#      instead of like this:
#           from pyplexstim import PyPlexStim
#      It's a minor convenience, but improves readability.
#   3) Explicitly states which classes and functions in PyPlexStim are meant to be
#      public parts of the API.

# 8/18/2022 - Updated to Python 3
#           - Updated to .dll version 2.3.19, which fixes intermittent initialization errors on some systems

# 11/30/2016 - Fixed ps_set_digital_output_mode; updated to 1.1.0
#            - Fixed ps_get_pattern_type
#            - Fixed ps_get_monitor_channel
#            - Fixed ps_get_rate
#            - Fixed ps_get_repetitions
#            - Fixed ps_get_trigger_mode
#            - Fixed ps_get_stim_pattern_duration
#            - Fixed ps_is_waveform_balanced
#            - Fixed ps_get_period
#            - Fixed ps_abort_all
#            - Fixed ps_channel_stim_started

__author__ = 'Chris Heydrick (chris@plexon.com)'
__version__ = '1.2.0'
__dll_version__ = '2.3.19.0'

from .pyplexstimlib import PyPlexStim

from .pyplexstimlib import PS_PATTERN_RECT, PS_PATTERN_ARB
from .pyplexstimlib import PS_TRIG_SOFT, PS_TRIG_PULSE, PS_TRIG_LEVEL
from .pyplexstimlib import PS_VMON_SCALING_0_25, PS_VMON_SCALING_2_5, PS_VMON_SCALING_25, PS_VMON_SCALING_250
from .pyplexstimlib import PS_DIGITAL_OUTPUT_HIGH, PS_DIGITAL_OUTPUT_LOW
from .pyplexstimlib import PS_INVALID_ARGUMENTS, PS_OK, PS_DEVICE_ERROR, PS_NO_PLEXON_STIM, PS_CRC_ERROR, PS_WRONG_TRIG_MODE, PS_MISMATCH_NPOINTS, PS_PATTERN_NOT_READY, PS_INVALID_FILE_NAME, PS_FILE_NOT_FOUND, PS_FILE_OPEN_FAILED, PS_FILE_TOO_MANY_LINES, PS_FILE_INVALID_CONTENT, PS_FILE_TOO_FEW_LINES, PS_FILE_MISMATCHED_PAIRS, PS_NULL_POINTER


