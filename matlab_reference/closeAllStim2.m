function isQuit = closeAllStim2()
%% Constants
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
% Options
% opts.Default = BUTTON_TRY;       % option dedault
% opts.Interpreter = 'tex';   % option LaTeX

%% Variables
isQuit = false;

%% Check
for channel_idx = 1:16
    [type,~] = PS_GetPatternType(1,channel_idx);
    if type
         PS_SetPatternType(1,channel_idx,0);
    end
end

%% Function
errCloseAllStim = 1;                    % initialize errors
while errCloseAllStim ~= 0              % close stimulators
    fprintf('Closing all stimulators...');
    startTime = tic;
    errCloseAllStim = PS_CloseAllStim();% get errors
    switch errCloseAllStim              % getting errors
        case {0,1}                                         % closing stimulators
            errCloseAllStim = 0;
            [endTime,unit] = getEndTime(startTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);   % stimulators closed
%         case 1                                          % errors found
%             msg = 'ERROR CLOSING ALL STIMULATORS';      % stimulators not closed
%             fprintf('\n%s',msg);
%             quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errCloseAllStim ~= 0
        switch quest                        % apply choice
            case BUTTON_TRY                     % try again
                fprintf('\nTrying again...\n'); % trying again
            case BUTTON_QUIT                    % quit
                fprintf('\nQuitting...');	% quitting
                isQuit = true;
                fprintf('OK\n');
                return;                     % exit program
            otherwise                       % cancel
                fprintf('\nQuitting...');	% quitting
                isQuit = true;
                fprintf('OK\n');
                return;                     % exit program
        end
    end
end

end