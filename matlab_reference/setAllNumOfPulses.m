function isQuit = setAllNumOfPulses(numOfPulses)
%% Constants
NUM_OF_CHANNELS = 16;
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
fprintf('Setting number of pulses for all channels...');
startTime = tic;
if numOfPulses == 0 || isinf(numOfPulses)                 % infinite number of pulses
    numOfPulses = 0;
    numOfPulses_use = 'infinite';   % infinite number of pulses (string)
else                                        % finite number of pulses
    numOfPulses_use = addCommas(numOfPulses);
end
errSetRepetitions   = 1;                                                    % initialize errors
while errSetRepetitions ~= 0                                                 % set number of pulses
    for channelNum = 1:NUM_OF_CHANNELS
        numOfPulses_check = [];
        while ~isequal(numOfPulses_check,numOfPulses)
            errSetRepetitions = PS_SetRepetitions(1,channelNum,numOfPulses);  % get errors
            [numOfPulses_check,~] = PS_GetRepetitions(1,channelNum);
        end
    end
    switch errSetRepetitions                            % getting errors
         case 0                                         % no errors
             [endTime,unit] = getEndTime(startTime);
             fprintf('%s pulses \t\t(%.2f %s)\n',numOfPulses_use,endTime,unit);   % number of pusles set
         case -1                            % errors found
             msg = 'INVALID ARGUMENT(S)';   % invalid arguement(s)
             fprintf('\n%s\n',msg);
             quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errSetRepetitions ~= 0
        switch quest                        % apply choice
            case BUTTON_QUIT            	% quit
                fprintf('\nQuitting...\n\n');	% quitting
                isQuit = true;
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
                isQuit = true;
                return;                     % exit program
        end
    end
end


end