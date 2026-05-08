function quitProgram = stopStimAllChannels(stimNum)
%% Constants
% Buttons
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_QUIT = 'Quit';
% Titles
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
quitProgram = 0;

%% Function
fprintf("All channels' stimulation...");
startTime = tic;
errStopStimAllChannel = 1;                                          	% initialize errors
stimOn_arr = zeros(1,16);
while errStopStimAllChannel ~= 0                                      % start stimulating channel
    for channel_idx = 1:16
        [stimOn,~] = PS_ChannelStimStarted(1,channel_idx);
        stimOn_arr(channel_idx) = stimOn;
    end
    if any(stimOn_arr)
        errStopStimAllChannel = PS_StopStimAllChannels(stimNum);  % get errors
    else
        errStopStimAllChannel = 0;
    end
    switch errStopStimAllChannel              % getting errors
        case 0                              % no errors
            [endTime,unit] = getEndTime(startTime);
            fprintf('OFF (%.2f %s)\n',endTime,unit);
        case 1                                                          	% errors found
            msg = 'DEVICE ERROR';                                           % error stopping stimulation
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        case -1                                                             % errors found
            msg = 'INVALID ARGUMENT(S)';                                   	% invalid argument(s)
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errStopStimAllChannel ~= 0
        switch quest                        % apply choice
            case BUTTON_QUIT            	% quit
                fprintf('\nQuitting...\n\n');	% quitting
                quitProgram = 1;
                return;                     % exit program
            case BUTTON_TRY               	% try again
                fprintf('\nTrying again...\n\n');% trying again
            otherwise                       % cancel
                fprintf('\nQuitting...\n\n');	% quitting
                quitProgram = 1;
                return;                     % exit program
        end
    end
end

end