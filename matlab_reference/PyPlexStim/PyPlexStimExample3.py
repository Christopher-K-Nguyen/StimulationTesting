# PyPlexStimExample3.py - Example 3 for PyPlexStim
#
# (c) 2015 Plexon, Inc., Dallas, Texas
# www.plexon.com - support@plexon.com
# 
# This software is provided as-is, without any warranty.
# You are free to modify or share this file, provided that the above
# copyright notice is kept intact.

# PyPlexStim Example 3 - Load a channel with an arbitrary waveform shape, and stimulate
# until the user presses 'Enter'

from pyplexstim import PyPlexStim, PS_OK, PS_PATTERN_ARB
import sys
import os

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
    
    # Set stimulator 1 channel 1 to stimulate with an arbitrary waveform shape (as opposed
	# to a rectangular pulse).
    res = p.ps_set_pattern_type(1, 1, PS_PATTERN_ARB)
    
    # Arbitrary pattern file path.
    pattern_path = "c:\\PlexonData\Stim-2\\Waveform pattern files\\3_pulse_burst_fixed.pat"
    
    # Load arbitrary waveform .pat file on stimulator 1 channel 1
    res = p.ps_load_arb_pattern(1, 1, pattern_path)
    
    # Display some information on the loaded pattern on stimulator 1 channel 1.
    # See the comments in pyplexstimlib.py for more information on what this is.
    n_points, res = p.ps_get_n_points_arb_pattern(1, 1)
    coords, res = p.ps_get_arb_pattern_points(1, 1, n_points)
    print('Pattern has {} points'.format(n_points))
    for x, y in zip(coords[::2], coords[1::2]):
        print('x:{} y:{}'.format(x,y))
    
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