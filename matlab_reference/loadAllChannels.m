function isQuit = loadAllChannels(stimNum)
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
fprintf('Loading parameters to stimulator...');
startTime = tic;
% patternType_arr = zeros(16,1);
% for channel_idx = 1:16
% [type,err] = PS_GetPatternType(1,channel_idx);
% 
% end
errLoadChannel = 1;                                 % initialize errors
while errLoadChannel ~= 0                           % load channel stimulation
    errLoadChannel = PS_LoadAllChannels(stimNum);   % get errors
    switch errLoadChannel                               % getting errors
        case 0                                          % no errors
            [endTime,unit] = getEndTime(startTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);                                     % loaded channel stimulation
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
    if errLoadChannel == 1
        switch quest                        % apply choice
            case BUTTON_QUIT            	% quit
                fprintf('Quitting...\n\n');	% quitting
                isQuit = true;
                break;                     % exit program
            case BUTTON_TRY               	% try again
                fprintf('Trying again...\n\n');% trying again
            otherwise                       % cancel
                fprintf('Quitting...\n\n');	% quitting
                isQuit = true;
                break;                     % exit program
        end
    end
end
if isQuit
    return;
end

end