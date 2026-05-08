function quitProgram = startMonoplarStim(stimNum,channelNum)
%% Constants
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
quitProgram = false;
errStartStim = true; 

%% Stimulation
while errStartStim                                      % start stimulating channel
    fprintf('Channel %d stimulation...',channelNum);
    errLoadChannel = PS_LoadAllChannels(stimNum);   % get errors
    errStartStimAllChannels = PS_StartStimAllChannels(stimNum);  % get errors
    errStartStim = any(errLoadChannel) && any(errStartStimAllChannels);
    switch errStartStimAllChannels      % getting errors
        case 0                      % no errors
            fprintf('ON.\n');   % channel stimulating
        otherwise                                                               % errors found
            msg = 'ERROR STARTING STIMULATION';                                 % error starting stimulation
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errStartStim
        switch quest                        % apply choice
            case BUTTON_QUIT            	% quit
                fprintf('Quitting...\n\n');	% quitting
                quitProgram = true;
                return;                     % exit program
            case BUTTON_TRY               	% try again
                fprintf('Trying again...\n\n');% trying again
            otherwise                       % cancel
                fprintf('Quitting...\n\n');	% quitting
                quitProgram = true;
                return;                     % exit program
        end
    end
end

end