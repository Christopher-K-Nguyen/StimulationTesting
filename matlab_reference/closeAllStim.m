function quitProgram = closeAllStim()
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
errCloseAllStim = 1;                    % initialize errors
while errCloseAllStim ~= 0              % close stimulators
    fprintf('Closing all stimulators...');
    errCloseAllStim = PS_CloseAllStim();% get errors
    switch errCloseAllStim              % getting errors
        case 0                                          % closing stimulators
            fprintf('OK\n');   % stimulators closed
        case 1                                          % errors found
            msg = 'ERROR CLOSING ALL STIMULATORS';      % stimulators not closed
            fprintf('\n%s',msg);
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errCloseAllStim ~= 0
        switch quest                        % apply choice
            case BUTTON_TRY                     % try again
                fprintf('\nTrying again...\n'); % trying again
            case BUTTON_QUIT                    % quit
                fprintf('\nQuitting...');	% quitting
                quitProgram = true;
                fprintf('OK\n');
                return;                     % exit program
            otherwise                       % cancel
                fprintf('\nQuitting...');	% quitting
                quitProgram = true;
                fprintf('OK\n');
                return;                     % exit program
        end
    end
end

end