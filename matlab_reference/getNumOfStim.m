function [numOfStim,quitProgram] = getNumOfStim()
%% Constants
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
errGetNStim = 1;
quitProgram = false;
numOfStim = 0;

%% Function
while errGetNStim ~= 0
    fprintf('Getting number of stimulators connected...'); % get stimulator(s)
    startTime = tic;
    [numOfStim,errGetNStim] = PS_GetNStim();            % getting stimulator(s)
    switch errGetNStim                                                  % getting errors
        case 0                                                          % no errors
            [endTime,unit] = getEndTime(startTime);
            if numOfStim > 1
                fprintf('\nNumber of stimulators: %d (%.3f %s)\n',numOfStim,endTime,unit);  % number of stimulator(s) found
            else
                fprintf('%d (%.2f %s)\n',numOfStim,endTime,unit);  % number of stimulator(s) found
            end
        case 1                                                          % stimulator(s) not found
            msg = 'NO STIMULATORS FOUND';                               % no stimulators
            disp(msg);
            quest = questdlg(masg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errGetNStim == 1
        switch quest                        % apply choice
            case BUTTON_QUIT               	% quit
                fprintf('Quitting...');	% quitting
                quitProgram = true;
                return;                     % exit program
            case BUTTON_TRY                 % try again
                fprintf('\nTrying again...\n\n');    % trying again
                try
                    PS_InitAllStim();
                    pause(5);
                catch
                end
            otherwise                       % cancel
                fprintf('Quitting...');	% quitting
                quitProgram = true;
                return;                     % exit program
        end
    end
end

end