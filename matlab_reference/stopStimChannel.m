function quitProgram = stopStimChannel(stimNum,channelNum)
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

%% Function
errStartStimChannel = 1;                                          	% initialize errors
while errStartStimChannel ~= 0                                      % stop stimulating channel
    fprintf('Channel %d stimulation...',channelNum);
    [stimOn,~] = PS_ChannelStimStarted(1,channelNum);
    if stimOn
        errStartStimChannel = PS_StopStimChannel(stimNum,channelNum);  % get errors
    else
        errStartStimChannel = 0;
    end
    switch errStartStimChannel              % getting errors
        case 0                              % no errors
            fprintf('OFF.\n');   % channel stopping
            pause(0.5);
%                 disp('Stopped Stimulation OK.');% channels stimulating
        case 1                                                                  % errors found
            msg = 'ERROR STARTING STIMULATION';                                 % error starting stimulation
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        case 4                                                                  % errors found
            msg = 'WRONG TRIGGER MODE ON STIMULATOR';                           % wrong trigger mode
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        case -1                                                                 % errors found
            msg = 'INVALID ARGUMENT(S)';                                        % invalid argument(s)
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errStartStimChannel == 1
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