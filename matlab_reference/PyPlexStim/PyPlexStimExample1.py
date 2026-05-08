# PyPlexStimExample1.py - Example 1 for PyPlexStim
#
# (c) 2015 Plexon, Inc., Dallas, Texas
# www.plexon.com - support@plexon.com
# 
# This software is provided as-is, without any warranty.
# You are free to modify or share this file, provided that the above
# copyright notice is kept intact.

# PyPlexStim Example 1 - Get some basic info from the stimulator,
# set a monitor channel and number of repetitions (using default stimulation
# parameters), and stimulate until the user presses 'Enter'.

from pyplexstim import PyPlexStim, PS_OK
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
    
    # Get and display the number of stimulators.
    n_stim, res = p.ps_get_n_stim()
    print('{} stimulators detected.'.format(n_stim))
    
    # Get and display the number of channels on each stimulator
    for n in range(n_stim):
        n_ch, res = p.ps_get_n_channels(n+1)
        print('Stimulator {0} has {1} channels'.format(n+1, n_ch))
    
    # Set the monitor channel of stimulator 1 to channel 1.
    res = p.ps_set_monitor_channel(1,1)
            
    # Set the number of stimulation repetitions on stimulator 1 channel 1 to 0, which
    # means repeat infinitely.
    res = p.ps_set_repetitions(1, 1, 0)
    
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
