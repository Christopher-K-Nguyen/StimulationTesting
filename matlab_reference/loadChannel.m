function isQuit = loadChannel(numOfStim,channelNum)
%% Constants
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
isQuit = false;

%% Function
fprintf('Loading Channel %d to stimulator...',channelNum);
for stimNum = 1:numOfStim   % loop through stimulators   
    errLoadChannel = 1;                                     % initialize errors
    while errLoadChannel ~= 0                               % load channel stimulation
        errLoadChannel = PS_LoadChannel(stimNum,channelNum);% get errors
        switch errLoadChannel                               % getting errors
            case 0                                          % no errors
                 fprintf('OK.\n');	% loaded channel stimulation
            case 1                                                          % errors found
                msg = 'ERROR LOADING PARAMETERS';                           % errors loading parameters
                quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
            case 3                                                          % errors found
                msg = 'CRC ERROR';                                          % CRC error
                quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
            case 6                                                          % errors found
                msg = 'STIMULATION PATTERN NOT READY';                      % pattern not ready
                quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
            case -1                                                         % errors found
                msg = 'INVALID ARGUMENT(S)';                                % invalide argument(s)
                quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        end
        if errLoadChannel ~= 0
            switch quest                        % apply choice
                case BUTTON_QUIT            	% quit
                    fprintf('Quitting...\n\n');	% quitting
                    isQuit = true;
                    return;                     % exit program
                case BUTTON_TRY               	% try again
                    fprintf('Trying again...\n\n');% trying again
%                     try
% 			            pause(5);
%                         PS_InitAllStim();
%                     catch
%                     end
                otherwise                       % cancel
                    fprintf('Quitting...\n\n');	% quitting
                    isQuit = true;
                    return;                     % exit program
            end
        end
    end
end

end