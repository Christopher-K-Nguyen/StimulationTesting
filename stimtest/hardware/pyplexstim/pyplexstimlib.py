# pyplexstimlib.py - Classes and functions for accessing functions
# in PlexStim.dll
#
# (c) 2015 Plexon, Inc., Dallas, Texas
# www.plexon.com - support@plexon.com
# 
# This software is provided as-is, without any warranty.
# You are free to modify or share this file, provided that the above
# copyright notice is kept intact.

from ctypes import *
import os
import platform

# Stimulator pattern types
PS_PATTERN_RECT = 0
PS_PATTERN_ARB = 1

# Stimulator trigger modes
PS_TRIG_SOFT = 0
PS_TRIG_PULSE = 1
PS_TRIG_LEVEL = 2

# Stimulator voltage monitor scaling
PS_VMON_SCALING_0_25 = 0
PS_VMON_SCALING_2_5 = 1
PS_VMON_SCALING_25 = 2
PS_VMON_SCALING_250 = 3

# Stimulator digital output modes
PS_DIGITAL_OUTPUT_HIGH = 0
PS_DIGITAL_OUTPUT_LOW = 1

# Stimulator status
PS_INVALID_ARGUMENTS = -1
PS_OK = 0
PS_DEVICE_ERROR = 1
PS_NO_PLEXON_STIM = 2
PS_CRC_ERROR = 3
PS_WRONG_TRIG_MODE = 4
PS_MISMATCH_NPOINTS = 5
PS_PATTERN_NOT_READY = 6
PS_INVALID_FILE_NAME = 7
PS_FILE_NOT_FOUND = 8
PS_FILE_OPEN_FAILED = 9
PS_FILE_TOO_MANY_LINES = 10
PS_FILE_INVALID_CONTENT = 11
PS_FILE_TOO_FEW_LINES = 12
PS_FILE_MISMATCHED_PAIRS = 13
PS_NULL_POINTER = 14

class PS_RectPattern(Structure):
    _fields_ = [("Amp1",c_int),
                ("Amp2",c_int),
                ("W1",c_int),
                ("W2",c_int),
                ("Delay",c_int)]

class PyPlexStim:
    def __init__(self, plexstim_dll_path = 'bin'):
        self.platform = platform.architecture()[0]
        self.plexstim_dll_path = os.path.abspath(plexstim_dll_path)
        if self.platform == '32bit':
            self.plexstim_dll_file = os.path.join(self.plexstim_dll_path, 'PlexStim.dll')
        else:
            self.plexstim_dll_file = os.path.join(self.plexstim_dll_path, 'PlexStim64.dll')
        self.dll_init = 0
        try:
            self.plexstim_dll = CDLL(self.plexstim_dll_file)
        except OSError:
            # LOCAL FIX: upstream catches ``WindowsError`` which is undefined
            # on non-Windows platforms (NameError). On Windows in Py3,
            # ``WindowsError`` is an alias for ``OSError``, so the broader
            # catch covers both cases. ``dll_init`` stays 0 — the consumer
            # in plexon.py checks that flag and raises a structured error.
            import sys
            print("Error: Can't load the PlexStim .dll at: " + self.plexstim_dll_file,
                  file=sys.stderr)
        else:
            self.dll_init = 1
            
    def ps_init_all_stim(self):
        """ 
        Initializes all stimulators on the computer (max 4)
        
        Usage:
            res = ps_init_all_stim()
            
        Returns:
            res:
                0 - OK
                1 - error initializing devices
                2 - no stimulators found
        """
        self.result = getattr(self.plexstim_dll, "?PS_InitAllStim@@YAHXZ")()
        
        return self.result
        
    def ps_close_stim(self, stim_n):
        """
        Closes stimulator stim_n. Any stimulation in progress
        is aborted.
        
        Usage:
            res = ps_close_stim(stim_n)
        
        Args:
            stim_n - stimulator number to close (starts from 1)
        Returns:
            -1 - invalid argument
            0 - OK
            1 - device error
        """
        self.stim_n = c_int(stim_n)
        
        self.result = getattr(self.plexstim_dll, "?PS_CloseStim@@YAHH@Z")(self.stim_n)
        
        return self.result
        
    def ps_close_all_stim(self):
        """
        Finalizes work with all available stimulators. Any stimulation in progress
        is aborted.
        
        Usage:
            res = ps_close_all_stim()
        
        Returns:
            0 - OK
            1 - device error
        """
        self.result = getattr(self.plexstim_dll, "?PS_CloseAllStim@@YAHXZ")()
        
        return self.result
        
    def ps_load_channel(self, stim_n, ch_n):
        """
        Loads parameters of ch_n on stimulator stim_n to the stimulator hardware.
        
        Usage:
            res = ps_load_channel(stim_n, ch_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number
        Returns:
            res:
                -1 - invalid argument(s)
                0 - OK
                1 - device error
                3 - CRC error
                6 - stimulator pattern is not ready (when loading arbitrary pattern)
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        
        self.result = getattr(self.plexstim_dll, "?PS_LoadChannel@@YAHHH@Z")(self.stim_n, self.ch_n)
        
        return self.result
        
    def ps_load_all_channels(self, stim_n):
        """
        Loads parameters of all channels on stimulator stim_n to the stimulator hardware.
        
        Usage:
            res = ps_load_all_channels(stim_n)
        
        Args:
            stim_n - stimulator number (starts from 1)

        Returns:
            res:
                -1 - invalid argument(s)
                0 - OK
                1 - device error
                3 - CRC error
                6 - stimulator pattern is not ready (when loading arbitrary pattern)
        """
        self.stim_n = c_int(stim_n)
        
        self.result = getattr(self.plexstim_dll, "?PS_LoadAllChannels@@YAHH@Z")(self.stim_n)
        
        return self.result
        
    def ps_start_stim_all_channels(self, stim_n):
        """
        Starts stimulation on all channels on stimulator stim_n.
        
        Usage:
            res = ps_start_stim_all_channels(stim_n)
        
        Args:
            stim_n - stimulator number (starts from 1)

        Returns:
            res:
                -1 - invalid argument(s)
                0 - OK
                1 - device error
                4 - wrong trigger mode (trigger mode isn't set to PS_TRIG_SOFT)
        """
        self.stim_n = c_int(stim_n)
        
        self.result = getattr(self.plexstim_dll, "?PS_StartStimAllChannels@@YAHH@Z")(self.stim_n)
        
        return self.result
        
    def ps_stop_stim_all_channels(self, stim_n):
        """
        Stops stimulation on all channels on stimulator stim_n.
        
        Usage:
            res = ps_stop_stim_all_channels(stim_n)
        
        Args:
            stim_n - stimulator number (starts from 1)

        Returns:
            res:
                -1 - invalid argument(s)
                0 - OK
                1 - device error
                4 - wrong trigger mode (trigger mode isn't set to PS_TRIG_SOFT)
        """
        self.stim_n = c_int(stim_n)
        
        self.result = getattr(self.plexstim_dll, "?PS_StopStimAllChannels@@YAHH@Z")(self.stim_n)
        
        return self.result
        
    def ps_start_stim_channel(self, stim_n, ch_n):
        """
        Starts stimulation on channel ch_n on stimulator stim_n.
        
        Usage:
            res = ps_start_stim_channel(stim_n, ch_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number

        Returns:
            res:
                -1 - invalid argument(s)
                0 - OK
                1 - device error
                4 - wrong trigger mode (trigger mode isn't set to PS_TRIG_SOFT)
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        
        self.result = getattr(self.plexstim_dll, "?PS_StartStimChannel@@YAHHH@Z")(self.stim_n, self.ch_n)
        
        return self.result
        
    def ps_stop_stim_channel(self, stim_n, ch_n):
        """
        Stops stimulation on channel ch_n on stimulator stim_n.
        
        Usage:
            res = ps_stop_stim_channel(stim_n, ch_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number

        Returns:
            res:
                -1 - invalid argument(s)
                0 - OK
                1 - device error
                4 - wrong trigger mode (trigger mode isn't set to PS_TRIG_SOFT)
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        
        self.result = getattr(self.plexstim_dll, "?PS_StopStimChannel@@YAHHH@Z")(self.stim_n, self.ch_n)
        
        return self.result
        
    def ps_abort(self, stim_n):
        """
        Causes all stimulation to cease immediately even if there is a pulse or arbitrary
        waveform in progress. This is in contrast to stopping stimulation by calling
        ps_stop_stim_channel or ps_stop_stim_all_channels.
        
        Usage:
            res = ps_abort(stim_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
        Returns:
            0 - OK
            1 - device error
        """
        self.stim_n = c_int(stim_n)
        
        self.result = getattr(self.plexstim_dll, "?PS_Abort@@YAHH@Z")(self.stim_n)
        
        return self.result
        
    def ps_abort_all(self):
        """
        Causes all stimulation to cease immediately even if there is a pulse or arbitrary
        waveform in progress. This is in contrast to stopping stimulation by calling
        ps_stop_stim_channel or ps_stop_stim_all_channels.
        
        Usage:
            res = ps_abort_all()
        
        Args:
            stim_n - stimulator number (starts from 1)
        Returns:
            res:
                0 - OK
                1 - device error
        """
        self.result = getattr(self.plexstim_dll, "?PS_AbortAll@@YAHXZ")()
        
        return self.result
        
    def ps_set_pattern_type(self, stim_n, ch_n, pattern_type):
        """
        Configures ch_n on stimulator stim_n to use either a rectangular pulse pattern,
        or a preloaded arbitrary waveform pattern.
        
        Usage:
            res = ps_set_pattern_type(stim_n, ch_n, pattern_type)
            
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - stimulator channel number
            pattern_type - one of two options:
                PS_PATTERN_RECT - rectangular pulse pattern
                PS_PATTERN_ARB - arbitrary waveform pattern
                
        Returns:
            res:
                -1 - invalid argument(s)
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.pattern_type = c_int(pattern_type)
        
        self.result = getattr(self.plexstim_dll, "?PS_SetPatternType@@YAHHHW4PS_PATTERN_TYPE@@@Z")(self.stim_n, self.ch_n, self.pattern_type)
        
        return self.result
        
    def ps_get_pattern_type(self, stim_n, ch_n):
        """
        Gets pattern type set for channel ch_n on stimulator stim_n.
        
        Usage:
            pattern_type, res = ps_get_pattern_type(stim_n, ch_n)
            
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - stimulator channel number

        Returns:
            pattern_type - one of two options:
                PS_PATTERN_RECT - rectangular pulse pattern
                PS_PATTERN_ARB - arbitrary waveform pattern
            res:
                -1 - invalid argument(s)
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.pattern_type = c_int(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetPatternType@@YAHHHPAW4PS_PATTERN_TYPE@@@Z")(self.stim_n, self.ch_n, byref(self.pattern_type))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetPatternType@@YAHHHPEAW4PS_PATTERN_TYPE@@@Z")(self.stim_n, self.ch_n, byref(self.pattern_type))
        
        return self.pattern_type.value, self.result
        
    def ps_set_rect_param(self, stim_n, ch_n, param):
        """
        Sets parameters of a rectangular pulse on channel ch_n of stimulator stim_n.
        
        Usage:
            param = (100, -100, 25, 25, 25)
            res = ps_set_rect_param(stim_n, ch_n, param)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number
            param - tuple with five values:
                param[0] - first phase amplitude
                param[1] - second phase amplitude
                param[2] - first phase width
                param[3] - second phase width
                param[4] - inter-phase delay
                
        Returns:
            res:
                -1 - invalid argument(s)
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        
        #param MUST be a tuple with five values
        if type(param) != tuple:
            return -1
        
        if len(param) != 5:
            return -1
        
        self.param = PS_RectPattern(param[0], param[1], param[2], param[3], param[4])
        
        self.result = getattr(self.plexstim_dll, "?PS_SetRectParam@@YAHHHUPS_RectPattern@@@Z")(self.stim_n, self.ch_n, self.param)
        
        return self.result
        
    def ps_get_rect_param(self, stim_n, ch_n):
        """
        Gets parameters of the rectangular pulse on channel ch_n of stimulator stim_n.
        
        Usage:
            param, res = ps_get_rect_param(stim_n, ch_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number
 
        Returns:
            param - tuple with five values:
                param[0] - first phase amplitude
                param[1] - second phase amplitude
                param[2] - first phase width
                param[3] - second phase width
                param[4] - inter-phase delay
            res:
                -1 - invalid argument(s)
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.param = PS_RectPattern()
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetRectParam@@YAHHHPAUPS_RectPattern@@@Z")(self.stim_n, self.ch_n, byref(self.param))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetRectParam@@YAHHHPEAUPS_RectPattern@@@Z")(self.stim_n, self.ch_n, byref(self.param))

        # LOCAL FIX (deviates from upstream Plexon 1.2.0): the shipped wrapper
        # returns Amp1 five times AND calls .value on a Structure field — both
        # bugs. Structure fields of c_int unwrap to Python int on access, so
        # .value raises AttributeError. Return all five fields in the order
        # documented in the docstring.
        return (self.param.Amp1, self.param.Amp2, self.param.W1, self.param.W2, self.param.Delay), self.result
        
    def ps_load_arb_pattern(self, stim_n, ch_n, pattern_path):
        """
        Load an arbitrary waveform pattern from a .pat file into channel ch_n on stimulator stim_n.
        
        Usage:
            res = ps_load_arb_pattern(stim_n, ch_n, pattern_path)
            
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - stimulator channel number
            pattern_path - string path (512 characters max) of .pat file
        
        Returns:
            res:
                -1 - invalid argument(s)
                0 - pattern loaded successfully
                7 - length of file name exceeds 512 characters
                8 - file doesn't exist
                9 - file is opened by another process
                10 - number of points in pattern exceeds 1000 points
                11 - file contains invalid value(s)
                12 - file contains too few lines (min is 3)
                13 - file contains a mismatched amplitude duration pair
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.pattern_path = c_char_p(pattern_path.encode('utf-8'))
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_LoadArbPattern@@YAHHHPAD@Z")(self.stim_n, self.ch_n, self.pattern_path)
        else:
            self.result = getattr(self.plexstim_dll, "?PS_LoadArbPattern@@YAHHHPEAD@Z")(self.stim_n, self.ch_n, self.pattern_path)
        
        return self.result
        
    def ps_get_n_points_arb_pattern(self, stim_n, ch_n):
        """
        Gets number of points in arbitrary pattern loaded on channel ch_n of stimulator stim_n.
        
        Usage:
            n_points, res = ps_get_n_points_arb_pattern(stim_n, ch_n)
            
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - stimulator channel number

        Returns:
            n_points - number of points in pattern
            res:
                -1 - invalid argument(s)
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.n_points = c_int(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetNPointsArbPattern@@YAHHHPAH@Z")(self.stim_n, self.ch_n, byref(self.n_points))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetNPointsArbPattern@@YAHHHPEAH@Z")(self.stim_n, self.ch_n, byref(self.n_points))
        
        return self.n_points.value, self.result
        
    def ps_get_arb_pattern_points(self, stim_n, ch_n, n_points):
        """
        Gets X and Y coordinates of a graphical representation of the arbitrary waveform
        pattern loaded into the selected stimulator and channel.
        
        Usage:
            coords, res = ps_get_arb_pattern_points(stim_n, ch_n, n_points)
        
        Args:
            stim_n - stimulator number to finalize (starts from 1)
            ch_n - channel number to check if stimulation has started (starts from 1)
            n_points - number of points in arbitrary waveform pattern (see ps_get_npoints_arb_pattern())
        Returns:
            coords - an array of integer coordinates of the points (x1, y1, x2, y2, ...)

            res:
                -1 - invalid argument(s)
                0 - OK
                5 - n_points is not equal to number of points for pattern in this channel
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.n_points = c_int(n_points)
        self.coords = (c_int * (n_points * 2))()
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetArbPatternPoints@@YAHHHHPAH@Z")(self.stim_n, self.ch_n, self.n_points, byref(self.coords))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetArbPatternPoints@@YAHHHHPEAH@Z")(self.stim_n, self.ch_n, self.n_points, byref(self.coords))
        
        return tuple(x for x in self.coords), self.result
        
    def ps_get_arb_pattern_points_x(self, stim_n, ch_n, n_points):
        """
        Gets X coordinates of a graphical representation of the arbitrary waveform
        pattern loaded into the selected stimulator and channel.
        
        Usage:
            x_coords, res = ps_get_arb_pattern_points_x(stim_n, ch_n, n_points)
        
        Args:
            stim_n - stimulator number to finalize (starts from 1)
            ch_n - channel number to check if stimulation has started (starts from 1)
            n_points - number of points in arbitrary waveform pattern (see ps_get_npoints_arb_pattern())
        Returns:
            coords - an array of integer coordinates of the points (x1, x2, x3, ...)

            res:
                -1 - invalid argument(s)
                0 - OK
                5 - n_points is not equal to number of points for pattern in this channel
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.n_points = c_int(n_points)
        self.x_coords = (c_int * n_points)()
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetArbPatternPointsX@@YAHHHHPAH@Z")(self.stim_n, self.ch_n, self.n_points, byref(self.x_coords))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetArbPatternPointsX@@YAHHHHPEAH@Z")(self.stim_n, self.ch_n, self.n_points, byref(self.x_coords))
        
        return tuple(x for x in self.x_coords), self.result
        
    def ps_get_arb_pattern_points_y(self, stim_n, ch_n, n_points):
        """
        Gets Y coordinates of a graphical representation of the arbitrary waveform
        pattern loaded into the selected stimulator and channel.
        
        Usage:
            y_coords, res = ps_get_arb_pattern_points_y(stim_n, ch_n, n_points)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number to check if stimulation has started (starts from 1)
            n_points - number of points in arbitrary waveform pattern (see ps_get_npoints_arb_pattern())
        Returns:
            coords - an array of integer coordinates of the points (y1, y2, y3, ...)

            res:
                -1 - invalid argument(s)
                0 - OK
                5 - n_points is not equal to number of points for pattern in this channel
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.n_points = c_int(n_points)
        self.y_coords = (c_int * n_points)()
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetArbPatternPointsY@@YAHHHHPAH@Z")(self.stim_n, self.ch_n, self.n_points, byref(self.y_coords))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetArbPatternPointsY@@YAHHHHPEAH@Z")(self.stim_n, self.ch_n, self.n_points, byref(self.y_coords))
        
        return tuple(x for x in self.y_coords), self.result
        
    def ps_set_digital_output_mode(self, stim_n, mode):
        """
        Sets the digital output to low or high during the inter-pulse interval. Each
        stimulator channel has a dedicated digital output that indicates when stimulation
        is occurring on that channel. The digital output is always high during the pulse
        or arbitrary waveform output, but the user can control the state of the digital output
        during the time in between pulses or arbitrary waveforms.
        
        Usage:
            res = ps_set_digital_output_mode(stim_n, mode)
        
        Args:
            stim_n - stimulator number (starts from 1)
            mode:
                0 - high
                1 - low
                
        Returns:
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.mode = c_int(mode)
        
        self.result = getattr(self.plexstim_dll, "?PS_SetDigitalOutputMode@@YAHHW4PS_DIGITAL_OUTPUT@@@Z")(self.stim_n, self.mode)
        
        return self.result
        
    def ps_get_digital_output_mode(self, stim_n):
        """
        Checks if the digital output is low or high during the inter-pulse interval. Each
        stimulator channel has a dedicated digital output that indicates when stimulation
        is occurring on that channel. The digital output is always high during the pulse
        or arbitrary waveform output, but the user can control the state of the digital output
        during the time in between pulses or arbitrary waveforms.
        
        Usage:
            mode, res = ps_get_digital_output_mode(stim_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
        Returns:
            mode:
                0 - high
                1 - low
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.mode = c_int(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetDigitalOutputMode@@YAHHPAW4PS_DIGITAL_OUTPUT@@@Z")(self.stim_n, byref(self.mode))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetDigitalOutputMode@@YAHHPEAW4PS_DIGITAL_OUTPUT@@@Z")(self.stim_n, byref(self.mode))
        
        return self.mode.value, self.result
        
    def ps_set_monitor_channel(self, stim_n, ch_n):
        """
        Selects one channel for output on the voltage and current monitor.
        
        Usage:
            res = ps_set_monitor_channel(stim_n, mon_ch_n)
            
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - stimulator channel number 
        Returns:
            res:
                -1 - invalid argument(s)
                0 - OK
                1 - device error
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        
        self.result = getattr(self.plexstim_dll, "?PS_SetMonitorChannel@@YAHHH@Z")(self.stim_n, self.ch_n)
        
        return self.result
    
    def ps_get_monitor_channel(self, stim_n):
        """
        Gets channel number being output to the voltage and current monitor.
        
        Usage:
            res = ps_get_monitor_channel(stim_n)
            
        Args:
            stim_n - stimulator number (starts from 1)
        
        Returns:
            res:
                -1 - invalid argument(s)
                0 - OK
                1 - device error
        """
        self.stim_n = c_int(stim_n)
        self.mon_ch_n = c_int(0)

        # LOCAL FIX (deviates from upstream Plexon 1.2.0): the shipped wrapper
        # passes byref(self.ch_n) (a leftover from earlier calls — does not
        # exist on a fresh instance) and then returns self.mon_ch_n.value
        # which is always the 0 we initialized. Real hardware would crash
        # plexon.py's read-back equality check on every set_monitor_channel
        # call. Pass byref(self.mon_ch_n) so the DLL writes into the
        # variable we actually return.
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetMonitorChannel@@YAHHPAH@Z")(self.stim_n, byref(self.mon_ch_n))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetMonitorChannel@@YAHHPEAH@Z")(self.stim_n, byref(self.mon_ch_n))

        return self.mon_ch_n.value, self.result
    
    def ps_set_period(self, stim_n, ch_n, period):
        """
        Sets period (in milliseconds) on channel ch_n on stimulator stim_n.
        
        Usage:
            res = ps_set_period(stim_n, ch_n, period)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number
            period - period value in milliseconds, ranges from .02 to 125,000

        Returns:
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.period = c_double(period)
        
        self.result = getattr(self.plexstim_dll, "?PS_SetPeriod@@YAHHHN@Z")(self.stim_n, self.ch_n, self.period)
        
        return self.result
        
    def ps_get_period(self, stim_n, ch_n):
        """
        Gets period (in milliseconds) on channel ch_n on stimulator stim_n.
        
        Usage:
            period, res = ps_get_period(stim_n, ch_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number

        Returns:
            period - period value in milliseconds, ranges from .02 to 125,000
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.period = c_double(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetPeriod@@YAHHHPAN@Z")(self.stim_n, self.ch_n, byref(self.period))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetPeriod@@YAHHHPEAN@Z")(self.stim_n, self.ch_n, byref(self.period))
        
        return self.period.value, self.result
        
    def ps_set_rate(self, stim_n, ch_n, rate):
        """
        Sets repetition rate (in Hertz) of channel ch_n on stimulator stim_n.
        
        Usage:
            res = ps_set_rate(stim_n, ch_n, rate)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number
            rate - rate in Hertz, ranges from .008 Hz to 50000 Hz

        Returns:
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.rate = c_double(rate)
        
        self.result = getattr(self.plexstim_dll, "?PS_SetRate@@YAHHHN@Z")(self.stim_n, self.ch_n, self.rate)
        
        return self.result
        
    def ps_get_rate(self, stim_n, ch_n):
        """
        Gets repetition rate on channel ch_n of stimulator stim_n.
        
        Usage:
            rate, res = ps_get_rate(stim_n, ch_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number

        Returns:
            rate - rate in Hertz, ranges from .008 Hz to 50000 Hz
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.rate = c_double(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetRate@@YAHHHPAN@Z")(self.stim_n, self.ch_n, byref(self.rate))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetRate@@YAHHHPEAN@Z")(self.stim_n, self.ch_n, byref(self.rate))
        
        return self.rate.value, self.result
        
    def ps_set_repetitions(self, stim_n, ch_n, repetitions):
        """
        Sets number of repetitions on channel ch_n of stimulator stim_n.
        
        Usage:
            res = ps_set_repetitions(stim_n, ch_n, repetitions)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number
            repetitions - number of repetitions, can range from 1 to 32767, set to 0 for infinite repetitions
            
        Returns:
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.repetitions = c_int(repetitions)
        
        self.result = getattr(self.plexstim_dll, "?PS_SetRepetitions@@YAHHHH@Z")(self.stim_n, self.ch_n, self.repetitions)
        
        return self.result
        
    def ps_get_repetitions(self, stim_n, ch_n):
        """
        Gets number of set repetitions on channel ch_n of stimulator stim_n.
        
        Usage:
            repetitions, res = ps_get_repetitions(stim_n, ch_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number

        Returns:
            repetitions - number of repetitions, can range from 1 to 32767, set to 0 for infinite repetitions
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.repetitions = c_int(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetRepetitions@@YAHHHPAH@Z")(self.stim_n, self.ch_n, byref(self.repetitions))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetRepetitions@@YAHHHPEAH@Z")(self.stim_n, self.ch_n, byref(self.repetitions))
        
        return self.repetitions.value, self.result
        
    def ps_set_trigger_mode(self, stim_n, mode):
        """
        Sets trigger mode for stimulator stim_n.
        
        Usage:
            res = ps_set_trigger_mode(stim_n, mode)
        
        Args:
            stim_n - stimulator number (starts from 1)
            mode - one of the three options:
                PS_TRIG_SOFT - start stimulation from software
                PS_TRIG_PULSE - start stimulation when digital input transitions from 0V to 5V
                PS_TRIG_LEVEL - start stimulation when digital input transition from 0V to 5V, but
                    stimulation will idle after protocol completes if digital input is still at 5V

        Returns:
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.mode = c_int(mode)
        
        self.result = getattr(self.plexstim_dll, "?PS_SetTriggerMode@@YAHHW4PS_TRIG_MODE@@@Z")(self.stim_n, self.mode)
        
        return self.result
        
    def ps_get_trigger_mode(self, stim_n):
        """
        Gets trigger mode for stimulator stim_n.
        
        Usage:
            mode, res = ps_get_trigger_mode(stim_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
            
        Returns:
            mode - one of the three options:
                PS_TRIG_SOFT - start stimulation from software
                PS_TRIG_PULSE - start stimulation when digital input transitions from 0V to 5V
                PS_TRIG_LEVEL - start stimulation when digital input transition from 0V to 5V, but
                    stimulation will idle after protocol completes if digital input is still at 5V
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.mode = c_int(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetTriggerMode@@YAHHPAW4PS_TRIG_MODE@@@Z")(self.stim_n, byref(self.mode))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetTriggerMode@@YAHHPEAW4PS_TRIG_MODE@@@Z")(self.stim_n, byref(self.mode))
        
        return self.mode.value, self.result
        
    def ps_set_vmon_scaling(self, stim_n, scaling):
        """
        Set the voltage monitor scaling to one of four pre-set ratios.
        
        Usage:
            res = ps_set_vmon_scaling(stim_n, scaling)
            
        Args:
            stim_n - stimulator number (starts from 1)
            scaling - one of four options (volts output per stimulation volts):
                PS_VMON_SCALING_0_25 - .25 V / V
                PS_VMON_SCALING_2_5 - 2.5 V / V
                PS_VMON_SCALING_25 - 25 V / V
                PS_VMON_SCALING_250 - 250 V / V
                
        Returns:
            res:
                -1 - invalid argument(s)
                0 - OK
                1 - device error
        """
        self.stim_n = c_int(stim_n)
        self.scaling = c_int(scaling)
        
        self.result = getattr(self.plexstim_dll, "?PS_SetVmonScaling@@YAHHW4PS_VMON_SCALING@@@Z")(self.stim_n, self.scaling)
        
        return self.result
        
    def ps_get_vmon_scaling(self, stim_n):
        """
        Gets the voltage monitor scaling, one of four pre-set ratios.
        
        Usage:
            scaling, res = ps_get_vmon_scaling(stim_n)
            
        Args:
            stim_n - stimulator number (starts from 1)
                
        Returns:
            scaling - one of four options (volts output per stimulation volts):
                PS_VMON_SCALING_0_25 - .25 V / V
                PS_VMON_SCALING_2_5 - 2.5 V / V
                PS_VMON_SCALING_25 - 25 V / V
                PS_VMON_SCALING_250 - 250 V / V
            res:
                -1 - invalid argument(s)
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.scaling = c_int(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetVmonScaling@@YAHHPAW4PS_VMON_SCALING@@@Z")(self.stim_n, byref(self.scaling))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetVmonScaling@@YAHHPEAW4PS_VMON_SCALING@@@Z")(self.stim_n, byref(self.scaling))
        
        return self.scaling.value, self.result
        
    def ps_set_auto_discharge(self, stim_n, enabled):
        """
        Enables or disables automatic discharge.
        
        ***WARNING***
        Disabling automatic discharge is ONLY recommended in very specific circumstances
        when the stimulator is used with the AStAR system.
        ***WARNING***
        
        Usage:
            res = ps_set_auto_discharge(stim_n, enabled)
        
        Args:
            stim_n - stimulator number (starts from 1)
            enabled - 1 to enable automatic discharge, 0 to disable
        Returns:
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.enabled = c_bool(enabled)
        
        self.result = getattr(self.plexstim_dll, "?PS_SetAutoDischarge@@YAHH_N@Z")(self.stim_n, self.enabled)
        
        return self.result
        
    def ps_get_auto_discharge(self, stim_n):
        """
        Checks to see if automatic discharge is enabled.
        
        ***WARNING***
        Disabling automatic discharge is ONLY recommended in very specific circumstances
        when the stimulator is used with the AStAR system.
        ***WARNING***
        
        Usage:
            is_auto_discharge, res = ps_get_auto_discharge(stim_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
        Returns:
            is_auto_discharge:
                0 - disabled
                1 - enabled
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.is_auto_discharge = c_int(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetAutoDischarge@@YAHHPA_N@Z")(self.stim_n, byref(self.is_auto_discharge))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetAutoDischarge@@YAHHPEA_N@Z")(self.stim_n, byref(self.is_auto_discharge))
        
        return self.is_auto_discharge.value, self.result
        
    def ps_channel_stim_started(self, stim_n, ch_n):
        """
        Checks if stimulation is started for channel ch_n.
        
        Usage:
            is_started, res = ps_channel_stim_started(stim_n, ch_n)
        
        Args:
            stim_n - stimulator number to finalize (starts from 1)
            ch_n - channel number to check if stimulation has started (starts from 1)
        Returns:
            is_started:
                0 - channel has not started
                1 - channel has started
            res:
                -1 - invalid argument(s)
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.is_started = c_bool(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_ChannelStimStarted@@YAHHHPA_N@Z")(c_int(self.stim_n), c_int(self.ch_n), byref(self.is_started))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_ChannelStimStarted@@YAHHHPEA_N@Z")(c_int(self.stim_n), c_int(self.ch_n), byref(self.is_started))
        
        return int(self.is_started.value), self.result
    
    def ps_get_n_stim(self):
        """
        Gets the number of available stimulators. The maximum is 4.
        
        Usage:
            n, res = ps_get_n_stim()
            
        Returns:
            n - number of stimulators
            
            res:
                0 - OK
        """
        self.num_stim = c_int(0)
        self.result = c_int(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetNStim@@YAHPAH@Z")(byref(self.num_stim))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetNStim@@YAHPEAH@Z")(byref(self.num_stim))
        
        return self.num_stim.value, self.result
    
    def ps_get_n_channels(self, stim_n):
        """
        Gets number of channels on stimulator stim_n.
        
        Usage:
            n_ch, res = ps_get_n_channels(stim_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
        Returns:
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.n_ch = c_int(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetNChannels@@YAHHPAH@Z")(self.stim_n, byref(self.n_ch))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetNChannels@@YAHHPEAH@Z")(self.stim_n, byref(self.n_ch))
        
        return self.n_ch.value, self.result
    
    def ps_get_extended_error_info(self, error_code):
        """ 
        Returns an description of the specified error code.
        
        Usage:
            error_string, res = ps_get_extended_error_info(error_code)
            
        Args:
            error_code - the error code for which you want a description
        Returns:
            error_string - description of error
            res:
                0 - OK
        """
        self.error_code = c_int(error_code)
        self.error_string = (c_char * 512)()
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetExtendedErrorInfo@@YAHHPAD@Z")(self.error_code, byref(self.error_string))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetExtendedErrorInfo@@YAHHPEAD@Z")(self.error_code, byref(self.error_string))
        
        return self.error_string.value, self.result
        
    def ps_get_description(self, stim_n):
        """
        Gets the description (hardware model) of the stimulator
        
        Usage:
            description, res = ps_get_description(stim_n)
        
        Args:
            stim_n - stimulator number to finalize (starts from 1)
        Returns:
            description - stimulator hardware description
            res:
                0 - OK
                1 - device error
        """
        self.stim_n = c_int(stim_n)
        self.description = (c_char * 128)()
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetDescription@@YAHHPAD@Z")(self.stim_n, byref(self.description))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetDescription@@YAHHPEAD@Z")(self.stim_n, byref(self.description))
        
        return self.description.value, self.result
    
    def ps_get_fw_version(self, stim_n):
        """
        Gets the firmware version of the stimulator
        
        Usage:
            fw_version, res = ps_get_fw_version(stim_n)
        
        Args:
            stim_n - stimulator number to finalize (starts from 1)
        Returns:
            fw_version - stimulator hardware firmware (string)
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.fw_version = (c_char * 512)()

        # LOCAL FIX (deviates from upstream Plexon 1.2.0): the shipped wrapper
        # uses the mangled name suffix "PAH"/"PEAH" (int*) when the out-buffer
        # is actually char*. Compare to ps_get_description / ps_get_serial_number
        # which correctly use "PAD"/"PEAD" (char*). With the wrong suffix the
        # getattr lookup either fails or resolves to the wrong overload.
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetFwVersion@@YAHHPAD@Z")(self.stim_n, byref(self.fw_version))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetFwVersion@@YAHHPEAD@Z")(self.stim_n, byref(self.fw_version))

        return self.fw_version.value, self.result
    
    def ps_get_serial_number(self, stim_n):
        """
        Get the serial number of stimulator stim_n.
        
        Usage:
            serial, res = ps_get_serial_number(stim_n)
            
        Args:
            stim_n - stimulator number (starts from 1)
            
        Returns:
            serial - serial number (string)
            res:
                0 - OK
                1 - device error
        """
        self.stim_n = c_int(stim_n)
        self.serial = (c_char * 512)()
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetSerialNumber@@YAHHPAD@Z")(self.stim_n, byref(self.serial))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetSerialNumber@@YAHHPEAD@Z")(self.stim_n, byref(self.serial))
        
        return self.serial.value, self.result
    
    def ps_get_stim_pattern_duration(self, stim_n, ch_n):
        """
        Gets duration of the whole stimulation pattern.
        
        Usage:
            duration, res = ps_get_stim_pattern_duration(stim_n, ch_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number

        Returns:
            duration - duration in milliseconds
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.duration = c_double(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_GetStimPatternDuration@@YAHHHPAN@Z")(self.stim_n, self.ch_n, byref(self.duration))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_GetStimPatternDuration@@YAHHHPEAN@Z")(self.stim_n, self.ch_n, byref(self.duration))
        
        return self.duration.value, self.result
    
    def ps_is_waveform_balanced(self, stim_n, ch_n):
        """
        Checks if the stimulation waveform is balanced (net charge is zero).
        
        Usage:
            is_balanced, res = ps_is_waveform_balanced(stim_n, ch_n)
        
        Args:
            stim_n - stimulator number (starts from 1)
            ch_n - channel number

        Returns:
            is_balanced - 1 if the waveform is balanced, 0 otherwise
            res:
                -1 - invalid argument
                0 - OK
        """
        self.stim_n = c_int(stim_n)
        self.ch_n = c_int(ch_n)
        self.is_balanced = c_bool(0)
        
        if self.platform == '32bit':
            self.result = getattr(self.plexstim_dll, "?PS_IsWaveformBalanced@@YAHHHPA_N@Z")(self.stim_n, self.ch_n, byref(self.is_balanced))
        else:
            self.result = getattr(self.plexstim_dll, "?PS_IsWaveformBalanced@@YAHHHPEA_N@Z")(self.stim_n, self.ch_n, byref(self.is_balanced))

        # LOCAL FIX (deviates from upstream Plexon 1.2.0): the shipped wrapper
        # references self.balanced (typo for self.is_balanced) which raises
        # AttributeError on every call.
        return int(self.is_balanced.value), self.result