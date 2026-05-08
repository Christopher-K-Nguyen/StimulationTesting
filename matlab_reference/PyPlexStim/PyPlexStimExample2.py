# PyPlexStimExample2.py - Example 2 for PyPlexStim
#
# (c) 2015 Plexon, Inc., Dallas, Texas
# www.plexon.com - support@plexon.com
# 
# This software is provided as-is, without any warranty.
# You are free to modify or share this file, provided that the above
# copyright notice is kept intact.

# PyPlexStim Example 2 - Set up a rectangular pulse, and stimulate
# until the user presses 'Enter'

from pyplexstim import PyPlexStim, PS_OK, PS_PATTERN_RECT
import sys

if __name__ == "__main__":
    # Create an instance of the PyPlexStim class.
    p = PyPlexStim()
    
    # Initialize all stimulators.
    res = p.ps_init_all_stim()
    
    # If res isn't PS_OK (integer value 0), print out the error information.
    # For brevity, the status code return values of the rest of the functions in
    # this program will be ignored.
    if res != PS_OK:
        info, res = p.ps_get_extended_error_info(res)
        print(info)
        sys.exit()
    
    # Set the monitor channel of stimulator 1 to channel 1.
    res = p.ps_set_monitor_channel(1,1)
    
    # Set the number of stimulation repetitions on stimulator 1 channel 1 to 0, which
    # means repeat infinitely.
    res = p.ps_set_repetitions(1, 1, 0)
    
    # Parameters for a rectangular pulse
    pattern = (100, -100, 25, 25, 25)
    
    # Set stimulator 1 channel 1 to stimulate with a rectangular pulse (as opposed to
    # an arbitrary waveform shape).
    res = p.ps_set_pattern_type(1, 1, PS_PATTERN_RECT)
    
    # Set stimulator rectangular pulse pattern on stimulator 1 channel 1.
    res = p.ps_set_rect_param(1, 1, pattern)
    
    # Load stimulator 1 channel 1 (prepare it for stimulation)
    res = p.ps_load_channel(1, 1)
    
    # Start stimulating on stimulator 1 channel 1
    res = p.ps_start_stim_channel(1, 1)
    
    # Wait for 'Enter' to be pressed (stimulation will continue even while
    # the program is paused waiting for user input).
    input("Press Return to Stop")
    
    # Stop stimulating on stimulator 1 channel 1
    res = p.ps_stop_stim_channel(1, 1)
    
    # Close all stimulators
    res = p.ps_close_all_stim()