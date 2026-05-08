function [quitProgram] = abortStim(stimNum)
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
errAbortStim = 1;                                          	% initialize errors
while errAbortStim ~= 0                                      % stop stimulating channel
    errAbortStim = PS_Abort(stimNum);  % get errors
    switch errAbortStim              % getting errors
        case 0                              % no errors
        case 1                                                          	% errors found
            msg = 'DEVICE ERROR';                                           % error stopping stimulation
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        case -1                                                             % errors found
            msg = 'INVALID ARGUMENT(S)';                                   	% invalid argument(s)
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errAbortStim == 1
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