function [quitProgram] = setNumOfPulses(stimNum,channelNum,numOfPulses)
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
if numOfPulses == 0 || isinf(numOfPulses)                 % infinite number of pulses
    numOfPulses = 0;
    numOfPulses_str = 'infinite';   % infinite number of pulses (string)
else                                        % finite number of pulses
    numOfPulses_str = num2str(numOfPulses); % finite number of pulses (string)
end
fprintf('Setting Channel %d number of pulses...',channelNum);
errSetRepetitions = 1;                                                      % initialize errors
while errSetRepetitions ~= 0                                                 % set number of pulses
    errSetRepetitions = PS_SetRepetitions(stimNum,channelNum,numOfPulses);  % get errors
    switch errSetRepetitions                            % getting errors
         case 0                                         % no errors
             fprintf('%s pulses.\n',numOfPulses_str);   % number of pusles set
         case -1                            % errors found
             msg = 'INVALID ARGUMENT(S)';   % invalid arguement(s)
             fprintf('\n%s\n',msg);
             quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errSetRepetitions ~= 0
        switch quest                        % apply choice
            case BUTTON_QUIT            	% quit
                fprintf('\nQuitting...\n\n');	% quitting
                quitProgram = true;
                return;                     % exit program
            case BUTTON_TRY               	% try again
                fprintf('\nTrying again...\n\n');% trying again
%                 try
%                     pause(5);
%                     PS_InitAllStim();
%                 catch
%                 end
            otherwise                       % cancel
                fprintf('\nQuitting...\n\n');	% quitting
                quitProgram = true;
                return;                     % exit program
        end
    end
end


end